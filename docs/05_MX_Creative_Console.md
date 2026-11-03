# Live-Bedienung im Meeting: Logi MX Creative Console

Stand: 2026-11-03. Grundlage: `docs/06_Beauftragung_MX_Console.md`. Zielgruppe: Nutzende der
Kette, die im Meeting ohne Terminal korrigieren wollen; technisch Interessierte finden den
Steuerkanal in Abschnitt 6.

> Menübezeichnungen in Logi Options+ sind versionsabhängig und entsprechen dem Kenntnisstand der
> Autoren (nicht gegen die aktuelle Version geprüft). Die Verdrahtung Schritt für Schritt steht in
> `integrations/mx_console/README.md`.

## 1. Konzept

Im Meeting korrigiert man **Symptome, nicht Parameter**: „zu viel Raum in den Pausen", „zischt",
„klingt gepresst", „zu leise für die anderen". Jede Taste der Konsole ist deshalb ein **Makro** mit
hörbarem Effekt, kleiner Schrittweite und harter Grenze. Technisch sind alle Tasten **Offsets auf
die kalibrierten Werte**:

- Die Kalibrierung (`calibrate`) liefert die Basiswerte, die adaptive Regelung führt sie langsam
  nach. Ein Offset verschiebt das **Ziel** der Regelung (und ihren Ruhewert), die Regelung arbeitet
  weiter und regelt den Offset **nicht** zurück.
- Offsets gelten nur für die laufende Sitzung. Dauerhaft werden sie nur mit „Snapshot speichern"
  (`config/chain.live.yaml`).
- „Reset" nimmt alles zurück: Offsets 0, Lautheitsziel auf Kalibrierwert, Regelung auf ihre
  Ruhewerte, Mute und Bypass aus.
- Kein Tastendruck blockiert den Audio-Callback. Kommandos laufen über einen Socket in den
  Service-Thread der Kette; Parameteränderungen nehmen die knackfreien `set_param`-Pfade, Mute und
  Bypass blenden in 30 ms.

Die Konsole hat 8 belegbare Tasten je Ebene (die neunte ist bei Logi die Seitennavigation) und
ein Dialpad (Drehen, Drücken). Zwei Ebenen: **Meeting** (häufige Griffe) und **Feinabstimmung**.

## 2. Ebene 1 „Meeting"

Belegung nach der PO-Prüfung vom 2026-11-03 (`docs/06` §2): beide Zisch-Richtungen auf einer
Ebene, Mute sichtbar auf einer Taste mit Display, Dial-Druck als „Standardwert"-Konvention.

| Element | Funktion | Kommando (`ctl`) | Wirkung (Offset) | Schritt / Grenze | Begründung (Toningenieur) |
|---|---|---|---|---|---|
| Dial drehen rechts/links | Lauter / Leiser | `louder` / `quieter` | Ziel-Lautheit des Normalizers | ±0,5 LU je Rastung, Ziel −24…−14 LUFS (Offset −6…+4 LU) | Der häufigste Griff gehört auf das feinfühligste Bedienelement. 0,5 LU ist die kleinste Stufe, die man im Meeting braucht. Ohne Mitteilung je Rastung (nur bei Ablehnung): zehn Rastungen wären sonst zehn Banner, die sich stauen. Obergrenze −14 LUFS reicht für die AGC der Konferenzsysteme. |
| Dial drücken | Lautheit 0 | `loudness-reset` | Lautheits-Offset zurück auf den Kalibrierwert | sofort | Encoder-Konvention „Druck = Standardwert"; ein Mute auf dem Dial wäre ein zweiter, unsichtbarer Mute neben Zoom/Teams und beim Drehen fehlbedienbar. |
| K1 | Wenig Raum | `room-less` | Expander-Schwelle höher (`room_db` +2) | +2 dB je Druck, −6…+6 dB; Sprach-Kappe (Schwelle ≤ Sprache − 12 dB) gilt nach dem Offset | Lüfter, Nachhall oder Tippen in Pausen: die Schwelle anheben senkt die Pausen weiter ab. 2 dB sind hörbar, aber nicht abrupt; die Kappe schützt leise Silben. |
| K2 | Mehr Raum | `room-more` | Expander-Schwelle tiefer (`room_db` −2) | −2 dB je Druck | Wenn Wortenden abgeschnitten klingen oder der Raum „atmet", öffnet man den Expander wieder. |
| K3 | Zischen − | `deess-more` | De-Esser-Schwelle tiefer (`deess_db` −2) | −2 dB je Druck, −6…+6 dB | Zischen ist das zweithäufigste Problem bei Kondensatormikros. Minus = weniger Zischen (wie Lauter/Leiser), der CLI-Name sagt „mehr De-Essing". |
| K4 | Zischen + | `deess-less` | De-Esser-Schwelle höher (`deess_db` +2) | +2 dB je Druck | Die Gegenrichtung auf derselben Ebene: Lispeln nach einer Überkorrektur lässt sich ohne Ebenenwechsel zurücknehmen. |
| K5 | Natürlich | `natural` | Intensität −1: Kompressor-Schwelle +2 dB | Stufe −3…+3 (De-Esser nicht gekoppelt) | Klingt die Stimme gepresst, verdichtet eine Stufe weniger. Der De-Esser bleibt unberührt, sonst würde „Natürlich" ein „Zischen −" zurücknehmen. |
| K6 | Dichter | `dense` | Intensität +1: Kompressor-Schwelle −2 dB | Stufe −3…+3 | Bei unruhigem Pegel (Bewegung, Blick zur Seite) verdichtet eine Stufe spürbar. |
| K7 | Freeze | `freeze` | Adaptive Regelung einfrieren bzw. freigeben | Toggle; Statuszeile `FROZEN` | Vor einer Präsentation oder nach Tipp-Phasen will man keine Nachführung; danach gibt man wieder frei. Entspricht `kill -USR1`. |
| K8 | Reset | `reset` | Bedienzustand zurück: alle Offsets 0, Lautheit auf Kalibrierwert, Mute/Bypass/DSP-aus/Freeze aus, Normalizer-Deckel und Schutzzustände auf Konfiguration; die nachgeführten Schwellen der Regelung bleiben | sofort, mit Blende | Der Notausgang für die Bedienung. Die Regelung behält ihr Wissen über den Raum, sonst wären die Pausen im Lüfterraum nach jedem Reset für etwa eine Minute lauter. |

