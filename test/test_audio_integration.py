"""Contract tests against a separately supplied real audio checkout, no model download."""
import sys
import tempfile
import unittest

from gdo.sip.connector.AudioBridge import audio_emitter
from gdo.sip.connector.CallRegistry import CallRegistry


@unittest.skipUnless('gdo.audio' in sys.modules, 'Supply optional audio checkout to runner')
class AudioIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_whisper_helper_to_call_registry(self):
        from gdo.audio.whisper import transcribe_file
        registry = CallRegistry()
        call = registry.start(88)
        received = []
        async def emit(event):
            received.append(event)
        class FakeModel:
            def transcribe(self, path, language):
                return {'text': ' Hallo vom Audiomodul '}
        with tempfile.NamedTemporaryFile() as audio:
            event = await transcribe_file(audio.name, 88, call.call_id, 0,
                audio_emitter(registry, call, emit), model_loader=lambda name: FakeModel())
        self.assertEqual(1, len(received))
        self.assertEqual(event, received[0].to_event())
        self.assertEqual('Hallo vom Audiomodul', received[0].text)

    async def test_pcm_routes_are_isolated_and_bounded(self):
        import asyncio
        from gdo.audio.pcm import PCMFormat, PCMChunk, PCMRoute
        fmt = PCMFormat(16000, 1, 2)
        a, b = PCMRoute(fmt, 1), PCMRoute(fmt, 1)
        chunk = PCMChunk(fmt, 0, b'\0\0' * 160)
        a.feed_input(chunk)
        with self.assertRaises(asyncio.QueueFull):
            a.feed_input(chunk)
        b.write_output(chunk)
        self.assertEqual(chunk, await a.read_input())
        self.assertEqual(chunk, await b.read_output())
        a.close()
        b.close()
        self.assertIsNone(await a.read_input())
        self.assertIsNone(await b.read_input())
