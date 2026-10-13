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

    def test_rejects_wrong_types_and_status_injection(self):
        valid = dict(channel_id=1, call_id='call', sequence=0, status='final', text='Hallo', language='de')
        for field, value in [('channel_id', True), ('channel_id', 1.5), ('channel_id', '1'),
                             ('sequence', False), ('sequence', 0.5), ('sequence', None),
                             ('status', 'final\n$command'), ('status', None),
                             ('call_id', None), ('text', []), ('text', 'a\x00b'),
                             ('language', None)]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                ChatStream(**{**valid, field: value})

    def test_string_end_status_cannot_bypass_validation(self):
        with self.assertRaises(ValueError):
            ChatStream(1, 'call', 0, 'ended', 'not empty')
        self.assertIs(ChatStream(1, 'call', 0, 'final').status, ChatStreamStatus.FINAL)
