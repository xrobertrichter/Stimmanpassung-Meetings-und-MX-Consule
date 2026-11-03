# Prüfung Product Owner (audiofachkundig) – Runde 4 (Abschlussprüfung): PO-23 … PO-28, PO-15/20/24

Stand: 2026-11-03 · Commit 6fc21ab (HEAD; 2c5cc6b Kalibrierung/Regelung, dc3e36c §6.4 zweistufig) · geprüft gegen
`docs/00_Beauftragung.md` §6.4 a/b, `docs/02` §7/§12, `docs/03` §8/§9/§11 und den eigenen Runde-3-Bericht.
Keine Repo-Datei verändert. Skripte und Rohausgaben: `<pruefordner>/r4_po/` (`r4common.py` = Runde-3-Systemsimulation
mit Spuren `low_snr`/`snr_lu`; `sim_r4_po23.py`, `sim_r4_po23b.py`, `sim_r4_po24.py`, `sim_r4_po25_28.py`,
`sim_r4_po25b.py`, `sim_r4_loudness.py`, `sim_r4_start.py`; Ausgaben `out_*.txt`, `out/`).

Ausgeführt: `python -m voicechain selftest` → **12/12, Exit 0**; `pytest -q` → **336 bestanden, 1 übersprungen** (287 s).
Alle Simulationen auf Systemebene (kalibrierte Kette per `run_calibration` auf Teppich −50 dBFS mit neuem Synth: Trim +6,
Expander −34,1, Kompressor −20/+6,3, De-Esser −16,6 @ 6,8 kHz, Gate −40, max_gain 12; `LiveAnalyzer` am Roheingang,
`AdaptiveController` 2-s-Takt, 30 s Lernphase).

## Kurzurteil

**Nicht abnehmbar** – wegen einer neuen Regression, die der Umbau des Normalizers (Zustand `low_snr`) eingeschleppt hat,
nicht wegen der Runde-3-Befunde:

