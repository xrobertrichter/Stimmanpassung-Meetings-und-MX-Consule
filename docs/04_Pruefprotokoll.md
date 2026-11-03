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
_wird nach Abschluss der Nacharbeit ergänzt_

## Prüfung der Nacharbeit (Runde 2)
_wird nach Abschluss ergänzt_

## Abschlussreview
_wird nach Abschluss ergänzt_
