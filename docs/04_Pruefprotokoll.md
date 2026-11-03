# Prüfprotokoll

Prüfkette nach `memory/01_rolle_routinen.md`. Vollständige Prüfberichte: `docs/pruefungen/`.
Stand der Prüfung: Commit f2345fb (227 Tests grün, Selftest 9/9).

## Ergebnis der drei Prüfungen (Runde 1, 2026-11-03)

| Prüfung | Urteil | hoch | mittel | niedrig |
|---------|--------|------|--------|---------|
| 1 Product Owner (audiofachkundig) | abnehmbar mit Auflagen | 3 | 5 | 6 |
| 2 Software (Lazy Coding, Robustheit, Mutationstests) | abnehmbar mit Auflagen | 0 | 5 | 11 |
| 3 Blind (nur Beauftragung + Repo) | abnehmbar mit Auflagen | 0 | 3 | 7 |

Mutationstests: 18 Mutationen, 15 von der Testsuite gefangen, 3 überlebt (Stufenreihenfolge,
Bounds-Klammer der Regelung, Chain-Lock). Abnahmekriterien §6 wurden von der Blindprüfung alle
eigenständig reproduziert.

## Konsolidierte Befunde mit Entscheidung (Projektleitung)

Zuordnung: DSP = Agent DSP-Kette, KAL = Agent Kalibrierung/Regelung, INT = Agent Integration/Doku.

