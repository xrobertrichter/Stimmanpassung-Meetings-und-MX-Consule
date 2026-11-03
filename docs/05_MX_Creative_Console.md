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

| Element | Funktion | Kommando (`ctl`) | Wirkung (Offset) | Schritt / Grenze | Begründung (Toningenieur) |
|---|---|---|---|---|---|
| Dial drehen rechts/links | Lauter / Leiser | `louder` / `quieter` | Ziel-Lautheit des Normalizers | ±0,5 LU je Rastung, Ziel −24…−12 LUFS (Offset −6…+6 LU) | Der häufigste Griff gehört auf das feinfühligste Bedienelement. 0,5 LU ist die kleinste Stufe, die man im Meeting noch braucht; Tasten wären zu grob und zu langsam. |
| Dial drücken | Mute an/aus | `mute` | Ausgang blendet in 30 ms auf Stille bzw. zurück | Toggle; Statuszeile `MUTE` | Stille ohne Zoom-Menü. Die Blende verhindert das Knacken eines harten Schalters, 30 ms sind unhörbar kurz. |
| K1 | Weniger Raum | `room-less` | Expander-Schwelle höher (Offset `room_db` +2) | +2 dB je Druck, −6…+6 dB; Sprach-Kappe (Schwelle ≤ Sprache − 12 dB) bleibt | Lüfter, Nachhall oder Tippen in Pausen: die Schwelle anheben senkt die Pausen weiter ab. 2 dB sind ein hörbarer, aber nicht abrupter Schritt; die Kappe schützt leise Silben. |
| K2 | Mehr Raum | `room-more` | Expander-Schwelle tiefer (`room_db` −2) | −2 dB je Druck | Wenn Wortenden abgeschnitten klingen oder der Raum „atmet", öffnet man den Expander wieder. |
| K3 | Weniger Zischen | `deess-more` | De-Esser-Schwelle tiefer (`deess_db` −2) | −2 dB je Druck, −6…+6 dB | Zischen ist das zweithäufigste Problem bei Kondensatormikros; mehr De-Essing ist eine Tastenstufe, nicht ein Parameter. Negativ = weniger Zischen, Name `deess-more` = mehr De-Essing. |
| K4 | Natürlicher | `natural` | Intensität −1: Kompressor-Schwelle +2 dB, De-Esser-Schwelle +1 dB | Stufe −3…+3 | Klingt die Stimme gepresst, wird mit einem Griff weniger verdichtet **und** weniger entzischt – beides zusammen klingt „offener". |
| K5 | Dichter | `dense` | Intensität +1: Kompressor −2 dB, De-Esser −1 dB | Stufe −3…+3 | Bei unruhigem Pegel (Bewegung, Blick zur Seite) verdichtet eine Stufe spürbar, ohne an Parametern zu drehen. |
| K6 | Freeze / Weiter | `freeze` | Adaptive Regelung einfrieren bzw. freigeben | Toggle; Statuszeile `FROZEN` | Vor einer Präsentation oder nach Tipp-Phasen will man keine Nachführung; danach gibt man wieder frei. Entspricht `kill -USR1`. |
| K7 | Reset | `reset` | Alle Offsets 0, Lautheit auf Kalibrierwert, Regelung auf Ruhewerte, Mute aus, Bypass aus | sofort, mit Blende | Der Notausgang: egal, was man verstellt hat, ein Druck stellt den geprüften Kalibrierzustand her. |
| K8 | Status | `status` | macOS-Mitteilung: Lautheit, Störabstand, aktive Offsets, Freeze/Mute | nur lesend | Man muss sehen, was aktiv ist, ohne das Terminal zu suchen. |

## 3. Ebene 2 „Feinabstimmung"

