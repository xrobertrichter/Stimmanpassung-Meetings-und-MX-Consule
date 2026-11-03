# Prüfung Product Owner (audiofachkundig) – Runde 3 (Abschlussprüfung): Nacharbeit zu PO-15 … PO-22

Stand: 2026-11-03 · Commit d277ce7 (HEAD; davor 20baefc Kalibrierung/Regelung, adc6d5d Integration) · geprüft gegen
`docs/00_Beauftragung.md` (§6.3, §6.4 neu), `docs/04_Pruefprotokoll.md` („Entscheidungen Projektleitung für Runde 3")
und den eigenen Runde-2-Bericht `docs/pruefungen/04_product_owner_runde2.md`.
Der Prüfer hat nur gelesen, ausgeführt und simuliert; keine Datei im Repo verändert. Skripte und Rohausgaben liegen unter
`<pruefordner>/r3_po/`: `r3common.py` (Systemsimulation Chain + LiveAnalyzer + AdaptiveController, Takt 2 s wie
`runtime.adaptive_tick`), `sim_r3_po15.py` (+ `_pink`), `sim_r3_po16.py`, `sim_r3_po19.py`, `sim_r3_po18_20_21.py`,
`sim_r3_po20b.py`, `sim_r3_po20c.py`, `sim_r3_sw17.py`; Ausgaben `out_*.txt`, `out/` (WAV, YAML, Berichte, JSONL-Logs).

Ausgeführt: `python -m voicechain selftest` → **12/12 bestanden, Exit 0** (Störabstandsgewinn +7,8 dB, Pausenlautheit
−46,6 LUFS, −18,2 LUFS, −1,67 dBTP, 0,432 ms je 256er-Block; `selftest.txt`). `pytest -q` → **325 bestanden, 1 übersprungen**
(199 s; `pytest.txt`).

Alle Simulationen laufen auf **Systemebene**: kalibrierte Kette (`run_calibration` auf ein 60-s-Sample mit 10 s Vorlauf,
6-s-Pause, Teppich −50 dBFS, Sprache −26 dBFS → Trim +6, Expander −34, Kompressor −20/+6,5, De-Esser −16,5 @ 6,8 kHz,
Gate −40, max_gain 12), `LiveAnalyzer` am rohen Eingang, `AdaptiveController` mit 2-s-Takt und 30 s Lernphase.

## Kurzurteil

**Nicht abnehmbar** im Stand d277ce7 – wegen eines einzigen, aber zentralen Punkts: Die in Runde 3 eigens in die
Beauftragung aufgenommene Anforderung §6.4 („Teppich 8–12 dB lauter → Pause ≤ Sprache − 15 LU, nächster Satz ≤ +1,5 dB")
ist bei **+12 dB nicht erfüllt**, und zwar mit demselben Hörbild wie der ursprüngliche PO-1/PO-15-Befund: Die 20-s-Pause
liegt **auf Sprachlautheit** (±0,1 LU), der Normalizer-Gain läuft in der Pause um **+2,4 … +6,7 dB** hoch, der nächste Satz
setzt **+2,1 … +5,8 dB** zu laut ein – mit und ohne Regelung, mit weißem wie mit rosa Lüfterrauschen, vor und nach der
Expander-Nachführung (PO-23). Bei **+8 dB** ist der Kern des Befunds dagegen behoben: Der Gain friert in beiden Pausen
exakt ein (0,00 dB, in Runde 2 noch 1,3 → 12,0 dB), der Einsatz bleibt ≤ +1,2 dB; der Abstand Pause/Sprache erreicht mit
weißem Rauschen −9,2/−12,2 LU und mit rosa Rauschen −14,2/−16,5 LU, also nur im realistischeren Fall und nur nach
Nachführung die geforderten 15 LU.

Erschwerend: Der gelieferte Nachweis (`tests/test_dsp_chain.py::test_room_gets_louder_after_calibration`, Zahlen in
docs/02 §7: „−42 dBFS → 18,8 LU, −38 dBFS → 15,0 LU") setzt die Expander-Schwelle auf Teppich + 16 dB (−26 bzw. −22 dBFS).
Diese Schwelle setzt der Controller **nie**: Seine Zielfunktion kappt bei Sprache − 12 = −32 dBFS, die Bounds bei −30.
Mit der wirklich erreichten Schwelle (−31,5) steht der Teppich bei +8 dB nur 4,5 dB und bei +12 dB 0,5 dB unter der
Schwelle – statische Absenkung 4–6 dB bzw. ~1 dB statt der 15 dB, von denen Test und Doku ausgehen.

Alles Übrige ist in Ordnung oder zweitrangig: PO-16 (De-Esser bei unzischender Stimme) ist sauber behoben (0 % aktive
Rahmen > 1 dB, in Runde 2 100 %), PO-17/18/19/21/22 sind behoben, PO-20 teilweise. Die Aufwärtsrate 1 dB/s soll bleiben
(Begründung unten). **Weg zur Abnahme:** PO-23 ist lokal behebbar (Normalizer: bei Teppich-Schätzer näher als 15 LU an der
Referenz den Gain einfrieren statt den Schätzer auf „Sprache − 15" zu klemmen; Test auf die reale Controller-Schwelle
umstellen; docs/02 §7 / docs/03 §8 korrigieren). Alternativ kann die Projektleitung §6.4 auf +8 dB einengen und +12 dB als
dokumentierte Grenze führen – dann ist das Projekt **abnehmbar mit Auflagen** (PO-24, PO-25, Doku-Punkte).

## Statustabelle PO-15 … PO-22

| Nr | Schwere (R2) | Status | Messzahlen / Beleg |
|---|---|---|---|
| PO-15 | hoch | **teilweise behoben** (+8 dB ja, +12 dB nein) | `sim_r3_po15.py`, `out_po15.txt`, `out_po15_pink.txt`; Szenario: kalibriert −50, 240 s, Pausen 60–80 / 180–200 s, Teppich ab 40 s. **−42 (+8 dB), Regelung an:** Pause 1 (vor Nachführung, Expander −33,5): Pause −27,9 LUFS = **−9,2 LU** unter Sprache (Soll ≤ −15; Eingang −12,7 LU), Gain 1,27 → 1,27 (**0,00 dB**), Einsatz **+0,40 dB**; Pause 2 (Expander −31,5): **−12,2 LU**, Gain 0,00, Einsatz **+1,13 dB**. Rosa Rauschen: **−14,2 / −16,5 LU**, Gain 0,00/0,00, Einsatz +0,25/+1,16. Ohne Regelung: −8,3/−8,2 LU, Gain 0,00, Einsatz +0,5/+1,2 → der interne Teppich-Schätzer friert den Gain auch ohne Regelung ein (R2: 1,25 → 12,0). Nachführung: erster Schritt 18 s nach Lüfterstart (Ziel −32 ± 2 damit erreicht), Endwert −31,5 bei 84 s. **−38 (+12 dB), Regelung an:** Pause 1 **+0,0 LU** (Pause = Sprache, −18,1 LUFS), Gain **2,62 → 5,25 dB**, Einsatz **+2,12 dB**; Pause 2 **+0,1 LU**, Gain **2,25 → 5,26**, Einsatz **+3,08 dB**; rosa: Gain **+6,15 / +6,66**, Einsatz **+4,35 / +5,77 dB**; ohne Regelung gleich. Kontrolle −50: −28,9 LU, Gain 0,00, Einsatz +0,6/+1,2. Mechanismus und Nachweislücke: PO-23. |
| PO-16 | mittel | **behoben** | `sim_r3_po16.py`, `out_po16.txt`. `sibilance_db=-12` (gemessen −19,4 dB): Schwelle **−18,2** (Bandspitze −20,2 + 2), Reduktion in Zischrahmen Median **0,0 dB**, aktive Rahmen > 1 dB **0 %** (Soll < 20 %; R2: 100 %), Zischkriterium **bestanden** (+2,5 dB, Regel „bleibt unter Referenz"), Begründung im Bericht nennt die Formel; Systemlauf 180 s mit Regelung: 0 De-Esser-Schritte, Schwelle bleibt −18,17, danach weiterhin 0 %. `sibilance_db=+3` (gemessen −4,7 dB): Schwelle **−11,5** (innerhalb der neuen Grenze −10, nicht geklemmt), Zischrahmen Median **3,5 dB**, p10 2,8, p90 4,3, **0 % am Deckel** (8 dB), 20 % in [4, 7]; aktive Rahmen > 1 dB 23 %; Systemlauf 0 Schritte. Median 3,5 statt ≥ 4: dieselbe Definitionsfrage wie R2/PO-4 (Regel zielt auf das p95, meine „Zischrahmen" sind p95 − 3 dB) – akzeptiert. Neuer Nebenbefund bei Grenzstimme: PO-25. |
| PO-17 | niedrig | **behoben** | `config/chain.default.yaml:92` Grenze −10; `sibilance_db=+3` → −11,5 ungeklemmt. Das Selftest-Signal (+6 dB) landet mit Ziel ≈ −9,4 weiterhin 0,6 dB an der Grenze – ohne Folgen (0 % am Deckel bei +3). |
| PO-18 | niedrig | **behoben** (Mechanik), Wirkung weiter ungeprüft | `out_po18_20_21.txt`: sechs Kalibrierungen (Zischanteil −1,7 … −19,4, hohe Stimme, Lüfter −42) → High-Shelf **0,0 dB** in allen Fällen (R2: −4,0 in allen), Zischrahmen ausgeschlossen 15–16 % (0 % bei unzischender Stimme), Anteil steht im Bericht; Grenze −3 dB (`calibrate.py:108`). Aber: Ohne Zischrahmen liest das Band 7–14 kHz beim Synth jetzt ~7 dB **zu dunkel** (der Synth hat keine stimmhafte Energie > 5 kHz) → Korrektur wäre positiv → cut-only → 0. Der Absenkpfad (−3 dB) wird von keinem Testsignal ausgelöst; Hörvergleich mit echter Aufnahme steht weiter aus (Restrisiko). |
| PO-19 | niedrig | **behoben** | `sim_r3_po19.py`: 300 s normale Sprache (pause_ratio 0,3, zwei Seeds): **0 speech_cut**, 0 weitere Schritte, Expander bleibt −34,04, Lautheit −18,0 LUFS; 18 von 150 Intervallen vollständig Sprache, davon 1 mit GR-Max > 6 dB (kein Doppel → kein Auslösen); dichter Vortrag (pause_ratio 0,1): 0 speech_cut; 6 × 20,07-s-Pausen mit Ende 70 ms nach Intervallbeginn: 0 speech_cut. Kein Sägezahn mehr (R2: 3 Schritte in 40 s, danach Paare alle 1–2 min). Nebenbeobachtung dichter Vortrag: PO-26. |
| PO-20 | niedrig | **teilweise behoben** | `sim_r3_po20b.py`/`po20c.py`: kalibriert −50, Sprecher −34 dBFS roh (−28 n. Trim). Raum −50 (Dynamik 18,2 dB, Grund nur `level`): Kappe greift, Expander −34 → **−38,5** (Kappe −39,9 + Totzone) ✔. Raum −46 (Dynamik **14,3 dB < 15** → Grund `dynamic_range` → `_speech_present()` False → Kappe entfällt): Expander **−34 → −31,5** (Kappe −39,7) ✘ – exakt das Beispiel, das docs/03 §8 als geschützt beschreibt („−28 dBFS in einem −46-dBFS-Raum → höchstens −40, nicht −36"). Schaden mit Synth-Sprache nicht messbar (GR in aktiven Rahmen eingeschwungen: > 3 dB 1,2 %, wie bei normalem Sprecher 1,3 %), weil der Synth keine leisen Konsonanten hat. Siehe PO-24. |
| PO-21 | niedrig | **behoben** | `calibrate.py:901-905` erster Durchlauf mit `normalizer.enabled: false`. Teppich −38: Pause 1. Durchlauf −24,1 LUFS, 2. Durchlauf −24,0 (R2: −21,1 mit AGC-Artefakt), max_gain 0,1; −42: −31,3/−30,5, max_gain 7,3; −50: −48,2/−47,2, max_gain 12. Nebeneffekt bleibt: bei −38 Lautheit −20,2 LUFS (−2,2 LU, Kriterium verfehlt) – der Bericht sagt für diesen Raum ohnehin „nicht bestanden" (Störabstandsgewinn −4,1 dB, Pause +10,9 dB). Kosmetisch, akzeptiert. |
| PO-22 | niedrig | **behoben** | docs/01:276 nennt nur noch die Mikrofonberechtigung (kein „Treiber erlauben" mehr); docs/03:185-186 (§7) und :349 (§10) beschreiben `kill -USR1 <pid>`; README:122,139. |

Ebenfalls geprüft, ohne Befund: Rückkehr nach Lüfterstart ohne Pausenprobleme im ruhigen Raum (−50-Kontrolllauf, 0 Schritte);
`sibilance_rest`/`sibilance_track` bewegen eine kalibrierte De-Esser-Schwelle im Dauerbetrieb nicht (0 Schritte in 2 × 180 s);
NaN-Erholung und Budget-Test über `pytest`/Selftest.

## Neue Befunde (PO-23 ff.)

| Nr | Schwere | Fundstelle | Befund | Empfehlung |
|---|---|---|---|---|
| PO-23 | **hoch** | `voicechain/dsp/normalizer.py:208-209` (`_floor_db = min(floor, speech − 15)`), `:214-246` (Gate und `gain_cap` aus `_floor_db`), `:231-233` (Referenz-Abklingen); `tests/test_dsp_chain.py:333` (Expander = Teppich + 16; ebenso `:191`); `docs/02:262-275` (§7 „Grenze des Verfahrens"), `docs/03:261-268` (§8 „Raumänderung im Betrieb") | **§6.4 bei +12 dB nicht erfüllt; der PL-beschlossene Gain-Deckel ist genau dann unwirksam, wenn er gebraucht wird.** Messung (−38, Regelung an, beide Pausen): Teppich-Schätzer roh −22,7 … −23,5 LUFS, Referenz L_s −20,4 → `_floor_db` wird auf L_s − 15 ≈ −35 … −38 geklemmt → wirksames Gate −27,4 (Pause bei −18 … −25 LUFS liegt **darüber**), Deckel = −18 − 15 − (−38) = **+5,2 dB** statt −18 − 15 − (−23) = −10 dB. Die Pause passiert beide Gates, wird als Sprache gelernt, L_s sinkt auf −23,2, der Gain steigt mit 1 dB/s bis zum Deckel (5,25 dB; rosa Rauschen 8,7 dB), die Pause landet auf −18 LUFS = Sprachlautheit, der nächste Satz setzt +2,1 … +5,8 dB zu laut ein – das Hörbild von PO-1. Die Klemmung „L_T nie näher als 15 LU an L_s" (docs/02 §7 Punkt 1) deutet einen zu nahen Teppich in einen tieferen um, statt ihn als „nicht gatebar" zu behandeln. Der Nachweis in Test und Doku ist nicht systemtreu: Der Test setzt `expander.threshold_db = Teppich + 6 + 10` (−26 / −22 dBFS); der Controller liefert min(Teppich + 10, Sprache − 12) ∧ Bounds ≤ −30 = **−31,5**. Mit dieser Schwelle liegt der Teppich nach Trim (−36 / −32) nur 4,5 / 0,5 dB unter der Schwelle → statische Absenkung 4–6 / ~1 dB; die in docs/02 genannten 18,8 / 15,0 LU setzen 15 dB voraus. Auch bei +8 dB bleibt daher nach Nachführung nur −12,2 LU (weiß) bzw. −16,5 LU (rosa). Physikalisch: Mit Kalibrierraum-SNR 24 dB (−26/−50) und der Sprach-Kappe Sprache − 12 ist bei +12 dB (SNR 12 dB) kein Abstand von 15 LU erreichbar, ohne Sprache abzuschneiden – docs/03 §11 nennt 22 dB selbst als Untergrenze. | (1) Normalizer: Wenn `floor_raw > L_s − 15` (Teppich nicht gatebar), Gain **einfrieren** (Ziel ≤ aktueller Gain) und keine Intervalle unterhalb `floor_raw + gate_margin_lu` als Sprache lernen bzw. die Referenz nicht darauf abklingen lassen; die Klemmung nur für das Gate, nicht für den Deckel verwenden. Erwartung: Pause bleibt beim rohen Abstand (−8 LU bei +12 dB), Gain 0,00, Einsatz ≤ +0,5 dB – §6.4 „−15 LU" wird bei +12 dB trotzdem nicht erreicht, aber der aktive Schaden (Hochziehen, lauter Einsatz) entfällt. (2) Test `test_room_gets_louder_after_calibration` mit der Schwelle des Controllers (−32 bzw. −31,5) und mit rosa Rauschen fahren; Soll für −38 ehrlich anpassen. (3) Projektleitung: §6.4 entweder auf **+8 dB** eingrenzen (dort ist −15 LU mit realistischem Lüfterrauschen nach Nachführung erreichbar) oder den Kalibrierraum mit SNR ≥ 30 dB als Voraussetzung nennen. (4) docs/02 §7 und docs/03 §8 korrigieren (siehe PO-27). |
| PO-24 | mittel | `voicechain/adaptive.py:491-495` (`_speech_present`: `dynamic_range` zählt als „keine Sprache"), `:115` (`PLAUSIBLE_MIN_DYNAMIC_RANGE_DB` 15); `docs/03:237-241` | **Sprach-Kappe des Expanders entfällt beim leisen Sprecher im leicht lauteren Raum.** −34 dBFS roh in −46-Raum (SNR 12 dB, Dynamik 14,3 dB): 111 von 112 Intervallen `dynamic_range` → Kappe aus → Expander −34 → **−31,5** (Kappe wäre −39,7). Dasselbe Signal im −50-Raum (Dynamik 18,2): Kappe greift (−38,5). Die PL-Entscheidung „Kappe bleibt aktiv, solange überhaupt Sprache erkannt ist" ist mit dem Dynamikmaß umgesetzt, das aber bei leisem Sprecher + Teppich systematisch unter 15 dB fällt; „überhaupt Sprache" wäre `speech_detected` (Abstand ≥ 6 dB). Das Doku-Beispiel in docs/03 §8 ist damit widerlegt. Mit Synth-Sprache kein messbarer Schaden (1,2 % der aktiven Rahmen > 3 dB), echte Sprache hat Konsonanten 15–20 dB unter dem RMS, die bei Schwelle 3,5 dB unter Sprach-RMS abgeschnitten würden. | `_speech_present` auf `no_speech`/`speech_ratio` beschränken (Dynamik < 15 bei erkannter Sprache ist der leise Sprecher, nicht Tippen) oder bei `dynamic_range` als einzigem Zusatzgrund die Kappe mit dem gemessenen Pegel beibehalten; docs/03-Beispiel nachmessen. |
| PO-25 | mittel | `voicechain/calibrate.py:748-757` (Kriterium `sibilance_reduced`: bei `deesser_needed` strikt `sib_delta < 0`), `:60` (Referenz −15), `evaluate` | **Kalibrierung „nicht bestanden" bei einer unauffälligen Grenzstimme.** `sibilance_db=-6` → gemessener Zischanteil **−13,6 dB** (1,4 dB über der Referenz) → De-Esser engagiert (−19,4, Zischrahmen 3,5 dB Reduktion, 23 % aktive Rahmen > 1 dB) → Zischanteil nach Kette −13,5 → **+0,1 dB → Kriterium verfehlt → `passed=False`**. Ursache: Kompressor/Normalizer heben das 5–10-kHz-Band relativ an (bei ruhendem De-Esser gemessen +2,5 … +6,2 dB), der De-Esser holt bei Grenzstimmen gerade das zurück. Nutzende sehen für eine normale Stimme „nicht bestanden"; die YAML ist dennoch brauchbar. | Für engagierte Stimmen mit Zischanteil ≤ Referenz + 3 dB auch „nachher ≤ Referenz" als bestanden werten (Hysterese), oder die Referenz der Engagement-Entscheidung um 2 dB anheben; im Bericht die HF-Anhebung durch Kompressor/Normalizer als Erklärung nennen. |
| PO-26 | niedrig | `voicechain/adaptive.py` (`LiveAnalyzer.snapshot`: `noise_floor_dbfs` = 10. Perzentil der Rahmenpegel), `docs/03:258-259` („Richtung ändert sich nur, wenn sich das Signal ändert") | **Teppich-Schätzung der Regelung driftet bei dichtem Vortrag.** 300 s Sprache mit 10 % Pausenanteil im unveränderten −50-Raum: 7 × `noise_floor_track`, Expander **−34 → −31,5** (geklemmt an Sprache − 12 / Bounds −30), ohne dass sich der Raum geändert hat; bei 30 % Pausen 0 Schritte. Das 10. Perzentil liest bei < 10 % Pausen Silbentäler statt Teppich. Folgenlos im Synth (0,4 % aktive Rahmen > 3 dB), bei echter Sprache rückt der Expander 2,5 dB näher an leise Konsonanten. | Teppich als Minimum-Statistik (z. B. 5. Perzentil der inaktiven Rahmen oder gleitendes Minimum wie im Normalizer) statt globalem 10. Perzentil; docs/03 §8 Satz zur Richtungsstabilität relativieren. |
| PO-27 | niedrig | `docs/03:261-268` (§8), `docs/03:356-396` (§11), `docs/02:262-275, 318-320` (§7), `docs/02:431-434` (§12) | **Doku zur Raumänderung widersprüchlich und zu optimistisch; Aufwärtsraten-Zahl irreführend.** docs/03 §8: „hält Pausen ab dem nächsten Satz auf Abstand, ohne auf die Regelung zu warten" – docs/02 §7 sagt das Gegenteil (ohne Nachführung nicht trennbar) und die Messung bestätigt docs/02 (vor Nachführung −9,2 LU bei +8 dB, +0,0 LU bei +12 dB). docs/03 §11 nennt die Raumänderung im Betrieb **gar nicht** als Grenze, obwohl sie das relevanteste Live-Risiko ist; docs/02 §12 erweckt den Eindruck, nach der Nachführung sei alles gut (bei +12 dB falsch). docs/02 §7: „mit 1 dB/s ~55 s" stammt aus einem Normalizer-Solo-Test (−6 → −26 LUFS); auf Systemebene ist ein 10 dB leiserer Sprecher nach 2 s auf −2,8 dB und nach 10 s auf −0,4 dB (siehe Rate-Empfehlung). | docs/03 §8 an docs/02 §7 angleichen; §11 einen Punkt „Raum wird nach der Kalibrierung lauter" mit Zahlen (+8 dB: Gain bleibt stehen, Abstand erst nach ~1–1,5 min; +12 dB: Kette hebt Pausen hoch → Freeze/Neukalibrierung) aufnehmen; docs/02 §7 die 55-s-Angabe durch den Systemwert ersetzen. |
| PO-28 | niedrig | `voicechain/synth.py:45` (`HARMONIC_MAX_HZ` 5000), `calibrate.py:404-410` | **High-Shelf-Absenkung ist durch kein Testsignal auslösbar.** Nach Ausschluss der Zischrahmen liest das Band 7–14 kHz beim Synth −37 dB zum Anker (Referenz erwartet −29,6) → Korrektur wäre +3,7 → cut-only → immer 0. Der eigentliche Zweck (zu helle Mikrofon/Stimme-Kombination um ≤ 3 dB abdunkeln) ist unbelegt; in R2 war er dagegen immer am Limit. | Synth mit stimmhafter Energie bis 8 kHz (oder einem Hochton-Rauschanteil der Stimme) ergänzen, so dass ein Fall mit −1 … −3 dB Shelf entsteht; vor Freigabe eine echte Aufnahme kalibrieren und den Bericht lesen. |

## Empfehlung zur Aufwärtsrate (`normalizer.rate_db_per_s`)

**Empfehlung: Default 1 dB/s beibehalten.** Begründung aus `sim_r3_sw17.py` (`out_sw17.txt`, Systemebene, kalibriert −50,
60 s normal, dann 150 s leiser, Rate 1/2/3 dB/s):

| Sprung | Rate 1 dB/s | Rate 2 dB/s | Rate 3 dB/s |
|---|---|---|---|
| −6 dB (Zurücklehnen) | sofort innerhalb ±2 (−1,0 dB nach 2 s, −0,3 nach 10 s), Kompressor folgt auf −24 | identisch | identisch |
| −10 dB (Abstand 20 → 60 cm) | **−2,8 dB nach 2 s, −0,4 nach 10 s**, dauerhaft ±2 ab 4 s; −18,3 LUFS | identisch (−2,1 / −0,3) | identisch (−2,1 / −0,2) |
| −20 dB (Sprecher 2 m weg) | **nie**: Gain am Deckel 12 (bei 100 s), Ausgang −5,5 dB zu leise (−23,8 LUFS); Expander schneidet (Sprache −40 n. Trim unter Schwelle −34, GR Median 4,9 dB) | nie (Deckel bei 80 s) | nie (Deckel bei 80 s) |

Die Rate ist auf Systemebene nicht der begrenzende Faktor: Bei −6/−10 dB nimmt der Kompressor den Großteil ab (die leisere
Sprache verliert seine ~5–6 dB Reduction) und die Sprachreferenz des Normalizers (EMA τ = 1,5 s) folgt schneller als jede der
drei Raten – die Verläufe sind bis auf 0,7 dB in den ersten 2 s identisch. Bei −20 dB begrenzen `max_gain_db` (12) und der
Expander, nicht die Rate; dieser Fall ist ohnehin kein Regelfall, sondern eine Neukalibrierung. Die „~55 s" in docs/02 §7
entstammen einem Normalizer-Solo-Test, der von −6 LUFS startet; sie beschreiben nicht den Webseminar-Betrieb. Eine höhere
Rate brächte also keinen messbaren Nutzen, erhöht aber das Risiko in dem einen Fall, der ohnehin wackelt: Im PO-15-Szenario
−38 steigt der Gain in der Pause bei jeder Rate bis zum Deckel (Rate 1: +2,6 dB, Rate 2/3: +2,4 … +2,7 dB – nur weil der
Deckel ihn fängt); mit behobenem PO-23 wäre die Rate wieder der Faktor, der bestimmt, wie schnell ein Fehlurteil des Gates
hörbar wird. 1 dB/s entspricht zudem der Praxis langsamer Sende-AGCs („Hand am Fader"). Doku-Auflage: docs/02 §7 die
55-s-Angabe durch die Systemzahlen (−10 dB: 2 s / 10 s) ersetzen.

## Grenzen des Verfahrens in der Doku (docs/02 §7, docs/03 §8/§11)

- **docs/02 §7 „Grenze des Verfahrens"** ist im Mechanismus ehrlich (Normalizer kann einen Teppich < 15 LU unter der Sprache
  nicht von Sprachdips unterscheiden; §6.4 ist eine Eigenschaft des Gesamtsystems) und fachlich korrekt formuliert. Die
  Zahlen („mit nachgeführter Schwelle: −42 → 18,8 LU, −38 → 15,0 LU") sind jedoch mit einer Schwelle gemessen, die der
  Controller nicht setzt (PO-23); real −12,2 / 0 LU (weiß) bzw. −16,5 / 0 LU (rosa). Für Nutzende ohne Audio-Vorwissen ist
  der Absatz zu technisch („Normalizer-Eingang", „statische Expander-Absenkung") und zieht keine praktische Konsequenz
  („Lüfter an → in der ersten Minute …, bei deutlich lauterem Raum → Freeze und neu kalibrieren").
- **docs/03 §8** ist gut lesbar (Tabellen für Regelgesetze, Schutzregeln, Konvergenzzeit mit Beispiel). Der Absatz
  „Raumänderung im Betrieb" verspricht aber mehr, als gemessen wird („ab dem nächsten Satz auf Abstand, ohne auf die
  Regelung zu warten"; „max_gain so, dass der Teppich rechnerisch nie auf Sprachlautheit kommen kann" – gilt nur für den
  Kalibrierraum), und das Beispiel zur Sprach-Kappe (−28 in −46 → höchstens −40) trifft nicht zu (PO-24). Der Pump-Detektor
  ist ehrlich als nicht vorhanden erklärt.
- **docs/03 §11** ist in Ton und Sprache für Nutzende verständlich (Störabstand 22 dB mit Abhilfe, „schlechter als das rohe
  Mikrofon", Referenzen als Annahmen, Mono-Konvention). Es fehlt der wichtigste Live-Punkt – **Raum wird nach der
  Kalibrierung lauter** – komplett; „Plausibilitätsprüfung" und „Leiser oder lauterer Sprecher" sagen zweimal Ähnliches und
  könnten zusammengelegt werden; „Synthetisches Sample" bezieht sich nur auf `make-sample`, nicht darauf, dass **sämtliche
  Nachweise** (Tests, Selftest, Prüfberichte) auf synthetischer Sprache beruhen – das sollte als Grenze genannt werden.

## Restrisiken für den Echtbetrieb (ehrlich)

1. **Alles synthetisch.** Jede Messung dieser und der vorigen Runden nutzt `synth.make_speech_like`: schmale Pegeldynamik
   (p10 der aktiven Rahmen nur 3 dB unter dem Median, keine leisen Konsonanten 15–20 dB unter dem RMS), keine Plosive,
   Atmer, Raumhall, keine stimmhafte Energie über 5 kHz, Teppich als weißes/rosa Rauschen ohne tonale Lüfteranteile. Direkt
   betroffen: Expander-Kappe (PO-20/24/26 – „kein Schaden messbar" heißt hier „nicht messbar"), speech_cut-Regel (Synth
   erzeugt kaum Intervalle, die vollständig Sprache sind), High-Shelf (PO-28), De-Esser-Dosierung (Zischlaut-Spektrum des
   Synth ist ein Bandpassrauschen), Lautheitsabstände (K-Bewertung liest weißes Rauschen ~4 LU lauter als rosa). Vor dem
   ersten Webseminar gehört ein Hördurchlauf mit einer echten NT-USB-Aufnahme (`record`, `calibrate`, A/B) dazu.
2. **Raumänderung im Betrieb** (PO-23): Bei +12 dB zieht die Kette Pausen auf Sprachlautheit und lässt Sätze bis +6 dB zu
   laut einsetzen; bei +8 dB in der ersten Minute Abstand nur 9–14 LU. Workaround bis zur Behebung: Lüfter/Fenster vor der
   Kalibrierung in den Betriebszustand bringen, bei Raumänderung `kill -USR1` (Freeze) und neu kalibrieren – das steht so
   noch nicht in docs/03 §11.
3. **Leiser Sprecher im lauteren Raum** (PO-24/26): Expander rückt auf 3,5 dB unter den Sprach-RMS; mit echten Konsonanten
   wahrscheinlich hörbare Wortenden-Verluste. Nicht synthetisch belegbar.
4. **Grenzstimmen** (PO-25) bekommen ein „nicht bestanden", obwohl die Kette funktioniert – Nutzende könnten die YAML
   verwerfen.
5. **Nicht geprüft in dieser Umgebung:** macOS-Audio-Pfad (CoreAudio, BlackHole, Aggregat, Geräte-Hotplug), Echtzeit-Thread
   und Dropouts (nur Rechenzeit im Container: 0,432 ms Median je Block), Zoom/Teams-eigene AGC/Rauschunterdrückung
   hinter der Kette (die Lautheits- und Pausenziele sind Annahmen über deren Erwartung), Dual-Mono-Messung (+3 LU).
6. **Zweite Runde Nachweise sind teils nicht systemtreu** (PO-23): Nach der Behebung sollten die Chain-Tests, die
   „kalibriert" behaupten, die Parameter über `calibrate.recommend`/den Controller beziehen, nicht hart setzen.

## Auflagen / nächste Schritte

1. PO-23 beheben (Normalizer-Deckel/Gate bei nicht gatebarem Teppich; Test auf reale Controller-Schwelle) **oder** §6.4 durch
   die Projektleitung auf +8 dB eingrenzen und +12 dB als Grenze dokumentieren. Nachweis mit `sim_r3_po15.py` (weiß und rosa):
   Gain in beiden Pausen ≤ 1 dB, Einsatz ≤ +1,5 dB bei −42 **und** −38; Abstand ≤ −15 LU bei −42 nach Nachführung.
2. PO-24 (`_speech_present` ohne `dynamic_range`) und PO-25 (Hysterese im Zischkriterium) beheben; je ein Test.
3. Doku: docs/03 §8/§11 und docs/02 §7/§12 nach PO-27 korrigieren; „synthetisch geprüft" als Grenze aufnehmen.
4. Vor der Freigabe ein Hördurchlauf mit echter Aufnahme (Kalibrierbericht, A/B, 5 min Live mit Lüfter an/aus); PO-26 und
   PO-28 können als bekannte Grenzen dokumentiert werden, wenn sie nicht umgesetzt werden.
