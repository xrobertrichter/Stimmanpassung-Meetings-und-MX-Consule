# Interne Quellen (Repo-Struktur)

Repo: `/home/user/Test`, Branch `claude/blackhole-rode-ntusb-config-jdoa16`

| Pfad | Inhalt | Verantwortlich |
|------|--------|----------------|
| docs/00_Beauftragung.md | Verbindliche Beauftragung, Schnittstellenvertrag, Abnahmekriterien | Claude (PL) |
| docs/01_macOS_Setup.md | Einrichtung Mac, BlackHole, Zoom/Teams | Agent Integration/Doku |
| docs/02_Parameter_Begruendung.md | Jeder Parameter mit Begründung | Agent DSP |
| docs/03_Betrieb_und_Kalibrierung.md | Kalibrierung, Live, adaptive Regelung | Agent Kalibrierung |
| docs/04_Pruefprotokoll.md | Prüfergebnisse PO / Software / Blind | Claude (PL) |
| config/chain.default.yaml | Startkonfiguration, kommentiert | Agent DSP |
| voicechain/dsp/*.py | Stufen 0–6 und Chain | Agent DSP |
| voicechain/meters.py | Lautheit, True Peak, Rauschteppich, Zischanteil | Agent Kalibrierung |
| voicechain/calibrate.py | Sample-Analyse → Empfehlung → Bericht | Agent Kalibrierung |
| voicechain/adaptive.py | Nachregelung im Live-Betrieb | Agent Kalibrierung |
| voicechain/runtime.py, cli.py, __main__.py | Live-Stream, CLI | Agent Integration/Doku |
| tests/ | pytest, ohne Audiogeräte lauffähig | je Agent für eigene Module |
| voicechain/synth.py | Synthetische Sprach-/Klick-Signale für Tests, Selftest, make-sample | Agent Kalibrierung |
| docs/pruefungen/ | Vollständige Prüfberichte Runde 1 (01–03) und Runde 2 (04–06), Runde 3 PO (07) | Prüfrollen |
| scripts/run_live.sh, calibrate.sh | venv anlegen, Python ≥ 3.11 prüfen, CLI starten | Agent Integration |
| memory/ | Projekt-Memory (dieses Verzeichnis) | Claude (PL) |

CLI-Kommandos (`python -m voicechain`): devices, run, record, calibrate, process, selftest, make-sample.
Konfigurationsquelle ist ausschließlich `config/chain.default.yaml`; kalibrierte Konfiguration
standardmäßig `config/chain.calibrated.yaml`, Bericht `reports/calibration_report.md`,
Regelungslog `logs/adaptive.jsonl`.
