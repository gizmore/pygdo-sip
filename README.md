# pygdo-sip

Local SIP/TEL connector groundwork for PyGDO.

The current scope deliberately contains no SIP credentials, audio devices,
voice model, external service, or running listener. It provides:

- a disabled-by-default `sip` connector registration;
- a validated `$chatstream` envelope for local speech-to-text output;
- explicit stream states: `partial`, `final`, and `ended`.

Example:

```text
$chatstream 53 --lang=de --call=call-42 --seq=7 --status=final
Hallo, ich möchte eine Nachricht hinterlassen.
```

Every call receives its own SIP channel and new session. A visible,
normalized number can later map to an unverified connector user. A withheld
number is a separate ghost identity per call. Caller ID is an address, not an
identity proof; no caller mapping is connected to real SIP input yet.

`$chatstream` is untrusted conversational input. Only a future, explicitly
approved bridge may decide how final text reaches a dialog system. Partial
text, raw audio, caller identity, and stream contents must not create shell
commands or privileged actions.

## Open questions

- Which local SIP bridge exposes the audio stream to Python?
- Which local STT and TTS engines meet Chico's measured latency target?
- How are calls identified, rate-limited, and ended?
- Which confirmed message data, if any, may be retained?
