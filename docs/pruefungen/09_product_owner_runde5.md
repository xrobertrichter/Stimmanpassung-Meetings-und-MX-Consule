# Prüfung Product Owner (audiofachkundig) – Runde 5 (Schlussverifikation): PO-23 … PO-32

Stand: 2026-11-03 · Commit dce171f (HEAD; 4fa57bf Kalibrierung/Regelung, 9d97843 Integration) · geprüft gegen
`docs/00_Beauftragung.md` §6.4 a/b (a präzisiert: ≥ 15 LU oder mindestens Eingangsabstand), `docs/02` §7, `docs/03` §8/§9/§11
und die eigenen Berichte der Runden 3 und 4. Keine Repo-Datei verändert. Skripte und Rohausgaben: `<pruefordner>/r5_po/`
(`r5common.py`, `sim_r5_start.py`, `sim_r5_po23.py`, `sim_r5_po30.py`, `sim_r5_po25_31_32.py`, `sim_r5_modulation.py`,
Ausgaben `out_*.txt`, `out/`).

Ausgeführt: `python -m voicechain selftest` → **12/12, Exit 0**; `pytest -q` → **371 bestanden, 1 übersprungen** (448 s).
Alle Simulationen auf Systemebene: kalibrierte Kette (`run_calibration`, Teppich −50 dBFS: Trim +6,0, Expander −34,1,
Kompressor −20/+6,3, De-Esser −16,6, Gate −40, max_gain 12; Normalizer-Gain am Ende des Bewertungsdurchlaufs +1,21 dB),
`LiveAnalyzer` am Roheingang, `AdaptiveController` 2-s-Takt, 30 s Lernphase; Rauschen weiß und Standard-Rosa (Kellet).

## Kurzurteil

**Abnehmbar mit Auflagen.**

Die beiden schweren Befunde der Runden 3/4 sind geschlossen und auf Systemebene bestätigt: Der Start mit Vorlaufrauschen
(3 s und 10 s, Räume −55/−50/−45, weiß und rosa) lässt den Gain im Vorlauf bei **0,00 dB**, die Referenz wird mit dem ersten
Wort gelernt und die Sprache steht nach 30 s und am Ende bei **−18,3 … −18,4 LUFS** (PO-29); die Kalibrierung unzischender
Stimmen besteht wieder mit Normalizer-Gain **+2,1 dB**. Bei Raumänderung (+8/+12 dB, rosa/weiß) steigt der Gain in keiner der
acht 20-s-Pausen (**0,00 dB**), der nächste Satz setzt **≤ +0,08 dB** lauter ein, die Abstandsverluste liegen mit **−1,7 … −5,8 LU
vor** und **+0,8 … −4,9 LU nach** der Nachführung innerhalb der §6.4-b-Toleranzen (7/5 LU), `low_snr` wird gemeldet und in der
Statuszeile gezeigt. Die Modulationsbedingung erzeugt keine Regression bei monotonen Sprechern, langen Vokalen, langsamen
Silben oder Flüstern.

