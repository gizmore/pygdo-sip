# pygdo-sip

Uses [PySIP](https://github.com/moha-abdi/PySIP)
to manage incoming calls.

A user is created for each phone number.

a channel is created for each call.

Calls are routed to mira with $chatstreamstart <channel_id> <userid>
and $chatstream <channel_id> text from call

Normal connector.send_to_channel() calls work.

Uses gdo/[audio](https://github.com/mira-gpt/pygdo-audio)
which uses whisper to decode speech2text.
