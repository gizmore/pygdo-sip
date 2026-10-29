import asyncio
from pathlib import Path
import struct
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from gdo.sip import module_sip
from gdo.sip.connector.SIP import SIP, SIPCall, SpeechTurn, chatstream, chatstream_begin, is_private_sip_registrar, load_secrets, phone_dialstring, phone_username
from gdo.sip.GDT_Phone import GDT_Phone
from gdo.base.Logger import Logger


class SIPTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.log_dir = tempfile.TemporaryDirectory()
        Logger.init(f'{self.log_dir.name}/')

    def tearDown(self):
        self.log_dir.cleanup()

    def test_module_is_exported(self):
        self.assertEqual('module_sip', module_sip.__name__)
        self.assertIn('account', module_sip().gdo_dependencies())

    def test_phone_number_and_events(self):
        self.assertEqual('4915112345678', phone_username('+49 151 123 45678'))
        self.assertEqual('4915112345678', phone_username('0049 (151) 123-45678'))
        self.assertEqual('004915112345678', phone_dialstring('+49 151 123 45678'))
        self.assertEqual('004915112345678', phone_dialstring('0049 (151) 123-45678'))
        self.assertTrue(is_private_sip_registrar('192.168.2.1:5060'))
        self.assertTrue(is_private_sip_registrar('127.0.0.1:5060'))
        self.assertFalse(is_private_sip_registrar('sip.example.org:5060'))
        self.assertIsNone(phone_username('anonymous'))
        self.assertEqual('$chatstreambegin 12 --lang=de', chatstream_begin(12))
        self.assertEqual('$chatstreambegin 12 --lang=de', chatstream_begin('12'))
        self.assertEqual('$chatstream 12 hello', chatstream(12, 'hello'))

    def test_phone_field_normalizes_and_rejects_invalid_values(self):
        phone = GDT_Phone().val('+49 (151) 123-45678')
        self.assertEqual('004915112345678', phone.get_val())
        self.assertTrue(phone.validate(phone.get_val()))
        self.assertIsNone(GDT_Phone.normalize_number('anonymous'))
        self.assertIsNone(GDT_Phone.normalize_number('+49-call-me'))

    def test_secrets_require_server_values(self):
        with tempfile.NamedTemporaryFile('w', suffix='.toml') as secret:
            secret.write('[sip]\nserv_url = "host:5060"\nserv_username = "user"\nserv_password = "pass"\n')
            secret.flush()
            values = load_secrets(Path(secret.name))
        self.assertEqual('host:5060', values['serv_url'])
        self.assertEqual('user', values['serv_username'])

    async def test_connection_uses_pysip_registration_state(self):
        class Client:
            def __init__(self):
                self.registered = asyncio.Event()
                self.registered.set()

        class Account:
            def __init__(self):
                self._SipAccount__sip_client = Client()
                self.sip_core = SimpleNamespace(on_message_callbacks=[])

            def on_incoming_call(self, callback):
                self.callback = callback

            async def register(self):
                # PySIP exposes successful SIP responses as an enum whose
                # numeric code lives in ``.value``.
                response = SimpleNamespace(
                    method='REGISTER', status=SimpleNamespace(value=200),
                )
                for callback in list(self.sip_core.on_message_callbacks):
                    await callback(response)
                return None

        with tempfile.NamedTemporaryFile('w', suffix='.toml') as secret:
            secret.write('[sip]\nserv_url = "host:5060"\nserv_username = "user"\nserv_password = "pass"\n')
            secret.flush()
            connector = SIP()
            connector.secrets_path = lambda: Path(secret.name)
            connector._account_factory = lambda values: Account()
            self.assertTrue(await connector.gdo_connect())

    async def test_connection_failure_is_not_reported_as_ready(self):
        class Account:
            def on_incoming_call(self, callback):
                self.callback = callback

            async def register(self):
                raise OSError('unreachable')

        with tempfile.NamedTemporaryFile('w', suffix='.toml') as secret:
            secret.write('[sip]\nserv_url = "host:5060"\nserv_username = "user"\nserv_password = "pass"\n')
            secret.flush()
            connector = SIP()
            connector.secrets_path = lambda: Path(secret.name)
            connector._account_factory = lambda values: Account()
            self.assertFalse(await connector.gdo_connect())
            self.assertIsNone(connector._account)

    async def test_speech_turn_flushes_after_silence(self):
        captured = []

        async def emit(frames):
            captured.append(frames)

        turn = SpeechTurn(emit, threshold=100, silence_frames=2, min_frames=1)
        turn.feed(b'\xff\x7f' * 160)
        turn.feed(b'\0\0' * 160)
        turn.feed(b'\0\0' * 160)
        await turn.close()
        self.assertEqual(1, len(captured))
        self.assertEqual(960, len(captured[0]))

    async def test_speech_turn_adapts_to_quiet_background(self):
        captured = []

        async def emit(frames):
            captured.append(frames)

        turn = SpeechTurn(emit, threshold=100, noise_margin=80, silence_frames=2, min_frames=1)
        turn.feed(struct.pack('<h', 30) * 160)
        self.assertEqual(110, turn.trigger_level)
        turn.feed(struct.pack('<h', 150) * 160)
        turn.feed(b'\0\0' * 160)
        turn.feed(b'\0\0' * 160)
        await turn.close()
        self.assertEqual(1, len(captured))

    async def test_speech_turn_flushes_continuous_speech_in_bounded_segments(self):
        captured = []

        async def emit(frames):
            captured.append(frames)

        turn = SpeechTurn(emit, threshold=100, min_frames=1, max_frames=3)
        for _ in range(3):
            turn.feed(struct.pack('<h', 500) * 160)
        await asyncio.sleep(0)
        await turn.close()
        self.assertEqual(1, len(captured))
        self.assertEqual(960, len(captured[0]))

    async def test_outgoing_call_uses_the_users_phone_setting(self):
        class User:
            def get_setting_val(self, key):
                return '+49 151 123 45678' if key == 'phone' else None

            def get_id(self):
                return 34

        class Channel:
            def get_id(self):
                return '12'

            async def on_user_joined(self, user):
                return None

        class Server:
            async def on_user_joined(self, user, channel):
                return None

            def get_or_create_channel(self, name, display_name, user):
                return Channel()

        class Call:
            def __init__(self):
                self.call_handler = SimpleNamespace(say=self.say)
                self._rtp_session = None
                self.started = False

            async def say(self, text):
                return None

            async def start(self):
                self.started = True

            def on_frame_received(self, callback):
                self.frame_callback = callback

            def on_call_hanged_up(self, callback):
                self.hangup_callback = callback

            def on_call_state_changed(self, callback):
                self.state_callback = callback

        class Account:
            def make_call(self, number):
                self.number = number
                self.call = Call()
                return self.call

        connector = SIP()
        connector._server = Server()
        connector._account = Account()
        connector._connected = True

        emitted = []

        async def emit(session, event):
            emitted.append(event)

        connector.emit = emit
        session = await connector.gdo_call_user(User())
        await asyncio.sleep(0)
        self.assertTrue(session.outgoing)
        self.assertEqual('004915112345678', connector._account.number)
        self.assertTrue(connector._account.call.started)
        self.assertEqual([], emitted)
        await connector.gdo_call_state(session, SimpleNamespace(name='ANSWERED'))
        self.assertEqual(['$chatstreambegin 12 --lang=de'], emitted)

    async def test_whisper_event_keeps_call_metadata(self):
        class Channel:
            def get_id(self):
                return 12

        emitted = []

        async def transcribe_file(audio_file, channel_id, call_id, sequence, emit):
            self.assertEqual(12, channel_id)
            self.assertEqual('call-42', call_id)
            self.assertEqual(0, sequence)
            await emit('$chatstream 12 --lang=de --call=call-42 --seq=0 --status=final\nHallo')

        connector = SIP()

        async def emit(session, event):
            emitted.append(event)

        connector.emit = emit
        session = SIPCall(object(), object(), Channel(), call_id='call-42')
        with patch('gdo.audio.whisper.transcribe_file', transcribe_file):
            await connector.transcribe(session, b'\0\0' * 160)

        self.assertEqual(
            ['$chatstream 12 --lang=de --call=call-42 --seq=0 --status=final\nHallo'],
            emitted,
        )
        self.assertEqual(1, session.transcript_sequence)

    async def test_chatstream_is_delivered_through_mira_bridge(self):
        connector = SIP()
        session = SIPCall(object(), object(), object())
        event = '$chatstreambegin 12 --lang=de'

        with patch('gdo.sip.connector.SIP.send_to_mira') as send:
            await connector.emit(session, event)

        send.assert_called_once_with(event)

    async def test_response_is_spoken_only_on_its_active_call_channel(self):
        spoken = []

        class Channel:
            def get_id(self):
                return 12

        class CallHandler:
            async def say(self, text):
                spoken.append(text)

        channel = Channel()
        connector = SIP()
        connector._calls[12] = SIPCall(
            SimpleNamespace(call_handler=CallHandler()), object(), channel,
        )
        message = SimpleNamespace(_env_channel=channel, _result='Die Antwort.')

        await connector.gdo_send_to_channel(message)

        self.assertEqual(['Die Antwort.'], spoken)


if __name__ == '__main__':
    unittest.main()