Auflagen (keine davon blockiert die Abnahme, alle sind klein): **PO-25** bleibt für Stimmen mit Zischanteil **−17 … −15 dB**
– also genau um die Referenz – ein Fehlurteil („nicht bestanden" bei +1,9 … +2,2 dB, Grenze +1 dB; mittel); der `low_snr`-Zustand
rastet im +8-dB-Rosa-Raum ein und erzeugt alle 30 s den Hinweis „Raum verbessern" in einem Raum, der §6.4 a erfüllt (niedrig);
§6.4 a („mindestens Eingangsabstand") wird in der ersten Pause nach dem Lüfterstart um 1,7 LU verfehlt und erst nach der
Nachführung erfüllt (Doku sagt das, die Beauftragung nicht); extrem monotone, pausenlose Sprache wird vom Plateau-Schätzer als
Raum gelesen (niedrig, synthetischer Extremfall). Vor dem Produktivbetrieb bleibt der Hördurchlauf mit einer echten
NT-USB-Aufnahme Pflicht – sämtliche Nachweise sind synthetisch.

## Statustabelle PO-23 … PO-32

| Nr | Schwere | Status | Messzahlen / Beleg |
|---|---|---|---|
| PO-23 (§6.4 a/b) | hoch | **erfüllt** (a nach Nachführung; b vollständig) | `out_po23.txt`, 240 s, Pausen 60–80/180–200 s, Teppich ab 40 s. Gain-Anstieg in allen 8 Pausen **0,00 dB** (Soll ≤ 1); Einsatz **−0,26 … +0,08 dB** (≤ +1,5). Abstand Sprache−Pause Eingang → Ausgang (Chain-Test-Metrik, ungegatetes K-Mittel; gegatete Werte in Klammern): **+8 rosa** Pause 1 14,4 → 12,7 (−1,7; gegatet −2,0), Pause 2 14,6 → **15,4** (+0,8) → §6.4 a nach Nachführung erfüllt (≥ 15), davor weder ≥ 15 noch ≥ Eingang (−1,7 LU); **+8 weiß** 11,6 → 9,0 (−2,6) / 11,8 → 11,3 (−0,5); **+12 rosa** 10,6 → 4,8 (**−5,8**) / 10,8 → 6,3 (**−4,5**); **+12 weiß** 8,0 → 2,6 (−5,4) / 8,2 → 3,3 (−4,9) → alle innerhalb ≤ 7 LU vor / ≤ 5 LU nach der Nachführung. Keine Pause durch Hochziehen auf Sprachlautheit; bei +12 weiß liegt die Pause 1 rechnerisch 2,6 LU unter der Sprache (Eingang 8,0, Kompressorverlust 5,4) – das ist die Arithmetik der dokumentierten Toleranz, nicht das Fehlerbild PO-1 (Gain eingefroren). `low_snr`: Meter ab 54,5 s in allen vier Fällen, Ereignis 7 × je Lauf (alle 30 s, `source: normalizer`, Hinweis korrekt). Expander-Nachführung −34,1 → −31,6 in 5 Schritten bis ~84 s. Dichter Vortrag (10 % Pausen, Raum konstant): `low_snr` nie, 0 Expander-Schritte, −18,2 LUFS. |
| PO-24 | mittel | unverändert behoben (R4) | Code seit R4 nur um Hysterese ergänzt; R4-Messung: Kappe hält (−38,1 ≤ −37,7). Nicht erneut gemessen. |
| PO-25 | mittel | **teilweise** (Fenster verschoben, nicht geschlossen) | `out_po25_31_32.txt`, `out_po25b.txt`: engagierte Seite jetzt sauber – Zischanteil −10,1 → −1,1 ok, −11,0 → −0,6 ok, −11,5 → −0,4 ok, −12,0 → −0,2 ok, −12,9 → +0,1 ok, −13,8 → +0,4 ok, −14,7 → +0,5 ok; +3 (−4,3) → −4,0 ok. **Aber** −15,1 → **+1,9 FAIL**, −15,5 → **+2,0 FAIL**, −15,9 → **+2,1 FAIL**, −16,3 → **+2,1 FAIL**, −17,1 → **+2,2 FAIL**; −18,1 → +2,5 ok (nachher −15,6 ≤ Referenz). Ursache: Unterhalb der Referenz ruht der De-Esser, Kompressor/Normalizer heben das Band um 2,0–2,5 dB, der Zweig „kein klares Problem" erlaubt nur +1 dB, und erst ab ca. −17,5 landet das Ergebnis wieder unter −15. Ein 2,5-dB-Fenster **genau um die Referenz** – die häufigste Stimme – bekommt „nicht bestanden". |
| PO-26 | niedrig | **behoben** (Hysterese wirkt im Normalfall) | 300 s dichter Vortrag −26 dBFS: 0 Expander-Schritte; normaler Vortrag an der ±6-dB-Kante (−32 dBFS roh, 30 % Pausen): 0 `implausible`, 5 monotone Schritte, kein Pendeln (R4: 13 Wechsel). Rest: dichter Vortrag an der Kante pendelt weiter (13 Schritte −35,6 ↔ −36,1, Grund `dynamic_range 10,4 < 15`, nicht `level`) – siehe PO-36. |
| PO-27 | niedrig | **behoben** | docs/03 §8 „Raumänderung im Betrieb, gemessener Ablauf" nennt jetzt Einfrieren, Abstandsverlust bis 7 LU, Pause 1 bei +8 dB 13 LU / +12 dB 4,8 LU, Nachführung ~1,5 min, `low_snr`/Statuszeile, und relativiert `max_gain_db` korrekt; docs/02 §7 Tabelle weist `low_snr` „ja" für +8 rosa aus (stimmt mit dem System überein); keine Formulierung „ab dem nächsten Satz … ohne auf die Regelung zu warten" mehr (grep leer). |
| PO-28 | niedrig | **behoben** | Absenkpfad wird jetzt erreicht: High-Shelf **−1,53 dB** (Zischanteil −15,5), **−1,09** (−16,3), **−0,67** (−17,1); bei zischenden Stimmen (Zischrahmen ausgeschlossen) weiterhin 0,0 dB; Grenze −3 dB nie erreicht. Plausibel. |
| PO-29 | hoch | **behoben** | `out_start.txt`: weiß −55/−50/−45 und rosa −45/−50, Vorlauf 3 s und 10 s: **Gain im Vorlauf max 0,00 dB** in allen 9 Läufen; Referenz gelernt bei t = Vorlaufende (3,0/10,0 s); Gain nach 30 s 1,16 … 1,24, am Ende 1,16 … 1,30; Lautheit nach 30 s und 120–180 s **−18,3/−18,4 LUFS** (Soll −18 ± 2); `low_snr` 0 % außer weiß −45 (31–55 %, snr_lu 16,3–16,5 im Hysteresefenster, Regelung unbeeinträchtigt). Kalibrierung Standard-Sample (10 s Vorlauf): sib −12 **−18,4 LUFS ok, Gain Ende +2,13 dB**; sib −18 **−18,5 ok, +2,16**; sib −3 −18,3, +1,21; alle bestanden. |
| PO-30 | mittel | **behoben** (mit dokumentierter Grenze) | `out_po30.txt`: +8 dB rosa, nach Nachführung Sprecher **−6 dB** (ab 200 s): aktiver Sprachpegel sofort nur −0,7 dB unter vorher (Kompressor-Entlastung ~5 dB), Gain 1,05 → 3,32 (bis 230 s), Lautheit 240–300 s **−19,6**, 300–360 s **−19,2 LUFS** → innerhalb ±2 ab 0 s nach dem Sprung. Die Begründung des DSP-Agenten (Kompressor fängt den Sprung ab) ist zutreffend; der Normalizer lernt im `low_snr` weiter (Referenz −19,1 → −21,3). Ruhiger Raum zum Vergleich: −18,3 LUFS, Gain 6,26. **Grenze:** −10 dB im +8-dB-Raum (Sprache dann ~6 dB über dem Teppich, unter „sichere Sprache" = Teppich + 13) wird nicht nachgeregelt: −3,8 … −4,1 dB, −22,3/−21,0 LUFS, nie im Ziel – gewollt, aber docs/02 („a quieter speaker is still followed") braucht die Bedingung (PO-35). |
| PO-31 | niedrig | **behoben** | `LiveEngine.status_line()` mit echter Kette: Raum −50 → `… floor −47.4 gate −39.4 LUFS | snr 28.2 LU | …`; Raum −38 → `… | SNR LOW (1.6 LU) | …` (`runtime.py:512-524`, Test `tests/test_runtime.py`). |
| PO-32 | niedrig | **behoben** | `test_dsp_chain.standard_pink` (−3 dB/Okt 20 Hz–20 kHz): HPF-Verlust **1,0 dB**, −41,3 LUFS bei −42 dBFS (Kellet-Rosa 1,5 dB/−41,7; alter Generator 3,1 dB/−43,4); Beauftragung §6.4 b nennt jetzt „−4,5 bis −6,9 LU bei +12 dB, rosa wie weiß" und 7/5 LU – deckt sich mit meiner Messung (−5,8/−4,5 rosa, −5,4/−4,9 weiß). |

## Modulationsbedingung – gezielte Regressionssuche (`out_modulation.txt`)

Systemebene, 10 s Vorlaufrauschen −50, dann 120 s Signal; Spanne = Spanne der 10-ms-Intervall-Lautheit über 2 s aus dem
Ring des Normalizers (Schwelle 6 LU), gemessen am Normalizer-Eingang.

| Signal | Spanne Median (p10) | „moduliert" | Referenz gelernt | Gain Ende / Lautheit out | `low_snr` |
|---|---|---|---|---|---|
| normale Synth-Sprache | 32,7 (23,7) LU | 100 % | ja, 10,0 s | 0,9 dB / −18,3 LUFS | 0 % |
| monoton (gleiche Silbenamplitude, 5 % Fade, 30 % Pausen) | 31,9 (8,8) | 97 % | ja | 0,8 / −18,3 | 0 % |
| monoton **und** pausenlos | **6,8 (5,5)** | 79 % | ja (1937 Intervalle) | 0,6 / −18,2 | **77 %** |
| lange Vokale 0,6–1,2 s | 31,2 (22,0) | 100 % | ja | 1,0 / −18,3 | 0 % |
| lange Vokale + monoton + Phrasen 6–10 s, 10 % Pausen | 19,7 (16,5) | 100 % | ja | 0,9 / −18,0 | 54 % |
| langsame Silben 2–3/s | 32,1 (23,3) | 100 % | ja | 1,3 / −18,3 | 0 % |
| Flüstern −40 dBFS (unvoiced, Sprachhüllkurve) | 17,9 (14,0) | 100 % | ja | 5,7 / −19,4 | 40 % |
| Flüstern −34 dBFS | 24,1 (20,0) | 100 % | ja | 3,0 / −18,3 | 0 % |
| Dauervokal 120 s (Singen, 3 % Vibrato) | **3,2 (3,1)** | **0 %** | nur Einschwingen (199) | 5,4 gehalten / −17,8 | 72 % |
| Dauervokal 20 s, dann Sprache | 33,3 (3,2) | 85 % | ja | 1,1 / −18,3 | 0 % |

Befund: **Keine Regression für sprachähnliche Signale.** Die Grenze liegt bei einer 2-s-Spanne von 6 LU; selbst der
konstruierte Extremfall (gleich laute Silben, 5 % Fade, keine Pausen) liegt mit 5,5–8,5 LU noch darüber und wird gelernt,
echte Sprache liegt bei 15–30 LU. Nicht gelernt werden stationäre Töne (Singen, Summen, Testton) – der Gain wird gehalten,
was die richtige Reaktion ist. Nebenbefund PO-33: Monotone, pausenlose Sprache und Dauertöne sind für den **Plateau-Schätzer**
(≤ 3 LU über 0,5 s) ein Raumteppich → `low_snr` 54–77 %, Gain gehalten, falscher Hinweis „Raum verbessern". Flüstern wird
gelernt und geregelt; bei −40 dBFS (10 dB über dem Teppich nach Trim) nur noch teilweise (−19,4 LUFS, `low_snr` 40 %) –
nachvollziehbar.

## Neue Befunde

| Nr | Schwere | Fundstelle | Befund | Empfehlung |
|---|---|---|---|---|
| PO-25 (Rest) | **mittel** | `calibrate.py:331-343` (`sibilance_criterion_passed`), `:159` (`SIBILANCE_CLEAR_PROBLEM_DB` −10), `:155` (+1 dB) | Fehlurteil für Zischanteile **−17 … −15 dB** (+1,9 … +2,2 dB bei erlaubt +1): s. Statustabelle. Der ruhende De-Esser (Schwelle p95 + 2) nimmt nichts zurück, die Kette hebt das Band um 2–2,5 dB; das Kriterium verlangt dort mehr, als die Kette ohne De-Esser leisten kann. | Für Stimmen ohne klares Problem die Toleranz auf die gemessene Anhebung setzen (+3 dB) **oder** den Ruhezweig bei Zischanteil ≥ Referenz − 3 dB leicht engagieren (Schwelle p95 − 1 dB), damit das Band um die Referenz nicht steigt; Test über −18 … −10 in 1-dB-Schritten. |
| PO-33 | niedrig | `normalizer.py` `PLATEAU_RANGE_LU` 3 / `PLATEAU_S` 0,5 (`:98-105`), `_update_floor` | Extrem monotone, pausenlose Sprache (Spanne 5,5–8,5 LU über 2 s, ≤ 3 LU über 0,5 s) und Dauertöne werden als Raumplateau gelesen: Teppich steigt zur Sprache, `low_snr` 54–77 %, Gain gehalten, Regelung meldet alle 30 s „Raum verbessern". Ausgang blieb in den Tests zufällig im Ziel (Trim+Makeup). Synthetischer Extremfall; echte Sprache moduliert > 7 LU je 0,5 s. | Plateau-Kandidaten nur annehmen, wenn das Fenster *nicht* zugleich die Modulationsbedingung erfüllt hat (kein Plateau innerhalb von 2 s nach moduliertem Signal), oder Plateau-Spanne 2 LU; als Grenze in docs/03 §11 nennen („gesungene Töne, Summen"). |
| PO-34 | niedrig | `normalizer.py` `LOW_SNR_EXIT_LU` 17; `adaptive.py` `_check_low_snr`; docs/02 §7 Tabelle | `low_snr` rastet im **+8-dB-Rosa-Raum** ein (snr_lu nach Nachführung 15,5–16,5 < 17) und bleibt 77 % der Zeit gesetzt; die Regelung schreibt alle 30 s „Störabstand zu gering: Raum verbessern oder neu kalibrieren" und die Statuszeile zeigt `SNR LOW`, obwohl der Raum §6.4 a erfüllt (15,4 LU) und die Regelung seit Runde 5 unbeeinträchtigt ist (PO-30). Für Nutzende ein Fehlalarm. | Austritt 16 LU, oder den Hinweis an den Eingangs-Störabstand des Analyzers (< 16 dB) binden und das Normalizer-Meter nur als Zustand anzeigen. |
| PO-35 | niedrig | docs/02 §7 Punkt 3 („learns … from the certain-speech intervals … a quieter speaker is still followed"), `CERTAIN_SPEECH_ABOVE_GATE_LU` 5 | Im `low_snr`-Raum wird ein leiser gewordener Sprecher nur gefolgt, solange seine Sprache ≥ Teppich + 13 LU liegt: −6 dB ja (−19,2 LUFS), −10 dB nein (−21 … −22 LUFS, dauerhaft). Gewollte Grenze, in der Doku ohne Bedingung formuliert. | Satz in docs/02 §7 und docs/03 §11 ergänzen: „… solange die Sprache mindestens 13 LU über dem Teppich liegt; darunter bleibt der Gain stehen". |
| PO-36 | niedrig | `adaptive.py` `PLAUSIBLE_MIN_DYNAMIC_RANGE_DB` 15; `LiveAnalyzer.snapshot` (`dynamic_range_db` = p95 − p10 der Rahmenpegel) | Dichter Vortrag (10 % Pausen) in ruhigem Raum liest Dynamik 10,4–10,7 dB → dauerhaft `implausible_speech` (112 von 135 Intervallen) → De-Esser/Kompressor werden nie nachgeführt; an der ±6-dB-Pegelkante pendelt der Expander ±0,5 dB (13 Wechsel) zwischen Kappe und Totzone. Benign (kalibrierte Werte bleiben, ±0,5 dB unhörbar), aber die Plausibilitäts-Hysterese (PO-26) greift nur beim Pegel. | Dynamikmaß auf aktive Rahmen vs. inaktive Rahmen umstellen (wie der Teppich) oder Schwelle 10 dB wie bei der Kappe; Totzone für die Kappe. |

## Restrisiken für den Echtbetrieb (kurz)

1. **Synthetisch geprüft.** Alle Zahlen stammen aus `synth.py`-Sprache und weißem/rosa Rauschen; echte Konsonanten, Atmer,
   Plosive, tonale Lüfter, Raumhall und die Mikrofonfärbung fehlen. docs/03 §11 sagt das jetzt; der Hördurchlauf mit echter
   NT-USB-Aufnahme (Kalibrierbericht lesen, A/B, 5 min Live mit Lüfter an/aus, Start mit Stille) sollte Abnahmebedingung für
   den Produktivbetrieb bleiben.
2. **Räume mit Störabstand um 16 dB** (z. B. +8 dB Lüfter): Betriebspunkt sitzt an der `low_snr`-Schwelle – Fehlalarm
   (PO-34) und die Grenze für leise Sprecher (PO-35) treten dort auf; die Regelung selbst bleibt stabil.
3. **Kalibrierbericht** gibt für Stimmen um die Referenz (−17 … −15 dB) ein Fehlurteil (PO-25); Nutzende sollten bis zur
   Korrektur die Zeile „Zischanteil" lesen und bei ≤ +2,5 dB Anstieg ohne klares Zischproblem die YAML trotzdem verwenden.
4. **Nicht geprüft:** macOS/CoreAudio/BlackHole, Echtzeit-Dropouts (nur Rechenzeit), Zoom/Teams-AGC hinter der Kette,
   Statuszeile nur per `status_line()`-Aufruf, nicht im laufenden Terminal.

## Auflagen

1. PO-25 (Rest) schließen; Nachweis: Kalibrierung über Zischanteile −18 … −10 dB in 1-dB-Schritten ohne Fehlurteil.
2. PO-34 (Austritt/Hinweis) und PO-35 (Doku-Bedingung) beheben; PO-33 und PO-36 dokumentieren oder beheben.
3. Beauftragung §6.4 a um „nach Nachführung der Expander-Schwelle (ca. 1,5 min)" ergänzen – so ist es gemessen und in
   docs/03 §8 beschrieben; vor der Nachführung liegt der Abstand bei +8 dB rosa 1,7 LU unter dem Eingang.
4. Hördurchlauf mit echter Aufnahme vor Produktivfreigabe.
