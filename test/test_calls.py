import asyncio
import unittest
from gdo.sip.connector.CallRegistry import CallRegistry
from gdo.sip.connector.ChatStream import ChatStream
from gdo.sip.connector.SIP import SIP


class CallTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.calls = CallRegistry()
        self.events = []

    async def emit(self, event):
        self.events.append(event)

    def event(self, call, seq=0, status='final', text='Hallo'):
        return ChatStream(call.channel_id, call.call_id, seq, status, text)

    async def test_unknown_call_rejected(self):
        with self.assertRaises(ValueError):
            await self.calls.dispatch(ChatStream(1, 'unknown', 0, 'final'), self.emit)
        self.assertEqual([], self.events)

    async def test_two_calls_remain_separate(self):
        a = self.calls.start(1)
        b = self.calls.start(2)
        self.assertNotEqual(a.user_key, b.user_key)
        self.assertNotEqual(a.session_id, b.session_id)
        self.assertFalse(a.authenticated)
        await asyncio.gather(self.calls.dispatch(self.event(a), self.emit),
                             self.calls.dispatch(self.event(b), self.emit))
        self.assertEqual({1,2}, {e.channel_id for e in self.events})
        with self.assertRaises(ValueError):
            await self.calls.dispatch(ChatStream(2, a.call_id, 1, 'final'), self.emit)

    async def test_number_labels_do_not_authenticate(self):
        a = self.calls.start(1, '+4915112345678')
        b = self.calls.start(2, '+4915112345678')
        self.assertEqual(a.user_key, b.user_key)
        self.assertNotEqual(a.session_id, b.session_id)
        self.assertFalse(a.authenticated)
        for number in ('anonymous', '', '015112345678', '+1', 123):
            with self.assertRaises(ValueError):
                self.calls.start(3, number)

    async def test_sequence_and_duplicate(self):
        call = self.calls.start(1)
        event = self.event(call)
        self.assertTrue(await self.calls.dispatch(event, self.emit))
        self.assertFalse(await self.calls.dispatch(event, self.emit))
        for bad in (self.event(call, 0, text='different'), self.event(call, 2)):
            with self.assertRaises(ValueError):
                await self.calls.dispatch(bad, self.emit)
        self.assertEqual(1, len(self.events))

    async def test_end_blocks_late_input_and_channel_can_be_reallocated(self):
        call = self.calls.start(1)
        end = self.event(call, status='ended', text='')
        await self.calls.dispatch(end, self.emit)
        self.assertFalse(await self.calls.dispatch(end, self.emit))
        new = self.calls.start(1)
        self.assertNotEqual(call.call_id, new.call_id)
        with self.assertRaises(ValueError):
            await self.calls.dispatch(self.event(call, 1), self.emit)
        await self.calls.dispatch(self.event(new), self.emit)

    async def test_delivery_error_does_not_advance_sequence(self):
        call = self.calls.start(1)
        async def fail(event):
            raise OSError('test')
        with self.assertRaises(OSError):
            await self.calls.dispatch(self.event(call), fail)
        self.assertTrue(await self.calls.dispatch(self.event(call), self.emit))

    async def test_concurrent_same_event_emitted_once(self):
        call = self.calls.start(1)
        async def slow(event):
            await asyncio.sleep(0)
            self.events.append(event)
        results = await asyncio.gather(*[self.calls.dispatch(self.event(call), slow) for _ in range(8)])
        self.assertEqual(1, results.count(True))
        self.assertEqual(1, len(self.events))

    async def test_capacity_and_channel_ownership(self):
        calls = CallRegistry(max_calls=1, closed_capacity=1)
        a = calls.start(1)
        for channel in (1, 2):
            with self.assertRaises(ValueError):
                calls.start(channel)
        await calls.dispatch(self.event(a, status='ended', text=''), self.emit)
        b = calls.start(2)
        await calls.dispatch(self.event(b, status='ended', text=''), self.emit)
        with self.assertRaises(ValueError):
            await calls.dispatch(self.event(a, status='ended', text=''), self.emit)

    async def test_stub_does_not_claim_live_connection(self):
        connector = SIP()
        self.assertFalse(await connector.gdo_connect())
        self.assertFalse(connector.is_connected())
        self.assertTrue(connector.gdo_needs_authentication())
        self.assertTrue(connector.gdo_has_channels())


if __name__ == '__main__':
    unittest.main()