## 3. Ebene 2 „Feinabstimmung"

| Element | Funktion | Kommando | Wirkung | Schritt / Grenze | Begründung |
|---|---|---|---|---|---|
| Dial | wie Ebene 1 | `louder` / `quieter` / `loudness-reset` | Lautheit / Lautheit 0 | wie oben | Der häufigste Griff muss auf jeder Ebene gleich sein. |
| K1 | Wärmer | `warmer` | Klang-Tilt −1 dB (Low-Shelf 250 Hz +½ dB, High-Shelf 4 kHz −½ dB) | Tilt −3…+3 dB | Klangfarbe braucht man selten live; ein Tilt ist der schonendste Eingriff (zwei kleine Shelves statt eines EQ-Bands). |
| K2 | Heller | `brighter` | Klang-Tilt +1 dB | | Mehr Präsenz, wenn die Stimme bei schlechter Verbindung dumpf ankommt. |
| K3 | Status | `status` | macOS-Mitteilung: Lautheit, Störabstand, aktive Offsets, Freeze/Mute/Bypass/OFF | nur lesend | Man muss sehen, was aktiv ist, ohne das Terminal zu suchen. |
| K4 | DSP an/aus (Kill-Switch) | `power` | Kette wirklich abschalten: nur Input Trim + Limiter werden gerechnet, Analyzer, Regelung und Lernphasen-Uhr pausieren | Toggle; Statuszeile `OFF`, Regelung `paused` | Spart Prozessorlast (Laptop im Akkubetrieb, lange Sitzung) und ist der Notaus, wenn die Verarbeitung selbst das Problem ist. Wiedereinschalten setzt die Filterzustände zurück und blendet 30 ms ein. |
| K5 | Neu lernen | `relearn` | Regelung auf Kalibrierwerte, Lernphase neu, Analyzer leer; Offsets bleiben | | Nach Platz- oder Raumwechsel muss die Regelung Rauschteppich und Sprachpegel neu hören. Das ist der Weg, die nachgeführten Schwellen zu verwerfen (Reset tut das bewusst nicht). |
| K6 | Snapshot | `snapshot` | hörbaren Ist-Zustand (nachgeführte Werte + Offsets) als `config/chain.live.yaml` schreiben, Zeitstempel im Schlüssel `_live.created` | | Was sich bewährt hat, wird dauerhaft; die Kalibrierdatei selbst bleibt unverändert. |
| K7 | Bypass A/B | `bypass` | Kette umgehen (nur Input Trim + Limiter), Stufen rechnen weiter; 30-ms-Crossfade | Toggle; Statuszeile `BYPASS` | Der ehrliche Vergleich „mit / ohne" – der Limiter bleibt als Schutz drin, der Rückweg ist knackfrei. |
| K8 | Mute | `mute` | Ausgang blendet in 30 ms auf Stille bzw. zurück | Toggle; Statuszeile `MUTE` | Stille ohne Zoom-Menü – auf einer Taste mit Display, damit der Zustand sichtbar ist (Toningenieur-Regel: ein Mute, und der ist sichtbar). |

