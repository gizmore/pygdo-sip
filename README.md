# pygdo-sip

Uses [PySIP](https://github.com/moha-abdi/PySIP)
to manage incoming calls.

A user is created for each phone number.

a channel is created for each call.

Calls are routed to Mira with `$chatstreambegin <channel_id> --lang=de`.
The same begin event is emitted for incoming and outgoing calls. The channel
represents exactly one call; caller identity remains connector metadata.
It is sent only after the SIP call is answered.

Recognised speech is sent through the same Mira bridge as
`$chatstream <channel_id> --lang=de --call=… --seq=… --status=final`, followed
by the transcript text. SIP is event-driven: no wake word or "Mira" address
is required; every recognised turn is forwarded immediately.

Replies are bidirectional: a normal PyGDO `send_to_channel()` addressed to
that exact active channel is rendered through TTS/PCM into that call only.
After the call ends, the channel mapping is removed and sending fails safely.

Uses gdo/[audio](https://github.com/mira-gpt/pygdo-audio)
which uses whisper to decode speech2text.
