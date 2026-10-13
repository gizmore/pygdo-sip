"""Transport-neutral call routing. No DB users, listeners, credentials or shell."""
import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
import re
from typing import Awaitable, Callable
from uuid import uuid4

from gdo.sip.connector.ChatStream import ChatStream, ChatStreamStatus


@dataclass(frozen=True)
class CallIdentity:
    channel_id: int
    call_id: str
    session_id: str
    user_key: str
    authenticated: bool = False


@dataclass
class _CallState:
    identity: CallIdentity
    last: ChatStream | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class CallRegistry:
    """One live call per externally allocated channel, with per-call ordering.

    Used on one asyncio loop. The caller must allocate a real PyGDO channel first.
    Numbers only label unverified identities, never user_link or permissions.
    """

    def __init__(self, max_calls: int = 32, closed_capacity: int = 256):
        if type(max_calls) is not int or max_calls < 1:
            raise ValueError('Invalid call capacity.')
        if type(closed_capacity) is not int or closed_capacity < 1:
            raise ValueError('Invalid closed-call capacity.')
        self.max_calls = max_calls
        self.closed_capacity = closed_capacity
        self._calls: dict[str, _CallState] = {}
        self._channels: dict[int, str] = {}
        self._closed: OrderedDict[str, ChatStream] = OrderedDict()

    def start(self, channel_id: int, number: str | None = None) -> CallIdentity:
        if type(channel_id) is not int or channel_id < 1:
            raise ValueError('A positive allocated channel id is required.')
        if channel_id in self._channels:
            raise ValueError('Channel already belongs to an active call.')
        if len(self._calls) >= self.max_calls:
            raise ValueError('Call capacity reached.')
        if number is not None and (not isinstance(number, str) or
                                   not re.fullmatch(r'\+[1-9][0-9]{7,14}', number)):
            raise ValueError('Use a normalized E.164 number, or None for withheld.')
        call_id = uuid4().hex
        identity = CallIdentity(channel_id, call_id, uuid4().hex,
                                'tel:' + number if number else 'ghost:' + call_id)
        self._calls[call_id] = _CallState(identity)
        self._channels[channel_id] = call_id
        return identity

    async def dispatch(self, stream: ChatStream,
                       emit: Callable[[ChatStream], Awaitable[None]]) -> bool:
        """Deliver in order. False means an identical last event was repeated.

        The emitter receives a typed event, not an executable command. A failed
        emitter does not advance state. External emitters must deduplicate by
        (call_id, sequence) if delivery succeeded before they raised an error.
        """
        if not isinstance(stream, ChatStream) or not callable(emit):
            raise ValueError('A validated stream and explicit async emitter are required.')
        state = self._calls.get(stream.call_id)
        if state is None:
            if self._closed.get(stream.call_id) == stream:
                return False
            raise ValueError('Unknown or ended call.')
        async with state.lock:
            if state.identity.channel_id != stream.channel_id:
                raise ValueError('Channel does not belong to this call.')
            if state.last == stream:
                return False
            if state.last is not None and state.last.status is ChatStreamStatus.ENDED:
                raise ValueError('Call has ended.')
            expected = 0 if state.last is None else state.last.sequence + 1
            if stream.sequence != expected:
                raise ValueError('Duplicate, missing or out-of-order sequence.')
            await emit(stream)
            state.last = stream
            if stream.status is ChatStreamStatus.ENDED:
                del self._calls[stream.call_id]
                del self._channels[stream.channel_id]
                self._closed[stream.call_id] = stream
                while len(self._closed) > self.closed_capacity:
                    self._closed.popitem(last=False)
            return True
