# Blindprüfung Runde 2 – voicechain gegen docs/00_Beauftragung.md

Prüfer: unabhängig, ohne Kenntnis früherer Prüfungen (memory/, docs/04, docs/pruefungen/ nicht gelesen).
Repository-Stand: Commit `d37813a`. Umgebung: Container ohne PortAudio, Python 3.13.18, numpy 2.5.3, scipy 1.18.1, soundfile 0.14.0, sounddevice 0.5.6, PyYAML 6.0.3, pytest 9.2.0.
Eigene Dateien: `<pruefordner>/r2_blind/` (Skripte `check_43.py`, `check_click.py`, `sim_64.py`, `sim_64b.py`; Ausgaben `sample*.wav`, `chain*.calibrated.yaml`, `calibration*_report.md`, `sim_*.jsonl`).

## 1. Kurzurteil

**Abnehmbar mit Auflagen.**

Alle sechs Abnahmekriterien aus §6 sind im Container nachvollziehbar erfüllt (302 Tests grün, Selbsttest 12/12, Kalibrierung mit allen fünf Kriterien bestanden, Simulation der Regelung konvergent innerhalb der Grenzen, Kettenreihenfolge korrekt). Die Software ist vollständig, sauber strukturiert und die Dokumentation beschreibt den tatsächlichen Stand. Zwei Punkte sind vor der endgültigen Abnahme zu klären bzw. zu beheben:

1. Die Schutzregel `speech_cut` der adaptiven Regelung feuert in meiner Simulation zweimal auf synthetischer Sprache, obwohl keine Sprache abgeschnitten wurde (Fehlalarm durch den Ausläufer einer Pause am Intervallanfang). Folge: die Expander-Schwelle driftet in 0,5-dB-Schritten unter das Kalibrierziel, bis die Totzone (2 dB) sie wieder hochholt – ein kleiner, aber dauerhafter Sägezahn (BL-1, mittel).
2. §3.2 verlangt, dass `calibrate` aus dem Sample **alle** Parameter ableitet. Tatsächlich werden Trim, Expander-Schwelle, De-Esser-Frequenz und -Schwelle, EQ-Gains, Kompressor-Schwelle und -Makeup, Normalizer-Gate und -Max-Gain abgeleitet; High-Pass, alle Zeitkonstanten, Ratios, Knie, Bandbreite, Limiter bleiben statisch. Die gemessene spektrale Neigung und der True Peak fließen in keinen Parameter ein. Das ist fachlich begründet (docs/03 §4) und offen dokumentiert, entspricht aber nicht dem Wortlaut der Beauftragung; der Auftraggeber muss entscheiden, ob die dokumentierte Einschränkung akzeptiert wird (BL-2, mittel).

Alle weiteren Befunde sind niedrig (Dokumentationswidersprüche, ein nicht knackfreier Parameterwechsel außerhalb des adaptiven Pfads, Schnittstellenabweichung bei `update()`).

## 2. Anforderungsmatrix

Status: ✅ erfüllt · ◐ teilweise · ❌ nicht erfüllt

### §2 Fachlicher Rahmen

| Anforderung | Status | Beleg |
|---|---|---|
| Reihenfolge 0 Input Trim → 1 HPF → 2 Expander → 3 De-Esser → 4 EQ → 5 Compressor → 5b Normalizer → 6 Limiter, unverändert | ✅ | `voicechain/dsp/chain.py:21` `STAGE_ORDER = ("input_trim","highpass","expander","deesser","eq","compressor","normalizer","limiter")`; `Chain.process` iteriert in dieser Reihenfolge (chain.py:111 ff.); Test `tests/test_dsp_chain.py:40 test_from_config_and_stage_order` |
| Normalisierung zweistufig: statisch (Trim durch Kalibrierung) + dynamisch (Normalizer nach Kompressor, vor Limiter) | ✅ | `calibrate.py:451-459` setzt `input_trim.gain_db`; Normalizer an Position 7 von 8 |
| Umsetzung als Python-Echtzeitprozess Rode → Python → BlackHole; BlackHole verarbeitet nichts; DAW/AU nur als Option erwähnt | ✅ | `runtime.py` (Duplex-Stream), README „Wichtig: BlackHole verarbeitet selbst nichts", README „Alternativen (nur als Hinweis)" |

### §3.1 Software

| Anforderung | Status | Beleg |
|---|---|---|
| Python 3.11+, numpy, scipy, soundfile, sounddevice, PyYAML | ✅ | `requirements.txt`; Skripte prüfen ≥ 3.11 (`scripts/run_live.sh:13`) |
| Paket `voicechain/` mit DSP-Stufen, Metern, Kalibrierung, adaptiver Regelung, Runtime, CLI | ✅ | `voicechain/dsp/*.py` (8 Stufen + biquad/envelope), `meters.py`, `calibrate.py`, `adaptive.py`, `runtime.py`, `cli.py` |
| `config/chain.default.yaml` mit allen Parametern, jeder Wert mit Kommentar (warum) | ✅ | Datei gelesen: jede Zeile inkl. `enabled`, `device`, `channels` trägt einen Begründungskommentar |
| CLI `devices`, `run`, `calibrate`, `process`, `selftest` | ✅ | `cli.py:641-689 build_parser`; `--help` aller Kommandos ausgeführt (Abschnitt 4); zusätzlich `record`, `make-sample` |
| Tests laufen ohne Audiogeräte | ✅ | `pytest -q` im Container: 302 passed, 1 skipped (root-Rechte), 134,8 s |
| Jede Stufe hat messende Tests (HPF ≥ X dB bei 50 Hz, Limiter nie über Ceiling, Expander ≥ Y dB in Pausen, De-Esser senkt Zischband, Normalizer trifft Ziel ± Toleranz) | ✅ | `tests/test_dsp_highpass.py:17 test_50hz_attenuation_order4`, `test_dsp_limiter.py:12,19`, `test_dsp_expander.py:33,46`, `test_dsp_deesser.py:12`, `test_dsp_normalizer.py:37`; je Stufe 7–14 Tests mit Zahlenaussagen |

