# Fehlerlog (Lernprozess)

| Nr | Datum | Fehler / Befund | Ursache | Behebung | Status |
|----|-------|-----------------|---------|----------|--------|
| F1 | 2026-11-02 | `sounddevice` im Container nicht importierbar (PortAudio fehlt) | Keine Audiobibliothek in der Cloud-Umgebung | Lazy Import nur in `runtime.py`; DSP, Meter, Kalibrierung, Adaptive geräteunabhängig | gelöst durch Architektur |
