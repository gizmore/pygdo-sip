import asyncio
from dataclasses import dataclass, field
import hashlib
import ipaddress
import logging
from pathlib import Path
import random
import re
import socket
import struct
import tempfile
import tomllib
from uuid import uuid4
import wave

from gdo.base.Logger import Logger
from gdo.base.Message import Message
from gdo.base.Render import Mode
from gdo.core.Connector import Connector
from gdo.core.GDO_Server import GDO_Server
from gdo.core.GDT_UserType import GDT_UserType
from gdo.mira.util import send_to_mira
from gdo.sip.GDT_Phone import GDT_Phone


def load_secrets(path: str | Path) -> dict[str, str]:
    with open(path, 'rb') as handle:
        values = tomllib.load(handle).get('sip', {})
    required = ('serv_url', 'serv_username', 'serv_password')
    if not all(isinstance(values.get(key), str) and values[key] for key in required):
        raise ValueError('SIP secrets require serv_url, serv_username and serv_password.')
    return {key: str(value) for key, value in values.items()}


def registration_duration(values: dict[str, str]) -> int:
    """Return a sensible SIP registration interval without accepting junk."""
    try:
        duration = int(values.get('serv_register_duration', '300'))
    except (TypeError, ValueError):
        raise ValueError('SIP registration duration must be an integer.') from None
    if not 300 <= duration <= 3600:
        raise ValueError('SIP registration duration must be between 300 and 3600.')
    return duration


