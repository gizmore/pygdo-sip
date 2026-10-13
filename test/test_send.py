from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from gdo.base.Application import Application
from gdo.sip.connector.SIP import SIP


class SendFailureTest(unittest.IsolatedAsyncioTestCase):
    async def test_channel_wrapper_does_not_publish_false_success(self):
        connector = SIP()
        publisher = AsyncMock()
        message = SimpleNamespace(_env_user='caller')
        with patch.object(Application, 'EVENTS', SimpleNamespace(publish=publisher), create=True), \
             patch.object(connector, 'gdo_get_dog_user') as dog:
            with self.assertRaisesRegex(RuntimeError, 'no active call transport'):
                await connector.send_to_channel(message)
            publisher.assert_not_called()
            dog.assert_not_called()
        self.assertEqual('caller', message._env_user)

    async def test_user_wrapper_does_not_publish_false_success(self):
        connector = SIP()
        publisher = AsyncMock()
        message = SimpleNamespace(_env_user='caller')
        with patch.object(Application, 'EVENTS', SimpleNamespace(publish=publisher), create=True), \
             patch.object(connector, 'gdo_get_dog_user') as dog:
            with self.assertRaisesRegex(RuntimeError, 'no verified active call mapping'):
                await connector.send_to_user(message)
            publisher.assert_not_called()
            dog.assert_not_called()

    async def test_disabled_events_still_reject_unavailable_send(self):
        connector = SIP()
        with self.assertRaises(RuntimeError):
            await connector.send_to_channel(SimpleNamespace(), with_events=False)
