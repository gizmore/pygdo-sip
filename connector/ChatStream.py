from dataclasses import dataclass
from enum import StrEnum
import re


class ChatStreamStatus(StrEnum):
    PARTIAL = 'partial'
    FINAL = 'final'
    ENDED = 'ended'


@dataclass(frozen=True)
class ChatStream:
    channel_id: int
    call_id: str
    sequence: int
    status: ChatStreamStatus
    text: str = ''
    language: str = 'de'

    MAX_TEXT_LENGTH = 4096
    CALL_ID_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$')
    LANGUAGE_PATTERN = re.compile(r'^[a-z]{2}$')

    @classmethod
    def from_event(cls, event: str) -> 'ChatStream':
        """Parse one envelope; the entire body remains untrusted text."""
        if not isinstance(event, str) or len(event) > cls.MAX_TEXT_LENGTH + 512:
            raise ValueError('Invalid event size or type.')
        header, separator, body = event.partition('\n')
        match = re.fullmatch(
            r'\$chatstream ([1-9][0-9]{0,19}) --lang=([a-z]{2}) '
            r'--call=([A-Za-z0-9][A-Za-z0-9._:-]{0,127}) '
            r'--seq=(0|[1-9][0-9]{0,19}) --status=(partial|final|ended)', header)
        if not separator or match is None:
            raise ValueError('Invalid chatstream envelope.')
        channel, language, call, sequence, status = match.groups()
        return cls(int(channel), call, int(sequence), status, body, language)

    def __post_init__(self):
        if type(self.channel_id) is not int or self.channel_id < 1:
            raise ValueError('SIP channel id must be positive.')
        if not isinstance(self.call_id, str) or not self.CALL_ID_PATTERN.fullmatch(self.call_id):
            raise ValueError('Invalid SIP call id.')
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError('Chat stream sequence must not be negative.')
        if not isinstance(self.language, str) or not self.LANGUAGE_PATTERN.fullmatch(self.language):
            raise ValueError('Chat stream language must be an ISO 639-1 code.')
        if not isinstance(self.text, str) or len(self.text) > self.MAX_TEXT_LENGTH or '\x00' in self.text:
            raise ValueError('Chat stream text exceeds the maximum length.')
        try:
            object.__setattr__(self, 'status', ChatStreamStatus(self.status))
        except (ValueError, TypeError):
            raise ValueError('Invalid chat stream status.') from None
        if self.status is ChatStreamStatus.ENDED and self.text:
            raise ValueError('Ended chat streams must not contain text.')

    def to_event(self) -> str:
        return (
            f'$chatstream {self.channel_id} --lang={self.language} --call={self.call_id} '
            f'--seq={self.sequence} --status={self.status}\n{self.text}'
        )