### §3.2 Prüfskripte / Kalibrierung (Kernanforderung)

| Anforderung | Status | Beleg |
|---|---|---|
| Sample WAV 30–120 s mit Pausen wird eingelesen | ✅ | `calibrate.py:815-837 run_calibration` (Warnung außerhalb 30–120 s, Fehler < 10 s, Fehler ohne Pause ≥ 5 s) |
| Misst Rauschteppich, Sprachpegel, Zischanteil, spektrale Neigung, Spitzen | ✅ | `calibrate.py:334-415 analyze`: `noise_floor_dbfs`, `speech_level_dbfs`, `sibilance_ratio_db`, `spectral_tilt_db_per_oct`, `sample_peak_dbfs`, `true_peak_dbtp` (+ f0, Crest, Bandenergien, Pause) |
| Leitet daraus **alle** Parameter ab (mit Grenzen) | ◐ | `calibrate.py:432-602 recommend` + `605-640 refine_normalizer`: abgeleitet werden `input_trim.gain_db`, `expander.threshold_db`, `deesser.freq_hz/threshold_db`, EQ-Gains (4 Bänder), `compressor.threshold_db/makeup_db`, `normalizer.gate_lufs/max_gain_db`, jeweils mit Grenzen. Nicht abgeleitet: HPF, Zeitkonstanten, Ratios, Knie, De-Esser-Bandbreite, Limiter (docs/03:116 „bleiben"). Spektrale Neigung und True Peak werden gemessen, aber in `recommend` nicht verwendet (grep: `spectral_tilt` nur in `analyze` und Bericht). → **BL-2** |
| Schreibt neue YAML und Bericht (Messwerte vorher/nachher, Kriterien bestanden/nicht) | ✅ | eigener Lauf: `chain.calibrated.yaml` (valide, lädt in `run`), `calibration_report.md` mit Abschnitten 1–5, Tabelle vorher/nachher/Differenz, Kriterien mit Ergebnis und Hinweis |
| Adaptive Regelung: Meter alle N Sekunden ausgewertet | ✅ | `runtime.py:530 _service_loop` liest alle `interval_s` (2 s) `chain.get_meters()` + `analyzer.snapshot()` und ruft `controller.update` (Service-Thread, nicht Audio-Thread) |
| Langsam, kleine Schritte, Hysterese, harte Grenzen | ✅ | `adaptive.py:105` 2 Intervalle Mindestabstand, `max_step_db` 0,5 (`_track`), Totzonen 2/1,5/2 dB, `bounds` aus YAML; Simulation: 0 Grenz-/Schrittverletzungen bei 20 Änderungen |
| Expander-Schwelle folgt Rauschteppich, De-Esser-Schwelle folgt Zischanteil, Kompressor-Schwelle folgt Sprachpegel, Normalizer hält Ziel | ✅ | `adaptive.py:475-531 _propose` mit `expander_threshold_target`, `deesser_threshold_target`, `compressor_threshold_target` (gleiche Gesetze wie Kalibrierung); Normalizer hält Ziel selbst (feedforward) |
| „Erst zuhören" (Lernphase) | ✅ | `adaptive.py:364` keine Änderung vor `learn_s`; Simulation: erste Änderung exakt bei t = 30 s, 0 Änderungen davor |
| Bei Problemen (Pumpen, Übersteuern, zu starke Absenkung) zurücknehmen | ◐ | Übersteuern: `limiter_protect` (3 dB / 1,5 dB Hysterese, `_normalizer_hold`, adaptive.py:534); zu starke Absenkung: `speech_cut` (adaptive.py:505). **Pumpen**: keine Regel vorhanden. `speech_cut` liefert Fehlalarme → **BL-1**, **BL-5** |
| Alle Änderungen mit Grund protokolliert (JSONL) | ✅ | `adaptive.py:575 _log(entry)` `{t, stage, name, old, new, reason}`; Simulation: 20 Änderungen, 20 Logzeilen, 0 ohne Pflichtfelder; `update()`-Rückgabe = Log |
| „Freeze" möglich | ✅ | `adaptive.py:346 freeze(on)`; `run --freeze`; SIGUSR1-Umschaltung `runtime.py:402`; Simulation: im Freeze 9 `frozen`-Ereignisse, 0 angewandte Änderungen |

### §3.3 Dokumentation (Deutsch)

| Anforderung | Status | Beleg |
|---|---|---|
| README: Überblick, Schnellstart in 10 Schritten | ✅ | README „Schnellstart in 10 Schritten" (genau 10), Signalweg-Diagramm, Kommandotabelle |
| docs/01: BlackHole, Audio-MIDI-Setup (Aggregate), Rode-Einstellungen, Zoom/Teams (Original Sound/High-Fidelity, Rauschunterdrückung, Pegelautomatik aus), Kopfhörer vs. Lautsprecher, Fehlersuche (Dropouts, Samplerate, Latenz, Mikrofonrechte) | ✅ | docs/01 §2, §3 (Variante A/B), §4, §5 (Zoom: Original sound, Hintergrundgeräusche, Pegelautomatik), §6 (Teams: Rauschunterdrückung, High-Fidelity), §4/§8 Echo, §8 Fehlertabelle (17 Zeilen), §9 Latenz, §2.3 Mikrofonrechte |
| docs/02: jeder Parameter mit Startwert, Bereich, Begründung, Hörbild bei Fehlstellung | ✅ | docs/02 §1–§8 Tabellen je Stufe + „Hörbild"-Absatz; §11 adaptive Parameter; Bereiche gegen `PARAM_RANGES` geprüft: alle stimmen bis auf Ratio-Untergrenze (BL-6c) |
| docs/03: Ablauf Kalibrierung, Live-Betrieb, A/B-Vergleich, was die Regelung tut und wie man sie liest | ✅ | docs/03 §1–§11; §8 Regelgesetze/Schutzregeln, §9 JSONL-Lesart mit Ereignistabelle |
| docs/04 Prüfprotokoll | – | vorhanden, auftragsgemäß nicht gelesen |

### §4.1 Konfiguration (YAML-Schema)

| Anforderung | Status | Beleg |
|---|---|---|
| Schlüssel und Struktur wie §4.1 | ✅ | `config/chain.default.yaml` identische Struktur; `config.py:validate_config` strikt (unbekannte/fehlende Schlüssel, Bereiche, Bounds-Konsistenz) |
| Startwerte wie §4.1 | ◐ (begründet) | Abweichungen: `expander.hold_ms` 50 → 100, `release_ms` 150 → 250 (YAML:35-36, docs/02 §3 begründet mit Hörurteil); optional ergänzt: `normalizer.release_rate_db_per_s`, `expander.detector_ms`, `limiter.true_peak_margin_db` (Defaults im Code, docs/02 §10). Akzeptabel, da §4.1 Startwerte nennt |
| dB-Werte dBFS, Zeiten ms/s | ✅ | YAML-Kopf, docs/02 Präambel |

### §4.2 Python-API

| Signatur | Status | Beleg |
|---|---|---|
| `Chain.from_config(cfg)`, `process(x)` float32 mono blockweise mit Zustand, `set_param(stage,name,value)` threadsicher, `get_param`, `get_meters()` mit Stufenwerten + `output{peak_dbfs,rms_dbfs}`, `reset()` | ✅ | chain.py:62-150; `process` liefert float32 (`.astype(np.float32)`); RLock, Design außerhalb/Tausch innerhalb des Locks (dsp/__init__.py `StageBase.set_param`); eigene Prüfung 4.3e: Meter-Schlüssel `expander/deesser/compressor{gain_reduction_db,…}`, `normalizer{gain_db,…}`, `limiter{gain_reduction_db,…}`, `output{peak_dbfs,rms_dbfs}` |
| Stufen: `__init__(sample_rate, **params)`, `process`, `set_param(name,value)`, `get_param`, `meters()`, `reset()` | ✅ | alle acht Stufen; `set_param` hat zusätzliches optionales `lock=` (abwärtskompatibel) |
| `LoudnessMeter(sample_rate)`, `push`, `momentary_lufs` (400 ms), `short_term_lufs` (3 s), `integrated_lufs` (gated) | ✅ | meters.py:154-275; K-Bewertung reproduziert BS.1770-Tabelle (`test_dsp_biquad.py`) |
| `true_peak_dbfs(x, sr)` 4× Oversampling | ✅ | meters.py:317 (`resample_poly`, Faktor 4) |
| `rms_dbfs`, `noise_floor_dbfs(x, sr, frame_ms=100.0)` Perzentil, `speech_level_dbfs`, `sibilance_ratio_db` (5–10 kHz minus 0,3–3 kHz, aktive Rahmen), `spectral_tilt_db_per_oct`, `band_energies_db(x, sr, edges)` | ✅ | meters.py:337, 401 (10. Perzentil, Default 100 ms), 407, 523 (Bänder 5000–10000 / 300–3000, aktive Rahmen), 588, 603 |
| `calibrate.analyze(x, sr)`, `recommend(m, base_cfg)` (Kopie), `evaluate(x_in, x_out, sr, cfg)`, `run_calibration(wav, base, out, report)` | ✅ | calibrate.py:334, 432 (`copy.deepcopy`, Test `test_recommend_does_not_mutate_base_config`), 647, 815; zusätzliche optionale Keyword-Argumente |
| `AdaptiveController(cfg, chain, clock=time.monotonic, log_path=None)`, `update(meters, now=None) -> list[dict]` `{stage,name,old,new,reason}`, `freeze(on)` | ◐ | adaptive.py:300, 357, 346. Signaturen stimmen. **Abweichung:** `update()` regelt nur, wenn `meters["analysis"]` (Schnappschuss eines `LiveAnalyzer`) enthalten ist; mit reinem `chain.get_meters()` entsteht nur `no_analysis` (eigener Test (c): 15× `no_analysis`, 0 Änderungen). In docs/03 §8 dokumentiert → **BL-3** |

### §4.3 Verhaltensregeln (eigene Messung, `check_43.py`, `check_click.py`)

| Anforderung | Status | Beleg |
|---|---|---|
| Blockgrößenunabhängig, 64 vs. ein großer Block identisch bis 1e-5 | ✅ | Kette default: 64 vs 4096 → 0,0; 64 vs ein Block (6 s) → 2,3e-10; kalibriert 3,6e-12 / 4,7e-10; je Stufe ≤ 1e-15; Blöcke 1, 7, 97, 250 vs 256 → 0,0 |
| Filterzustände über Blöcke fortgeführt | ✅ | `SosFilter.process` mit `zi` (biquad.py:194-206); Invarianz oben |
| Keine großen Puffer-Allokationen pro Block, keine Python-Sample-Schleifen; 256er-Block < 1 ms | ✅ (Container) | Hüllkurven geschlossen vektorisiert (envelope.py `ReleaseDecay` Lindley-Form, `maximum_filter1d`, cumsum); Laufzeit 256er-Block: Median 0,43 ms, p99 0,61–0,93 ms, Maximum 1,9–6,9 ms (Ausreißer, Container ohne Echtzeit-Scheduling); Selbsttest 0,434 ms; Stufen: Expander 78 µs, De-Esser 74 µs, Normalizer 76 µs, Kompressor 47 µs, Limiter 43 µs. Kleine Temporäre je Block (astype, Stufenausgaben) vorhanden, keine großen Puffer |
| Stille rein → Stille raus (kein DC, kein Rauschen, keine NaN) | ✅ | 2 s Nullen → Ausgang exakt 0,0, finite, DC 0; 3 s Stille nach Sprache → letzte Sekunde Spitze −300 dBFS (numerisch 0) |
| Parameteränderungen knacken nicht (Koeffizienten neu, Zustände behalten) | ◐ | 1-kHz-Sinus −14 dBFS, Änderung mitten im Signal, 24 Parameterwechsel in 8 Stufen (Tabelle Abschnitt 4): alle 0,5-dB-Schritte (adaptiver Pfad) ≤ 0,29 dB Blocksprung, HF-Spitze ≤ −95 dBFS, Krümmungsmaß unverändert. Gain-Sprünge ≥ 3 dB (Trim, Makeup) werden über 10 ms gerampt (1,2–1,6 dB je 5,3-ms-Block, kein Transient). **Ausnahmen:** `highpass.order` 4 → 6 bzw. 4 → 2: Zustands-Reset bei Sektionszahlwechsel (biquad.py:183) → Transient −45 dBFS bei −14-dBFS-Signal (Krümmung 5× Schranke) = hörbarer Tick → **BL-4**; `limiter.lookahead_ms` harter Sprung (dokumentiert, limiter.py:76) |

### §5 Qualitätsregeln

| Anforderung | Status | Beleg |
|---|---|---|
| Keine Platzhalter, TODO, `pass`-Rümpfe, `NotImplementedError`, auskommentierte Funktionen, „später" | ✅ | grep über `voicechain/ config/ scripts/ tests/ docs/` ohne Treffer (nur englische Kommentare „fail later", „300 ms later") |
| Keine stummen `except:`; Fehler mit Kontext | ✅ | kein `except:`/`pass`; 8× `except Exception` in runtime.py, jeweils mit Weitergabe als `AudioBackendError` oder Konsolenmeldung mit `repr(exc)` (Audio-Thread darf nicht abbrechen); `cli.main` meldet Fehler ohne Traceback (geprüft: `devices`/`run`/`record` im Container) |
| Tests prüfen Zahlen | ✅ | Stichproben: HPF 50-Hz-Dämpfung, Limiter-Ceiling, Normalizer ± 1 LU, Chain-Blocksprung < 0,5 dB, Adaptive-Konvergenz in 40 Intervallen |
| Jede Konstante konfigurierbar oder im Kommentar begründet | ✅ | adaptive.py:70-140, meters.py:40-122, calibrate.py:39-153, cli.py:47-98: jede Konstante mit Begründung bzw. „Assumption" |
| Dokumentation beschreibt tatsächlichen Stand | ✅ mit Ausnahmen | alle Kommandos/Optionen/Zahlen stimmen bis auf BL-6/BL-7 |
| Code Englisch, Doku Deutsch | ✅ | Bezeichner/Kommentare englisch; Nutzermeldungen und Bericht deutsch; Code-Kommentare verweisen auf „memory/04", „PO-2", „SW-13" → BL-9 |

### §6 Abnahmekriterien (selbst ausgeführt)

| Kriterium | Status | Beleg |
|---|---|---|
| 6.1 `pytest -q` grün ohne Audiogeräte | ✅ | 302 passed, 1 skipped, 0 failed (134,8 s) |
| 6.2 `selftest` mit messbaren Prüfwerten, alle bestanden | ✅ | 12/12 „ja": SNR-Gewinn +9,5 dB, Pause −48,3 LUFS ≤ −38, Zischanteil −7,1 dB, Lautheit −18,3 LUFS, TP −1,95 dBTP, NaN-Block gezählt, 0,434 ms/Block, Vollaussteuerungs-Sinus −2,48 dBTP, Impulse −12,32 dBTP, Stille → −inf |
| 6.3 `calibrate` erzeugt YAML + Bericht; SNR-Gewinn ≥ 6 dB, Pause ≤ Ziel − 20 LU in Pause ≥ 5 s, Zischanteil ↓, Lautheit ± 2 LU, TP ≤ Ceiling | ✅ | make-sample (60 s, Pause 32–38 s): SNR +10,7 dB; Pause max −46,3 LUFS; Zischanteil −1,5 dB; −18,3 LUFS; −4,26 dBTP; Exit 0. Zweites Sample (Teppich −58): +10,9 dB, −56,2 LUFS, −1,6 dB, −18,3 LUFS, −3,16 dBTP |
| 6.4 Regelung konvergiert innerhalb der Grenzen, oszilliert nicht, greift in Lernphase nicht ein, protokolliert jede Änderung mit Grund | ✅ mit Einschränkung | eigene 240-s-Simulation (Abschnitt 4): 0 Änderungen vor 30 s; Expander −43 → −34 dBFS nach Teppichsprung +10 dB (Ziel −32 ± 2) in 82 s; 0 Grenzverletzungen; 1 Richtungswechsel (verursacht durch zwei `speech_cut`-Fehlalarme, BL-1); 20/20 Änderungen protokolliert |
| 6.5 Doku ermöglicht Einrichtung < 30 min | ✅ | README 10 Schritte + docs/01 vollständig; fehlende Kleinigkeit: Beschaffung von Python ≥ 3.11 auf macOS (BL-8) |
| 6.6 Reihenfolge wie Ausgangsbild | ✅ | chain.py:21 |

## 3. Befundtabelle

| Nr. | Schwere | Fundstelle | Befund | Empfehlung |
|---|---|---|---|---|
| BL-1 | **mittel** | `voicechain/adaptive.py:133` (`SPEECH_CUT_MIN_ACTIVE_RATIO = 0.95`), `:409-421 _accumulate`, `:505` | `speech_cut` wird als „Sprache wird abgeschnitten" gewertet, wenn das Expander-Maximum eines Intervalls > 6 dB ist und ≥ 95 % der letzten 40 Analyzer-Rahmen aktiv waren. Endet eine Pause innerhalb der ersten ≤ 100 ms des Meter-Intervalls, liegt das Maximum (noch geschlossenes Gate, 12–14 dB) im Intervall, während der Analyzer 39/40 aktive Rahmen zählt → Fehlalarm. Eigene Simulation (`sim_64b.py` (a)): 3 von 60 Intervallen erfüllen die Bedingung, Lage des Maximums 0,07–0,08 s nach Intervallbeginn; zwei Eingriffe (t = 30 s, 112 s) senken die Schwelle je 0,5 dB ohne Totzone und ohne Wartezeit. Zusätzlich sammelt `_accumulate` während der gesamten Lernphase (15 Intervalle), sodass der erste Eingriff nach der Lernphase aus einem beliebigen Lernintervall stammen kann. Netto: Sägezahn von ~0,5 dB zwischen Ziel − 2 dB und Ziel − 2,5 dB; der Expander arbeitet dauerhaft ~2 dB unter dem Kalibrierziel. Der vorhandene Test `test_adaptive.py:371` prüft nur 90 % Aktivanteil, nicht den Pausen-Ausläufer. | Maximum nur aus Blöcken werten, deren Analyzer-Rahmen aktiv **und** deren Vorlaufzeit ≥ hold + release seit letzter Inaktivität ist (z. B. Expander-Meter „GR max während `speech_active`" in der Stufe selbst akkumulieren, oder Rahmen 0–2 des Intervalls ausschließen); `_acc_expander_speech` zu Beginn der Regelphase leeren; Test mit realer Kette und Pause am Intervallanfang ergänzen. |
| BL-2 | **mittel** | `voicechain/calibrate.py:432-602`, `docs/03:116` | §3.2 verlangt Ableitung **aller** Parameter aus dem Sample. Abgeleitet: 10 Werte (Trim, Expander-Schwelle, De-Esser f/Schwelle, 4 EQ-Gains, Kompressor-Schwelle/Makeup, Normalizer-Gate/Max-Gain). Nicht abgeleitet: HPF (freq/order), alle Attack/Hold/Release, Ratios, Knie, De-Esser-Bandbreite/Max-Reduktion, Normalizer-Ziel/Fenster/Raten, Limiter. Die gemessene spektrale Neigung (§3.2 ausdrücklich genannt) und der True Peak fließen in keinen Parameter ein; f0 nur in die Low-Shelf-Entscheidung. Fachlich vertretbar und in docs/03 §4 transparent („bleiben"), aber Abweichung vom Wortlaut. | Auftraggeber-Entscheidung einholen. Naheliegende Erweiterungen mit Grenzen: `highpass.freq_hz` aus f0 (z. B. 0,7·f0, begrenzt 60–120 Hz), `limiter.ceiling`/Makeup-Headroom aus Crest-Faktor, Expander-`range_db` aus Störabstand; alternativ Beauftragung §3.2 auf „alle pegel-/klangabhängigen Parameter" präzisieren. |
| BL-3 | niedrig | `voicechain/adaptive.py:357-383 update`, `docs/00 §4.2` | `update(meters)` benötigt `meters["analysis"]` aus `LiveAnalyzer`; mit reinem `chain.get_meters()` (Vertrag §4.2) entstehen nur `no_analysis`-Ereignisse, keine Regelung (eigener Test (c): 15× `no_analysis`). Dokumentiert in docs/03 §8, nicht in §4.2 der Beauftragung nachgezogen. | Schnittstellenvertrag §4.2 um `LiveAnalyzer`/`meters["analysis"]` ergänzen oder `AdaptiveController` optional selbst einen Analyzer führen (`push()`-Methode). |
| BL-4 | niedrig | `voicechain/dsp/biquad.py:181-192 set_sos`, `dsp/highpass.py` | Wechsel von `highpass.order` (Sektionszahl ändert sich) setzt `zi` auf Null: Transient −45 dBFS bei −14-dBFS-Sinus, Krümmung 5× Schranke (hörbarer Tick). §4.3 „Zustände behalten" nicht erfüllt für diesen Parameter; nur `limiter.lookahead_ms` ist als nicht knackfrei dokumentiert (limiter.py:76). Der vorhandene Chain-Test (`test_dsp_chain.py:89`) ändert `order` nicht. Nicht Teil des adaptiven Pfads. | Bei Sektionszahlwechsel alten Filter mit altem Zustand 10 ms weiterlaufen lassen und überblenden (wie bei gleicher Sektionszahl) oder in docs/02 §2 als nicht knackfrei kennzeichnen; Test ergänzen. |
| BL-5 | niedrig | `voicechain/adaptive.py:_propose` | §3.2 nennt „Pumpen" als Rücknahmegrund; es gibt keine Erkennung (z. B. Schwankung der Kompressor-/Normalizer-Gain-Reduction im Intervall). Praktisches Risiko gering (Normalizer feedforward, 1 dB/s; Kompressor-Schwelle auf RMS-Höhe). | Entweder Regel ergänzen (z. B. Standardabweichung der Kompressor-GR je Intervall > X dB → Schwelle nicht weiter senken) oder in docs/03 §8 explizit „kein Pump-Detektor" vermerken. |
| BL-6 | niedrig | README:84, docs/01:245 · docs/03:46 vs docs/01:225/runtime.py:684 · docs/02:145,196 vs docs/02:347/`PARAM_RANGES` · docs/01:143 | Widersprüche zwischen den Dokumenten: (a) „Alle Zeilen müssen „bestanden" zeigen" – die Selbsttest-Tabelle zeigt „ja"/„NEIN" und „BESTANDEN" nur in der Ergebniszeile; (b) Aufnahmepegel „Spitzen unter −6 dBFS" (docs/03) vs. „ZU LAUT ab −3 dBFS" (README, docs/01, Code); (c) Ratio-Bereich „1–20" in den Tabellen De-Esser/Kompressor vs. Untergrenze 1,1 in docs/02 §10 und Code; (d) docs/01 Variante B „Alternativ ohne Änderung der YAML … (dann muss `input.channel` in der YAML trotzdem 2 sein)" – ist eine Änderung der YAML. | Wortlaut angleichen: „ja" bzw. „Ergebnis BESTANDEN"; einheitlich −3 dBFS (oder −6 als Empfehlung, −3 als Warnschwelle benennen); „1,1–20"; Satz in docs/01 umformulieren. |
| BL-7 | niedrig | `docs/02:9`, `docs/02:358` vs `adaptive.py:534 _normalizer_hold` | docs/02 behauptet, die Regelung verändere „ausschließlich die drei Schwellen"; tatsächlich setzt die Limiter-Schutzregel auch `normalizer.max_gain_db` (dokumentiert in docs/03 §8). `normalizer.max_gain_db` hat keinen Eintrag in `adaptive.bounds` (begrenzt auf 0…konfigurierter Wert). | docs/02 §0/§11 ergänzen; optional `normalizer.max_gain_db` in `bounds` aufnehmen. |
| BL-8 | niedrig | README:50,67 | Systemvoraussetzung „Python 3.11 oder neuer", Schnellstart nutzt `python3 -m venv` ohne Hinweis, woher ein 3.11+ auf macOS kommt (Bordmittel ist häufig älter). Die Shell-Skripte prüfen und nennen `brew install python@3.12`, der README-Weg nicht. | Schritt 2: `brew install python@3.12` bzw. Hinweis auf `scripts/run_live.sh`/`scripts/calibrate.sh`, die venv und Abhängigkeiten selbst anlegen. |
| BL-9 | niedrig | `calibrate.py:39,45,64,73`, `runtime.py:11`, `adaptive.py:29,118`, Kommentare „memory/04", „PO-2", „SW-13", „docs/04 SW-13", „BL-10", „M12" | Code-Kommentare verweisen auf interne Prüf-/Gedächtnisartefakte, die für Leser des Pakets ohne Projektkontext nichts bedeuten. | Verweise auf docs/02/docs/03-Abschnitte umstellen oder die fachliche Begründung direkt im Kommentar nennen. |
| BL-10 | niedrig (Hinweis) | `check_43.py` 4.3d | Laufzeit je 256er-Block im Container: Median 0,43 ms, p99 bis 0,93 ms, Einzelmaxima 1,9–6,9 ms (GC/Scheduler). Budget < 1 ms ist im Median klar erfüllt; Python ist nicht echtzeitfest, Ausreißer werden durch PortAudio-Puffer (`latency="low"`) abgefangen, Xruns werden gezählt. Auf dem Zielsystem nicht gemessen. | Auf dem Mac M-Chip `xruns` über eine Seminarlänge protokollieren; ggf. `gc.freeze()`/`gc.disable()` im Audio-Betrieb erwägen und dokumentieren. |
| BL-11 | niedrig | Arbeitsverzeichnis: `s.wav` (5,7 MB), `logs/adaptive.jsonl` (leer), `.pytest_cache`, `__pycache__` | Nicht versionierte Artefakte im Arbeitsbaum (alle per `.gitignore` ausgeschlossen, nicht committet). Kein Abnahmehindernis. | Vor Übergabe `git clean -ndX` prüfen. |

## 4. Kurzausgaben der eigenen Skripte

### 4.1 Abnahmekommandos (§6)

```
pytest -q                 302 passed, 1 skipped in 134.79s   (SKIPPED: root ignores file permissions)
selftest                  Ergebnis: 12/12 bestanden -> BESTANDEN, exit 0
make-sample --out …/sample.wav              exit 0 (60 s, 48 kHz, 16 bit)
calibrate --wav … --out … --report …        acceptance: passed, exit 0
   noise_floor_reduction  +10.7 dB | pause_loudness -46.3 LUFS (≤ -38) | sibilance -1.5 dB |
   loudness -18.3 LUFS | true peak -4.26 dBTP
process --in … --out … --config <kalibriert>   before rms -28.6 / after -20.9 dBFS; SNR 22.1 → 32.8 dB;
   integrated -26.1 → -18.3 LUFS; 0.438 ms per 256-block, exit 0
devices / run / record (Container)          "error: PortAudio is not available: PortAudio library not found.
   On macOS install it with 'brew install portaudio' …" exit 2, kein Traceback
```

### 4.2 §4.3 (`check_43.py`, `check_click.py`)

```
Blockgroessen-Invarianz: chain default 64 vs 4096 = 0.00e+00; 64 vs ein Block = 2.33e-10;
   calibrated 3.64e-12 / 4.66e-10; je Stufe <= 1e-15; Bloecke 1/7/97/250 vs 256 = 0.0
Stille -> Stille: Ausgang exakt 0.0, finite, DC 0; nach Sprache letzte 1 s -300 dBFS
Laufzeit 256er-Block: median 0.429/0.433 ms, p99 0.613/0.925 ms, max 1.88/6.92 ms
   (Blockdauer 5.33 ms); Stufen us: trim 7, HPF 19, exp 78, dees 74, eq 19, comp 47, norm 76, lim 43

set_param bei 1-kHz-Sinus -14 dBFS (max Blocksprung dB / HF>12k Spitze dBFS / Krümmung vs Schranke):
  Referenz ohne Aenderung          0.195 / -145.2 / 0.68
  input_trim.gain_db   0->0.5      0.287 / -110.0 / 0.71      0->3.0   1.157 / -93.1 / 0.88 (10-ms-Rampe)
  highpass.freq_hz     80->120     0.195 / -109.2 / 0.68
  highpass.order       4->6        0.205 /  -45.4 / 5.04   <-- Zustands-Reset, Tick (BL-4)
  highpass.order       4->2          —   /  -45.3 / 4.62   <-- dito
  expander.threshold   -45->-44.5  0.195 / -145.2 / 0.68    -45->-30  0.195 / -145.2 / 0.68
  deesser.threshold    -30->-29.5  0.195 /    —   /  —      -30->-45  0.245 / -95.4 / 0.68
  deesser.freq_hz      6500->7000  0.195 / -145.2 / 0.68
  eq.bands.1.gain_db   2->4        0.215 / -109.7 / 0.69    eq.bands.0.freq_hz 220->300  0.219 / -118.9 / 0.68
  compressor.threshold -20->-20.5  0.275 / -121.5 / 0.68    -20->-26  1.152 / -101.5 / 0.68 (Release-Glättung)
  compressor.makeup    0->3        1.607 /  -94.7 / 0.96 (10-ms-Rampe)   ratio 3->5  0.387 / -118.2 / 0.68
  compressor.attack    10->30      0.196 /  -93.2 / 0.68
  normalizer.max_gain  12->2       0.211 / -145.4 / 0.68    target -18->-24  0.211 / -145.4 / 0.68
  limiter.ceiling      -1->-3      0.195 / -145.2 / 0.68    -1->-12  0.195 / -145.2 / 0.68
  limiter.release      50->200     0.195 /    —   /  —
  limiter.lookahead    5->8       281.99 /  -29.3 / 30.6   <-- harter Sprung (dokumentiert)
```

### 4.3 §6.4 Simulation (`sim_64.py`, 240 s, Kette + LiveAnalyzer + AdaptiveController, kalibrierte YAML mit Trim +6,07 dB)

Szenario: Sprache −26 dBFS RMS, Teppich −58 dBFS (0–120 s) → −48 dBFS (120–240 s), 180–240 s nur Tastaturklicks (3/s, −30 dBFS); Intervall 2 s, Lernphase 30 s; Freeze per `freeze(True/False)` 90–110 s.

```
t     exp     des     comp   nmax | floor  speech  ratio dyn | expGRmax  chg
 10  -42.00  -16.31  -20.00  12.0 | -52.0  -19.8   0.71  34  |  13.3     0   (Lernphase)
 30  -42.50  -16.31  -20.00  12.0 | -52.0  -19.8   0.72  34  |  14.1     1   speech_cut (Fehlalarm, BL-1)
112  -43.00  -16.31  -20.00  12.0 | -52.0  -20.0   0.69  34  |  12.4     1   speech_cut (Fehlalarm, BL-1)
134  -42.50  … 202  -34.00   12.0 | -42.1  -20.0   0.72  24  |   0.3…8.7 18  noise_floor_track, je 0,5 dB alle 4 s
192 ff.  implausible_speech (speech_ratio 0.28 < 0.3; ab 200 s zusätzlich level -32 dBFS, dynamic_range 10.5 dB)
240  -34.00  -16.31  -20.00  12.0 | -42.1  -32.1   0.15  11  |   8.6     0

Log: 62 Zeilen, 20 Aenderungen (18 noise_floor_track, 2 speech_cut), 42 Ereignisse
     (25 implausible_speech, 14 learning, 2 freeze, 1 learn_done); 0 ohne Pflichtfelder;
     update()-Rueckgaben == Log; 0 Grenz-/Schrittverletzungen; erste Aenderung t=30.0
Erwartetes Expander-Ziel: -41.9 (0-120 s), -31.9 (120-240 s); Endwert -34.0 (Totzone 2 dB) nach 82 s
De-Esser/Kompressor: 0 Schritte (im Ziel bzw. implausibel gehalten); Normalizer-Gain 0.8-1.6 dB; Limiter-GR 0
Freeze 90-110 s: 0 angewandte Aenderungen (in der Ruhephase keine Vorschlaege)
```

`sim_64b.py`: (b) Freeze 70–90 s während aktiver Nachführung → 9 `frozen`-Ereignisse mit `proposed`, 0 angewandte Änderungen, Nachführung nach Freigabe fortgesetzt. (c) `update(chain.get_meters())` ohne Analyzer → nur `no_analysis` (BL-3). (a) `speech_cut`-Diagnose: Maxima liegen 0,07–0,08 s nach Intervallbeginn, `recent_speech_ratio` 0,975 (BL-1).

## 5. Doku-Lesung (Person ohne Audio-Vorwissen)

Geprüft: jedes Kommando und jede Option in README, docs/01, docs/03 gegen `python -m voicechain <kommando> --help` – alle vorhanden und korrekt benannt (`record --out --seconds --input-device --config`, `run --config --input-device --output-device --no-adaptive --freeze`, `calibrate --wav --config --out --report`, `process --in --out --config`, `selftest --config --report`, `make-sample --out`). Standardpfade `config/chain.calibrated.yaml`, `reports/calibration_report.md`, `logs/adaptive.jsonl` stimmen mit `cli.py:44-45` und YAML überein. Zahlen in docs/02 (alle Bereiche je Parameter, 240 Samples Lookahead, 10-ms-Raster, τ 0,2 s, 16,4 dB bei 50 Hz, 400–450 µs/Block) gegen `PARAM_RANGES`/Code geprüft: stimmen, Ausnahme Ratio-Untergrenze (BL-6c). Zahlen in docs/03 (30–120 s, Minimum 10 s, Pause ≥ 5 s, 0,5 s Settle, +10/−12 dB, 5–9 kHz, Overshoot 4/(1−1/R), ±4 dB/50 %/±1 dB/150 Hz, Gate +8/−40/Ziel−15, Max-Gain Ziel−(Pause+6), Historie 20 s, ready 2 s, ±6 dB/30 %/15 dB, 60 s rest, 3/1,5/6 dB, 95 %, 2 Intervalle) stimmen mit den Konstanten überein; die Konvergenzabschätzung (75–90 s) deckt sich mit meiner Messung (82 s). `make-sample`-Beschreibung (10 s Stille, Pause 32–38 s, −26/−48 dBFS) stimmt mit `synth.write_sample_wav`.

Fehlende Schritte für die 30-Minuten-Einrichtung: nur BL-8 (Python-Version beschaffen). Alles andere – BlackHole, PortAudio, Abmelden/Anmelden, Audio-MIDI-Setup 48 kHz, Mikrofonrechte für das Terminal, Zoom/Teams-Schalter, Testanruf, Rückstellung nach dem Seminar – ist in nachvollziehbarer Reihenfolge beschrieben; der Hinweis, dass Menüpfade nicht gegen aktuelle Versionen geprüft wurden, ist ehrlich. Widersprüche zwischen den vier Dokumenten: BL-6 (a–d), BL-7.

## 6. Reihenfolge der Kette

`voicechain/dsp/chain.py:21` und `voicechain/dsp/__init__.py` (Modul-Docstring und Importreihenfolge) legen `input_trim → highpass → expander → deesser → eq → compressor → normalizer → limiter` fest; `Chain.from_config` baut und `Chain.process` durchläuft genau diese Reihenfolge; `test_from_config_and_stage_order` sichert sie. Entspricht §2 (0, 1, 2, 3, 4, 5, 5b, 6).