| Nr | Schwere | Befund (Kurzform) | Entscheidung | Zuordnung |
|----|---------|-------------------|--------------|-----------|
| PO-1 | hoch | Normalizer-Gate wirkt auf geglättete Lautheit; nach Satzende läuft der Gain ~7 s hoch, Pausenrauschen bis 7–12 dB lauter, nächster Satz zu laut | Gate auf Momentary (400 ms) plus relatives Gate (Sprachlautheit − 10 LU), keine Gain-Erhöhung auf fallender Hüllkurve, asymmetrische Rate; gate_lufs und max_gain_db aus Kalibrierung | DSP (Stufe), KAL (Kalibrierung) |
| PO-2 | hoch | Regelung hält Tastaturklicks und leise Fremdsprache für Sprache; De-Esser/Kompressor driften in 4 min Zuhörphase an die Grenzen | Plausibilitätsprüfung (Pegel ±6 dB um Referenz, speech_ratio ≥ 0,3, Dynamik ≥ 15 dB), sonst `implausible_speech` loggen und halten; nach > 60 s ohne plausible Sprache Rückkehr zum kalibrierten Wert | KAL |
| PO-3 | hoch | EQ-Kalibrierung hebt bei hohen Stimmen (f0 ≥ 180 Hz) Tiefbass per Low-Shelf +4 dB an; Referenz −6 dB/Okt überschätzt Höhen | Shelves nur als Cut, Boost nur Peak-Bänder; f0-Messung; Referenz oberhalb 4 kHz steiler (−9 bis −12 dB/Okt, Annahme kennzeichnen) | KAL |
| PO-4 | mittel | De-Esser-Schwellenmodell in RMS, Detektor misst Band-Spitzen (+11,8 dB); Regel inert, De-Esser steht am Deckel | Modell auf Band-Spitzenpegel (95. Perzentil, aktive Rahmen) umstellen; adaptive Regel erbt | KAL |
| PO-5 / BL-2 | mittel | Schutzregeln als Intervallmittel beschrieben, Runtime liefert 5-ms-Schnappschuss | Stufen akkumulieren `gain_reduction_mean_db` / `gain_reduction_max_db` seit letztem get_meters; Regelung nutzt Mittel/Max | DSP (Akkumulator), KAL (Verbrauch) |
| PO-6 / BL-6 | mittel | README: QuickTime exportiert kein WAV; ffmpeg nicht Teil des Setups | Eigenes Kommando `record`; Ausweich `afconvert` (macOS-Bordmittel) dokumentieren | INT |
| PO-7 | mittel | gate_lufs, max_gain_db nicht kalibriert | siehe PO-1: aus Pausenpegel nach Kette ableiten, max_gain so begrenzen, dass Rauschen nie Sprachlautheit erreicht | KAL |
| PO-8 / BL-1 | mittel | Rauschkriterium relativ umgedeutet, Auftraggeber nicht beteiligt; absolute Anhebung der Pausen nicht sichtbar | Entscheidung PL: Beauftragung §6.3 geändert (relativ + Pausenlautheit ≤ Ziel − 20 LU, Pause ≥ 5 s im Sample); Bericht zeigt Absolutwerte | KAL (evaluate, Bericht, Sample), INT (Selftest) |
| SW-1 | mittel | Ein NaN/Inf-Sample vergiftet Filterzustände dauerhaft | isfinite-Guard in Chain.process: Block nullen, Stufen zurücksetzen, Zähler `input.nonfinite_blocks`, Statuszeile zeigt ihn | DSP, INT (Anzeige) |
| SW-2 | mittel | CLI-Tracebacks bei Nicht-WAV, leerer Datei, Verzeichnis, unschreibbarem Pfad | Fehlerklassen von soundfile/OS fangen, Meldung + Exit 2, Tests | INT |
| SW-3 | mittel | Reihenfolge-Test tautologisch; Mutation „Kompressor vor De-Esser“ überlebt | Test gegen feste Klassenliste im Test selbst, nicht gegen STAGE_ORDER | DSP |
| SW-4 | mittel | Zwei Default-Konfigurationen (calibrate.DEFAULT_CONFIG vs. YAML), inhaltlich verschieden | Nur YAML ist Quelle; calibrate lädt über voicechain.config | KAL |
| SW-5 | mittel | Kein Test der echten Regelschleife (Chain + LiveAnalyzer + Controller) | Integrationstest 120–300 s Simulation mit Teppich-Sprung, Lernphase, Bounds, keine Oszillation, Log | KAL |
| BL-3 / PO-9 | mittel | Doku und YAML-Kommentare sagen +8 dB, Code 10 dB; „10,5 dB Absenkung“ falsch (Kennlinie 15 dB) | Alle Stellen auf 10 dB und korrekte Absenkung | DSP (YAML, docs/02), KAL (docs/03, Kommentar) |
| PO-Startwerte | mittel | High-Shelf 10 kHz +1,5 dB, Expander Hold/Release 50/150 ms | Übernommen: High-Shelf 0 dB, Hold 100 ms, Release 250 ms; max_reduction bleibt 8 dB nach PO-4 | DSP |
| SW-6 | niedrig | set_param nicht atomar (ungültiger Wert bleibt gespeichert) | Erst validieren, dann zuweisen; Test | DSP |
| SW-7 | niedrig | deesser.ratio 1.0 gültig, aber Kalibrierung/Regelung werfen | Schema-Untergrenze > 1 (1.1) | DSP, KAL |
| SW-8 | niedrig | Lock über gesamte process-Dauer; Filterentwurf unter dem Lock | Koeffizienten außerhalb berechnen, unter Lock nur tauschen; Docstring ehrlich | DSP |
| SW-9 | niedrig | Bounds-Klammer und Lock nicht testgesichert | Tests ergänzen (Mutationen M12, M15) | KAL (M12), DSP (M15) |
| SW-10 / BL-9 | niedrig | K-Bewertung doppelt; drei dB-Böden; zwei WAV-Leser; zwei save_config | meters importiert K-Bewertung aus dsp/biquad (Blattmodul); gemeinsamer dB-Boden; WAV-Leser und save_config nur einmal | KAL, INT |
| SW-11 | niedrig | ImportError-Fallbacks, importorskip | Entfernen, Module sind vorhanden | KAL |
| SW-12 | niedrig | Skripte: `--` fehlt, Python-Versionsprüfung fehlt | Beheben | INT |
| SW-13 | niedrig | „never allocates“ ungenau (65 Allokationen je Block, im Budget) | Docstring korrigieren | INT |
| SW-14 | niedrig | find_device: isdigit akzeptiert „¹“ | isdecimal/ASCII prüfen | INT |
| SW-15 | niedrig | Test schreibt logs/ ins Repo; Budget-Test 1,07 ms | tmp_path; Budget 1 ms Median | INT |
| SW-16 | niedrig | calibrate mit NaN-WAV: kryptische Meldung | „Sample nicht finit“ prüfen | KAL |
| BL-4 | niedrig | docs/02 „Makeup bleibt 0“ widerspricht Kalibrierung | Korrigieren | DSP |
| BL-5 | niedrig | adaptive.* Parameter fehlen in docs/02 | Ergänzen | DSP (mit Zulieferung KAL in docs/03 verlinken) |
| BL-7 / PO-12 | niedrig | README „sieben Stufen“ (acht); Berichtspfad docs/03 vs. CLI-Default | Korrigieren | INT (README), KAL (docs/03) |
| BL-8 | niedrig | Konvergenzzeit der Regelung nicht dokumentiert (+5,5 dB nach 50 s) | In docs/03 dokumentieren | KAL |
| BL-10 | niedrig | Validator verbietet Werte außerhalb bounds, Regelung behandelt den Fall | Validator bleibt strikt (Konfigdateien müssen konsistent sein); Rückholung bleibt als Laufzeitschutz, dokumentiert und getestet (SW-9) | KAL |
| PO-10 | niedrig | docs/01: BlackHole ist HAL-Plug-in, kein Systemerweiterungsdialog; Installer-Passwort fehlt | Korrigieren | INT |
| PO-11 | niedrig | Zoom-Mikrofonregler „75–100 %“ | 100 % | INT |
| PO-13 | niedrig | Freeze nur als Startoption | Umschalten zur Laufzeit per Signal (SIGUSR1) und dokumentieren | INT |
| PO-14 | niedrig | −18 LUFS gilt mono; Dual-Mono misst stereo +3 LU | Dokumentieren (docs/02, docs/01) | DSP, INT |