def patch_pysip_register_duration() -> None:
    """Make PySIP honour its ``register_duration`` constructor argument.

    PySIP stores that argument but currently emits ``Expires: 60`` in every
    REGISTER request.  A Speedport rejects that interval with 423 and asks for
    at least 300 seconds.  Keep the compatibility patch local to this process
    instead of changing a third-party installation.
    """
    from PySIP.sip_client import SipClient
    from PySIP.sip_call import SipCall
    from PySIP.sip_core import DialogState, SIPCompatibleMethods, SipMessage

    if getattr(SipClient, '_pygdo_register_duration', False):
        return
    original = SipClient.build_register_message
    original_client_init = SipClient.__init__

    def client_init(client, *args, **kwargs):
        original_client_init(client, *args, **kwargs)
        client._pygdo_auth_username = getattr(
            client.sip_core, '_pygdo_auth_username', client.username,
        )

    def build_register_message(client, auth=False, received_message=None, unregister=False):
        if getattr(client.sip_core, '_pygdo_speedport_register', False) and not unregister:
            return build_speedport_register_message(client, auth, received_message)

        identity = client.username
        auth_username = getattr(client, '_pygdo_auth_username', identity)
        if auth and auth_username != identity:
            # PySIP has one username field for both the AOR and digest login.
            # Temporarily switch only while computing the digest/header, then
            # restore the AOR in To and Contact below.
            core_identity = client.sip_core.username
            client.username = auth_username
            client.sip_core.username = auth_username
            try:
                message = original(client, auth, received_message, unregister)
            finally:
                client.username = identity
                client.sip_core.username = core_identity
            if message is not None:
                message = message.replace(
                    f'To: <sip:{auth_username}@', f'To: <sip:{identity}@',
                ).replace(
                    f'Contact: <sip:{auth_username}@', f'Contact: <sip:{identity}@',
                )
        else:
            message = original(client, auth, received_message, unregister)
        if message is not None and not unregister:
            message = message.replace(
                'Expires: 60\r\n',
                f'Expires: {client.register_duration}\r\n',
            )
        return message

    original_parse = SipMessage.parse

    def parse(message):
        result = original_parse(message)
        fill_pysip_register_challenge(message)
        return result

    original_message_handler = SipClient.message_handler

    async def message_handler(client, message):
        if (
            str(getattr(message, 'method', '')).upper() == 'REGISTER'
            and int(getattr(getattr(message, 'status', None), 'value', 0)) == 401
            and not getattr(message, 'rport', None)
        ):
            try:
                message.rport = str(client.sip_core.get_extra_info('sockname')[1])
            except (TypeError, ValueError, IndexError):
                pass
        await original_message_handler(client, message)

    original_construct_invite = SipCall.construct_invite_message
    original_error_handler = SipCall.error_handler
    original_call_message_handler = SipCall.message_handler
    original_setup_local_session = SipCall.setup_local_session

    def construct_invite_message(call, ip, port, cseq, auth_header=None, received_message=None):
        if not getattr(call.sip_core, '_pygdo_speedport_register', False):
            return original_construct_invite(
                call, ip, port, cseq, auth_header, received_message,
            )
        # Keep the local-PBX URI compact and avoid outbound-proxy-only
        # extensions the router does not implement.  ``SipCall`` already
        # keeps host and port separately; this is a Speedport compatibility
        # envelope, not a workaround for a duplicate port.
        registrar = call.server
        uri = f'sip:{call.callee}@{registrar}'
        branch_id = call.sip_core.gen_branch()
        transaction = call.dialogue.add_transaction(branch_id, 'INVITE')
        # PySIP only advances PREDIALOG after an authenticated INVITE.  The
        # local Speedport accepts the first INVITE without challenge, so mark
        # this dialog INITIAL before its 180/200 replies arrive; otherwise
        # PySIP discards the answer SDP and cannot start RTP.
        if call.dialogue.state is DialogState.PREDIALOG:
            call.dialogue.state = DialogState.INITIAL
        message = (
            f'INVITE {uri} SIP/2.0\r\n'
            f'Via: SIP/2.0/UDP {ip}:{port};branch={branch_id};rport\r\n'
            'Max-Forwards: 70\r\n'
            f'From: <sip:{call.caller_id}@{registrar}>;tag={call.dialogue.local_tag}\r\n'
            f'To: <{uri}>\r\n'
            f'Call-ID: {call.call_id}\r\n'
            f'CSeq: {transaction.cseq} INVITE\r\n'
            f'Contact: <sip:{call.username}@{ip}:{port}>\r\n'
            f'Allow: {", ".join(SIPCompatibleMethods)}\r\n'
            'Supported: replaces, timer\r\n'
            'Content-Type: application/sdp\r\n'
        )
        if auth_header:
            message += auth_header
        body = str(call.dialogue.local_session_info)
        return message + f'Content-Length: {len(body.encode())}\r\n\r\n{body}'

    async def error_handler(call, message):
        """Log only a response code for failed calls, never SIP headers."""
        status = getattr(message, 'status', None)
        code = getattr(status, 'code', getattr(status, 'value', None))
        if isinstance(code, int) and 400 <= code <= 699:
            Logger.warning(f'SIP INVITE rejected with status {code}.')
        await original_error_handler(call, message)

    async def call_message_handler(call, message):
        """Record an INVITE result code without retaining SIP payloads."""
        if str(getattr(message, 'method', '')).upper() == 'INVITE':
            status = getattr(message, 'status', None)
            code = getattr(status, 'code', getattr(status, 'value', None))
            if isinstance(code, int):
                Logger.debug(f'SIP INVITE response status {code}.')
        await original_call_message_handler(call, message)

    def setup_local_session(call):
        if not getattr(call.sip_core, '_pygdo_speedport_register', False):
            return original_setup_local_session(call)
        # The router expects a conventional G.711 A-law offer.  PySIP's
        # generic SDP uses an arbitrary (occasionally odd) RTP port and extra
        # attributes intended for Internet providers.  An even RTP port and
        # this minimal PCMA offer work with the router's local IP-PBX.
        from PySIP.sip_core import SipMessage

        ip = call.sip_core.get_local_ip()
        port = random.randrange(10_000, 20_000, 2)
        session_id = random.getrandbits(31)
        ssrc = random.getrandbits(32)
        sdp = (
            'v=0\r\n'
            f'o=- {session_id} {session_id} IN IP4 {ip}\r\n'
            's=-\r\n'
            f'c=IN IP4 {ip}\r\n'
            't=0 0\r\n'
            f'm=audio {port} RTP/AVP 8\r\n'
            'a=rtpmap:8 PCMA/8000\r\n'
            f'a=rtcp:{port + 1} IN IP4 {ip}\r\n'
            'a=ptime:20\r\n'
            'a=sendrecv\r\n'
            f'a=ssrc:{ssrc} cname=pygdo-sip\r\n'
        )
        call.dialogue._local_session_info = SipMessage.parse_sdp(SipMessage.sdp_to_dict(sdp))

    SipClient.build_register_message = build_register_message
    SipClient.__init__ = client_init
    SipClient.message_handler = message_handler
    SipCall.construct_invite_message = construct_invite_message
    SipCall.error_handler = error_handler
    SipCall.message_handler = call_message_handler
    SipCall.setup_local_session = setup_local_session
    SipMessage.parse = parse
    SipClient._pygdo_register_duration = True


