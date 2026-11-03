# Blindprüfung voicechain gegen docs/00_Beauftragung.md

Prüfer: unabhängig, ohne Kenntnis der Entstehung. Geprüft: Repository `/home/user/Test`
(Stand Commit f2345fb), ausgenommen `memory/` und `docs/04_Pruefprotokoll.md`.
Umgebung: Linux-Container, Python 3.13.18, numpy 2.5.3, scipy 1.18.1, sounddevice 0.5.6
(ohne PortAudio-Bibliothek), soundfile 0.14.0, PyYAML 6.0.3, pytest 9.2.0.
Eigene Dateien: `<pruefordner>/blind/`
(`check_43.py`, `sim_adaptive.py`, `check_protect_sampling.py`, jeweils `.out`; `sample.wav`,
`cal.yaml`, `report.md`, `out.wav`, `sim_adaptive.jsonl`).

## Kurzurteil

**Abnehmbar mit Auflagen.**

Alle sechs Abnahmekriterien aus §6 werden im Container bestanden; der Schnittstellenvertrag §4 ist
vollständig und nachweislich eingehalten (Blockgrößen-Invarianz bis 1e-9, Stille→Stille exakt 0,
keine Knackser bei Laufzeitänderungen, 0,40 ms je 256er-Block). Die adaptive Regelung greift in der
Lernphase nicht ein, bleibt in den Grenzen, oszilliert nicht und protokolliert jede Änderung mit
Grund. Auflagen: (1) Der Product Owner muss die im Code vorgenommene Umdeutung des Kriteriums
„Rauschteppich ↓ ≥ 6 dB" in „Störabstand ↑ ≥ 6 dB" ausdrücklich bestätigen (BL-1); (2) die
Schutzregeln der adaptiven Regelung sind in der Runtime nicht die dokumentierte Intervall-Mittelung,
sondern eine Ein-Block-Stichprobe (BL-2); (3) Doku und Konfigurationskommentare widersprechen dem
Code beim Expander-Abstand (8 dB vs. 10 dB, BL-3). Die übrigen Befunde sind niedrig.

## Ausgeführte Abnahmeschritte (§6) – Ergebnisse

| Schritt | Kommando | Ergebnis |
|---|---|---|
| 6.1 | `python -m pytest -q` | **227 passed in 58.55 s**, ohne Audiogeräte |
| 6.2 | `python -m voicechain selftest` | 9/9 bestanden, Exit 0. Werte: SNR-Gewinn +8,7 dB, Zischen −7,5 dB, −17,3 LUFS (+0,7 LU), True Peak −1,97 dBTP, 0,403 ms/Block, Limiter-Sinus −2,49 dBTP, Impulse −15,02 dBTP, Stille −inf dBFS |
| 6.3 | `make-sample` → `calibrate --wav … --out cal.yaml --report report.md` | Exit 0, YAML + Bericht erzeugt. Kriterien: Störabstand +9,9 dB (**absoluter Rauschteppich nur −0,9 dB**, −48,1 → −49,0 dBFS), Zischanteil −2,8 dB, −17,1 LUFS (+0,9 LU), True Peak −2,48 dBTP |
| 6.3 | `process --in sample.wav --out out.wav --config cal.yaml` | Exit 0, Messzeilen vorher/nachher, 0,402 ms/Block |
| – | `python -m voicechain devices` | Exit 2, Meldung „error: PortAudio is not available: … brew install portaudio …", kein Traceback |
| – | `python -m voicechain run --config …` | Exit 2, gleiche sprechende Meldung, kein Traceback |
| – | `run --config nope.yaml`, `calibrate --wav nope.wav` | Exit 2, „configuration file not found“ / „WAV file not found“ |
| 6.4 | eigene Simulation (`sim_adaptive.py`) | siehe unten, bestanden |
| 6.6 | Reihenfolge im Code | `STAGE_ORDER = (input_trim, highpass, expander, deesser, eq, compressor, normalizer, limiter)` (`voicechain/dsp/chain.py:20`), `Chain.__init__` iteriert genau in dieser Reihenfolge (`chain.py:46`, `:102`) → entspricht §2 (0,1,2,3,4,5,5b,6) |

## Ausgaben der eigenen Prüfskripte (Kurzform)

### check_43.py (§4.3, §4.2)