| Element | Funktion | Kommando | Wirkung | Schritt / Grenze | Begründung |
|---|---|---|---|---|---|
| Dial | wie Ebene 1 | `louder` / `quieter` / `mute` | Lautheit / Mute | wie oben | Der häufigste Griff muss auf jeder Ebene gleich sein. |
| K1 | Wärmer | `warmer` | Klang-Tilt −1 dB (Low-Shelf 250 Hz +½ dB, High-Shelf 4 kHz −½ dB) | Tilt −3…+3 dB | Klangfarbe braucht man selten live; ein Tilt ist der schonendste Eingriff (zwei kleine Shelves statt eines EQ-Bands). |
| K2 | Heller | `brighter` | Klang-Tilt +1 dB | | Mehr Präsenz, wenn die Stimme bei schlechter Verbindung dumpf ankommt. |
| K3 | Mehr Zischen | `deess-less` | De-Esser-Schwelle höher (`deess_db` +2) | +2 dB je Druck | Rücknahme, wenn S-Laute lispeln (De-Esser zu stark). |
| K4 | Lautheit zurück | `loudness-reset` | Lautheits-Offset 0 (Ziel = Kalibrierwert) | | Nach vielen Dial-Rastungen schnell zum Referenzpegel, ohne alle anderen Offsets zu verlieren. |
| K5 | Neu lernen | `relearn` | Lernphase der Regelung neu starten; Offsets bleiben | | Nach Platz- oder Raumwechsel muss die Regelung Rauschteppich und Sprachpegel neu hören. |
| K6 | Snapshot speichern | `snapshot` | Basiswerte + Offsets als `config/chain.live.yaml` (Zeitstempel im Kommentar) | | Was sich bewährt hat, wird dauerhaft; die Kalibrierdatei selbst bleibt unverändert. |
| K7 | Bypass A/B | `bypass` | Kette umgehen (nur Input Trim + Limiter), 30-ms-Crossfade | Toggle; Statuszeile `BYPASS` | Der ehrliche Vergleich „mit / ohne" – der Limiter bleibt als Schutz drin. |
| K8 | Marker | `marker` | Zeitmarke mit Text ins Regelungslog | | Für die spätere Analyse: „hier klang es komisch" lässt sich im JSONL wiederfinden. |

Die Zuordnung Taste → Skript steht in `integrations/mx_console/functions.json`.

## 4. Verhalten gegenüber der adaptiven Regelung

- Mit Regelung: Ein Offset geht in das **Ziel** der Regelung und in ihren Ruhewert (`rest`).
  Die Regelung führt weiterhin nach (Expander folgt dem Rauschteppich, De-Esser dem Zischanteil,
  Kompressor dem Sprachpegel), aber um den verschobenen Sollwert. Sie regelt den Offset nicht
  zurück (Nachweis: 300-s-Simulation in den Tests der Regelung).
- Ohne Regelung (`run --no-adaptive`): Offsets wirken direkt auf die Kalibrierwerte.
- Freeze stoppt nur die Nachführung; Offsets wirken weiter und lassen sich weiter ändern.
- Relearn startet die Lernphase neu (Rauschteppich/Sprachpegel neu schätzen); Offsets bleiben.
- Grenzen: Jeder Offset ist geklemmt (Tabellen oben). Am Anschlag antwortet die Kette mit
  „abgelehnt" bzw. dem unveränderten Wert, die Mitteilung sagt das.
- Die Sprach-Kappe des Expanders (Schwelle höchstens Sprachpegel − 12 dB) gilt auch mit Offset:
  „Weniger Raum" kann nie in die leisen Silben schneiden.

## 5. Reset und Snapshot

- **Reset** (`reset`): Offsets auf 0, Lautheitsziel auf den Kalibrierwert, die Regelung auf ihre
  Ruhewerte (`rest`), Mute aus, Bypass aus. Alle Parameter sind danach identisch mit der geladenen
  Konfiguration; das Umschalten läuft über die knackfreien Pfade (Blende bei Mute/Bypass).
