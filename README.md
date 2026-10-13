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

## Lokale Anrufverwaltung

`CallRegistry` verwaltet getrennte Anrufe ohne Listener oder Datenbankzugriff.
Ein späterer Adapter muss zuerst einen echten PyGDO-Channel anlegen und dessen
ID an `start(channel_id, number)` übergeben. Daraus entstehen neue Call- und
Session-IDs. Sichtbare Nummern müssen bereits im E.164-Format vorliegen; `None`
ergibt eine eigene Ghost-Kennung. Diese Kennungen erzeugen noch keine GDO-User,
keinen `user_link` und keine Authentifizierung.

`await registry.dispatch(stream, emit)` prüft Channel-Zuordnung und lückenlose
Sequenzen ab 0. Derselbe zuletzt angenommene Abschnitt wird nicht erneut
zugestellt. Nach `ended` wird kein weiterer Abschnitt dieses Anrufs angenommen.
Parallele Zustellungen desselben Anrufs werden serialisiert. Der Emitter erhält
ein `ChatStream`-Objekt und muss explizit bereitgestellt werden. Er darf
`to_event()` nicht als Shell-Befehl ausführen. Emitter dürfen `dispatch` desselben
Anrufs nicht rekursiv aufrufen. Netzwerkseitige Genau-einmal-Zustellung ist nicht
garantiert: Scheitert ein Emitter nach bereits erfolgter Zustellung, muss er anhand
von `(call_id, sequence)` selbst deduplizieren.

Der Connector meldet ohne reale Transportbrücke keine Verbindung. Tests starten
weder Dog noch SIP und ändern keine installierten Module:

```sh
python3 test/run_tests.py /path/to/pygdo
```

Noch offen: echte Channel-/User-Erzeugung, SIP-Transport, PCM-Anbindung und
Zeitüberschreitungen bei nicht sauber beendeten Anrufen. Die private Telefon-
Mailbox von Shippi ist nicht Teil dieses Moduls.

## Audio-Vertragsprüfung

`audio_emitter(registry, identity, emit)` bindet Text-Ereignisse des Audiohelfers
an genau einen Anruf. `ChatStream.from_event()` liest nur einen festen Header;
der gesamte nachfolgende Inhalt bleibt Text, auch wenn er Befehle enthält.
Kein Shell-Aufruf, keine automatische Aktion und keine Audioinstallation.

Geprüfter Audio-Stand: `mira-gpt/pygdo-audio`, `feature/pcm-io`,
`ef5cd22957207539e503c90dd0db616be0d21ab2`. Mit separat ausgechecktem Modul:

```sh
python3 test/run_tests.py /path/to/pygdo /path/to/pygdo-audio
```

Der Integrationstest nutzt den echten Dateihelfer mit einem simulierten
Whisper-Modell. Er belegt Ereignisübergabe und PCM-Queue-Verhalten, keine
Spracherkennungsqualität oder echte Telefonie. Pro PCM-Richtung ist im aktuellen
Audio-Stand nur ein wartender Leser zulässig. PCM-Typen werden strikt geprüft;
close beendet den wartenden Leser. 23 SIP-/Integrationstests und 14 Audio-Tests
bestehen gegen diesen Stand.