def build_speedport_register_message(client, auth: bool, received_message=None) -> str | None:
    """Build the small RFC 3261 REGISTER dialect the Speedport accepts.

    PySIP registers against a registrar URI (``sip:host``).  The Speedport's
    local PBX instead expects the registered extension in that URI
    (``sip:**71@speedport.ip``).  It also rejects PySIP's RFC 5626 ``ob`` and
    ``alias`` decorations.  This remains a narrowly scoped compatibility path
    for a local Speedport account; all other PySIP accounts retain its native
    message builder.
    """
    identity = client.username
    registrar = client.server.rsplit(':', 1)[0]
    uri = f'sip:{identity}@{registrar}'
    try:
        local_ip, local_port = client.sip_core.get_extra_info('sockname')
    except (TypeError, ValueError, IndexError):
        return None
    branch = f'z9hG4bK{uuid4().hex}'
    local_tag = client.register_tags.get('local_tag')
    if not local_tag:
        local_tag = uuid4().hex
        client.register_tags['local_tag'] = local_tag
    cseq = next(client.register_counter)
    client.register_tags['type'] = 'REGISTER'
    client.register_tags['cseq'] = cseq
    to_tag = ''
    authorization = ''
    if auth:
        if received_message is None:
            return None
        realm = getattr(received_message, 'realm', None)
        nonce = getattr(received_message, 'nonce', None)
        if not realm or not nonce:
            return None
        to_tag_value = getattr(received_message, 'to_tag', None)
        if to_tag_value:
            to_tag = f';tag={to_tag_value}'
        auth_username = getattr(client, '_pygdo_auth_username', identity)
        a1 = hashlib.md5(
            f'{auth_username}:{realm}:{client.password}'.encode(), usedforsecurity=False,
        ).hexdigest()
        a2 = hashlib.md5(f'REGISTER:{uri}'.encode(), usedforsecurity=False).hexdigest()
        qop_match = re.search(r'(?:^|,)\s*qop="?([^",]+)', str(getattr(received_message, 'qop', '')))
        # The helper below preserves qop as ``auth`` when the router sends it;
        # tolerate RFC 2069 challenges too for older firmware.
        qop = qop_match.group(1) if qop_match else getattr(received_message, 'qop', None)
        if qop == 'auth':
            nc = '00000001'
            cnonce = uuid4().hex
            response = hashlib.md5(
                f'{a1}:{nonce}:{nc}:{cnonce}:auth:{a2}'.encode(), usedforsecurity=False,
            ).hexdigest()
            authorization = (
                f'Authorization: Digest username="{auth_username}", realm="{realm}", '
                f'nonce="{nonce}", uri="{uri}", response="{response}", '
                f'algorithm=MD5, qop=auth, nc={nc}, cnonce="{cnonce}"\r\n'
            )
        else:
            response = hashlib.md5(
                f'{a1}:{nonce}:{a2}'.encode(), usedforsecurity=False,
            ).hexdigest()
            authorization = (
                f'Authorization: Digest username="{auth_username}", realm="{realm}", '
                f'nonce="{nonce}", uri="{uri}", response="{response}", algorithm=MD5\r\n'
            )
    return (
        f'REGISTER {uri} SIP/2.0\r\n'
        f'Via: SIP/2.0/UDP {local_ip}:{local_port};branch={branch};rport\r\n'
        'Max-Forwards: 70\r\n'
        f'From: <sip:{identity}@{registrar}>;tag={local_tag}\r\n'
        f'To: <sip:{identity}@{registrar}>{to_tag}\r\n'
        f'Call-ID: {client.call_id}\r\n'
        f'CSeq: {cseq} REGISTER\r\n'
        f'Contact: <sip:{identity}@{local_ip}:{local_port}>;expires={client.register_duration}\r\n'
        f'Expires: {client.register_duration}\r\n'
        f'{authorization}'
        'Content-Length: 0\r\n\r\n'
    )


