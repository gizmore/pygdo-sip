import asyncio
from dataclasses import dataclass, field
from pathlib import Path
import struct
import tempfile
import tomllib
from uuid import uuid4
import wave

from gdo.base.Application import Application
from gdo.base.Message import Message
from gdo.base.Render import Mode
from gdo.core.Connector import Connector
from gdo.core.GDO_Server import GDO_Server
from gdo.core.GDT_UserType import GDT_UserType


def load_secrets(path: str | Path) -> dict[str, str]:
    with open(path, 'rb') as handle:
        values = tomllib.load(handle).get('sip', {})
    required = ('serv_url', 'serv_username', 'serv_password')
    if not all(isinstance(values.get(key), str) and values[key] for key in required):
        raise ValueError('SIP secrets require serv_url, serv_username and serv_password.')
    return {key: str(value) for key, value in values.items()}


def phone_username(caller_id: object) -> str | None:
    if not isinstance(caller_id, str):
        return None
    number = ''.join(char for char in caller_id if char.isdecimal())
    return number if 7 <= len(number) <= 32 else None


def chatstream_start(channel_id: int, user_id: int) -> str:
    return f'$chatstreamstart {channel_id} {user_id}'


def chatstream(channel_id: int, text: str) -> str:
    return f'$chatstream {channel_id} {text}'


class SpeechTurn:
    def __init__(self, emit, threshold: int = 500, silence_frames: int = 25, min_frames: int = 8):
        self.emit = emit
        self.threshold = threshold
        self.silence_frames = silence_frames
        self.min_frames = min_frames
        self.frames: list[bytes] = []
        self.silence = 0
        self.tasks: set[asyncio.Task] = set()

    def feed(self, frame: bytes) -> None:
        if not isinstance(frame, bytes) or len(frame) % 2:
            return
        loud = self._level(frame) >= self.threshold
        if not self.frames and not loud:
            return
        self.frames.append(frame)
        self.silence = 0 if loud else self.silence + 1
        if self.silence >= self.silence_frames:
            self.flush()

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
        except ImportError as error:
            raise RuntimeError('PySIPio is not installed.') from error
        return SipAccount(
            values['serv_username'],
            values['serv_password'],
            values['serv_url'],
            connection_type=values.get('serv_transport', 'TCP'),
        )

    async def gdo_connect(self) -> bool:
        values = load_secrets(self.secrets_path())
        account = self.sip_account(values)
        account.on_incoming_call(self.gdo_incoming_call)
        self._account = account
        await account.register()
        client = getattr(account, '_SipAccount__sip_client', None)
        registered = getattr(client, 'registered', None)
        self._connected = bool(registered and registered.is_set())
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
        await self.emit(session, chatstream_start(channel.get_id(), user.get_id()))
        await call.accept()
        await call.call_handler.say('Hallo, wie kann ich helfen?')

    async def gdo_call_state(self, session: SIPCall, state) -> None:
        if getattr(state, 'name', None) != 'ANSWERED':
            return
        await asyncio.sleep(0)
        rtp = getattr(session.call, '_rtp_session', None)
        if rtp:
            asyncio.create_task(rtp.frame_monitor())

    async def gdo_audio_frame(self, session: SIPCall, frame: bytes) -> None:
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
        from gdo.audio.whisper import transcribe_file

        with tempfile.NamedTemporaryFile(suffix='.wav') as audio:
            with wave.open(audio, 'wb') as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(8000)
                writer.writeframes(frames)

            async def emit(event: str) -> None:
                _, _, text = event.partition('\n')
                if text:
                    await self.emit(session, chatstream(session.channel.get_id(), text))

            await transcribe_file(audio.name, session.channel.get_id(), session.call_id, 0, emit)

    async def emit(self, session: SIPCall, event: str) -> None:
        message = Message(event, Mode.render_txt)
        message.env_server(self._server).env_user(session.user, True).env_channel(session.channel)
        await Application.EVENTS.publish('new_message', message)

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