**PO-29 (hoch): Der Normalizer verriegelt sich beim normalen Start.** Beginnt der Stream im *kalibrierten* −50-Raum mit
Raumrauschen vor dem ersten Wort (3 s genügen; die Aufnahmeanweisung verlangt 10 s), wird die Sprachreferenz aus dem
Rauschen gelernt (−35 … −38 LUFS; das Rauschen liegt nach Trim und Makeup über `gate_lufs` −40), beim ersten Satz wird der
Teppich „benutzbar", `snr_lu` liest 12–14 LU → `low_snr` → in diesem Zustand wird nichts mehr gelernt → der Zustand kann
sich nie auflösen. Folge über 180 s: Gain **0,02 dB konstant**, Referenz bleibt bei −35,4 LUFS, `low_snr` 94–98 % der Zeit,
Regelung schreibt 6 × „Störabstand zu gering: Raum verbessern" – in einem Raum, der die Kalibrierung mit Bestnote besteht.
Im −45-Raum (weiß) regelt die Stufe im Vorlauf das Rauschen hoch und friert dann bei **+9,76 dB** ein: Sprache **−12,2 LUFS**
(6 dB zu laut, Limiter arbeitet) für die gesamte Sitzung. Ohne Vorlauf (Sprache ab t = 0) ist alles in Ordnung (Gain 1,3,
−18,3 LUFS, `low_snr` 0 %) – deshalb sehen weder Tests noch Selftest den Fehler. Nebenwirkung: Auf jedem Kalibrier-Sample
(10 s Vorlauf) steht der Normalizer-Gain im Bewertungsdurchlauf bei 0,0 dB; das Lautheitskriterium misst derzeit nur
Trim + Makeup, und unzischende Stimmen fallen damit durch (−20,3/−20,4 LUFS, „nicht bestanden", in Runde 3 bestanden).

Die Runde-3-Kernpunkte sind dagegen erreicht: Gain-Anstieg in allen acht 20-s-Pausen (weiß/rosa, +8/+12 dB) **0,00 dB**,
Einsatz des nächsten Satzes **≤ +0,08 dB**, keine Pause mehr durch Hochziehen auf Sprachlautheit, `low_snr` wird gemeldet,
PO-24 hält, PO-26 ist weg. Offen bleiben die 5-LU-Toleranz von §6.4 b (verletzt vor der Nachführung, danach knapp), ein
zweiter Latch-Fall (PO-30), die fehlende Statuszeilen-Anzeige (PO-31), ein verschobenes statt geschlossenes
Zischkriterium-Fenster (PO-25) und Doku-Reste. Nach Behebung von PO-29 (lokal: Referenz erst nach gesehener Modulation
lernen; im `low_snr` weiterhin aus Intervallen deutlich über dem Teppich lernen; Starttest „Rauschen, dann Sprache") ist das
Projekt aus PO-Sicht **abnehmbar mit Auflagen** (PO-30, PO-31, PO-25, Doku).

## Statustabelle

| Nr | Schwere | Status | Messzahlen / Beleg |
|---|---|---|---|
| PO-15 / PO-23 | hoch | **Kern behoben; §6.4 b-Toleranz teilweise** | `sim_r4_po23.py`, `out_po23.txt`; 240 s, Pausen 60–80 / 180–200 s, Teppich ab 40 s. **Gain-Anstieg in der Pause: 0,00 dB in allen 8 Pausen** (Soll ≤ 1; R3: +2,4 … +6,7). **Einsatz nächster Satz: −0,20 … +0,08 dB** (Soll ≤ +1,5). Pausenabstand Sprache−Pause Eingang → Ausgang (gegatete Sprachlautheit, Median Momentary in der Pause): **+8 rosa** 15,6 → **13,0** (Pause 1, Expander noch −34,1) / **16,3** LU (Pause 2, Expander −31,6) → §6.4 a erst nach der Nachführung erfüllt; **+8 weiß** 12,8 → 8,3 / 12,0 (−4,4 / −0,8); **+12 rosa** 11,7 → **4,8 / 6,4 (−6,9 / −5,4 LU)**; **+12 weiß** 8,1 → **2,6 / 3,3 (−5,5 / −4,9)**. Mit der Metrik des Chain-Tests (`sim_r4_po23b.py`, ungegatetes K-Mittel): rosa +12 −5,8 / −4,5, weiß +12 −5,4 / −4,9, rosa +8 −2,2 / +0,6, weiß +8 −3,2 / −0,5 → **die 5-LU-Toleranz wird vor der Nachführung (erste ~1,5 min) bei +12 dB verletzt und danach nur knapp eingehalten, rosa nicht besser als weiß** (§6.4 b sagt „nur bei weißem Rauschen nennenswert"; siehe PO-32). „Pause auf Sprachlautheit": weiß +12 Pause 1 Ausgang 2,6 LU unter Sprache bei 8,1 LU am Eingang – nicht durch Hochziehen (Gain 0,00), sondern Kompressor; rosa +12 4,8 LU. **Meter/Ereignis:** `low_snr` setzt 14 s nach Lüfterstart (54,5 s) in allen vier Fällen, auch +8 rosa (snr_lu 10,6 → 15,3, bleibt unter Austritt 17 → dauerhaft gesetzt, PO-30); Ereignis `low_snr` im Log 7 × je Lauf (alle 30 s, `source: normalizer`, `snr_lu`, `input_snr_db`, Hinweistext korrekt). Expander-Nachführung: −34,1 → −31,6 (Ziel −32) in 5 Schritten, erster bei ~56 s. |
| PO-24 (PO-20) | mittel | **behoben** | `out_po24.txt`: kalibriert −50, Sprecher −34 dBFS roh (−28 n. Trim) im −46-Raum (Dynamik 14,2 dB, Gründe `level`+`dynamic_range`): Expander −34,1 → **−38,1** (Kappe −39,7 + Totzone = −37,7) ✔; im −50-Raum −38,6 ✔; −32 roh im −46-Raum −36,1 (Kappe −35,8) ✔. GR in aktiven Rahmen (eingeschwungen) 0,0 % > 3 dB. `adaptive.py` `CAP_MIN_DYNAMIC_RANGE_DB` 10. |
| PO-25 | mittel | **teilweise** (Fenster verschoben, nicht geschlossen) | `out_po25_28.txt`, `out_po25b.txt`: Zischanteil −12,9 (Soll-Fall) → +0,1 dB **ok**, bestanden; −13,8 → ok. Aber **−12,0 → −0,2 dB FAIL**, **−11,5 → −0,4 FAIL**, **−11,0 → −0,6 FAIL** (Grenze „≤ Referenz + 3" wird um 0,0–1,0 dB verfehlt, Abnahme < 1 dB), **−15,5 → +2,0 dB FAIL** (0,5 dB über der Referenz, De-Esser engagiert, Band steigt trotzdem). −10,1 → −1,0 ok (gerade so). Ursache unverändert: Kompressor/Normalizer heben das 5–10-kHz-Band um +2 … +3,5 dB, der De-Esser (≥ 4 dB nur am p95) holt das bei Stimmen um −15 … −11 dB nicht zurück. Nutzende sehen für eine unauffällige Stimme „nicht bestanden". |
| PO-26 | niedrig | **behoben** | `out_po23.txt`: 300 s dichter Vortrag (10 % Pausen, −26 dBFS, Raum −50): **0 Expander-Schritte** (R3: 7, −34 → −31,5), `low_snr` 0 %, Lautheit −18,2 LUFS. Nebenbeobachtung: derselbe Vortrag mit −32 dBFS (genau an der ±6-dB-Plausibilitätsgrenze) pendelt Expander −35,6 ↔ −36,1 (13 ×) und De-Esser/Kompressor zwischen `track` und `rest` – harmlos (±0,5 dB), aber „kein Pendeln" aus docs/03 §8 gilt an der Toleranzkante nicht. |
| PO-27 | niedrig | **teilweise** | docs/02 §7 ist jetzt systemtreu (Controller-Schwelle −32, Tabelle rosa/weiß, ehrliche Lesart) ✔; docs/02 §12 ✔; docs/03 §11 hat die Punkte „Raum wird lauter" (a/b, Abhilfe) und „Synthetische Nachweise" ✔; docs/03 §9 kennt `low_snr` ✔; `rate_db_per_s`-Zeile mit Systemzahlen ✔. **Offen:** docs/03 §8 Absatz „Raumänderung im Betrieb" (`docs/03:268-274`) behauptet weiterhin „hält Pausen ab dem nächsten Satz auf Abstand, ohne auf die Regelung zu warten" und „max_gain so, dass der Teppich rechnerisch nie auf Sprachlautheit kommen kann" – beides widerspricht docs/02 §7 und der Messung (Pause 1: 13,0 LU bzw. 4,8 LU). docs/02-§7-Tabelle „+8 dB rosa: `low_snr` nein" trifft auf Systemebene nicht zu (PO-30). |
| PO-28 | niedrig | **nicht behoben** (Mechanik ja, Wirkung nein) | `synth.py:45,49` Harmonische bis 8 kHz, −12 dB/Okt über 4 kHz. Ergebnis in 9 Kalibrierungen (Zischanteil −1,3 … −23,1, hohe Stimme, Lüfter): High-Shelf **0,0 dB in allen Fällen**; Band 7–14 kHz liest −33,8 … −35,8 dB zum Anker gegen Referenz −29,6 → weiterhin ~5–6 dB „zu dunkel" → Korrektur wäre positiv → cut-only → 0. Der Absenkpfad (Grenze −3 dB) wird nach wie vor von keinem Testsignal ausgelöst. |
| PO-22/PO-17/PO-19/PO-21 | – | unverändert behoben | Stichproben: 0 speech_cut in 300-s-Läufen (`out_po23.txt`), Selftest/pytest grün. |

## Neue Befunde

| Nr | Schwere | Fundstelle | Befund | Empfehlung |
|---|---|---|---|---|
| PO-29 | **hoch** | `voicechain/dsp/normalizer.py:309-315` (Referenz wird auch vor `_modulated_seen` gelernt; `not self._low_snr` sperrt das Lernen), `:276-281` (`_update_low_snr`, Austritt 17 LU), `:227-240` (`_floor_usable`, `snr_lu`), `:329` (kein Anheben im `low_snr`), `calibrate.py:923-933` (Bewertungsdurchlauf startet kalt auf dem 10-s-Vorlauf) | **Start-Verriegelung: Vorlaufrauschen wird Referenz, `low_snr` sperrt das Umlernen für immer.** `sim_r4_start.py`/`out_start.txt`, kalibrierter −50-Raum, Regelung an: Vorlauf 10 s weiß → Referenz −35,4 LUFS, `snr_lu` 12,1–12,5, `low_snr` 94 %, **Gain 0,02 dB über 180 s**, 6 Ereignisse „Störabstand zu gering", Lautheit −19,6 LUFS (nur dank Trim+Makeup im Ziel); Vorlauf 3 s: identisch (98 %); rosa: identisch (Referenz −37,6). Raum −45 weiß, Vorlauf 10 s: Gain im Vorlauf auf **9,76 dB** geregelt und dann eingefroren → **Sprache −12,2 LUFS**, 6 dB zu laut, dauerhaft. Raum −55: in Ordnung (Rauschen bleibt unter `gate_lufs`). Vorlauf 0 s: in Ordnung (Gain 1,3, −18,3 LUFS, `low_snr` 0 %) – Tests, Selftest und meine PO-23-Läufe beginnen alle mit Sprache. Mechanismus: Rauschen nach Trim/Makeup (−38 dBFS ≈ −35 LUFS weiß) liegt über `gate_lufs` −40 → wird als Sprache gelernt; `_modulated_seen` wird erst beim ersten Satz wahr → Teppich „benutzbar" → `snr_lu` = Referenz(Rauschen) − Teppich ≈ 12–14 < 15 → `low_snr` → kein Lernen, kein Abklingen → Austritt (≥ 17) unerreichbar. **Kalibrierung betroffen:** `cal50.wav` durch die kalibrierte Kette: Gain max 0,03, Referenz −33,3, `low_snr` 83 % → jede Kalibrierbewertung läuft ohne Normalizer; Stimmen ohne Zischanteil fallen am Lautheitskriterium durch (sib −12: −20,3 LUFS, sib −18: −20,4; in Runde 3 bestanden). | (1) Referenz erst lernen, wenn Modulation gesehen wurde (stationäres Signal am Start ist Teppich-Kandidat, nicht Sprache); bis dahin Gain halten. (2) Im `low_snr` weiterhin aus Intervallen ≥ Teppich + `gate_margin_lu` + 10 LU lernen (eindeutig Sprache), damit der Zustand sich auflösen kann; nur das Hochregeln sperren. (3) Tests: „10 s Rauschen −55/−50/−45, dann Sprache" → Gain regelt, `low_snr` nur bei −45; Kalibrier-Test mit Vorlauf, der prüft, dass der Normalizer im Bewertungsdurchlauf regelt (unzischende Stimme −18 ± 2). (4) Selftest-Signal mit 5 s Vorlaufstille versehen. |
| PO-30 | mittel | `normalizer.py:276-281` (Hysterese 15/17), `:309,:329` (kein Lernen, kein Anheben im `low_snr`); docs/02 §7 Tabelle („+8 dB rosa: nein") | **`low_snr` rastet im §6.4-a-Raum ein und schaltet die Aufwärtsregelung ab.** `out_po23b.txt`: +8 dB rosa, nach Nachführung `snr_lu` 14,6–15,3 → Zustand bleibt (Austritt 17). Wird der Sprecher danach 6 dB leiser (ab 200 s), bleibt der Gain **2,28 dB konstant** bis 360 s, Referenz unverändert −20,3, Ausgang **−2,0 … −2,8 dB zu leise (−20,3 LUFS)**; im ruhigen Raum derselbe Sprecher: Gain 1,17 → 5,96, −18,3 LUFS. Das Hysteresefenster (2 LU) ist für einen Betriebspunkt, der systembedingt bei 15 ± 1 LU liegt (Expander −31,6 statt −32, rosa liest 1,7 LU lauter als das 1/√f-Rauschen des Chain-Tests), zu knapp; die Doku-Tabelle („nein") beschreibt den Chain-Test, nicht das System. | Austritt bei 16 LU oder `snr_lu` aus dem Maximum von Referenz und jüngster ungegateter Lautheit bilden; im `low_snr` langsames Hochregeln für Intervalle ≥ Teppich + 18 LU zulassen; Tabelle in docs/02 §7 um die Systemmessung ergänzen. |
| PO-31 | niedrig | `voicechain/runtime.py:469-520` (`status_line`: nur `floor`/`gate`), Beauftragung §6.4 b („Der Zustand ist in der Statuszeile sichtbar"), docs/02 §7 Punkt 3 („die Statuszeile zeigt es") | **Statuszeile zeigt `low_snr` nicht.** `grep low_snr voicechain/runtime.py voicechain/cli.py` → kein Treffer; die Zeile enthält `floor`/`gate`, aber weder `low_snr` noch `snr_lu`. Beauftragung und docs/02 behaupten die Anzeige. | `| SNR 12.1 LU LOW` (bzw. `snr …`) in `status_line` ergänzen, Test in `tests/test_runtime.py`. |
| PO-32 | niedrig | Beauftragung §6.4 b („gemessen −0,3 bis −4,5 LU, nur bei weißem Rauschen und +12 dB nennenswert"), `tests/conftest.py:38-47` (`pink_noise` 1/√f ab FFT-Bin 1), docs/02 §7 Tabelle | **Die dokumentierten Verluste sind zu günstig, weil das Test-Rosa tieffrequenzlastig ist.** `pink_noise` des Chain-Tests verliert im 80-Hz-HPF **3,1 dB** (3 dB seiner Energie liegen unter 80 Hz) und liest −43,4 LUFS bei −42 dBFS RMS; ein Standard-Rosa (Kellet) verliert 1,5 dB und liest −41,7 LUFS. Damit ist „+12 dB rosa" im Test effektiv ~1,5–2 dB milder. Systemmessung mit Standard-Rosa: +12 dB −5,8/−4,5 LU (Chain-Test-Metrik) bzw. −6,9/−5,4 (gegatet) – genauso „nennenswert" wie weiß. | Rosa-Generator auf 1/f ab ~20 Hz begrenzen oder Pegel nach HPF normieren; §6.4 b-Zahlen auf „bis ~6 LU in den ersten 1,5 min, danach ≤ 5 LU" korrigieren (siehe Punkt 5). |

## Antwort zu Punkt 5: Kompressor im `low_snr`-Zustand nicht entschärfen – vertretbar?

**Vertretbar als Konstruktionsentscheidung, aber nur mit Auflage zu den Zahlen.** Fachlich spricht für die Entscheidung:
Der Kompressor ist auf den Sprecher kalibriert (Schwelle auf RMS, 3:1, ~5–6 dB an den Silbenspitzen); ihn bei jedem Lüfterstart
zu entschärfen würde Dichte und Lautheit der Stimme hörbar ändern, während die Gegenseite die Pause in einem 12-dB-SNR-Raum ohnehin
hört – der Verlust ist ein Abstand von 11 → 5–6 LU, kein neues Fehlerbild, und die eigentliche Abhilfe (Raum, Neukalibrierung)
wird gemeldet. Zwei Dinge stimmen aber nicht mit der Begründung in §6.4 b überein: (1) Der Verlust ist bei lüfterähnlichem
(rosa) Rauschen **ebenso groß** wie bei weißem (−4,5 … −5,8 LU statt „nur weiß nennenswert", PO-32), und (2) in den ersten
~1,5 min nach dem Lüfterstart – bevor die Regelung die Expander-Schwelle nachgezogen hat – liegt er bei **−5,4 … −6,9 LU**,
also **über** der dokumentierten 5-LU-Toleranz; danach knapp darunter (−4,5 … −4,9). Empfehlung: Entscheidung beibehalten,
§6.4 b und docs/02 §7 mit den Systemzahlen korrigieren (Toleranz „≤ 5 LU nach Nachführung, ≤ 7 LU davor" oder schlicht
„≤ 6 LU"), und als billige Option prüfen, ob die Regelung im `low_snr` die Kompressor-Schwelle um bis zu 3 dB anhebt (halbiert den
Verlust, 1–2 dB weniger Dichte, in `max_step_db`-Schritten reversibel) – das wäre Auflage zweiter Ordnung, keine Abnahmebedingung.

## Restrisiken für den Echtbetrieb (kurz, ehrlich)

1. **PO-29 trifft den Normalfall:** `voicechain run` starten, Zoom beitreten, erst nach Sekunden sprechen → Normalizer ist für
   die Sitzung tot (oder im lauteren Raum 6 dB zu laut eingefroren) und das Log rät zur Raumverbesserung. Bis zur Behebung:
   Workaround ist keiner bekannt außer Neustart *während* man spricht; das ist für Nutzende nicht zumutbar.
2. **Weiterhin alles synthetisch** (schmale Pegeldynamik, keine leisen Konsonanten, Bandpass-Zischlaute, weißes/rosa Rauschen ohne
   tonale Lüfteranteile, keine Stimme, die den High-Shelf auslöst). Der neue Punkt „Synthetische Nachweise" in docs/03 §11 benennt
   das korrekt; die Hörprüfung mit echter NT-USB-Aufnahme steht aus und sollte Abnahmebedingung für den Produktivbetrieb sein.
3. **Betriebspunkt an der 15-LU-Kante** (PO-30): In Räumen mit Störabstand um 16 dB entscheidet 1 LU darüber, ob die
   Aufwärtsregelung läuft oder nicht; das ist mit synthetischem Rauschen reproduzierbar, mit echten Lüftern (Spektrum,
   Schwankung) unvorhersagbar.
4. **Kalibrierbericht liefert Fehlurteile** für Stimmen mit Zischanteil −15,5 und −12 … −11 dB (PO-25) und derzeit für alle
   unzischenden Stimmen (PO-29-Folge) – Nutzende könnten brauchbare Konfigurationen verwerfen.
5. **Nicht geprüft:** macOS/CoreAudio/BlackHole, Echtzeit-Dropouts (nur Rechenzeit 0,43 ms/Block), Zoom/Teams-AGC hinter der
   Kette, Statuszeile im Terminal (PO-31 nur per Quelltext).

## Auflagen / nächste Schritte

1. PO-29 beheben (Referenz erst nach Modulation; Lernen im `low_snr` aus eindeutig sprachlichen Intervallen; Starttests mit
   Vorlauf für −55/−50/−45; Kalibrierbewertung mit regelndem Normalizer). Nachweis: `sim_r4_start.py` → Gain regelt in allen
   Vorlauf-/Raumvarianten auf −18 ± 2 LUFS, `low_snr` nur bei −45; Kalibrierung sib −12/−18 bestanden.
2. PO-30 (Hysterese/Austritt) und PO-31 (Statuszeile) beheben; §6.4 b/docs/02 §7 Zahlen nach PO-32 korrigieren; docs/03 §8
   Absatz „Raumänderung im Betrieb" an docs/02 §7 angleichen (PO-27).
3. PO-25: Kriterium auf „De-Esser reduziert die Zischrahmen um ≥ 3 dB" (direkt messbar) umstellen oder die Toleranz auf
   Band-Ratio-Ebene für alle engagierten Stimmen auf +1 dB setzen.
4. PO-28 als bekannte Grenze dokumentieren oder Synth mit Stimme, die das Band 7–14 kHz über der Referenz liest; Hördurchlauf
   mit echter Aufnahme vor Freigabe.