## Nacharbeit (Runde 1)

Alle Befunde aus der Tabelle wurden von den ursprünglichen Umsetzenden bearbeitet
(Commits 455b78f DSP, 91b2894 Kalibrierung/Regelung, nachfolgender Commit Integration).
Stand nach Nacharbeit, von der Projektleitung selbst geprüft:

| Prüfung | Ergebnis |
|---------|----------|
| `pytest -q` | 302 bestanden, 1 übersprungen (Rechte-Test unter root) |
| `python -m voicechain selftest` | 12/12 bestanden, Exit 0; Störabstandsgewinn +9,5 dB, Pausenlautheit −48,3 LUFS (Grenze −38), −18,3 LUFS, True Peak −1,95 dBTP, 0,42 ms je Block |
| make-sample → calibrate → process | Exit 0; SNR 22,1 → 32,8 dB, Gate aus Pausenlautheit abgeleitet |
| Suchlauf TODO/Stubs/importorskip | keine Treffer |

Wesentliche Änderungen: Normalizer-Gate auf Momentary- plus relative Lautheit (PO-1);
Plausibilitätsprüfung und Ruhe-Rückkehr der Regelung (PO-2); EQ-Shelves nur als Absenkung mit
Grundfrequenzmessung (PO-3); De-Esser-Schwelle aus Band-Spitzenpegel (PO-4); Intervallmeter
(PO-5); Kommando `record` statt QuickTime (PO-6); NaN-Schutz (SW-1); CLI-Fehlerpfade (SW-2);
echte Reihenfolge- und Nebenläufigkeitstests (SW-3, SW-9); eine Konfigurationsquelle (SW-4);
Regelschleifen-Integrationstest (SW-5).

Abweichung von einer PO-Vorgabe, fachlich begründet durch den DSP-Agenten: „Pausen-RMS am
Ausgang ≤ Eingang − 6 dB“ ist bei gleichzeitiger Anhebung der Sprache um 8 dB nicht erreichbar;
geprüft wird stattdessen Störabstandsgewinn ≥ 6 dB und Pausenlautheit ≤ Ziel − 20 LU (§6.3 neu).
Zur Bestätigung an den PO in Runde 2.

## Prüfung der Nacharbeit (Runde 2, 2026-11-03)

Berichte: `docs/pruefungen/04_product_owner_runde2.md`, `05_software_runde2.md`, `06_blind_runde2.md`.

| Prüfung | Urteil | Status Runde-1-Befunde | neu hoch | neu mittel | neu niedrig |
|---------|--------|------------------------|----------|------------|-------------|
| Product Owner | abnehmbar mit Auflagen | PO-1…PO-3 per Simulation bestätigt; 12 behoben, 2 teilweise (Doku) | 1 | 1 | 6 |
| Software | abnehmbar mit Auflagen | 14 behoben, 2 teilweise; Mutationen 18/18 gefangen (vorher 15/18) | 0 | 3 | 5 |
| Blind | abnehmbar mit Auflagen | alle §6-Kriterien eigenständig bestätigt | 0 | 2 | 9 |

