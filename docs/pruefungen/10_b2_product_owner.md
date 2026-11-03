# PO-Prüfung Beauftragung 2 (Live-Bedienung MX Creative Console)

Prüfer: Product Owner (audiofachkundig, Broadcast-/Konferenz-Sprachbearbeitung) · Repo `/home/user/Test`, Stand `e680b56` · Datum 2026-11-03
Grundlage: `docs/06_Beauftragung_MX_Console.md` (verbindlich), `docs/00_Beauftragung.md` §4. Geprüft wurden `docs/05_MX_Creative_Console.md`, `integrations/mx_console/*`, `voicechain/livecontrol.py`, `voicechain/adaptive.py`, `voicechain/dsp/chain.py`, `voicechain/runtime.py` (Hooks), `voicechain/cli.py` (`ctl`), `docs/02` §0. Keine Repo-Datei geändert; alle Skripte und Rohdaten liegen unter `<pruefordner>/b2/po/`.

## 1. Kurzurteil

**Abnehmbar mit Auflagen.** Steuerkanal, Makro-Semantik, Offset-Haltung gegenüber der Regelung, Mute/Bypass/Kill-Switch (knackfrei, Regelung pausiert korrekt) und Reset auf Kalibrierwerte funktionieren in der 10-min-Simulation wie beauftragt; Selftest 12/12, pytest 170 bestanden (1 zeitabhängiger Fehlschlag unter Last, isoliert grün).