def fill_pysip_register_challenge(message) -> None:
    """Fill fields PySIP misses in a Speedport's standard 401 response.

    The router legitimately omits Via's optional ``received=`` parameter on a
    LAN.  PySIP currently stops parsing the entire response at that point and
    consequently loses the digest challenge.  This reads only in-memory SIP
    headers; it does not log the message or its credentials.
    """
    raw = str(getattr(message, 'data', ''))
    # PySIP normalises response status inconsistently, so identify the digest
    # challenge by its header instead of relying on the parsed status value.
    if 'www-authenticate:' not in raw.lower():
        return
    headers = {}
    for line in raw.splitlines():
        name, separator, value = line.partition(':')
        if separator:
            headers[name.strip().lower()] = value.strip()
    challenge = headers.get('www-authenticate', '')
    realm = re.search(r'realm="([^"]+)"', challenge)
    nonce = re.search(r'nonce="([^"]+)"', challenge)
    qop = re.search(r'qop="([^"]+)"', challenge)
    rport = re.search(r'(?:^|;)rport=([0-9]+)(?:;|$)', headers.get('via', ''))
    if realm:
        message.realm = realm.group(1)
    if nonce:
        message.nonce = nonce.group(1)
    if qop:
        message.qop = qop.group(1)

    if rport:
        message.rport = rport.group(1)


async def prepare_pysip_register_challenge(message, sip_core=None) -> None:
    """Compatibility callback retained for tests and external adapters."""
    fill_pysip_register_challenge(message)
    if not getattr(message, 'rport', None) and sip_core is not None:
        try:
            message.rport = str(sip_core.get_extra_info('sockname')[1])
        except (TypeError, ValueError, IndexError):
            pass


def phone_username(caller_id: object) -> str | None:
    number = GDT_Phone.normalize_number(caller_id)
    # Keep one stable caller identity for +49… and 0049…, while preserving
    # the explicit 00 prefix in the separate dialing representation.
    return number[2:] if number and number.startswith('00') else number


def phone_dialstring(phone: object) -> str | None:
    """Return a SIP dial string while preserving an explicit 00 prefix.

    A caller/user identity is canonicalised without punctuation, but a router
    may require ``00`` for an international outbound call.  Keep those two
    responsibilities separate.
    """
    if not isinstance(phone, str):
        return None
    value = phone.strip()
    if value.startswith('+'):
        value = f'00{value[1:]}'
    if any(char not in '0123456789 ()-./' for char in value):
        return None
    digits = ''.join(char for char in value if char.isdigit())
    return digits if 7 <= len(digits) <= 32 else None


def is_private_sip_registrar(endpoint: str) -> bool:
    """Whether a SIP endpoint is a local-router address."""
    host = endpoint.strip()
    if host.startswith('['):
        host = host[1:].split(']', 1)[0]
    elif host.count(':') == 1:
        host = host.rsplit(':', 1)[0]
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(result[4][0])
                for result in socket.getaddrinfo(host, None, type=socket.SOCK_DGRAM)
            }
        except OSError:
            return False
    return any(address.is_private or address.is_loopback for address in addresses)


def chatstream_begin(channel_id: int, language: str = 'de') -> str:
    """Announce one newly opened SIP conversation to the chat stream.

    A channel already identifies the individual call.  Caller identity stays
    on the connector/session side, rather than being copied into the command;
    a phone number is an address, not an authentication claim.
    """
    if isinstance(channel_id, str) and channel_id.isdecimal():
        channel_id = int(channel_id)
    if not isinstance(channel_id, int) or isinstance(channel_id, bool) or channel_id <= 0:
        raise ValueError('channel_id must be a positive integer.')
    if not isinstance(language, str) or not re.fullmatch(r'[a-z]{2}', language):
        raise ValueError('language must be a two-letter lowercase code.')
    return f'$chatstreambegin {channel_id} --lang={language}'


def chatstream(channel_id: int, text: str) -> str:
    return f'$chatstream {channel_id} {text}'