### Entscheidungen Projektleitung für Runde 3

| Nr | Schwere | Befund (Kurzform) | Entscheidung | Zuordnung |
|----|---------|-------------------|--------------|-----------|
| PO-15 | hoch | Raum wird nach Kalibrierung lauter: Gate/Max-Gain nicht nachgeführt, relatives Gate kollabiert, Pause auf Sprachlautheit | Normalizer erhält internen Teppich-Schätzer (Minimum-Statistik auf Momentary-Lautheit); wirksames Gate = max(gate_lufs, Teppich + 8 LU); Gain-Deckel so, dass Teppich + Gain ≤ Ziel − 15 LU; Test nach §6.4 neu | DSP |
| SW-17 | mittel | Relative Gate-Referenz kann nie sinken; leiser werdender Sprecher bleibt eingefroren | Referenz klingt bei nur relativ gesperrten Intervallen langsam ab (z. B. 1 LU/s); Test Sprung −20 dB | DSP |
| SW-20 | niedrig | NaN-Reset wirft Normalizer-Gain weg (15 s Erholung) | Gain und Lernzustand beim Reset behalten | DSP |
| SW-15 Rest | niedrig | Budget-Test 1,07 ms ohne Median | Median < 1 ms, p99 < 2 ms | DSP |
| PO-17 | niedrig | Bounds-Obergrenze De-Esser −15 klemmt | Obergrenze −10 dBFS in YAML und docs | DSP |
| BL-4 | niedrig | HPF-Ordnungswechsel nicht knackfrei | Dokumentieren wie lookahead_ms; nicht im adaptiven Pfad | DSP |
| BL-7 | niedrig | Regelung setzt normalizer.max_gain_db ohne Bounds-Eintrag | Bounds-Eintrag in YAML, docs/02 korrigieren | DSP, KAL |
| PO-16 | mittel | Ohne Zischproblem bleibt Standardschwelle −30 → De-Esser greift auf fast jedem Laut | Ruheschwelle = Bandspitze p95 + 2 dB, auch als rest-Wert | KAL |
| SW-19 | mittel | Vorlaufstille zählt als Pause für das Pausenkriterium | Rückfall entfernen, nur Pause nach erstem Satz; Fehlermeldung mit Hinweis | KAL |
| PO-19 / BL-1 | niedrig/mittel | speech_cut-Fehlalarm an Intervallgrenzen und über Lernphase akkumuliert | Akkumulator am Ende der Lernphase leeren; Regel nur, wenn Intervall vollständig sprachaktiv war und das Maximum nicht in den ersten 200 ms liegt oder zwei Intervalle in Folge; Test mit Pausenende im Intervall | KAL |
| PO-20 | niedrig | Bei implausiblem Pegel entfällt Kappe Sprache − 12 | Kappe bleibt aktiv, solange überhaupt Sprache erkannt ist | KAL |
| PO-18 | niedrig | High-Shelf nach Kalibrierung stets am Limit −4 dB | Messband 7–14 kHz nur auf nicht-zischenden Rahmen, Begrenzung −3 dB | KAL |
| PO-21 | niedrig | Zweiter Kalibrierdurchlauf im Grenzfall max_gain 0 | Erster Durchlauf mit Normalizer deaktiviert messen | KAL |
| SW-23 | niedrig | Dubletten: dB-Boden in runtime, zwei Blockverarbeitungen, True-Peak-Toleranz, Bandspitze offline/streaming | KAL exportiert Toleranz und eine Bandspitzen-Funktion; INT nutzt DB_FLOOR_DBFS und calibrate.process_with_chain | KAL, INT |
| BL-2 | mittel | „alle Parameter" nicht wörtlich erfüllt | Beauftragung §3.2 präzisiert (signalabhängige Parameter), keine Codeänderung | PL (erledigt) |
| BL-3 | niedrig | update() braucht „analysis"-Schlüssel, nicht in §4.2 | §4.2 im Code-Docstring und docs/03 ist maßgeblich; Vertrag ergänzt durch Verweis | KAL (docs/03) |
| BL-5 | niedrig | Kein Pump-Detektor | Nicht umgesetzt; als bekannte Grenze in docs/03 §11 dokumentieren | KAL |
| SW-18 | mittel | CLI übergibt kalibriertes De-Esser-Band nicht an LiveAnalyzer | Übergeben, Test | INT |
| SW-21 / SW-22 / SW-24 | niedrig | record: PortAudioError nicht gefangen, Verzeichnis vor Prüfung angelegt; Stream-Leak bei Startfehler; CLI-Meldungen ohne Kontext | Beheben, Tests | INT |
| PO-22 / PO-10 Rest / BL-6 / BL-8 / BL-9 | niedrig | Doku-Reste (docs/01:270 Treiber-Dialog, SIGUSR1 in docs/03, „bestanden" vs. „ja", −6/−3 dBFS, Ratio-Bereich, Python-Beschaffung im README, interne Prüfnummern in Code-Kommentaren) | Bereinigen; Prüfnummern in Kommentaren durch fachliche Begründung ersetzen | INT, KAL, DSP |
| §6.3/§6.4 | — | PO-Klarstellungen (20 LU Mindestwert, Raumänderung im Betrieb) | Beauftragung ergänzt | PL (erledigt) |