```
Chain 64 vs 4096:            max|diff| = 0.00e+00   OK (≤ 1e-5)
Chain 64 vs ein Block (380928): 1.86e-09            OK
Chain 64 vs 97:              0.00e+00               OK
je Stufe 64 vs 4096:         ≤ 3.9e-15              OK (alle 8 Stufen)
Stille (3 s) → max|y| = 0, kein DC, finite          OK
Stille nach 4 s Signal, letzte 1 s: max|y| = 0      OK
set_param zur Laufzeit (10 Fälle, 440-Hz-Sinus): max|Δy| am Umschaltpunkt
  identisch mit Referenz ohne Umschaltung (Verhältnis 1.00) → kein Knacken:
  input_trim.gain_db 0→12, highpass.freq_hz 80→300, expander.threshold_db −45→−30,
  deesser.threshold_db −30→−45, deesser.freq_hz 6500→5000, eq.bands.1.gain_db 2→8,
  compressor.threshold_db −20→−35, compressor.makeup_db 0→10,
  normalizer.max_gain_db 12→3, limiter.ceiling_dbfs −1→−12
Laufzeit 256er-Block (600 Blöcke): median 0.397 ms, p95 0.461 ms, max 0.636 ms   OK (< 1 ms)
get_meters(): alle Stufen + input/output mit peak_dbfs/rms_dbfs; gain_reduction_db bei
  expander/deesser/compressor/limiter, normalizer.gain_db                      OK
process() → float32, shape (n,)                                              OK
```

### sim_adaptive.py (§3.2, §6.4) – 120 s synthetische Sprache, Rauschteppich −55 → −45 dBFS bei 60 s, Default-Config, 2-s-Raster

```
Änderungen in der Lernphase (< 30 s): 0                       OK
Grenzverletzungen (bounds): 0                                 OK
Schritte > max_step_db (0,5 dB): 0                            OK
Expander-Schwelle: 60 s = −45.00 (Ziel Teppich+10 = −45.0); 120 s = −39.50 (Ziel −35.1)
  → nach 50 s erst +5,5 dB von +10 dB nachgeführt (0,5 dB je 4 s, Erkennung ab ~72 s)
Richtungswechsel: expander 0, deesser 0, compressor 0         keine Oszillation
Log: 46 Zeilen, 31 Änderungen = 31 zurückgegebene Änderungen, jede mit
  t/stage/name/old/new/reason; Events learning×14, learn_done×1   OK
freeze(True): 0 Änderungen; freeze(False): Regelung läuft weiter   OK
```

### check_protect_sampling.py (BL-2) – kalibrierte Kette auf dem make-sample-Signal

```
9000 Blöcke, Sprachanteil 0,69; Ein-Block-Stichprobe „speech_cut“: 0 Auslösungen
Intervall-Mittel Expander-GR bei Sprache: max 0,03 dB
Controller 60 s auf kalibriertem Sample: keine Änderungen (Schwelle bleibt −32,07)
```

## Anforderungsmatrix

Status: ✔ erfüllt · ◐ teilweise · ✘ nicht erfüllt

