import unittest

from gdo.sip.connector.ChatStream import ChatStream, ChatStreamStatus


class ChatStreamTest(unittest.TestCase):

    def test_renders_a_final_text_turn(self):
        stream = ChatStream(53, 'call-42', 7, ChatStreamStatus.FINAL, 'Hallo.', 'de')
        self.assertEqual(
            '$chatstream 53 --lang=de --call=call-42 --seq=7 --status=final\nHallo.',
            stream.to_event(),
        )

    def test_rejects_invalid_call_ids(self):
        with self.assertRaises(ValueError):
            ChatStream(53, '../call', 0, ChatStreamStatus.PARTIAL)

    def test_rejects_text_on_end(self):
        with self.assertRaises(ValueError):
            ChatStream(53, 'call-42', 8, ChatStreamStatus.ENDED, 'goodbye')

    def test_rejects_non_positive_channel_ids(self):
        with self.assertRaises(ValueError):
            ChatStream(0, 'call-42', 0, ChatStreamStatus.PARTIAL)