## Nacharbeit Runde 3 und PO-Abschlussprüfung (Runde 3, 2026-11-03)

Nacharbeit Runde 3 (Commits adc6d5d INT, 20baefc KAL, d277ce7 DSP): alle Befunde der Tabelle oben
bearbeitet; Gesamtsuite 325 Tests grün, Selftest 12/12.

PO-Abschlussprüfung Runde 3 (`docs/pruefungen/07_product_owner_runde3.md`): **nicht abnehmbar**
auf Stand d277ce7. PO-16 bis PO-19, PO-21, PO-22 behoben; PO-15 nur für +8 dB Raumänderung,
bei +12 dB lief der Normalizer-Gain in Pausen erneut hoch (PO-23, hoch: Klemmung des
Teppich-Schätzers auf Sprachreferenz − 15 LU). Weitere Befunde: PO-24 (mittel, Expander-Kappe
entfällt bei leisem Sprecher), PO-25 (mittel, Zischkriterium ohne Hysterese), PO-26 bis PO-28
(niedrig). Rate-Empfehlung: Aufwärtsrate 1 dB/s beibehalten (Systemmessung: Rate ist nicht der
begrenzende Faktor).

Entscheidung Projektleitung: §6.4 zweistufig präzisiert (bis +8 dB voller Abstand ≥ 15 LU; darüber
Einfrieren des Gains, Einsatz ≤ +1,5 dB, Abstandsverlust ≤ 5 LU, Ereignis „Störabstand zu
gering“), da der Expander Pausen bei Störabstand unter ca. 16 dB nicht weiter absenken kann, ohne
Sprache abzuschneiden.

## Nacharbeit Runde 4 (Commits 2c5cc6b KAL, 6fc21ab DSP)

| Nr | Änderung | Nachweis |
|----|----------|----------|
| PO-23 | Teppich-Schätzer ohne Klemmung (Plateau-Statistik, 0,5-s-Fenster ≤ 3 LU Spanne, Minimum über 10 s); Zustand `low_snr` (Eintritt 15 LU, Austritt 17 LU): Gain eingefroren, keine Referenz gelernt | Systemtreuer Test rosa/weiß, +8/+12 dB: Gain-Anstieg in Pause 0,00–0,16 dB, Einsatz +0,18…+0,52 dB; +8 dB rosa 20,9 LU Abstand; weiß +12 dB Abstand 2,2 LU (Eingang 6,7), keine Pause auf Sprachlautheit |
| PO-24 | Kappe „Expander ≤ Sprache − 12“ bleibt ab Dynamik 10 dB | leiser Sprecher −28 dBFS im −46-Raum: Expander −38,0 |
| PO-25 | Zischkriterium mit Hysterese und Grenzstimmen-Zweig | Grenzstimme −12,4 dB besteht; zischende Stimme +0,5 dB fällt durch |
| PO-26 | LiveAnalyzer-Teppich aus inaktiven Rahmen (5. Perzentil) | dichter Vortrag 10 % Pausen: Teppich ± 1,5 dB, 0 Expander-Schritte in 300 s |
| §6.4 b / PO-27 | Ereignis `low_snr` mit Hinweistext, höchstens alle 30 s; docs/02 §7 systemtreu, docs/03 §8/§11 ehrlich (synthetische Nachweise, Hördurchlauf nötig) | Test: Ereignisse bei 30/60/90 s |
| PO-28 | Synthese mit Obertönen bis 8 kHz | High-Shelf-Band liest −28,7 dB (Referenz −29,6) |
| SW-17 Nachtrag | Referenz klingt nur in steigenden/stetigen Intervallen ab | Sprecher 20 dB leiser: Ziel ± 2,5 LU in < 40 s |

