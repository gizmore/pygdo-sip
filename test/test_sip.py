import asyncio
from pathlib import Path
import tempfile
import unittest

from gdo.sip.connector.SIP import SIP, SpeechTurn, chatstream, chatstream_start, load_secrets, phone_username


class SIPTest(unittest.IsolatedAsyncioTestCase):
    def test_phone_number_and_events(self):
        self.assertEqual('4915112345678', phone_username('+49 151 123 45678'))
        self.assertIsNone(phone_username('anonymous'))
        self.assertEqual('$chatstreamstart 12 34', chatstream_start(12, 34))
        self.assertEqual('$chatstream 12 hello', chatstream(12, 'hello'))

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

            def on_incoming_call(self, callback):
                self.callback = callback

            async def register(self):
                return None

        with tempfile.NamedTemporaryFile('w', suffix='.toml') as secret:
            secret.write('[sip]\nserv_url = "host:5060"\nserv_username = "user"\nserv_password = "pass"\n')
            secret.flush()
            connector = SIP()
            connector.secrets_path = lambda: Path(secret.name)
            connector._account_factory = lambda values: Account()
            self.assertTrue(await connector.gdo_connect())

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


if __name__ == '__main__':
    unittest.main()
