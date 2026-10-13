"""Explicit audio-event boundary; does not import Whisper or start devices."""
from collections.abc import Awaitable, Callable

from gdo.sip.connector.CallRegistry import CallIdentity, CallRegistry
from gdo.sip.connector.ChatStream import ChatStream


def audio_emitter(registry: CallRegistry, identity: CallIdentity,
                  emit: Callable[[ChatStream], Awaitable[None]]):
    """Bind a Whisper emitter to exactly one already allocated call."""
    async def accept(event: str) -> None:
        stream = ChatStream.from_event(event)
        if (stream.call_id, stream.channel_id) != (identity.call_id, identity.channel_id):
            raise ValueError('Audio event belongs to another call.')
        await registry.dispatch(stream, emit)
    return accept