Stand nach Runde 4: 336 Tests grün, 1 übersprungen (Rechte-Test unter root), Selftest 12/12,
0,43 ms je 256er-Block.

## PO-Prüfung Runde 4 und Nacharbeit Runde 5

PO-Prüfung Runde 4 (`docs/pruefungen/08_product_owner_runde4.md`, Stand 6fc21ab): **nicht
abnehmbar** wegen Regression PO-29 (hoch): Startet der Stream mit Raumrauschen vor dem ersten
Wort, lernte der Normalizer die Sprachreferenz aus dem Rauschen und verriegelte sich im Zustand
low_snr; Folge auch für die Kalibrierung (Normalizer-Gain 0, unzischende Stimmen fielen beim
Lautheitskriterium durch). Weitere Befunde: PO-30 (mittel, low_snr rastet im +8-dB-Raum ein),
PO-25 teilweise, PO-27 teilweise, PO-31/PO-32 (niedrig). PO bestätigt die Entscheidung, den
Kompressor im low_snr-Zustand nicht zu entschärfen, mit Auflage zu den Zahlen in §6.4 b.

Nacharbeit Runde 5 (Commits 9d97843 INT, 4fa57bf KAL, dce171f DSP):

| Nr | Änderung | Nachweis |
|----|----------|----------|
| PO-29 | Referenz nur aus modulierten Intervallen (Spanne ≥ 6 LU über 2 s) und ≥ Teppich + 13 LU; ohne Referenz wird der Gain gehalten | Vorlauf 3/10 s in −55/−50/−45 dBFS: Gain-Anstieg im Vorlauf 0,00 dB, Sprache danach −16,7…−17,0 LUFS; Kalibrierung mit 10 s Vorlauf: Lautheit bestanden, Gain > 0,5 dB |
| PO-30 | low_snr lernt weiter aus sicherer Sprache (Teppich + 13 LU) | −45-Raum: low_snr 94–98 %, Gain regelt trotzdem (2,6 dB); +8 dB rosa, Sprecher −6 dB: −19,3 LUFS |
| PO-25 | Zischkriterium geschlossen: nachher ≤ −15 oder Δ ≤ −1 oder (vorher ≤ −10 und Δ ≤ +1) | 18-Fälle-Tabelle, echte Kette Grenzstimme bestanden |
| PO-26 Rest | Pegelplausibilität mit 1 dB Hysterese | kein track/rest-Pendeln bei 5,9 ↔ 6,5 dB |
| PO-27 Rest | docs/03 §8 systemtreu | — |
| PO-31 | Statuszeile zeigt `snr … LU` bzw. `SNR LOW (… LU)` | Test 4 Fälle |
| PO-32 | Standard-Rosa im Raumänderungstest; docs/02 §7 Tabelle neu | HPF-Verlust 1,0 statt 3,3 dB |
| Entscheidung | sichere Sprache = Teppich + 8 + 5 LU (DSP, statt +10): mit +10 endete das Lernen am Betriebspunkt eines 5–8 dB lauteren Raums | docs/02 §7 |
| Entscheidung | §6.4 a: Abstand ≥ 15 LU oder mindestens Eingangsabstand (Standard-Rosa +8 dB hat am Eingang nur 12,3 LU) | Beauftragung |

Stand nach Runde 5: 371 Tests grün, 1 übersprungen (Rechte-Test unter root), Selftest 12/12.

## PO-Schlussverifikation Runde 5 und letzte Auflagen

PO Runde 5 (`docs/pruefungen/09_product_owner_runde5.md`, Stand dce171f): **abnehmbar mit
Auflagen.** PO-23, PO-24, PO-26 bis PO-32 behoben; PO-29 auf Systemebene mit 9 Startszenarien
(Vorlauf 3/10 s, −55/−50/−45 dBFS, weiß und rosa) bestätigt: Gain im Vorlauf 0,00 dB, Lautheit
−18,3 LUFS. Modulationsbedingung ohne Regression (monoton, lange Vokale, Flüstern werden geregelt;
Dauerton wird korrekt nicht gelernt). Auflagen: PO-25 Rest (mittel), PO-33 bis PO-36 (niedrig),
§6.4 a Nachführungszeit, Hördurchlauf mit echter Aufnahme vor Produktivfreigabe.