class SpeechTurn:
    def __init__(self, emit, threshold: int = 120, silence_frames: int = 12,
                 min_frames: int = 8, noise_margin: int = 100,
                 max_frames: int = 100):
        self.emit = emit
        self.threshold = threshold
        self.noise_margin = noise_margin
        self.noise_floor: float | None = None
        self.silence_frames = silence_frames
        self.min_frames = min_frames
        self.max_frames = max_frames
        self.frames: list[bytes] = []
        self.silence = 0
        self.tasks: set[asyncio.Task] = set()

    def feed(self, frame: bytes) -> None:
        if not isinstance(frame, bytes) or len(frame) % 2:
            return
        level = self._level(frame)
        # Learn only between turns.  A caller's voice must not be absorbed
        # into the noise floor while they are speaking.
        if not self.frames and (self.noise_floor is None or level < self.trigger_level):
            self._learn_noise(level)
        loud = level >= self.trigger_level
        if not self.frames and not loud:
            return
        self.frames.append(frame)
        self.silence = 0 if loud else self.silence + 1
        # Whisper works on finite audio files.  Flush on a short pause for a
        # low-latency turn boundary, but also bound continuous speech to about
        # two seconds of 20 ms RTP frames so every part of a long utterance is
        # transcribed while the call remains open.
        if self.silence >= self.silence_frames or len(self.frames) >= self.max_frames:
            self.flush()

    @property
    def trigger_level(self) -> int:
        floor = 0 if self.noise_floor is None else self.noise_floor
        return max(self.threshold, round(floor + self.noise_margin))

    def _learn_noise(self, level: int) -> None:
        if self.noise_floor is None:
            # A call can begin with speech.  Do not let that first word set an
            # unreachable threshold before it has a chance to start a turn.
            self.noise_floor = float(min(level, self.threshold))
        else:
            # Slow adaptation filters short clicks without making a quiet room
            # progressively less sensitive.
            self.noise_floor = (self.noise_floor * 0.95) + (level * 0.05)

    def flush(self) -> None:
        if len(self.frames) < self.min_frames:
            self.frames.clear()
            self.silence = 0
            return
        frames, self.frames = b''.join(self.frames), []
        self.silence = 0
        task = asyncio.create_task(self.emit(frames))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def close(self) -> None:
        self.flush()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

    @staticmethod
    def _level(frame: bytes) -> int:
        return max((abs(value[0]) for value in struct.iter_unpack('<h', frame)), default=0)


@dataclass
class SIPCall:
    call: object
    user: object
    channel: object
    call_id: str = field(default_factory=lambda: uuid4().hex)
    speech: SpeechTurn | None = None
    outgoing: bool = False
    greeted: bool = False
    chat_started: bool = False
    task: asyncio.Task | None = None
    received_frames: int = 0
    transcript_sequence: int = 0
    monitor_task: asyncio.Task | None = None