| Nr. | Anforderung (Beauftragung) | Status | Beleg |
|---|---|---|---|
| §2 | Reihenfolge 0 Trim, 1 HPF, 2 Expander, 3 De-Esser, 4 EQ, 5 Comp, 5b Normalizer, 6 Limiter | ✔ | `dsp/chain.py:20-30`, `dsp/__init__.py:3-4`; eigener Druck von `STAGE_ORDER` |
| §2 | Python-Echtzeitprozess Rode → Kette → BlackHole; DAW/AU nur als Option erwähnt | ✔ | `runtime.py` (Duplex-Stream), README „Alternativen (nur als Hinweis)" |
| §3.1 | Paket `voicechain/` mit DSP, Metern, Kalibrierung, adaptiver Regelung, Runtime, CLI | ✔ | `voicechain/dsp/*`, `meters.py`, `calibrate.py`, `adaptive.py`, `runtime.py`, `cli.py` |
| §3.1 | `config/chain.default.yaml` mit allen Parametern, jeder Wert mit Kommentar | ◐ | Jeder Wert kommentiert; aber Kommentare zu Expander-Schwelle/Bounds nennen „+8 dB", Code 10 dB (BL-3) |
| §3.1 | CLI `devices`, `run`, `calibrate`, `process`, `selftest` | ✔ | `cli.py:build_parser`, `--help` geprüft; zusätzlich `make-sample` |
| §3.1 | Tests ohne Audiogeräte, je Stufe messbare Belege (HPF 50 Hz, Limiter Ceiling, Expander ≥ Y dB, De-Esser, Normalizer ± Toleranz) | ✔ | `tests/test_dsp_highpass.py:17` (50 Hz), `test_dsp_limiter.py:12,19`, `test_dsp_expander.py:33,46`, `test_dsp_deesser.py:12`, `test_dsp_normalizer.py:37`; sounddevice-Attrappen in `test_runtime.py`/`test_cli.py` |
| §3.2 | `calibrate`: misst Rauschteppich, Sprachpegel, Zischanteil, Neigung, Spitzen; leitet **alle** Parameter mit Grenzen ab; YAML + Bericht (vorher/nachher, Akzeptanz) | ✔ | `calibrate.py:analyze` (285), `recommend` (360), `evaluate` (537), `render_report` (817); eigener Bericht `report.md` mit 5 Abschnitten |
| §3.2 | Adaptive Regelung alle N s, kleine Schritte, Hysterese, harte Grenzen; Expander←Teppich, De-Esser←Zischen, Kompressor←Sprachpegel, Normalizer hält Ziel | ✔ | `adaptive.py:_propose` (345), `_track` (331, Totzone + max_step), `_apply` (393, 2-Intervall-Abstand, Grenzen); Normalizer ist feedforward (Stufe selbst) |
| §3.2 | Lernphase, vorsichtiges Nachregeln, Zurücknehmen bei Pumpen/Übersteuern/zu starker Absenkung | ◐ | Lernphase `update` (262); Limiter-Schutz + Speech-Cut (`_propose` 357-380). Schutzregeln beruhen in der Runtime auf einem Block je Intervall (BL-2); „Pumpen" wird nicht erkannt (nur Übersteuern und Abschneiden) |
| §3.2 | Protokoll JSONL mit Grund; Freeze | ✔ | `adaptive.py:_log`, `freeze` (237); `run --freeze`; Simulation: 31/31 Änderungen geloggt |
| §3.3 | README: Überblick, Schnellstart in 10 Schritten | ✔ | README „Schnellstart in 10 Schritten"; Schritt 7 lückenhaft (BL-6) |
| §3.3 | docs/01: BlackHole, Audio-MIDI (Aggregate), Rode, Zoom/Teams (Original Sound/HiFi, Rauschunterdrückung, Pegelautomatik aus), Kopfhörer vs. Lautsprecher, Fehlersuche (Dropouts, Samplerate, Latenz, Mikrofonrechte) | ✔ | docs/01 §2–§9, Tabelle §8 deckt alle genannten Punkte ab |
| §3.3 | docs/02: jeder Parameter mit Startwert, Bereich, Begründung, Hörbild | ◐ | Alle Chain-Parameter vollständig; `adaptive.*` (learn_s, interval_s, max_step_db, bounds, log_path) fehlen (BL-5); Expander-Abstand veraltet (BL-3); Makeup-Aussage widerspricht Kalibrierung (BL-4) |
| §3.3 | docs/03: Ablauf Kalibrierung, Live, A/B, adaptive Regelung lesen | ✔ | docs/03 §1–§11; Mittelungsaussage §8 entspricht nicht der Runtime (BL-2) |
| §3.3 | docs/04 | – | gemäß Auftrag nicht geprüft |
| §4.1 | YAML-Schema exakt wie Vertrag | ✔ | `config/chain.default.yaml`; `config.py:validate_config` streng; Zusatzschlüssel `detector_ms`, `true_peak_margin_db` optional mit Default |
| §4.2 | `Chain.from_config/process/set_param/get_param/get_meters/reset` | ✔ | `dsp/chain.py:54-127`; eigener Test der Signaturen und Rückgabetypen |
| §4.2 | Stufenklassen `__init__(sample_rate, **params)`, `process/set_param/get_param/meters/reset` | ✔ | `dsp/__init__.py:StageBase`, alle 8 Stufen |
| §4.2 | `meters.py`: LoudnessMeter (push, momentary, short_term, integrated), true_peak_dbfs (4x), rms_dbfs, noise_floor_dbfs(frame_ms=100), speech_level_dbfs, sibilance_ratio_db (5–10 kHz − 0,3–3 kHz, aktive Rahmen), spectral_tilt_db_per_oct, band_energies_db | ✔ | `meters.py:155-553`; Signaturen stimmen |
| §4.2 | `calibrate.py`: analyze, recommend, evaluate, run_calibration | ✔ | `calibrate.py:285, 360, 537, 718` |
| §4.2 | `AdaptiveController(cfg, chain, clock, log_path)`, `update(meters, now) -> [{stage,name,old,new,reason}]`, `freeze(on)` | ✔ | `adaptive.py:188, 254, 237`; Rückgabe enthält zusätzlich `t` |
| §4.3 | Blockgrößen-Invarianz 64 vs. ein Block ≤ 1e-5 | ✔ | eigener Test: 0 bzw. 1,9e-9 |
| §4.3 | Filterzustände (`zi`) fortgeführt | ✔ | `biquad.py:SosFilter.process` (196) |
| §4.3 | Keine großen Puffer pro Block, keine Sample-Schleifen; < 1 ms je 256er-Block | ✔ | `envelope.py` (vektorisierte Lindley-Form, lfilter), Normalizer-Schleife nur über 10-ms-Raster (≤ 2 Iterationen/Block); gemessen 0,40 ms median |
| §4.3 | Stille → Stille (kein DC, Rauschen, NaN) | ✔ | eigener Test: exakt 0, auch nach Signal |
| §4.3 | Laufzeitänderungen knackfrei | ✔ | eigener Test 10 Parameter; `GainRamp`, `SosFilter`-Überblendung (10 ms); einzige dokumentierte Ausnahme `lookahead_ms` (`limiter.py:66`) |
| §5 | Keine Platzhalter/TODO/pass/NotImplementedError/„später" | ✔ | grep: keine Treffer in Code |
| §5 | Keine stummen excepts | ✔ | 6× `except Exception` in `runtime.py`, alle mit Kontext/Weitergabe (117, 362, 463, 471, 492, 513) |
| §5 | Tests prüfen mit Zahlen | ✔ | Stichprobe `test_dsp_chain.py`, `test_adaptive.py`: Pegel, dB, Toleranzen |
| §5 | Jede Konstante konfigurierbar oder begründet | ✔ | Stichprobe `cli.py:40-78`, `meters.py:21-85`, `adaptive.py:63-98`, `envelope.py:40-57` |
| §5 | Doku beschreibt tatsächlichen Stand | ◐ | BL-3, BL-4, BL-7 |
| §5 | Code Englisch, Doku Deutsch | ✔ | Bezeichner/Kommentare englisch; deutsche Strings nur in Nutzerausgaben (Bericht, Selftest-Tabelle) |
| §6.1 | pytest grün ohne Audio | ✔ | 227 passed |
| §6.2 | selftest mit messbaren Prüfwerten, alle bestanden | ✔ | 9/9 |
| §6.3 | calibrate: Rauschteppich ↓ ≥ 6 dB, Zischanteil ↓, Lautheit ± 2 LU, True Peak ≤ Ceiling | ◐ | Zischen, Lautheit, True Peak erfüllt. Rauschteppich: absolut −0,9 dB, als Störabstand +9,9 dB (BL-1) |
| §6.4 | Adaptive Simulation: konvergiert in Grenzen, keine Oszillation, Lernphase, Log mit Grund | ✔ | eigene Simulation (oben); Konvergenz langsam (BL-8) |
| §6.5 | Einrichtung < 30 min ohne Audio-Vorwissen | ◐ | Doku vollständig und gut lesbar; nicht am Mac prüfbar; WAV-Aufnahmeweg lückenhaft (BL-6) |
| §6.6 | Reihenfolge = Ausgangsbild | ✔ | s. §2 |