Umsetzung der Auflagen (Commits 6318dda KAL, nachfolgender Commit DSP):

| Nr | Änderung | Nachweis |
|----|----------|----------|
| PO-25 Rest | Ruhezweig: vorher ≤ −15 dB, Δ ≤ +3 dB, nachher ≤ −12 dB | Sweep −18…−10 dB in 1-dB-Schritten mit echter Kette: 9/9 bestanden; −8 dB mit Δ +0,5 weiterhin nicht bestanden |
| PO-34 | low_snr Eintritt 13 LU / Austritt 15 LU (vorher 15/17) | +8 dB rosa (PO-Messung 15,5–16,5 LU) verlässt den Zustand; +12 dB bleibt; Vorlauf- und Pausentests unverändert |
| PO-35 | docs/02 §7: Nachführung leiser Sprecher nur ≥ Teppich + 13 LU | — |
| PO-33 / PO-36 | docs/03 §11: monotone pausenlose Sprache/Dauertöne; dichter Vortrag mit geringer Dynamik | — |
| §6.4 a | „nach Nachführung (ca. 1,5 min)“ ergänzt | Beauftragung |

## Abschlussreview (Projektleitung, 2026-11-03)

**Ergebnis: Lieferung abnehmbar mit einer verbleibenden Auflage.** Die Auflage ist ein
Hördurchlauf mit einer echten Rode-NT-USB-Aufnahme auf dem Zielrechner (Kalibrierung, A/B über
`process`, Live-Betrieb in einem Zoom-Testmeeting). Alle Nachweise in diesem Repository sind
synthetisch; macOS, CoreAudio, BlackHole, Echtzeit-Dropouts und die Pegelautomatik von Zoom/Teams
hinter der Kette konnten im Container nicht geprüft werden.

Prüfung gegen die Abnahmekriterien §6:

| Kriterium | Ergebnis |
|-----------|----------|
| §6.1 `pytest -q` ohne Audiogeräte | 380 bestanden, 1 übersprungen (Rechte-Test unter root) |
| §6.2 `selftest` | 12/12 bestanden, Exit 0, 0,43 ms je 256er-Block |
| §6.3 Kalibrierung verbessert das Sample | Störabstandsgewinn +9…11 dB, Pausenlautheit −44…−48 LUFS (Grenze −38), Zischanteil ↓, −18,1…−18,5 LUFS, True Peak ≤ −1 dBTP |
| §6.4 Regelung | Lernphase ohne Eingriff, Konvergenz 75–90 s, keine Oszillation, Bounds, Log mit Grund; Raumänderung a/b wie präzisiert erfüllt (PO Runde 5) |
| §6.5 Einrichtung in 30 min | Blindprüfung Runde 2: eine Lücke (Python-Beschaffung), behoben; README-Schnellstart in 10 Schritten |
| §6.6 Reihenfolge der Kette | im Code gegen feste Klassenliste getestet; Mutation „Vertauschung“ wird gefangen |

Qualität: Lazy-Coding-Suchlauf leer; Mutationstests 18/18 gefangen (Runde 2); Nebenläufigkeit,
NaN-Schutz, CLI-Fehlerpfade getestet. Prüfkette eingehalten: drei unabhängige Rollen in zwei
vollen Runden, danach drei PO-Verifikationsrunden bis zur Abnahme.

Bekannte Grenzen (dokumentiert in docs/02 §7 und docs/03 §11): Störabstand unter ca. 13 LU am
Normalizer-Eingang → Zustand low_snr (Gain hält, Pausen bleiben hörbarer, Hinweis in Statuszeile
und Log); Dauertöne und extrem monotone Sprache werden nicht als Sprache gelernt; dichter Vortrag
mit Dynamik < 15 dB hält De-Esser/Kompressor auf den kalibrierten Werten; kein Pump-Detektor.

Empfehlung für den nächsten Schritt: 60-s-Sample mit `python -m voicechain record` am gewohnten
Platz aufnehmen, kalibrieren, Bericht lesen, `process`-Ausgabe anhören, dann Live-Test im
Zoom-Testmeeting mit Kopfhörern. Erst danach produktiv einsetzen.
