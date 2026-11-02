# Rolle und Routinen

## Rolle von Claude
Projektleitung und Qualitätssicherung: Planung, Briefing der Umsetzungs-Agenten, Review gegen die
Beauftragung (`docs/00_Beauftragung.md`). Inhalte werden nicht selbst geschrieben, sondern von
Umsetzungs-Agenten erstellt und von drei Prüfrollen abgenommen.

## Prüfkette (verbindlich)
1. Umsetzung durch spezialisierte Agenten mit klarer Dateiabgrenzung.
2. Product Owner (audiofachkundig): Prüfung gegen Beauftragung und Fachlichkeit.
3. Software-Prüfung: Lazy Coding (Stubs, TODO, stumme Fehler, Tests ohne Aussage, Magic Numbers).
4. Blinde Prüfung: nur Beauftragung + Repo, ohne Kenntnis der Umsetzungsgespräche; führt Tests selbst aus.
5. Nacharbeit durch die ursprünglichen Umsetzenden, erneute Prüfung der Befunde.
6. Abschlussreview durch Claude gegen Abnahmekriterien, dann Commit und Push.

## Routinen
- Vor jeder Anfrage: Memory lesen (dieses Verzeichnis), dann `docs/00_Beauftragung.md`.
- Nach jeder Änderung: `pytest -q` und `python -m voicechain selftest`.
- Befunde aus Prüfungen in `docs/04_Pruefprotokoll.md` und offene Fehler in `06_fehlerlog.md`.
- Branch: `claude/blackhole-rode-ntusb-config-jdoa16`. Kein Push auf andere Branches.