Eine Zusage der Beauftragung ist **nicht umgesetzt**: Die Sprach-Kappe des Expanders („Schwelle ≤ Sprache − 12 dB bleibt auch mit Offset", docs/06 §2 K1, docs/05 §4) wird vom Raum-Offset ausgehebelt – der Offset wird *nach* der Kappe addiert. Gemessen: Schwelle 4,5 dB über der Kappe bei einem leiseren, aber plausiblen Sprecher; genau im Fall „lauter Raum", für den die Taste gedacht ist (PO2-1, hoch). Dazu zwei mittlere Reset-Befunde (Freeze bleibt stehen und wirft die Nachführung im geänderten Raum um 10 dB zurück; Limiter-Schutz wird nicht zurückgesetzt) und Ergonomie-Befunde zur Belegung (Zisch +/− auf zwei Ebenen, Dial-Druck = unsichtbarer Zweit-Mute, Mitteilung je Dial-Rastung).

## 2. Befundtabelle

| Nr. | Schwere | Fundstelle | Befund | Empfehlung |
|---|---|---|---|---|
| PO2-1 | **hoch** | `voicechain/adaptive.py` `_track()` (Offset wird auf das bereits gekappte Ziel addiert); `voicechain/livecontrol.py` `_apply_threshold_offsets()` (Sofortschritt ohne Kappe); Zusage in `docs/06` §2 K1, `docs/05` §2 K1 und §4 („kann nie in die leisen Silben schneiden") | Die Sprach-Kappe gilt mit Raum-Offset **nicht**. Simulation (Sprecher 5 dB leiser als kalibriert, also noch plausibel; Raum −50 dBFS; Raum +6): Expander-Schwelle −32,5 dBFS, Kappe Sprache − 12 = −37,0 → **+4,5 dB über der Kappe**; leise Silben relativ zu lauten −8,6 statt −7,1 dB (Eingang −8,2), max. Expander-Reduktion in reinen Sprachintervallen 15,1 dB (ohne Offset 7,2 dB). Mit Offset +6 und Bound −30 sind bis zu 7 dB über der Kappe möglich (Schwelle nur 5 dB unter Sprach-RMS). Reale Sprache hat mehr Silbendynamik als das Testsignal, das Risiko ist live größer. `speech_cut` greift nicht (nur bei ≥ 6 dB in zwei aufeinander folgenden reinen Sprachintervallen) | Kappe **nach** dem Offset anwenden: Ziel = min(Teppich + 10 + Offset, Sprache − 12), in `_track` (Expander-Sonderfall) **und** im Sofortschritt von LiveControl (Kappe aus dem letzten Analyzer-Snapshot oder vom Controller abfragen). Antwort bei Begrenzung: „Raum +6 (durch Sprach-Kappe auf −37 dBFS begrenzt)". Test: leiser Sprecher + Raum +6 → Schwelle ≤ Sprache − 12 ± Totzone |
| PO2-2 | mittel | `livecontrol.py` `_cmd_reset()` (kein `freeze(False)`), `controller.apply_rest_values()` verwirft nachgeführte Werte; `docs/06` §2 K7, `docs/05` §5 | Reset lässt **FROZEN stehen** (Statuszeile zeigt es, die Antwort „Reset: Kalibrierzustand …" nicht) und setzt die Regelung auf die Kalibrierwerte, auch wenn der Raum sich geändert hat. Simulation A (Lüfter +8 dB seit 5 min, Freeze seit 6 min, Reset bei 8 min): tiefe Pausen von −54,5 auf **−40,7 dBFS** (Pausenabstand 36,0 → 22,1 dB), und weil Freeze bleibt, dauerhaft – die Pausen sind danach **5 dB lauter als im Kill-Switch-Aus-Zustand** (−46,0; Makeup 6,2 dB + Normalizer ohne Expander-Wirkung). Variante B (Freeze 6 s nach Reset gelöst): Erholung in 52 s auf −38 dBFS, Pausenabstand 26,7 dB nach 60 s, 30,0 dB nach 120 s | Reset hebt Freeze auf (oder die Antwort sagt ausdrücklich „FROZEN bleibt"). Fachlich erwägen, Reset zu teilen: „Reset" = Offsets 0, Lautheit, Tilt, Mute, Bypass, Power, Freeze – nachgeführte Schwellen **behalten**; „Regelung zurück auf Kalibrierwerte" nur über Relearn bzw. ein eigenes Kommando. Mindestens Risiko in docs/05 §5 benennen |
| PO2-3 | mittel | `livecontrol.py` `_cmd_reset()`; `adaptive.py` `_normalizer_hold()`/`_limiter_protect`; Abnahmekriterium 5 | Nach einem Limiter-Schutz-Eingriff (`normalizer.max_gain_db` auf aktuellen Gain gedeckelt) stellt Reset den Wert **nicht** wieder her (gemessen: 4,0 dB bleibt, Konfig 12,0) und löscht den Schutzzustand nicht. „Alle Parameter identisch mit der geladenen Konfig" gilt damit nicht in jedem Zustand; der Reset-Test lässt nur die Expander-Schwelle driften | Reset: `normalizer.max_gain_db` auf Konfigwert, `_limiter_protect = False`, Akkumulatoren leeren; Test mit vorherigem `limiter_overload`-Eingriff |
| PO2-4 | mittel | `integrations/mx_console/functions.json` (Ebene 1 K3 `deess-more`, Ebene 2 K3 `deess-less`), `docs/06` §2 | Für Raum und Intensität liegen beide Richtungen auf Ebene 1, für Zischen nicht: eine Überkorrektur „Zisch −" (lispelt) lässt sich im Meeting nur per Ebenenwechsel oder Reset zurücknehmen. Jede Korrektur braucht ihre Gegenrichtung auf derselben Ebene | „Zischen +" auf Ebene 1; Freeze (selten im Meeting, eher Technikerwerkzeug) auf Ebene 2. Siehe Alternativbelegung §4 |
| PO2-5 | mittel | `functions.json` `dial.press` = `mute`; `docs/06` §2 „Dial drücken"; `docs/05` §8 Zeile „Mute vergessen" | Dial-Druck = Mute schafft einen **zweiten, unsichtbaren Mute** neben Zoom/Teams (Zoom zeigt „aktiv", die anderen hören nichts; das Dial hat kein Display, die Taste wohl). Dazu Fehlbedienungsrisiko beim Drehen (Druck-Encoder). Toningenieur-Regel: ein Mute, und der ist sichtbar | Dial-Druck = `loudness-reset` („Lautheit 0", die übliche Encoder-Konvention „Druck = Standardwert"); Mute nur, wenn gewünscht, auf eine Taste **mit Display** (Vorschlag Ebene 2 K8), sonst beim Konferenzsystem lassen |
| PO2-6 | mittel | `functions.json` `dial.rotate_*` → `ctl louder --notify`; `docs/05` §6 (Antwortzeit) | Jede Dial-Rastung startet einen Python-Prozess **und** eine macOS-Mitteilung: 10 Rastungen = 10 Banner; mit 40–80 ms `ctl` + 50–150 ms `osascript` seriell entsteht bei schnellem Drehen ein Rückstau von Sekunden, in dem die Lautheit „nachläuft" | Dial-Aktionen ohne `--notify` (die Statuszeile und die Status-Taste zeigen das Ziel); Mitteilung nur, wenn die Grenze erreicht ist. Optional: Dial-Schritt von Options+ als `--value` durchreichen, falls Options+ Rastungen bündelt |
| PO2-7 | niedrig | `livecontrol.py` `INTENSITY_DEESSER_DB_PER_STEP`, `threshold_offsets()`; `docs/06` §2 K4/K5 | Intensität koppelt den De-Esser (±1 dB je Stufe): „Natürlicher" nimmt eine halbe „Zisch −"-Stufe zurück, „Dichter ×3" senkt die De-Esser-Schwelle um 3 dB (Lispel-Risiko). Zischen ist eine Eigenschaft von Mikro und Stimme, nicht der Dichte. Außerdem verschiebt eine Intensitätsstufe den statischen Pegel, weil das Makeup (kalibriert 6,2 dB) fest bleibt: Short-Term-Lautheit kurz −19,5 → −17,9 LUFS, Normalizer korrigiert in ~1 s (gemessen bei 240–250 s) | Intensität nur auf den Kompressor (ggf. Makeup um −⅔ des Schwellenschritts mitführen, damit der Pegel steht); De-Esser ausschließlich über die Zisch-Tasten. Falls Kopplung bleibt: in der Tastenbeschreibung nennen |
| PO2-8 | niedrig | `livecontrol.py` `_cmd_offset()`/`_apply_threshold_offsets()` (Klemmung durch `tracked_bounds`); `docs/05` §4 („Am Anschlag … abgelehnt bzw. unveränderter Wert") | Am Anschlag der `bounds` antwortet die Kette `ok` mit „Raum +6 dB", obwohl die Schwelle bei −30 dBFS bleibt; der Offset zählt weiter (Phantomstufe). Gemessen (Basis −34): +2 → −32, +4 → −30, +6 → −30 (ok). Beim Zurückdrehen wirkt der erste Druck mit Regelung sofort (−2 dB vom Istwert), ohne Regelung erst der zweite | Antwort „Grenze erreicht (Expander −30 dBFS)", Offset nur bis zum wirksamen Wert zählen oder effektiven Offset im `state` ausweisen |
| PO2-9 | niedrig | `livecontrol.py` `snapshot_config()`; `docs/06` §2 K6 („aktuelle Basiswerte + Offsets"), `docs/05` §5 | Snapshot schreibt **Kalibrierbasis + Offsets**, nicht die nachgeführten Werte (Beispiel: Kette −46,0 nach Nachführung, Snapshot −43,0). Reproduzierbar, aber nicht „was jetzt gut klingt". Lädt man den Snapshot als Basis, startet der Offset-Bereich −6…+6 um die verschobene Basis neu (Bounds schützen). Datei validiert (`_live` erlaubt) | Doku präzisieren („Kalibrierwerte + Offsets, Nachführung nicht enthalten") oder optional die nachgeführten Werte schreiben (`--live`). `_live.offsets` im Kommentar ist gut |
| PO2-10 | niedrig | `functions.json` `label` | Alle Labels ≤ 10 Zeichen (max. 10: „Wenig Raum", „Neu lernen", „Bypass A/B", „Lautheit 0"). „Verarb." ist für Laien unverständlich; „Zisch −/+" lässt offen, ob „+" mehr Zischen oder mehr Entzischung heißt (die CLI nennt dieselbe Taste `deess-more`) | „DSP an/aus" (10) oder „Kette aus"; „Zischen −" / „Zischen +" (9) analog zu Lauter/Leiser: Minus = weniger Zischen |
| PO2-11 | niedrig | `integrations/mx_console/README.md`; `docs/06` §6.2 („17 Funktionen") vs. README („20 Stück") | Als Options+-Nutzer ohne Audiowissen verständlich verdrahtbar (Schritte 1–5 klar, Fehlersuche gut). Es fehlt eine Klartext-Tabelle „Wann drücke ich was" (die README verweist auf docs/05, das technisch ist). Die Skripte setzen `.venv` aus docs/01 voraus; ohne sie startet Options+ (minimaler PATH) Apples `python3` ohne PyYAML → Traceback ohne Mitteilung – im README nicht genannt. Zählung 17 vs. 20 Funktionen inkonsistent | Tabelle „Symptom → Taste" in Laiensprache (8 Zeilen), Hinweis „`.venv` nach docs/01 anlegen, sonst `PYTHON_BIN` setzen", Zählung angleichen |
| PO2-12 | niedrig | `adaptive.py` `learning` (uhrbasiert), `runtime.py` `adaptive_tick()` (kein Update im Aus-Zustand), `livecontrol.py` `_cmd_relearn()` | Lernphase läuft im Kill-Switch-Aus-Zustand nach Uhr weiter: Aus während der Lernphase oder nach „Neu lernen" → beim Einschalten ist die Lernphase „vorbei", ohne dass gehört wurde; nach Relearn-im-Aus beginnt die Regelung mit 2 s Analyzer-Historie | Beim Einschalten `_t_start` um die Aus-Dauer verschieben (Lernphase pausiert), Relearn im Aus-Zustand ablehnen oder vormerken |
| PO2-13 | niedrig | `config/…`/`livecontrol.py` `LOUDNESS_TARGET_BOUNDS_LUFS` (−24…−12) | Oberes Ende −12 LUFS ist für Konferenzsysteme zu heiß (deren AGC regelt es wieder herunter); gemessen bei +6: −12,7 LUFS, True Peak −1,72 dBTP, Limiter-GR Mittel 0,22 dB – technisch unkritisch, fachlich ohne Nutzen | Oberes Ende auf −14 LUFS (+4 LU) oder Hinweis in der Antwort ab −14 |
| PO2-14 | Hinweis | `tests/test_cli.py::test_run_opens_control_socket_and_answers_a_live_command` | Im vollen Lauf unter CPU-Last (parallel laufende Simulationen) fehlgeschlagen, isoliert bestanden (2,1 s). Zeitabhängig | An die Software-Prüfung: Timeout/Wartebedingung des Tests prüfen |

Positiv (kein Befund): Offsets werden von der Regelung nicht zurückgeregelt (23 Regelschritte, alle in Richtung Ziel + Offset); Reset stellt die drei Schwellen bitgenau auf die Kalibrierwerte; Mute, Bypass, Kill-Switch und Reset sind knackfrei (Ton-Test: Blocksprung in der Rampe ≤ 0,37 dB, außerhalb ≤ 0,21 dB); Kill-Switch pausiert Analyzer und Regelung ohne Spuren (Schwellen, `t_start`, Normalizer-Gain unverändert, keine Nachholschritte nach dem Einschalten, Freeze-Zustand korrekt wiederhergestellt, Reset schaltet wieder ein); Bypass-vs-Kill-Switch-Erklärung in docs/02 §0 und docs/05 §3 ist für Techniker klar und stimmt mit dem Code überein (Bypass rechnet weiter, Kill-Switch schläft und `recover()`t).

## 3. Fachliche Bewertung je Prüfpunkt

### 3.1 Tastenbelegung (docs/05, functions.json)
- Funktionsauswahl: Raum, Zischen, Intensität, Lautheit, Mute, Reset, Status sind die richtigen Meeting-Griffe; Tilt, Relearn, Snapshot, Bypass, Marker gehören auf Ebene 2. Freeze ist im Meeting selten (vor einer Präsentation) und eher ein Technikerwerkzeug – daher Kandidat für Ebene 2 (PO2-4).
- Richtungen stimmen in Code, Manifest, CLI und Doku überein: `deess-more` = `deess_db −2` = Schwelle tiefer = weniger Zischen; `room-less` = Schwelle höher; `warmer` = Tilt −1 (Low-Shelf +½, High-Shelf −½, Drehpunkt 1 kHz, in `eq.py` verifiziert: Low-Shelf −tilt/2); `natural` = Kompressor +2, De-Esser +1.
- Schrittweiten/Grenzen: Raum 2 dB/±6 (≈ 3 dB mehr Pausenabsenkung je Druck bei Ratio 2,5 – hörbar, nicht abrupt; gemessen tiefe Pausen −59,5 → −64,0 dBFS bei +4), Zischen 2 dB/±6 (≈ 1,5 dB mehr Reduktion je Druck bei Ratio 4, Deckel 8 dB), Intensität 2 dB Kompressor je Stufe (≈ 1,3 dB mehr Spitzenreduktion bei 3:1 – subtil, drei Stufen spürbar), Tilt 1 dB/±3 (0,5 dB je Shelf, über einen Konferenz-Codec an der Hörschwelle; akzeptabel, 1,5-dB-Schritte wären vertretbar), Lautheit 0,5 LU/±6 (richtig fein für ein Dial). Labels alle ≤ 10 Zeichen, zwei unklar (PO2-10).
- Dial = Lautheit: richtig (häufigster Griff, feinste Stufe). Dial-Druck = Mute: fachlich falsch gelöst (PO2-5), Vorschlag `loudness-reset`.

### 3.2 Makro-Semantik und Zusammenspiel mit der Regelung (livecontrol.py)
- Mit Controller geht der Offset in `set_user_offsets` (Ziel und `rest`), der Sofortschritt wird auf den Istwert addiert – sauber: Taste sofort hörbar, Regelung hält das verschobene Ziel (Simulation: nach Raum +4 keine Abwärtsschritte; nach Lüfter +8 steigt der Expander in 11 Schritten à 0,5 dB auf Ziel + Offset; nach Natürlicher bleibt Kompressor −18/De-Esser −15,5 bis zum Reset).
- Freeze: Offsets wirken weiter (direktes `set_param`), Regelung schweigt – korrekt (keine Änderung 360–480 s). Mute 20 s: Kette läuft weiter, Normalizer-Gain 0,15 → 0,11 → 0,21 dB, kein Nachregeln nach dem Unmute.
- low_snr: Der Normalizer meldet `low_snr` bei < 16 LU; `Weniger Raum` wird dann genau dort gedrückt, wo die Kappe schützen soll – und ist dort ohne Kappe am gefährlichsten (PO2-1). In der Simulation sank der Störabstand nach Reset (A) auf 18,7 LU ohne `low_snr`; der Status zeigt die Zahl, aber keinen Hinweis, dass die Regelung eingefroren ist und den Lüfter nicht mehr verfolgt.
- Kopplung Intensität ↔ De-Esser und Pegelsprung durch festes Makeup: PO2-7.

### 3.3 Reset, Relearn, Snapshot
- Reset: Offsets, Lautheit, Tilt, Mute, Bypass, Power – korrekt und sofort; Lernphase nicht neu (verifiziert: `t_start` unverändert). Freeze bleibt (PO2-2), Limiter-Schutz bleibt (PO2-3). Reset mitten im Satz: Schwellen springen bis 6 dB (Expander/Kompressor) bzw. 9 dB (De-Esser) – kein Knack (Ton-Test 0,19 dB), aber ein hörbarer Lautheitsbuckel bis der Normalizer in ~1 s nachzieht (Short-Term −18,3 → −19,3 LUFS in der Simulation bei 480–490 s). Vertretbar für einen „Notausgang", in docs/05 §5 erwähnen.
- Relearn: Lernphase neu, Analyzer leer, Offsets und nachgeführte Werte bleiben – entspricht dem Platzwechsel. Edge-Case im Aus-Zustand: PO2-12.
- Snapshot: PO2-9. Die Kalibrierdatei bleibt unberührt – gut.

### 3.4 Kill-Switch vs. Bypass (chain.py, docs/02 §0)
- Trennung sinnvoll erklärt (Tabelle docs/05 §3, Text docs/02 §0): Bypass = hören, Kill-Switch = abschalten. Code deckt das: Bypass-Crossfade bei laufenden Stufen; Kill-Switch blendet, schläft, `recover()` beim Einschalten (Filter frisch, Normalizer-Gain bleibt), Vorrang vor Bypass.
- Wiedereinschalten knackfrei: Ton −20 dBFS, Pfadunterschied 0,25 dB, max. Blocksprung 0,37 dB in der Rampe, 0,21 dB danach; Lautheit nach Sprache in < 1 s wieder bei −19,4 LUFS Short-Term (Normalizer-Gain 1,18 → 1,44 dB).
- Pausieren der Regelung: Runtime füttert den Analyzer nicht und ruft `controller.update` nicht; LiveControl friert zusätzlich ein und stellt den alten Freeze-Zustand wieder her (beide Varianten geprüft). Keine Nachholschritte nach dem Einschalten (B: 555–600 s keine Änderung, Ziel innerhalb der Totzone).
- Fachliche Anmerkung: Der Kill-Switch spart 0,44 → 0,07 ms je 5,3-ms-Block, also ~7 % eines Kerns; als Meeting-Taste ist er weniger wertvoll als „Lautheit 0", aber Nutzerwunsch – bleibt. Ausgang im Aus-Zustand ist roh (−20,1 LUFS, Raum ungedämpft) – korrekt dokumentiert.

### 3.5 Simulation – siehe §5.

### 3.6 Selftest und README
- `python -m voicechain selftest`: **12/12 bestanden** (Rechenzeit 0,438 ms je Block, Lautheit −18,6 LUFS, Pausenlautheit −47,1 LUFS, True Peak −1,52 dBTP). `pytest -q`: 170 bestanden, 1 übersprungen (root), 1 zeitabhängig (PO2-14).
- README aus Laiensicht: PO2-11.

## 4. Alternativvorschlag Tastenbelegung

Abweichungen zur Beauftragung sind **fett**. Ziel: jede Korrektur mit ihrer Gegenrichtung auf derselben Ebene, ein sichtbarer Mute, Dial-Druck ohne Fehlbedienungsfolgen.

| Ebene 1 „Meeting" | Funktion (Label) | Kommando | Begründung |
|---|---|---|---|
| K1 | Weniger Raum („Wenig Raum") | `room-less` | wie beauftragt |
| K2 | Mehr Raum („Mehr Raum") | `room-more` | wie beauftragt |
| K3 | Weniger Zischen (**„Zischen −"**) | `deess-more` | Label eindeutig: Minus = weniger |
| K4 | **Mehr Zischen („Zischen +")** | `deess-less` | Gegenrichtung auf derselben Ebene (PO2-4); „Lispeln" wird im Meeting sofort bemerkt |
| K5 | Natürlicher („Natürlich") | `natural` | wie beauftragt (ohne De-Esser-Kopplung, PO2-7) |
| K6 | Dichter | `dense` | wie beauftragt |
| K7 | Reset | `reset` | wie beauftragt, hebt Freeze auf (PO2-2) |
| K8 | Status | `status` | wie beauftragt |
| Dial drehen | Lauter / Leiser | `louder`/`quieter` | ohne `--notify` (PO2-6) |
| Dial drücken | **Lautheit 0 („Lautheit 0")** | `loudness-reset` | Encoder-Konvention „Druck = Standard"; kein unsichtbarer Mute (PO2-5) |

| Ebene 2 „Feinabstimmung" | Funktion (Label) | Kommando | Begründung |
|---|---|---|---|
| K1 | Wärmer | `warmer` | wie beauftragt |
| K2 | Heller | `brighter` | wie beauftragt |
| K3 | **Freeze / Weiter („Freeze")** | `freeze` | Technikerwerkzeug, selten im Meeting; macht Platz für „Zischen +" auf Ebene 1 |
| K4 | Verarbeitung an/aus (**„DSP an/aus"**) | `power` | Nutzerwunsch; verständliches Label |
| K5 | Neu lernen | `relearn` | wie beauftragt |
| K6 | Snapshot | `snapshot` | wie beauftragt |
| K7 | Bypass A/B | `bypass` | wie beauftragt |
| K8 | **Mute („Mute")** | `mute` | sichtbar auf einem Tastendisplay; wer den Konferenz-Mute nutzt, lässt die Taste frei. Marker wandert zu `unassigned` (CLI) |
| Dial | wie Ebene 1 | | |

Falls der Nutzer am Dial-Mute festhält: dann Mute **zusätzlich** auf Ebene 2 K8 sichtbar machen und in der Status-Mitteilung an erster Stelle „MUTE" nennen; `loudness-reset` bleibt dann ohne Taste.

## 5. Simulationszahlen

Aufbau (`sim_meeting.py`): Kalibrierung per `calibrate.recommend` auf 60 s Testsprache (−26 dBFS, Teppich −60 dBFS, Zischen −3 dB) → Trim +6,05 dB, Expander −44,01, De-Esser −16,47, Kompressor −20,0 dBFS, Makeup 6,21 dB, Ziel −18 LUFS. 600 s synthetische Sprache (Pausenanteil 0,3), Teppich −60 dBFS, ab 300 s −52 dBFS (Lüfter +8 dB). Chain (256er Blöcke) + LiveAnalyzer + AdaptiveController (Lernphase 30 s, Intervall 2 s, Schritt 0,5 dB) + LiveControl mit Fake-Hooks wie in der Runtime (Mute = 30-ms-Ausgangsrampe, Bypass = `set_bypass`, Kill-Switch = `set_processing` + Analyzer/Regelung pausieren). Variante A = Ablauf laut Auftrag; Variante B = zusätzlich Freeze aus bei 486 s. Messgrößen je Phase: integrierte Lautheit des Ausgangs, Pegel der kräftigen Sprachrahmen, Pegel der „tiefen Pausen" (≥ 400 ms nach Sprachende, Hold/Release abgeklungen), Pausenabstand = Sprache − tiefe Pause.

### 5.1 Ereignisse (A; B identisch bis 480 s)

| t | Ereignis | Expander | De-Esser | Kompressor | Antwort / Zustand |
|---|---|---|---|---|---|
| 120 s | Weniger Raum ×2 | −44,01 → **−40,01** (sofort) | – | – | „Raum +4 dB (weniger Raum, Expander −40 dBFS)" |
| 240 s | Natürlicher ×1 | – | −16,47 → **−15,47** | −20,00 → **−18,00** | „Intensität −1 (natürlicher, Kompressor −18 dBFS)"; Short-Term-Lautheit kurz −19,5 → −17,9 LUFS, nach 10 s wieder −18,1 |
| 300 s | Lüfter +8 dB | Regelung: 11 Schritte à +0,5 dB von 320 s bis 360 s (−40,01 → −34,51), Grund `noise_floor_track`, **kein Schritt abwärts** | – | – | Offsets gehalten: `ctrl.user_offsets` = {exp +4, des +1, comp +2} |
| 360 s | Freeze | eingefroren bei −34,51 (Ziel −31,95 = Teppich −45,95 + 10 + 4) | | | „Regelung eingefroren (FROZEN)"; 360–480 s keine Änderung |
| 420/440 s | Mute an / aus | – | – | – | Ausgang 420,1–439,9 s: −240 dBFS (Stille); Normalizer-Gain 0,15 → 0,11 → 0,21 dB; nach dem Unmute max. Blocksprung 0,19 dB (Ton-Test) |
| 480 s | Reset | −34,51 → **−44,01** | −15,47 → **−16,47** | −18,00 → **−20,00** | Offsets 0, Ziel −18, Tilt 0, Mute/Bypass aus; **frozen bleibt True**; `learning` False, `t_start` unverändert; `max_gain_db` 12 (kein Schutz aktiv gewesen) |
| 540 s | Kill-Switch aus | unverändert | | | „Verarbeitung AUS (nur Trim + Limiter …)"; Analyzer nicht gefüttert, `update` nicht aufgerufen; Ausgang −20,1 LUFS |
| 555 s | Kill-Switch an | unverändert (A −44,01, B −38,01) | | | A: „…, Regelung eingefroren (FROZEN)", B: „…, Regelung läuft weiter"; Freeze-Zustand wie vor dem Aus; Normalizer-Gain 1,18 → 1,18 dB; keine Nachholschritte bis 600 s |

### 5.2 Phasen (A = Auftrag; B-Werte in Klammern, wo abweichend)

| Phase | t [s] | Ausgang LUFS (int.) | Sprache aus [dBFS] | tiefe Pause ein → aus [dBFS] | Pausenabstand aus [dB] | Expander Ende | Zustand |
|---|---|---|---|---|---|---|---|
| P0 Start, Lernen + Regelung | 30–120 | −18,3 | −18,5 | −60,0 → −59,5 | 41,0 | −44,01 | – |
| P1 Raum +4 | 120–240 | −18,3 | −18,5 | −60,0 → **−64,0** | **45,5** (+4,5) | −40,01 | Offset gehalten |
| P2 + Natürlicher | 240–300 | −18,2 | −18,4 | −60,0 → −65,0 | 46,7 | −40,01 | Komp. −18, De-E. −15,47 |
| P3 Lüfter +8, Regelung | 300–360 | −18,3 | −18,5 | −52,0 → −49,2 | 30,7 | −36,01 | 11 Schritte aufwärts |
| P4 Freeze | 360–420 | −18,3 | −18,5 | −52,0 → −54,4 | 35,9 | −34,51 | FROZEN |
| P5 Mute | 420–440 | (Stille) | −240 | −240 | – | −34,51 | MUTE |
| P6 nach Mute | 440–480 | −18,2 | −18,5 | −52,0 → −54,5 | 36,0 | −34,51 | FROZEN |
| P7 nach Reset | 480–540 | −18,3 | −18,5 | −52,0 → **−40,7** (B −45,2) | **22,1** (B 26,7) | −44,01 (B −38,51) | A FROZEN; B regelt 488–532 s in 12 Schritten |
| P8 Kill-Switch aus | 540–555 | **−20,1** | −20,4 | −52,0 → −46,0 | 25,6 | unverändert | OFF, roher Pfad |
| P9 Kill-Switch an | 555–600 | −18,3 | −18,5 | −52,0 → −40,6 (B −48,5) | 22,1 (B 30,0) | −44,01 (B −38,01) | A FROZEN; B läuft, keine Änderung nötig |

Lesart: Lautheit steht in jeder aktiven Phase bei −18,2…−18,3 LUFS (Ziel −18); Raum +4 bringt 4,5 dB mehr Pausenabstand; die Regelung holt nach dem Lüfter 5,5 dB in 60 s (Erkennung 20 s + 11 × 4 s); Reset wirft im geänderten Raum 14 dB Pausenabstand weg und B zeigt, dass ohne Unfreeze nichts davon zurückkommt (PO2-2). Störabstand (Normalizer `snr_lu`): 38 LU (P0), 44 (P1/P2), 25,5 → 34 (P3/P4), nach Reset 18,7 LU (A) bzw. 27,4 LU (B); `low_snr` nie gesetzt.

### 5.3 Knackfreiheit (Ton 1 kHz −20 dBFS, `sim_tone.py`)

| Umschaltung | Pegel vorher → nachher [dBFS] | max. Blocksprung in der 30-ms-Rampe | außerhalb (bis +1,5 s) |
|---|---|---|---|
| Mute aus | Stille → −13,70 | (Rampe aus Stille) | 0,19 dB |
| Kill-Switch aus | −13,70 → −13,95 | 0,26 dB | 0,18 dB |
| Kill-Switch an | −13,95 → −13,70 | 0,37 dB | 0,21 dB |
| Bypass an / aus | −13,70 ↔ −13,95 | 0,26 / 0,20 dB | 0,18 / 0,19 dB |
| Reset | −13,70 → −13,70 | 0,19 dB | 0,19 dB |

Kriterium 6 (kein Sprung > 0,5 dB je Block außerhalb der Rampe) erfüllt; die Rampenblöcke (8 à 256) tragen die Blende.

### 5.4 Sprach-Kappe mit Raum-Offset (`sim_cap.py`, 240 s, Offset bei 60 s)

| Fall | Sprecher nach Trim | Teppich nach Trim | Offset | Schwelle Ende | Kappe (Sprache − 12) | Schwelle − Kappe | leise/laute Silben vorher → nachher | max. Exp.-GR in Sprachintervallen |
|---|---|---|---|---|---|---|---|---|
| 1 kalibriert, ruhig | −19,9 | −54,0 | +6 | −38,01 | −32,1 | −5,9 dB | −8,2 → −7,5 dB | 17,5 dB (Silbenlücken) |
| 2 leiser, Raum −50 | −24,9 | −44,0 | +6 | **−32,51** | −37,0 | **+4,5 dB** | −7,1 → **−8,6 dB** | 15,1 dB |
| 3 leiser, ohne Offset | −24,9 | −44,0 | 0 | −38,51 | −37,0 | −1,5 dB | −7,1 → −6,8 dB | 7,2 dB |

Regelung in Fall 2: 62 s −33,5, 66 s −33,0, 70 s −32,5 (Ziel −31 = min(−34,2, −37,0) + 6), kein `speech_cut`. Die Kappe wird nicht gehalten (PO2-1).

### 5.5 Lautheits-Offset (`sim_loud.py`, 120 s, Offset bei 40 s, Auswertung 80–120 s)

| Offset | Ziel | Ausgang integriert | True Peak | Limiter-GR Mittel (max. Intervall) | Normalizer-Gain |
|---|---|---|---|---|---|
| 0 | −18 | −18,4 LUFS | −4,35 dBTP | 0,00 dB | 1,6 dB |
| +3 | −15 | −15,3 LUFS | −2,48 dBTP | 0,00 (0,03) dB | 4,6 dB |
| +6 | −12 | −12,7 LUFS | −1,72 dBTP | 0,22 (0,38) dB | 7,6 dB |
| −6 | −24 | −24,4 LUFS | −10,35 dBTP | 0,00 dB | −4,4 dB |

±1 LU Offset → ±1 LU Ausgang (Kriterium 3 erfüllt, Abweichung ≤ 0,7 LU am oberen Ende); kein Limiter-Schutz ausgelöst.

### 5.6 Reset nach Limiter-Schutz, Anschlag, Snapshot (`sim_reset_protect.py`)

- Vor Reset: `normalizer.max_gain_db` = 4,0 (Schutz aktiv), Freeze an. Nach Reset: Antwort „Reset: Kalibrierzustand, Offsets 0, Mute und Bypass aus", **`max_gain_db` = 4,0 (Konfig 12,0), Schutz aktiv, frozen True** (PO2-2, PO2-3).
- Anschlag (Basis −34, Bound −30): +2 → −32, +4 → −30, +6 → „ok, Raum +6 dB", Schwelle −30 (PO2-8).
- Snapshot bei Kette −46,0 (nachgeführt), Basis −45 + Raum +2 → Datei −43,0; `_live` = {created, offsets}; `validate_config` ok (PO2-9).

## 6. Dateien

- Bericht: `<pruefordner>/b2/po/pruefung_po_b2.md`
- Simulation: `sim_meeting.py` (+ `sim_meeting.log`, `sim_A_result.json`, `sim_B_result.json`, `sim_A_adaptive.jsonl`, `sim_B_adaptive.jsonl`), `sim_tone.py`, `sim_cap.py`, `sim_loud.py`, `sim_reset_protect.py` im selben Verzeichnis.
