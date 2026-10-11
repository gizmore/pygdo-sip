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

    def __post_init__(self):
        if self.channel_id < 1:
            raise ValueError('SIP channel id must be positive.')
        if not self.CALL_ID_PATTERN.fullmatch(self.call_id):
            raise ValueError('Invalid SIP call id.')
        if self.sequence < 0:
            raise ValueError('Chat stream sequence must not be negative.')
        if not self.LANGUAGE_PATTERN.fullmatch(self.language):
            raise ValueError('Chat stream language must be an ISO 639-1 code.')
        if len(self.text) > self.MAX_TEXT_LENGTH:
            raise ValueError('Chat stream text exceeds the maximum length.')
        if self.status is ChatStreamStatus.ENDED and self.text:
            raise ValueError('Ended chat streams must not contain text.')

    def to_event(self) -> str:
        return (
            f'$chatstream {self.channel_id} --lang={self.language} --call={self.call_id} '
            f'--seq={self.sequence} --status={self.status}\n{self.text}'
        )