- **Snapshot** (`snapshot [--path DATEI.yaml]`): schreibt die aktuellen Basiswerte **plus Offsets**
  als vollständige Konfiguration, Standard `config/chain.live.yaml`, mit Zeitstempel im Kommentar.
  Diese Datei lässt sich mit `run --config config/chain.live.yaml` als neue Basis laden. Die
  Kalibrierdatei wird nie überschrieben.
- Ohne Snapshot sind alle Offsets nach `Ctrl-C` weg – das ist Absicht: Live-Korrekturen sind
  Reaktionen auf den Moment, nicht auf den Raum.

## 6. Steuerkanal und Latenz

- Transport: Unix-Domain-Socket `logs/control.sock` (konfigurierbar `control.socket_path` in der
  YAML; relativ zum Repository). Rechte 0600, eine alte Socket-Datei wird beim Start ersetzt, beim
  Beenden entfernt. Protokoll: eine JSON-Zeile Anfrage, eine JSON-Zeile Antwort
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
- `ctl`-Exit-Codes: 0 ausgeführt, 1 von der Kette abgelehnt (z. B. unbekannter Offset), 2 kein
  Server/Fehler (Kette läuft nicht, Socket antwortet nicht, falsche Option).

## 7. Statuszeile

Zusätzlich zu Pegeln, Gain-Reduction und Regelungszustand zeigt `run` bei aktivem Steuerkanal:
`MUTE`, `BYPASS`, die Offsets in Kurzform `off L+1.0 R+2 S-2 T0 I+1` (L Lautheit in LU,
R Raum, S Zischen, T Tilt in dB, I Intensität in Stufen) und das aktuelle Lautheitsziel
`target -17.0 LUFS`. `FROZEN` erscheint wie bisher bei eingefrorener Regelung.

## 8. Fehlersuche

| Symptom | Ursache | Maßnahme |
|---|---|---|
| Mitteilung/Ausgabe „voicechain run läuft nicht (kein Steuer-Socket unter …)" | Kette läuft nicht, oder sie läuft mit `--no-control` | `python -m voicechain run` starten (ohne `--no-control`) |
| „Socket … antwortet nicht … Alte Socket-Datei?" | `run` ist abgestürzt und hat die Datei hinterlassen | `run` neu starten; die Datei wird ersetzt |
| „hat innerhalb von 1.0 s nicht geantwortet" | Service-Thread blockiert (z. B. Prozess im Debugger) oder Rechner überlastet | Statuszeile prüfen; `run` neu starten |
| Falscher Socket-Pfad (Skript findet nichts, obwohl `run` läuft) | `run` nutzt eine YAML mit eigenem `control.socket_path` | Skript mit `--socket PFAD` oder `--config DATEI` aufrufen; Pfad steht beim Start von `run` in der Konsole („Control socket: …") |
| „exists and is not a socket" beim Start von `run` | Unter dem Socket-Pfad liegt eine normale Datei | Datei entfernen oder `control.socket_path` ändern |
| Keine Mitteilung, Antwort nur im Terminal | `osascript` fehlt oder darf keine Mitteilungen senden | Systemeinstellungen → Mitteilungen → Terminal / Options+ erlauben; ohne Recht funktioniert die Taste trotzdem |
| Options+ meldet „Permission denied" | Skript nicht ausführbar | `chmod +x integrations/mx_console/actions/*.sh` |
| Antwort „abgelehnt: …" | Grenze erreicht oder Kommando im aktuellen Zustand nicht möglich (z. B. Relearn ohne Regelung) | Text lesen; ggf. `reset` |
| Taste wirkt, aber die Regelung „holt zurück" | Nur scheinbar: die Regelung bewegt sich um das verschobene Ziel; prüfen mit `status` (Offsets bleiben) | Bei Bedarf `freeze` |
| Mute vergessen, die anderen hören nichts | `MUTE` in der Statuszeile, Mitteilung bei Dial-Druck | Dial drücken oder `reset` |