Nicht belegt, aber per CLI und Skript verfügbar: `marker` (Zeitmarke mit Text ins Regelungslog, für
die spätere Analyse). Die Zuordnung Taste → Skript steht in `integrations/mx_console/functions.json`
(`pages`, `dial`, `unassigned`). Insgesamt 20 Kommandos: 16 Tasten + 3 Dial-Funktionen + `marker`.

### Ein Druck, ein Schritt (`--value`)

Jede Offset-Taste bewegt den Wert um eine Schrittweite (0,5 LU, 2 dB, 1 dB, 1 Stufe). `--value`
im Skript- oder CLI-Aufruf bestimmt nur den Betrag dieses Schritts; die Kette begrenzt ihn auf
höchstens eine Schrittweite und nennt in der Antwort den wirksamen Wert (`louder --value 1` wirkt
mit 0,5 LU, `--value 0.25` mit 0,25 LU). Entscheidung: größere Sprünge pro Druck wären im Meeting
nicht vorhersehbar; wer weiter will, drückt mehrmals. Am Bereichs- oder Bounds-Anschlag antwortet
die Kette `ok: false` „am Anschlag" mit dem wirksamen Wert.

### Kill-Switch und Bypass – der Unterschied

| | Bypass A/B (`bypass`) | Verarbeitung aus (`power`) |
|---|---|---|
| Was man hört | Input Trim + Limiter | Input Trim + Limiter |
| Was gerechnet wird | die ganze Kette läuft weiter (Zustände, Meter, Regelung aktuell), nur der Ausgang wird umgeblendet | nur Input Trim + Limiter; Expander, De-Esser, EQ, Kompressor, Normalizer werden nicht gerechnet |
| Analyzer / Regelung | laufen weiter | pausieren (Zustand bleibt, nichts wird zurückgesetzt) |
| Zweck | ehrlicher A/B-Vergleich, jederzeit knackfrei zurück | Last sparen oder Verarbeitung ausschalten, wenn sie selbst stört |
| Zurückschalten | Crossfade 30 ms, Zustände sind warm | Filterzustände werden zurückgesetzt, kurze Einblendung 30 ms; die Regelung läuft mit dem alten Wissen weiter |
| Statuszeile | `BYPASS` | `OFF`, `adaptive paused`, sichtbar kleinere `cpu`-Last |

## 4. Verhalten gegenüber der adaptiven Regelung

- Mit Regelung: Ein Offset geht in das **Ziel** der Regelung und in ihren Ruhewert (`rest`).
  Die Regelung führt weiterhin nach (Expander folgt dem Rauschteppich, De-Esser dem Zischanteil,
  Kompressor dem Sprachpegel), aber um den verschobenen Sollwert. Sie regelt den Offset nicht
  zurück (Nachweis: 300-s-Simulation in den Tests der Regelung).
- Ohne Regelung (`run --no-adaptive`): Offsets wirken direkt auf die Kalibrierwerte.
- Freeze stoppt nur die Nachführung; Offsets wirken weiter und lassen sich weiter ändern. Reset hebt Freeze auf.
- Relearn startet die Lernphase neu (Rauschteppich/Sprachpegel neu schätzen); Offsets bleiben.
- Grenzen: Jeder Offset ist geklemmt (Tabellen oben). Am Anschlag antwortet die Kette mit
  „abgelehnt" bzw. dem unveränderten Wert, die Mitteilung sagt das.
