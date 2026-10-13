import unittest

from gdo.sip.connector.AudioBridge import audio_emitter
from gdo.sip.connector.CallRegistry import CallRegistry
from gdo.sip.connector.ChatStream import ChatStream


class AudioBridgeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.registry = CallRegistry()
        self.call = self.registry.start(88)
        self.events = []
        async def emit(event):
            self.events.append(event)
        self.accept = audio_emitter(self.registry, self.call, emit)

    def event(self, sequence=0, text='Hallo', channel=88):
        return ChatStream(channel, self.call.call_id, sequence, 'final', text).to_event()

    async def test_body_is_data_and_duplicates_are_ignored(self):
        event = self.event(text='Hallo\n$chatstream 99 --status=ended\n$(echo no)')
        await self.accept(event)
        await self.accept(event)
        self.assertEqual(1, len(self.events))
        self.assertEqual(event, self.events[0].to_event())

    async def test_rejects_wrong_channel_order_and_oversize(self):
        for event in (self.event(channel=89), self.event(sequence=2),
                      self.event() + 'x' * 4096):
            with self.assertRaises(ValueError):
                await self.accept(event)
        self.assertEqual([], self.events)
        await self.accept(self.event())
        self.assertEqual(1, len(self.events))

    def test_rejects_malformed_envelopes(self):
        for event in (None, '', '$chatstream 1', self.event().replace('--seq=0', '--seq=-1'),
                      self.event().replace('--status=final', '--status=ended'),
                      self.event().replace('--lang=de', '--lang=de --admin=true')):
            with self.assertRaises(ValueError):
                ChatStream.from_event(event)