class SIP(Connector):
    def __init__(self):
        super().__init__()
        self._account = None
        self._calls: dict[int, SIPCall] = {}
        self._account_factory = None

    def get_render_mode(self) -> Mode:
        return Mode.render_txt

    def render_user_connect_help(self) -> str:
        return 'Local SIP bridge'

    def gdo_needs_authentication(self) -> bool:
        return True

    def gdo_has_channels(self) -> bool:
        return True

    @classmethod
    def get_server(cls) -> GDO_Server:
        return GDO_Server.get_by_connector('sip')

    def secrets_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / 'secrets.toml'

    def sip_account(self, values: dict[str, str]):
        if self._account_factory:
            return self._account_factory(values)
        try:
            from PySIP.sip_account import SipAccount
            from PySIP.utils.logger import logger as pysip_logger
        except ImportError as error:
            raise RuntimeError('PySIPio is not installed.') from error
        # PySIP includes the account name in its INFO/WARNING messages.  The
        # connector's own diagnostics below carry the useful state without
        # putting account metadata into shared process logs.
        pysip_logger.setLevel(logging.ERROR)
        patch_pysip_register_duration()
        identity = values.get('serv_identity', values['serv_username'])
        account = SipAccount(
            identity,
            values['serv_password'],
            values['serv_url'],
            connection_type=values.get('serv_transport', 'TCP'),
            register_duration=registration_duration(values),
        )
        account.sip_core._pygdo_auth_username = values['serv_username']
        if is_private_sip_registrar(values['serv_url']):
            # A Speedport is the local registrar, not an Internet provider.
            # PySIP otherwise publishes an IPify address in Contact/Via, which
            # is wrong for a private LAN and can leave the router disconnected.
            account.sip_core.get_public_ip = account.sip_core.get_local_ip
            account.sip_core._pygdo_speedport_register = True
        return account

    async def gdo_connect(self) -> bool:
        try:
            values = load_secrets(self.secrets_path())
        except (OSError, ValueError) as error:
            self._account = None
            self._connected = False
            Logger.warning(f'SIP configuration unavailable: {type(error).__name__}.')
            return False
        transport = values.get('serv_transport', 'TCP').upper()
        # Do not log the account name, endpoint, or secret.  The log must be
        # useful for diagnosing registration while remaining safe to share.
        Logger.debug(f'SIP registration starting over {transport}.')
        register_ok = asyncio.Event()

        async def registration_response(message) -> None:
            status = getattr(message, 'status', None)
            status_code = getattr(status, 'value', status)
            if (
                str(getattr(message, 'method', '')).upper() == 'REGISTER'
                and str(status_code) == '200'
            ):
                register_ok.set()

        callbacks = None
        try:
            account = self.sip_account(values)
            core = getattr(account, 'sip_core', None)
            callbacks = getattr(core, 'on_message_callbacks', None)
            if isinstance(callbacks, list):
                callbacks.append(registration_response)
            account.on_incoming_call(self.gdo_incoming_call)
            self._account = account
            await account.register()
        except Exception as error:
            self._account = None
            self._connected = False
            Logger.warning(
                f'SIP registration failed over {transport}: {type(error).__name__}.'
            )
            return False
        client = getattr(account, '_SipAccount__sip_client', None)
        registered = getattr(client, 'registered', None)
        if isinstance(callbacks, list):
            try:
                await asyncio.wait_for(register_ok.wait(), timeout=5)
                self._connected = True
            except asyncio.TimeoutError:
                self._connected = False
            finally:
                callbacks.remove(registration_response)
        else:
            # Test adapters without PySIP's SIP-core callback interface.
            self._connected = bool(registered and registered.is_set())
        if self._connected:
            Logger.debug(f'SIP registration ready over {transport}.')
        else:
            Logger.warning(f'SIP registration was not acknowledged over {transport}.')
        return self._connected

    async def gdo_disconnect(self, quit_message: str):
        if self._account:
            await self._account.unregister()
        self._account = None
        self._connected = False

    async def gdo_incoming_call(self, call) -> None:
        username = phone_username(getattr(call, 'caller_id', None))
        if username is None:
            username = f'ghost-{uuid4().hex}'
            user_type = GDT_UserType.GHOST
        else:
            user_type = GDT_UserType.MEMBER
        user = await self._server.get_or_create_user(username, username, user_type)
        channel = self._server.get_or_create_channel(f'call-{uuid4().hex}', username, user)
        await self._server.on_user_joined(user, channel)
        await channel.on_user_joined(user)
        session = SIPCall(call, user, channel)
        session.speech = SpeechTurn(lambda frames: self.transcribe(session, frames))
        self._calls[channel.get_id()] = session
        call.on_frame_received(lambda frame: self.gdo_audio_frame(session, frame))
        call.on_call_hanged_up(lambda reason: self.gdo_call_hanged_up(session))
        call.on_call_state_changed(lambda state: self.gdo_call_state(session, state))
        await call.accept()

    async def gdo_call_user(self, user) -> SIPCall:
        number = phone_dialstring(user.get_setting_val('phone'))
        if number is None:
            raise ValueError('Target user has no phone number.')
        return await self.gdo_call_number(number, user)

    async def gdo_call_phone(self, phone: object) -> SIPCall:
        """Call a number through an owner-authorized direct-dial command.

        Directly dialed numbers use a per-number ghost identity only for the
        lifetime of the call.  They do not create a member account or confer
        any identity-based permissions.
        """
        number = phone_dialstring(phone)
        username = phone_username(number)
        if number is None or username is None:
            raise ValueError('Target phone number is invalid.')
        user = await self._server.get_or_create_user(username, username, GDT_UserType.GHOST)
        return await self.gdo_call_number(number, user)

    async def gdo_call_number(self, number: str, user) -> SIPCall:
        if self._account is None or not self.is_connected():
            raise RuntimeError('SIP call unavailable: connector is not connected.')
        call = self._account.make_call(number)
        channel = self._server.get_or_create_channel(f'call-{uuid4().hex}', number, user)
        await self._server.on_user_joined(user, channel)
        await channel.on_user_joined(user)
        session = SIPCall(call, user, channel, outgoing=True)
        session.speech = SpeechTurn(lambda frames: self.transcribe(session, frames))
        self._calls[channel.get_id()] = session
        call.on_frame_received(lambda frame: self.gdo_audio_frame(session, frame))
        call.on_call_hanged_up(lambda reason: self.gdo_call_hanged_up(session))
        call.on_call_state_changed(lambda state: self.gdo_call_state(session, state))
        # Starting the SIP INVITE must not depend on downstream chat listeners.
        session.task = asyncio.create_task(call.start())
        session.task.add_done_callback(self._consume_call_task)
        return session

    @staticmethod
    def _consume_call_task(task: asyncio.Task) -> None:
        if not task.cancelled():
            task.exception()

    async def gdo_call_state(self, session: SIPCall, state) -> None:
        Logger.debug(f'SIP call state: {getattr(state, "name", "unknown")}.')
        if getattr(state, 'name', None) != 'ANSWERED':
            return
        if not session.chat_started:
            session.chat_started = True
            await self.emit(session, chatstream_begin(session.channel.get_id()))
        if not session.greeted:
            session.greeted = True
            await session.call.call_handler.say('Hallo, wie kann ich helfen?')
        if session.monitor_task is None:
            session.monitor_task = asyncio.create_task(self.gdo_monitor_rtp(session))

    @staticmethod
    async def gdo_monitor_rtp(session: SIPCall) -> None:
        """Start frame delivery after PySIP's own ANSWERED callback creates RTP."""
        await asyncio.sleep(0.05)
        rtp = getattr(session.call, '_rtp_session', None)
        if rtp is None:
            Logger.warning('SIP RTP session was not created after answer.')
            return
        Logger.debug('SIP RTP frame monitor attached.')
        await rtp.frame_monitor()

    async def gdo_audio_frame(self, session: SIPCall, frame: bytes) -> None:
        session.received_frames += 1
        if session.received_frames == 1:
            Logger.debug('SIP RTP input is flowing for the active call.')
        if session.speech:
            session.speech.feed(frame)

    async def gdo_call_hanged_up(self, session: SIPCall) -> None:
        if session.speech:
            await session.speech.close()
        self._calls.pop(session.channel.get_id(), None)
        await session.channel.on_user_left(session.user)
        if not any(call.user.get_id() == session.user.get_id() for call in self._calls.values()):
            await self._server.on_user_quit(session.user)

    async def transcribe(self, session: SIPCall, frames: bytes) -> None:
        Logger.debug(f'SIP speech turn received ({len(frames)} PCM bytes).')
        from gdo.audio.whisper import transcribe_file

        try:
            channel_id = int(session.channel.get_id())
        except (TypeError, ValueError) as error:
            raise RuntimeError('SIP call has no valid numeric channel id.') from error
        if channel_id < 1:
            raise RuntimeError('SIP call has no valid numeric channel id.')
        sequence = session.transcript_sequence
        session.transcript_sequence += 1

        with tempfile.NamedTemporaryFile(suffix='.wav') as audio:
            with wave.open(audio, 'wb') as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(8000)
                writer.writeframes(frames)

            async def emit(event: str) -> None:
                await self.emit(session, event)

            await transcribe_file(
                audio.name,
                channel_id,
                session.call_id,
                sequence,
                emit,
            )

    async def emit(self, session: SIPCall, event: str) -> None:
        """Deliver SIP conversation events to Mira's local event bridge.

        ChatStream is an internal agent event, not a public IRC message.  The
        Mira bridge preserves the complete event envelope, including call and
        sequence metadata, and routes it directly to the agent terminal.
        """
        await asyncio.to_thread(send_to_mira, event)

    async def gdo_send_to_channel(self, msg: Message):
        channel = msg._env_channel
        session = self._calls.get(channel.get_id() if channel else 0)
        if session is None:
            raise RuntimeError('SIP send unavailable: no active call for this channel.')
        await session.call.call_handler.say(msg._result)

    async def gdo_send_to_user(self, msg: Message, notice: bool = False):
        matches = [session for session in self._calls.values() if session.user.get_id() == msg._env_user.get_id()]
        if len(matches) != 1:
            raise RuntimeError('SIP send unavailable: user has no unique active call.')
        await matches[0].call.call_handler.say(msg._result)