- Die Sprach-Kappe des Expanders (Schwelle höchstens Sprachpegel − 12 dB) gilt auch mit Offset:
  „Weniger Raum" kann nie in die leisen Silben schneiden. Einschränkung: Die Kappe setzt die
  Regelung durch. Ohne Regelung (`run --no-adaptive`) und während der Lernphase (die ersten 30 s
  nach Start oder „Neu lernen") gelten für den Offset zunächst nur die harten Bounds; die Regelung
  holt eine zu hohe Schwelle ab ihrer ersten Entscheidung auf Sprachpegel − 12 dB zurück.

## 5. Reset, Neu lernen und Snapshot

- **Reset** (`reset`) setzt den **Bedienzustand** zurück: Offsets auf 0, Lautheitsziel auf den
  Kalibrierwert, Mute aus, Bypass aus, DSP wieder an, Freeze aus, Normalizer-Deckel und
  Schutzzustände auf die Konfiguration. Die von der Regelung **nachgeführten Schwellen bleiben**
  und die Regelung läuft weiter – sie weiß weiterhin, wie laut der Raum ist. Blenden laufen über
  die knackfreien Pfade.
- **Neu lernen** (`relearn`) setzt die **Regelung** zurück: Schwellen auf die Kalibrierwerte,
  Lernphase neu, Analyzer-Historie leer. Die Offsets bleiben. Das ist der Griff nach Platz- oder
  Raumwechsel – und der Weg, nachgeführte Werte zu verwerfen, wenn sie nicht mehr passen.
- **Snapshot** (`snapshot [--path DATEI.yaml]`) schreibt den **hörbaren Ist-Zustand** – die
  nachgeführten Werte plus Offsets – als vollständige Konfiguration, Standard
  `config/chain.live.yaml`. Der Zeitstempel und die eingerechneten Offsets stehen im Block
  `_live` (`_live.created`, `_live.offsets`), den `load_config` wie die `_calibration`-Metadaten
  toleriert. Diese Datei lässt sich mit `run --config config/chain.live.yaml` als neue Basis
  laden. Die Kalibrierdatei wird nie überschrieben.
- Ohne Snapshot sind alle Offsets nach `Ctrl-C` weg – das ist Absicht: Live-Korrekturen sind
  Reaktionen auf den Moment, nicht auf den Raum.

## 6. Steuerkanal und Latenz

- Transport: Unix-Domain-Socket, Standard **`/tmp/voicechain-<user>.sock`** (konfigurierbar
  `control.socket_path` in der YAML; relative Pfade gelten ab Repository). Entscheidung: macOS
  erlaubt nur 103 Byte für Socket-Pfade, ein repo-relativer Pfad unter
  `/Users/<name>/Documents/…` überschreitet das leicht; `/tmp` ist kurz, pro Nutzer benannt,
  mit Rechten 0600 geschützt und unabhängig davon, wo das Repository liegt. `run` und `ctl`
  prüfen die Länge **vor** dem Binden/Verbinden und nennen die Abhilfe (kurzer absoluter Pfad wie
  `~/.voicechain/ctl.sock`). In der mitgelieferten `config/chain.default.yaml` ist der Eintrag
  `control.socket_path` nur auskommentiert, es gilt also der Standard `/tmp/voicechain-<user>.sock`;
  `run` und `ctl` lesen denselben Schlüssel, ein gesetzter Wert gilt daher für beide.
- Lebenszyklus: Beim Start wird eine vorhandene Socket-Datei **geprüft**: antwortet dort ein
  Server, verweigert `run` den Start („voicechain run läuft bereits"); antwortet niemand (Datei
  eines abgestürzten Laufs), wird sie ersetzt. Beim Beenden (Ctrl-C **oder SIGTERM**, z. B.
  `kill <pid>`) entfernt `run` nur seine eigene Socket-Datei (Inode-Vergleich) und schließt den
  Stream geordnet. Protokoll: eine JSON-Zeile Anfrage, eine JSON-Zeile Antwort
  (`{"ok": true, "state": {...}, "message": "…"}` oder `{"ok": false, "error": "…"}`).
- Der Server wird vom Service-Thread der Kette alle 20 ms abgefragt (`selectors`, nicht
  blockierend, höchstens 8 Kommandos je Durchlauf). Der Audio-Callback sieht davon nichts; Mute
  wirkt als Ausgangsgain-Rampe im Callback, Bypass als Crossfade in der Kette.
- Antwortzeit `python -m voicechain ctl …` (Interpreterstart + Socket + Antwort): im Prüf-Container
  (Linux, x86) **Median 61 ms, Maximum 62 ms** über 15 Aufrufe. `ctl` lädt dafür bewusst weder
  numpy noch scipy (nur `voicechain.control` und PyYAML); der Import der vollen CLI würde im
  selben Container 600 ms kosten. Auf einem Apple-Silicon-Mac ist mit 40–80 ms zu rechnen, die
  Mitteilung (`osascript`) kommt zusätzlich mit etwa 50–150 ms – insgesamt deutlich unter der
  Vorgabe von 300 ms. Die Zahl für den Mac ist eine Schätzung aus dem Container-Wert und nicht
  gemessen.
- `ctl`-Exit-Codes: 0 ausgeführt, 1 von der Kette abgelehnt (z. B. am Anschlag), 2 kein
  Server/Fehler (Kette läuft nicht, Socket antwortet nicht, falsche Option). `--notify` zeigt
  jede Antwort als Mitteilung, `--notify-errors` nur Ablehnungen und Fehler (Dial-Aktionen).

## 7. Statuszeile

Zusätzlich zu Pegeln, Gain-Reduction und Regelungszustand zeigt `run` bei aktivem Steuerkanal:
`OFF` (Kill-Switch), `MUTE`, `BYPASS`, die Rechenlast der Kette `cpu 12%` (Rechenzeit je Block
relativ zur Blockdauer; im Aus-Zustand deutlich kleiner), die Offsets in Kurzform
`off L+1.0 R+2 S-2 T0 I+1` (L Lautheit in LU, R Raum, S Zischen, T Tilt in dB, I Intensität in
Stufen) und das aktuelle Lautheitsziel `target -17.0 LUFS`. `FROZEN` erscheint wie bisher bei
eingefrorener Regelung, `adaptive paused` bei ausgeschalteter Verarbeitung.

## 8. Fehlersuche

| Symptom | Ursache | Maßnahme |
|---|---|---|
| Mitteilung/Ausgabe „voicechain run läuft nicht (kein Steuer-Socket unter …)" | Kette läuft nicht, oder sie läuft mit `--no-control` | `python -m voicechain run` starten (ohne `--no-control`) |
| `run` meldet „voicechain run läuft bereits" | Eine zweite Instanz; die erste antwortet auf dem Socket | Erste Instanz beenden (Ctrl-C oder `kill <pid>`), dann neu starten |
| „control socket path is … bytes long" | Socket-Pfad länger als 103 Byte (macOS-Grenze) | `control.socket_path` auf einen kurzen absoluten Pfad setzen oder entfernen (Standard `/tmp/voicechain-<user>.sock`) |
| Mitteilung „ModuleNotFoundError … yaml/numpy" von einer Taste | Options+ startet Apples System-Python (minimaler PATH) | `.venv` im Repository anlegen (docs/01 Schritt 2); die Skripte bevorzugen `.venv/bin/python`, sonst `VOICECHAIN_PYTHON`/Homebrew-Python |
| „Socket … antwortet nicht … Alte Socket-Datei?" | `run` ist abgestürzt und hat die Datei hinterlassen | `run` neu starten; die Datei wird ersetzt |
| „hat innerhalb von 1.0 s nicht geantwortet" | Service-Thread blockiert (z. B. Prozess im Debugger) oder Rechner überlastet | Statuszeile prüfen; `run` neu starten |
| Falscher Socket-Pfad (Skript findet nichts, obwohl `run` läuft) | `run` nutzt eine YAML mit eigenem `control.socket_path` | Skript mit `--socket PFAD` oder `--config DATEI` aufrufen; Pfad steht beim Start von `run` in der Konsole („Control socket: …") |
| „exists and is not a socket" beim Start von `run` | Unter dem Socket-Pfad liegt eine normale Datei | Datei entfernen oder `control.socket_path` ändern |
| Keine Mitteilung, Antwort nur im Terminal | `osascript` fehlt oder darf keine Mitteilungen senden | Systemeinstellungen → Mitteilungen → Terminal / Options+ erlauben; ohne Recht funktioniert die Taste trotzdem |
| Options+ meldet „Permission denied" | Skript nicht ausführbar | `chmod +x integrations/mx_console/actions/*.sh` |
| Antwort „abgelehnt: …" | Grenze erreicht oder Kommando im aktuellen Zustand nicht möglich (z. B. Relearn ohne Regelung) | Text lesen; ggf. `reset` |
| Taste wirkt, aber die Regelung „holt zurück" | Nur scheinbar: die Regelung bewegt sich um das verschobene Ziel; prüfen mit `status` (Offsets bleiben) | Bei Bedarf `freeze` |
| Mute vergessen, die anderen hören nichts | `MUTE` in der Statuszeile und auf der Taste | Mute-Taste (Ebene 2 K8) oder `reset` |
| Klingt plötzlich roh (Raum und Zischen zurück), Statuszeile `OFF` | Kill-Switch (`power`) ist aus | K4 auf Ebene 2 erneut drücken oder `ctl power --value on`; `reset` schaltet ebenfalls wieder ein |
