# Fehlerlog (Lernprozess)

| Nr | Datum | Fehler / Befund | Ursache | Behebung | Status |
|----|-------|-----------------|---------|----------|--------|
| F1 | 2026-11-02 | `sounddevice` im Container nicht importierbar (PortAudio fehlt) | Keine Audiobibliothek in der Cloud-Umgebung | Lazy Import nur in `runtime.py`; DSP, Meter, Kalibrierung, Adaptive geräteunabhängig | gelöst durch Architektur |
| F2 | 2026-11-02 | Validator lehnte `_calibration`-Schlüssel der kalibrierten YAML ab; `run`/`process` mit kalibrierter Config scheiterten | Schnittstelle zwischen Kalibrierung und Config nicht im Vertrag fixiert | Validator toleriert Top-Level-Schlüssel mit Unterstrich, Round-Trip-Test | gelöst |
| F3 | 2026-11-02 | Selftest: Störabstandsgewinn 5,8 dB knapp unter Kriterium 6 dB; absolutes Rauschkriterium falsch, weil Normalizer leise Signale insgesamt anhebt | Kompressor (Schwelle = Sprach-RMS) frisst ~6 dB des Expander-Gewinns (~12 dB); Kriterium muss relativ (SNR) bewertet werden | Kriterium relativ bewertet (calibrate.evaluate); Expander-Abstand 8 → 10 dB (Entscheidung PL), Selftest +8,7 dB, 227 Tests grün | gelöst |