## Befundtabelle

| Nr. | Schwere | Fundstelle | Befund | Empfehlung |
|---|---|---|---|---|
| BL-1 | **mittel** | `voicechain/calibrate.py:552-564` (`evaluate`), `cli.py:55-60`, Bericht §5; eigener Lauf: Rauschteppich −48,1 → −49,0 dBFS (−0,9 dB), Störabstand +9,9 dB | §6.3 verlangt wörtlich „Rauschteppich ↓ ≥ 6 dB". Der Code bewertet stattdessen den Störabstand (Sprache − Teppich) und besteht damit, obwohl der absolute Rauschteppich wegen Trim/Makeup/Normalizer praktisch unverändert bleibt. Die Umdeutung ist fachlich nachvollziehbar und im Bericht offen ausgewiesen, aber eine Abweichung von der Beauftragung, die der Auftraggeber nicht beschlossen hat. | PO muss die Umdeutung bestätigen oder ablehnen. Alternativ zusätzlich einen pegelnormierten absoluten Wert ausweisen (Teppich nachher minus Lautheitsanhebung) und das Kriterium in der Beauftragung präzisieren. |
| BL-2 | **mittel** | `voicechain/runtime.py:459-462` (`adaptive_tick` nur alle `interval_s`), `adaptive.py:300-311` (`_accumulate`), docs/03 §8 („im Intervall im Mittel > 3 dB"), `tests/test_adaptive.py:266` | Die Schutzregeln (Limiter-Überlast > 3 dB, Speech-Cut > 6 dB) sind als Mittel über das Intervall konzipiert und getestet, die Runtime ruft `update()` aber genau einmal je Intervall auf. Damit besteht das „Mittel" aus **einem einzigen 5,3-ms-Block alle 2 s**; eine Limiter-Überlast wird nur erkannt, wenn der zufällig abgetastete Block gerade in einer Spitze liegt. Auf dem kalibrierten Sample gab es keine Fehlauslösung (0/9000), die Erkennung ist aber unzuverlässig und die Doku beschreibt einen nicht vorhandenen Zustand. | Meter im Service-Thread häufiger abtasten (z. B. im 1-s-Statustakt `controller.accumulate(meters)`), oder in der Kette Summen/Maxima der GR seit dem letzten `get_meters()` führen und diese dem Controller geben. docs/03 §8 anpassen. |
| BL-3 | **mittel** | `docs/02_Parameter_Begruendung.md:85` („Die Kalibrierung setzt Rauschteppich + 8 dB"), `:310-314` (§11, „8 dB Abstand", „Selftest misst 5,8 dB"), `config/chain.default.yaml:30` und `:86` („+8 dB"); Code `calibrate.py:48` `EXPANDER_FLOOR_MARGIN_DB = 10.0` (Entscheidung 2026-11-03), `docs/03:99` (10 dB) | Doku und Konfigurationskommentare widersprechen dem Code und sich gegenseitig (docs/02 sagt 8, docs/03 sagt 10). §5: „Dokumentation beschreibt den tatsächlichen Stand". docs/02 §11 nennt ein veraltetes Selftest-Ergebnis (5,8 dB; aktuell 8,7 dB). | docs/02 Z. 85 und §11 sowie beide YAML-Kommentare auf 10 dB und das aktuelle Selftest-Ergebnis bringen; „Stand"-Datum in docs/02 aktualisieren. |
| BL-4 | niedrig | `docs/02:180` („Makeup bleibt 0, damit zwei Regler nicht gegeneinander arbeiten") vs. `calibrate.py:484-486` (Makeup 0…12 dB, im Lauf 6,49 dB), `docs/03:104` | docs/02 begründet Makeup = 0 als Designentscheidung; die Kalibrierung setzt Makeup regelmäßig > 0. Leser von docs/02 erwarten nach Kalibrierung weiterhin 0. | In docs/02 ergänzen: Startwert 0, die Kalibrierung setzt Makeup auf die statische Reduktion an Silbenspitzen (Verweis docs/03 §4). |
| BL-5 | niedrig | `docs/02` (kein Abschnitt zu `adaptive.*`), Bereiche nur in `config.py:39-43` | §3.3 fordert „jeder Parameter mit Startwert, Bereich, Begründung, Hörbild". `learn_s`, `interval_s`, `max_step_db`, `bounds`, `log_path` fehlen in docs/02 vollständig; docs/03 §8 nennt sie nur beiläufig ohne Bereiche (0–600 s, 0,1–60 s, 0,01–6 dB) oder Hörbild bei Fehlstellung. | Abschnitt „Adaptive Regelung" in docs/02 ergänzen (Startwert, Bereich aus `_ADAPTIVE_RANGES`, Begründung, Hörbild z. B. „max_step_db zu groß → hörbare Sprünge"). |
| BL-6 | niedrig | `README.md:86-88` (QuickTime → „als WAV exportieren"), `docs/03:46-48` | QuickTime Player exportiert Audio nur als .m4a; der in docs/03 genannte `ffmpeg`-Umweg ist in keinem Installationsschritt enthalten (`brew install ffmpeg` fehlt). Für eine Person ohne Audio-Vorwissen ist der Weg zum 48-kHz-WAV damit der unklarste Schritt des 30-Minuten-Pfads (§6.5). Die Kalibrierung lehnt andere Raten/Formate ab. | Entweder `ffmpeg` in Schritt 1 aufnehmen und den Umwandlungsbefehl ins README, oder ein CLI-Kommando `record --seconds 60 --out sample.wav` auf Basis von sounddevice ergänzen (docs/03 §2 enthält den Einzeiler bereits). |
| BL-7 | niedrig | `README.md:4` („sieben Stufen"), `docs/03:113` („reports/calibration.md") vs. `cli.py:41` Default `reports/calibration_report.md`, docs/03 §1/§3 verwenden `--report reports/calibration.md` | Kleinere Doku-Ungenauigkeiten: Kette hat acht Stufen (0–6 inkl. 5b); der Berichtsname in docs/03 §5 ist nur korrekt, wenn man den expliziten `--report` aus §3 benutzt, README nennt nur „unter reports/". | „acht Stufen (0–6 mit 5b)"; in docs/03 §5 „der Bericht (Pfad aus `--report`, Standard `reports/calibration_report.md`)". |
| BL-8 | niedrig | `adaptive.py:63-68` (HISTORY_S 20 s, „follow a changed environment within a minute"), `:80` (MIN_CHANGE_SPACING_INTERVALS 2), Simulation: +5,5 dB nach 50 s bei +10 dB Sprung | Effektive Nachführrate 0,5 dB je 4 s = 0,125 dB/s plus ~10 s Erkennungsverzögerung; ein 10-dB-Sprung des Rauschteppichs braucht ~90 s. Das ist bewusst „langsam" und konvergiert ohne Oszillation, aber weder docs/03 noch der Konstantenkommentar nennen die reale Konvergenzzeit. | In docs/03 §8 die erwartete Nachführzeit angeben (z. B. „≈ 10 s Erkennung + 8 s je dB"); ggf. `max_step_db`/Abstand als Nutzerentscheidung erläutern. |
| BL-9 | niedrig | `voicechain/meters.py:95-137` und `voicechain/dsp/biquad.py:112-147` | K-Bewertung (BS.1770) ist zweimal implementiert mit getrennt gepflegten Konstanten (Werte weichen ab der 12. Stelle ab). Kein Fehler, aber Doppelpflege. | Eine Implementierung (biquad.py) und aus meters.py importieren. |
| BL-10 | niedrig | `config.py:204-207` (Fehler, wenn aktueller Wert außerhalb `adaptive.bounds`) vs. `adaptive.py:404-406` (Wert außerhalb der Grenzen wird schrittweise zurückgeholt) | Loader und Regler widersprechen sich: Der Loader lässt den Fall, den der Regler ausdrücklich behandelt, gar nicht erst zu. Die Rückhol-Logik ist damit im Betrieb nur über `set_param` erreichbar. | Entweder Loader-Fehler zur Warnung abschwächen oder Rückhol-Logik/Test als bewusst defensiv dokumentieren. |

## Weitere Beobachtungen (keine Befunde)

- Fehlerpfade der CLI (fehlende Dateien, fehlendes PortAudio, ungültige YAML) liefern sprechende
  Meldungen mit Exit-Code 2; Selftest/Kalibrierung liefern Exit 1 bei Nichtbestehen.
- `python -m voicechain <kommando> --help` stimmt für alle sechs Kommandos mit README-Tabelle und
  docs/03 überein (Optionen, Defaults `config/chain.default.yaml`, `config/chain.calibrated.yaml`).
- Die Kalibrierung lehnt Samples ≠ 48 kHz mit einer klaren Meldung inkl. `ffmpeg`-Befehl ab;
  `process` hingegen akzeptiert andere Raten und baut die Kette dafür um – konsistent dokumentiert.
- Limiter nutzt eine feste True-Peak-Marge von 1,5 dB statt überabgetasteter Detektion; Vollpegel-
  Sinus landet bei −2,49 dBTP. Begründet in docs/02 §8; kostet 1,5 dB Headroom, fachlich vertretbar.
- Das Repository enthält keine getrackten `__pycache__`/`.pytest_cache`/`logs`-Dateien
  (`git ls-files` leer), die vorhandenen Artefakte stammen aus Läufen und sind per `.gitignore` ausgeschlossen.
