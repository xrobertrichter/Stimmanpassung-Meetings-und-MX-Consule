# Prüfung Product Owner (audiofachkundig) – Runde 2: Nacharbeit zu PO-1 … PO-14

Stand: 2026-11-03 · Commit d37813a (HEAD, nach 455b78f DSP, 91b2894 Kalibrierung/Regelung) · geprüft gegen
`docs/00_Beauftragung.md` (§6.3 neu), `docs/04_Pruefprotokoll.md` (Tabelle und „Nacharbeit (Runde 1)") und den
eigenen Bericht `docs/pruefungen/01_product_owner.md`.
Der Prüfer hat nur gelesen, ausgeführt und simuliert; keine Datei im Repo verändert. Eigene Skripte und
Rohausgaben liegen unter `<pruefordner>/r2_po/`
(`sim_po1.py`, `sim_po1b.py`, `sim_po2.py`, `sim_po2b.py`, `sim_po3.py`, `sim_po4.py`, `sim_po4b.py`, Ausgaben `out_*.txt`).

Ausgeführt: `pytest -q` → 302 bestanden, 1 übersprungen (131 s); `python -m voicechain selftest` → 12/12, Exit 0
(Störabstandsgewinn +9,5 dB, Pausenlautheit −48,3 LUFS, −18,3 LUFS, −1,95 dBTP, 0,422 ms/Block).

## Kurzurteil

**Abnehmbar mit Auflagen.**

Die drei hohen Befunde aus Runde 1 sind in der Sache behoben und durch eigene Simulation bestätigt: Der
Normalizer friert in der 20-s-Pause exakt ein (Gain-Änderung 0,000 dB in allen drei Räumen), die Regelung
verstellt De-Esser und Kompressor bei 4 Minuten Tippen bzw. leiser Fremdsprache nicht mehr und kehrt nach
60 s zum kalibrierten Wert zurück, und die EQ-Kalibrierung legt bei hohen Stimmen keinen Tiefbass-Boost mehr
an. Die Dokumentationskorrekturen sind bis auf zwei Restposten umgesetzt.

Zwei Dinge verhindern eine Abnahme ohne Auflagen:

1. **PO-15 (hoch):** Der Schutz gegen das Pausen-Hochziehen hängt am *kalibrierten* `gate_lufs`. Ändert sich
   der Raum nach der Kalibrierung (Lüfter geht an, Teppich −50 → −42 dBFS), läuft der Normalizer-Gain in der
   20-s-Pause von 1,3 auf 12,0 dB, die Pause liegt bei −19,5 LUFS (= Sprachlautheit) und der nächste Satz setzt
   +4,6 dB zu laut ein – das ist der alte PO-1-Befund, nur um eine Kalibrierung verschoben. Die adaptive Regelung
   führt `gate_lufs`/`max_gain_db` nicht nach, und das relative Gate (−10 LU) bricht zusammen, sobald der Teppich
   nach Kette weniger als ~12 LU unter der Sprachlautheit liegt, weil die Sprachlautheit aus genau den Intervallen
   gelernt wird, die das Gate passieren.
2. **PO-16 (mittel):** Für Stimmen, die laut Kalibrierung *kein* De-Essing brauchen (Zischanteil ≤ −15 dB), bleibt
   die Standardschwelle −30 dBFS stehen – die ist aggressiver als jede kalibrierte Schwelle (−15 … −17 dBFS):
   Der De-Esser greift dann in 99–100 % aller aktiven Rahmen (> 1 dB) und auf den Zischlauten mit 4,7–6,5 dB.

Beides ist lokal behebbar (Regelung: Gate aus dem gemessenen Teppich nachführen; Kalibrierung: Ruheschwelle
aus dem gemessenen Bandspitzenpegel ableiten). Nach Behebung von PO-15 und PO-16 ist das Projekt aus PO-Sicht
abnehmbar; die übrigen neuen Befunde sind niedrig.

## Statustabelle PO-1 … PO-14

| Nr | Status | Beleg (Messzahl bzw. Datei:Zeile) |
|---|---|---|
| PO-1 | **behoben** (im kalibrierten Raum; Restrisiko siehe PO-15) | `sim_po1.py`: kalibrierte Kette, 30 s Sprache / 20 s Pause / 20 s Sprache. Teppich −50: Normalizer-Gain in der Pause 0,94 → 0,94 dB (Änderung **0,000 dB**, Spanne 0,000), Pause −49,2 LUFS Median = **−30,8 LU unter Sprache** (Eingang −20,8 LU → Störabstandsgewinn in der Pause +10,0 dB), Pausen-RMS absolut −2,2 dB, Einsatz des ersten Satzes nach der Pause **−0,55 dB** relativ zum letzten Satz davor (Runde 1: +5…12 dB). Teppich −42: Gain 0,75 → 0,75 (0,000 dB), Einsatz −0,53 dB; Teppich −38: Gain 0 (max_gain kalibriert 0), Einsatz −0,58 dB. Das Gate schließt innerhalb von 0,5 s nach dem letzten Wort (Momentary-Verlauf −18 → −38 → −48 LUFS in 1 s). Code: `voicechain/dsp/normalizer.py:130-155` (Momentary-Gate, relatives Gate, keine Erhöhung auf fallender Hüllkurve, asymmetrische Rate 1/3 dB/s), `calibrate.py:605-640` (`refine_normalizer`). |
| PO-2 | **behoben** | `sim_po2.py` (Chain + LiveAnalyzer + AdaptiveController, Takt 2 s, Lernphase 30 s). Tastatur 3/s −30 dBFS, 4 min: De-Esser/Kompressor **0 Änderungen** nach den ersten 14 s (die 20-s-Historie enthält dann noch Sprache: 3 + 2 Schritte, zusammen ≤ 1,5 dB), 114 × `implausible_speech` (Gründe `level`, `speech_ratio`), Expander folgt korrekt dem leiseren Teppich (4 Schritte). `rest`: 7 Schritte ab t = 112 s (= 60 s nach der letzten plausiblen Historie), De-Esser und Kompressor exakt zurück auf −16,51 / −20,0. Fremdsprache −42 dBFS, 4 min: **0 Änderungen** an De-Esser/Kompressor (Gründe `level`, `dynamic_range`). Echte Sprache +5 dB (−21 dBFS roh, −15 nach Trim, innerhalb ±6): Kompressor −20 → −16,5 in 7 Schritten (Ziel −15, Totzone 2) – wird weiter geregelt. Stille 4 min: 0 Änderungen. Rückkehr nach Tippen/Fremdsprache: Sprache sofort wieder plausibel, keine `rest`-Schritte mehr. Code: `adaptive.py:455-468` (`_plausibility`), `:515-531`. |
| PO-3 | **behoben** | `sim_po3.py`: hohe Stimme (f0 190–250 Hz, gemessen 216 Hz): Band 80–170 Hz −22,7 dB zum Anker → Begründung „Grundfrequenz 216 Hz liegt über 150 Hz … kein Eingriff", **kein Low-Shelf angelegt**; auch mit Lüfter −42 kein LF-Eingriff. Tiefe Stimme (f0 85–110, gemessen 98 Hz) mit +6 dB Bassanhebung: Band +2,3 dB → Abweichung +0,9 dB innerhalb Totzone → kein Band; ohne Bass: −1,8 dB → Boost wäre fällig, „Shelf nur als Absenkung → 0 dB". Referenz oberhalb 4 kHz jetzt −10 dB/Okt (`calibrate.py:84-87`), Shelves cut-only (`:109`, `:542-544`), f0-Grenze 150 Hz (`:113`, `:531`). Siehe aber PO-18 (High-Shelf steht bei allen Testsignalen am Limit −4 dB). |
| PO-4 | **behoben** (mit Hinweis) | `sim_po4.py`: Schwelle aus Bandspitze (95. Perzentil der 50-ms-Spitzen, `meters.band_peak_dbfs`, `calibrate.py:245-261`). Zischanteil 0 dB (Ratio −7,7): Schwelle −15,0, Reduktion in Zischrahmen **Median 4,3 dB**, p90 5,1, 0 % am Deckel, 74 % in [4, 7] dB. Zischanteil −3 dB (Ratio −10,7, Selftest-ähnlich): Schwelle −16,5 (liegt jetzt **über** dem Startwert −30, Runde 1: nie möglich), Median 3,5 dB (Definition „laute Zischlaute" = Spitzen ≥ p95 − 3 dB, die p95-Rahmen erhalten ≥ 4 dB), 0 % am Deckel. Extremfall Zischanteil +6 dB (Ratio −1,7, das Selftest-Signal): Ziel −9,4 wird an der oberen Grenze `adaptive.bounds` −15 geklemmt → Median 7,4 dB, **36 % am Deckel** (siehe PO-17). Für realistische Zischanteile (−12 … −5 dB) stimmt die Regel; der Deckel ist nicht mehr der eigentliche Regler. |
| PO-5 | **behoben** | `dsp/envelope.py:341-378` `ReductionAccumulator` (Mittel/Max seit letztem `read`), eingebaut in Expander/De-Esser/Kompressor/Limiter (`expander.py:373-381`, `limiter.py:102`, `compressor.py:91`); `runtime.py:424-435` liest genau einmal je Regelintervall, Statuszeile nutzt die gecachte Messung (`:559-564`); `adaptive.py:409-436` verbraucht `limiter.gain_reduction_mean_db` und `expander.gain_reduction_max_db`. Docstring (`adaptive.py:47-53`) beschreibt das tatsächliche Verhalten. Nebenwirkung siehe PO-19 (Speech-Cut-Regel löst auf normaler Sprache aus). |
| PO-6 | **behoben** | `cli.py:266-314` Kommando `record` (gleiche Geräteauswahl wie `run`, 48 kHz mono, 24 Bit, Restzeit/Peak, „ZU LAUT" > −3 dBFS, Ctrl-C behält Aufnahme), `runtime.py:691-801` `record_wav`; README Schritt 7 (`README.md:85-97`), `docs/01_macOS_Setup.md:214-238` (§6a mit `afconvert -f WAVE -d LEI24@48000`, Warnung vor AAC), `docs/03:48-50`. Tests `tests/test_cli.py:306 ff.`. |
| PO-7 | **behoben** | `calibrate.py:308-317` (`normalizer_gate_target`, `normalizer_max_gain_target`), `:605-640` (`refine_normalizer` aus Pausenlautheit nach erstem Durchlauf), `:846-854`. Messung: Teppich −50 → Gate −40,0 / max_gain 12,0; −42 → Gate −33,0 / max_gain 8,5; −38 → Gate −33,0 / **max_gain 0,0** mit ACHTUNG-Text im Bericht. `max_gain` begrenzt rechnerisch „Rauschen nie auf Sprachlautheit" (Ziel − (Pause + 6 LU)). Einschränkung: nur zur Kalibrierzeit, nicht im Betrieb (PO-15). |
| PO-8 | **behoben** | `calibrate.py:686-697` Kriterium `pause_loudness` (Maximum Momentary in Pause ≥ 5 s ≤ Ziel − 20 LU) mit Hinweistext „Kette hebt die Pause zu weit an: Raum leiser machen … oder normalizer.max_gain_db senken"; Bericht zeigt Absolutwerte (`report_floor-42.md:91,98`: „absolute Änderung des Pausenpegels +6,5 dB", nicht bestanden). `docs/03:146-149` und `:332-336` sagen jetzt korrekt „schlechter als das rohe Mikrofon". Beauftragung §6.3 geändert, Pause ≥ 5 s Pflicht (`calibrate.py:155-159`, `docs/03:42`). |
| PO-9 | **behoben** | `config/chain.default.yaml:30-31` („+ 10 dB Abstand; 10 dB unter der Schwelle … 15 dB"), `docs/02:105-106`, `docs/03:106`, `calibrate.py:44-53`. Kein „+8 dB"/„10,5 dB" mehr (grep über README, docs, YAML, Code). |
| PO-10 | **teilweise** | `docs/01:46-49` korrigiert (Mac-Passwort, HAL-Plug-in, kein Sicherheitsdialog). **Aber** `docs/01:270` (Fehlersuche-Tabelle) empfiehlt weiterhin „Systemeinstellungen → Datenschutz & Sicherheit → Treiber erlauben" – widerspricht dem korrigierten Absatz. |
| PO-11 | **behoben** | `docs/01:176-179` („100 %", mit Begründung), `:200-201` Teams. |
| PO-12 | **behoben** | `README.md:4-5` („acht Stufen (0 … 5b Loudness-Normalizer, 6 Limiter)"). |
| PO-13 | **behoben** (Doku teilweise) | `runtime.py:390-421` (SIGUSR1 → Zähler → Service-Thread toggelt, Statuszeile `adaptive FROZEN`), `:649-659` Hinweis mit PID beim Start, `:672-674` Handler-Rückbau; Test `tests/test_runtime.py:501-516`. README `:113-118`, `docs/01:251,280`. **Lücke:** `docs/03` §7 (`:183-184`) und §10 „Wann `--freeze`" (`:315-323`) nennen nur die Startoption, nicht das Umschalten per Signal; die Event-Tabelle §9 kennt `freeze` zwar. |
| PO-14 | **behoben** | `docs/02:243` (Mono-Konvention, Stereo-Meter +3 LU), `docs/01:252-255`, `docs/03:355-357`. |

## Antwort zur §6.3-Abweichung

Frage: Ist „Pausen-RMS am Ausgang ≤ Eingang − 6 dB nicht erreichbar, stattdessen Störabstandsgewinn ≥ 6 dB
und Pausenlautheit ≤ Ziel − 20 LU" tragfähig?

**Ja, tragfähig – mit zwei Klarstellungen.**

Rechnung (Messung Teppich −50, `sim_po1.py`): Pause am Ausgang = Pause am Eingang + Trim (+6,0) + Makeup
(+6,5) + Normalizer-Gain (+0,9) − Expander-Absenkung (14 dB) = −2,2 dB absolut. Um −6 dB absolut zu
erreichen, müsste der Expander ≥ 19,4 dB absenken (Range 20), also der Teppich ≥ 13 dB unter der Schwelle
liegen; mit der Schutzregel „Schwelle ≤ Sprache − 12 dB" geht das nur bei Störabstand ≥ 25 dB. Der DSP-Agent
hat recht: Die absolute Forderung ist mit der gleichzeitigen Anhebung eines −26-dBFS-Sprechers auf −18 LUFS
nicht vereinbar, und sie wäre auch fachlich die falsche Größe – die Gegenseite hört den *Abstand*
zwischen Sprache und Pause, nicht den Absolutpegel der Pause (das habe ich in Runde 1, Antwort 1, schon so
bewertet).

Das Ersatzpaar ist richtig gewählt: Der Störabstandsgewinn misst, was der Expander leistet; die absolute
Pausenobergrenze (−38 LUFS bei −18 LUFS Ziel) fängt den Fall ab, in dem die Kette relativ „gewinnt", aber
die Pause trotzdem hörbar hochzieht. Beide Kriterien fallen im Lüfterraum (−42) korrekt durch (+1,2 dB, −32,1 LUFS)
und der Bericht sagt jetzt, dass die Kette dort schlechter ist als das rohe Mikrofon. Das ist ehrlich.

Klarstellungen (als Auflage zur Doku, nicht zur Technik):

1. **20 LU sind eine Untergrenze, kein Ziel.** Im ruhigen Raum erreicht die Kette 30 LU; −38 LUFS neben
   −18 LUFS Sprache ist in Pausen hörbar (Zoom-Teilnehmende mit Kopfhörer hören ein Rauschen 20 dB unter der
   Stimme). Die Beauftragung sollte das als Mindestwert bezeichnen und der Bericht den erreichten Abstand nennen.
2. **Das Kriterium bewertet nur den Kalibrierraum.** Es schützt nicht vor einer Raumänderung im Betrieb
   (PO-15). Die Beauftragung §6.4 (Regelung) sollte deshalb ergänzt werden: „Pausenlautheit bleibt auch bei
   Anstieg des Teppichs um 8 dB nach der Kalibrierung ≤ Ziel − 20 LU."

## Neue Befunde (PO-15 ff.)

| Nr | Schwere | Fundstelle | Befund | Empfehlung |
|---|---|---|---|---|
| PO-15 | **hoch** | `voicechain/adaptive.py` (kein Pfad setzt `normalizer.gate_lufs`; `max_gain_db` nur bei `limiter_protect`, `:534-545`); `voicechain/dsp/normalizer.py:132-148` (relatives Gate gegen `speech_loudness()`, die aus den ungegateten Intervallen gelernt wird); `calibrate.py:605-640` (Gate nur zur Kalibrierzeit) | **Raum wird nach der Kalibrierung lauter → alter PO-1-Fehler.** `sim_po1b.py`: Kette kalibriert bei Teppich −50 (Gate −40, max_gain 12, Expander −34). Lüfter an, Teppich −42, Szenario 30/20/20 s: Normalizer-Gain in der Pause **1,25 → 12,00 dB**, Pause **−19,5 LUFS Median = Sprachlautheit** (−1,4 LU), Pausen-RMS −23,4 dBFS (Eingang −42, also **+18,6 dB** lauter), nächster Satz **+4,6 dB** zu laut. Teppich −38: Gain 1,5 → 6,1 dB, Pause −18,0 LUFS (= Sprache), Einsatz +1,7 dB. Teppich −46: unauffällig (Gain konstant, Pause −39 LUFS). Mechanismus: Der Teppich nach Kette (−33 LUFS) liegt über dem absoluten Gate (−40) und nur ~11 LU unter der Sprachlautheit; einzelne Intervalle passieren das relative Gate (−10 LU), werden als „Sprache" gelernt, die Sprachlautheit sinkt, das relative Gate rutscht mit – Rückkopplung bis zum Teppich. Die Expander-Nachführung der Regelung (Teppich + 10 → −32 nach ~2 min) entschärft den Fall −42 (Gain dann konstant 0,93, Pause −32 LUFS), nicht aber −38, und in den ersten Minuten nach dem Lüfterstart passiert genau das Obige. `docs/02:249` und `:386-387` benennen die Schwäche selbst („muss die Kalibrierung das Gate … legen"), ziehen aber nicht die Konsequenz für den Betrieb. | (a) Regelung führt `normalizer.gate_lufs` nach: Ziel = (Teppich nach Trim − statische Expander-Absenkung an diesem Teppich + Makeup) + 8 dB, geklemmt auf [−40, Ziel − 15], Grund `gate_track`, mit Totzone 2 dB und `max_step_db`; analog `max_gain_db` wie `normalizer_max_gain_target` (PO-7-Regel live). (b) Zusätzlich im Normalizer ein eigener Teppich-Schätzer (gleitendes Minimum der Momentary-Lautheit über ~10 s, langsam steigend) und Gate ≥ Teppich + 8 LU; dann hängt der Schutz nicht an der Kalibrierung. (c) Sprachlautheit nur aus Intervallen lernen, die ≥ 15 LU über dem Teppich-Schätzer liegen. Test: Kette kalibriert bei −50, Signal mit −42/−38 → Gain in 20-s-Pause ≤ 1 dB, Pause ≤ −38 LUFS. |
| PO-16 | **mittel** | `voicechain/calibrate.py:259-260` (`deesser_threshold_target`: unterhalb der Referenz → `default_threshold_db`), `:504-507`; `adaptive.py:316-317,516-519` (`rest_values` = kalibrierter Wert, `sibilance_rest`); `config/chain.default.yaml:43` (−30) | **Stimme ohne Zischproblem bekommt den aggressivsten De-Esser.** `sim_po4b.py`: Zischanteil −19,4 dB (unter Referenz −15) → Schwelle bleibt −30,0; Bandspitze der Zischlaute nach Trim −20,2 dBFS → Reduktion in Zischrahmen **Median 6,5 dB**, p90 7,3 dB, **100 % aller aktiven Rahmen > 1 dB**. Zischanteil −27 / −28 dB: Schwelle −30, Median 4,7 dB, 99–100 % der aktiven Rahmen > 1 dB, Zischkriterium **nicht bestanden** (+6,1 / +4,0 dB, weil Kompressor/Normalizer den Teppich im 5–10-kHz-Band relativ anheben). Zum Vergleich die *engagierte* Kalibrierung bei Zischanteil −10,7 dB: Schwelle −16,5, Median 3,5 dB, 23 % der aktiven Rahmen > 1 dB. Die Begründung im Bericht („De-Esser nicht erforderlich; Standardschwelle bleibt") verspricht das Gegenteil dessen, was die Kette tut. Hörbild: eine klare, nicht zischende Stimme wird auf jedem s/sch um 5–7 dB abgedunkelt (Lispeln). | Im „nicht erforderlich"-Zweig die Schwelle aus derselben Messung ableiten: `Bandspitze p95 + 2 dB` (De-Esser bleibt in Ruhe, greift nur bei Ausreißern), geklemmt auf `bounds`; denselben Wert als `rest_values` für `sibilance_rest`. Startwert −30 in der YAML entweder anheben (−22) oder dokumentieren, dass er nur bis zur Kalibrierung gilt. Test: Synth mit `sibilance_db=-12` → Reduktion in aktiven Rahmen Median 0 dB, Zischkriterium bestanden. |
| PO-17 | niedrig | `config/chain.default.yaml:92` (`deesser.threshold_db: [-45, -15]`), `calibrate.py:494` | Bei stark zischenden Stimmen (Zischanteil ≥ −2 dB, Bandspitzen −4 dBFS nach Trim – das Selftest-Signal) will die Regel −9,4 dBFS, wird aber an −15 geklemmt; der De-Esser steht dann in 36 % der Zischrahmen am Deckel (Median 7,4 dB), `max_reduction_db` ist dort wieder der Regler. Realistische Stimmen erreichen diese Bandspitzen selten; der Selftest zeigt also nicht den kalibrierten Normalfall. | Obere Grenze auf −10 dBFS setzen (Begründung: Bandspitzen können bei Trim bis +24 dB nahe Vollaussteuerung liegen) oder das Selftest-Signal auf `sibilance_db=+3` setzen, damit die Zeile „Zischanteil-Absenkung" den Regelfall zeigt. |
| PO-18 | niedrig | `calibrate.py:84-87,103-108` (High-Shelf-Band 7–14 kHz, Referenz −10 dB/Okt), `meters.py:493-506` (aktive Rahmen inkl. Zischlaute) | Der 10-kHz-Shelf steht nach **jeder** der fünf Kalibrierungen am Limit (−3,8 / −4,0 / −4,0 / −4,0 / −4,0 dB), unabhängig von Stimme und Bass: Das Band 7–14 kHz wird über alle aktiven Rahmen gemessen, also inklusive der Zischlaute, die der De-Esser ohnehin behandelt, und des Teppichs; gegen die steile Referenz liest es immer „+8 dB zu hell". Beim Synth ist das teils ein Artefakt seiner −6-dB/Okt-Hüllkurve; bei realen Stimmen besteht dasselbe Risiko durch Zischlaute und Preamp-Rauschen. −4 dB Shelf plus De-Esser kann real dumpf klingen; die Wirkung ist nicht getestet, weil jedes Testsignal denselben Wert ergibt. | Band 7–14 kHz nur auf nicht-zischenden aktiven Rahmen messen (Rahmen mit Bandspitze unter der De-Esser-Schwelle) oder Shelf-Grenze auf −2 dB; im Bericht den Anteil Zischrahmen am Band ausweisen. Hörvergleich mit echter Aufnahme vor der Freigabe. |
| PO-19 | niedrig | `adaptive.py:129-133,504-507` (`speech_cut`, `SPEECH_CUT_MIN_ACTIVE_RATIO` 0,95), `docs/03:269` | Die Speech-Cut-Schutzregel löst bei **normaler Sprache** aus: in jedem Szenario drei Schritte in den ersten 40 s (Expander −34,0 → −35,5) und danach etwa alle 1–2 min ein Paar `speech_cut` (−0,5) / `noise_floor_track` (+0,5). Ursache: In 2-s-Intervallen mit ≥ 95 % aktiven 50-ms-Rahmen erreicht der 10-ms-RMS-Detektor in Silben-Ausklängen (als „aktiv" klassifiziert) 11–12 dB Reduktion. Hörbar ist das nicht (±0,5 dB), aber der Expander sitzt dauerhaft 1,5–2 dB unter dem kalibrierten Wert und das Log meldet „Sprache wird abgeschnitten", wo nichts abgeschnitten wird (gemessen: Expander-GR in aktiven Rahmen Median 0,0 dB). | Für `speech_cut` nicht das Blockmaximum, sondern das 90. Perzentil der Block-Mittel im Intervall verwenden, oder das Maximum nur über Blöcke, deren 50-ms-Rahmen *und* dessen Nachbarn aktiv waren; Totzone/Mindestabstand auch für `speech_cut`. |
| PO-20 | niedrig | `adaptive.py:113,460-462` (±6 dB), `:527-531` (`rest`); `docs/03:344-347` | Verhaltensänderung gegenüber Runde 1: Ein Sprecher, der dauerhaft > 6 dB leiser oder lauter spricht als bei der Kalibrierung, gilt als „nicht plausibel"; Kompressor bleibt auf −20 (gemessen: −34 dBFS roh → Kompressor-GR 2,2 dB statt 6,2; Normalizer gleicht mit +4,4 dB aus, Lautheit −18,3 LUFS). Das ist die von mir in Runde 1 empfohlene Regel und akzeptabel, aber die Kappe „Expander ≤ Sprache − 12" entfällt dann ebenfalls: Bei Teppich −46 und leisem Sprecher (−28 dBFS nach Trim) stieg der Expander auf −31,5 (nur 3,5 dB unter dem Sprach-RMS). Im Synth-Signal wurde nichts abgeschnitten (GR in aktiven Rahmen Median 0,0, 1 % > 3 dB), echte Sprache hat aber leise Konsonanten 15–20 dB unter dem RMS, die der Synth nicht hat. | Bei `implausible` wegen **Pegel** (nicht wegen Tippen) die Sprach-Kappe mit dem gemessenen Pegel beibehalten (`speech − 12`), nur bei `speech_ratio`/`dynamic_range`-Gründen fallen lassen; docs/03 §11 ergänzen, dass ein leiserer Sprecher nach 60 s die kalibrierte Kette plus Normalizer-Gain bekommt. |
| PO-21 | niedrig | `calibrate.py:846-855` (zweistufiger Durchlauf), `sim_po1.py` Teppich −38 | Zweistufige Kalibrierung ist stabil (Teppich −50/−42: Pause 1. und 2. Durchlauf identisch), aber im Grenzfall −38 enthält der erste Durchlauf das Hochziehen des noch unkalibrierten Normalizers (Pause −21,1 LUFS mit Gain +12) → `max_gain` 0,0 statt ~0,9 → zweiter Durchlauf −20,5 LUFS, Lautheitskriterium knapp verfehlt (−2,5 LU). In diesem Raum ist die Kette ohnehin nicht einsetzbar (Bericht sagt das), der Nebeneffekt ist kosmetisch. | Ersten Durchlauf mit `normalizer.enabled: false` messen (Pause ohne AGC) oder eine dritte Iteration, wenn sich `max_gain` um > 1 dB ändert. |
| PO-22 | niedrig | `docs/01:270`; `docs/03:183-184,315-323` | Doku-Restposten aus PO-10 und PO-13 (siehe Statustabelle): widersprüchlicher Fehlersuche-Eintrag „Treiber erlauben"; docs/03 kennt das Umschalten per `kill -USR1` nicht. | Zeile 270 auf „Ab-/Anmelden bzw. Neustart; `brew reinstall blackhole-2ch`" kürzen; in docs/03 §7 und §10 einen Satz zu SIGUSR1 ergänzen. |

Geprüft und **ohne Befund**:

- **f0-Messung** (`meters.py:432-470`): 85–110 → 95 Hz, 100–140 → 122, 140–170 → 154, 190–250 → 215, 250–320 → 286 Hz; stabil bei SNR 20/12/8 dB (122 Hz, voiced 83–88 %). Rauhe Stimme mit Subharmonischer (200–220 Hz + 105 Hz Modulation): Oktavfehler auf 105–107 Hz → Low-Shelf „erlaubt". Folgenlos, weil der Shelf nur absenken darf und das Band 80–170 Hz dann zu leise liest (Korrektur wäre positiv → 0). Behauchte Stimme → `voiced_ratio` niedrig, f0 `nan` → „nicht messbar" → kein Eingriff (sichere Seite).
- **Normalizer zu träge?** Nein: Sprachlautheit nach Kette −18,2 … −18,3 LUFS vor und nach der 20-s-Pause, erste 1,5 s nach der Pause −0,5 dB (keine Unterregelung), 1,5–4 s −0,4 dB. Ein −14 dB leiserer Sprecher wird auf −18,3 LUFS gebracht (Gain 8,8 dB).
- **Plausibilitätsregel bei leiser Stimme:** siehe PO-20 – kein Schaden gemessen, Restrisiko dokumentiert.
- **Rückkehr („rest")** funktioniert genau wie beschrieben (0,5 dB je 4 s zurück zum kalibrierten Wert, keine weiteren Schritte sobald Sprache plausibel ist).
- **Lange Stille und Lüfterwechsel** bleiben für die Regelung unkritisch (0 bzw. nur Expander-Schritte).

## Beurteilung der 12 Selftest-Zeilen

| Zeile | Ist | Fachliche Einordnung |
|---|---|---|
| Rauschteppich-Absenkung (relativ) | +9,5 dB; absolut −50,1 → −52,3 dBFS | Plausibel: 15 dB statische Expander-Absenkung minus ~6 dB Kompressor-Reduction; absolut nur −2 dB, weil Trim/Makeup/Normalizer +13 dB addieren – das Kriterium misst das Richtige (siehe §6.3-Antwort). |
| Pausenlautheit nach der Kette | −48,3 LUFS (Grenze −38), vorher −46,8 | Korrekt: 30 LU unter Sprache; der 6-s-Pause-Test misst jetzt den Normalizer im eingefrorenen Zustand. Deckt PO-15 nicht ab (Raum = Kalibrierraum). |
| Sprachpegel | −26,0 → −18,7 dBFS, SNR 24,1 → 33,5 dB | Stimmig: −20 nach Trim, Kompressor −6, Makeup +6,5, Normalizer +0,7 ≈ −18,7. |
| Zischanteil-Absenkung | −7,1 dB (−1,5 → −8,6) | Der Selftest fährt mit Zischanteil +6 dB einen Extremfall; dort arbeitet der De-Esser am Deckel (PO-17). Die Zeile beweist Wirksamkeit, nicht Dosierung. |
| Lautheit integriert | −18,3 LUFS, Short-Term max −17,7 | Im Ziel; kein Überschwingen. |
| True Peak | −1,95 dBTP (Ceiling −1) | 1,5 dB Marge greift; 0,45 dB Reserve zum Sample-Ceiling −2,5 dBFS, konsistent mit docs/02 §8. |
| Nicht-finite Blöcke (Sprache) | 0 | Erwartet. |
| NaN-Block | Zähler 1, Ausgang endlich, danach −inf | SW-1 korrekt: Block gemutet, Zustände zurückgesetzt. |
| Rechenzeit | 0,422 ms je 256er-Block | Budget < 1 ms, im Container; auf M-Chip weniger. |
| Limiter Sinus 0 dBFS | −2,48 dBTP | Sample-Ceiling −2,5 dBFS plus Oversampling-Messung; Sinus 1 kHz hat praktisch keinen Inter-Sample-Überschwinger – korrekt. |
| Limiter Impulse | −12,32 dBTP | Einzelsample-Impulse: HPF/EQ/Lookahead verschmieren die Energie, der True Peak liegt weit unter dem Ceiling; die Zeile prüft eher „kein NaN" als das Ceiling. Akzeptabel. |
| Stille → Stille | −inf dBFS, finite | Kein DC, kein Rauschen. |

Gesamt: 12/12 fachlich nachvollziehbar. Der Selftest bildet den kalibrierten ruhigen Raum ab; die beiden
neuen Befunde (PO-15 Raumänderung, PO-16 unzischende Stimme) liegen außerhalb seines Testsignals – ein
Selftest-Fall „Teppich steigt nach der Kalibrierung" und ein Kalibrierfall `sibilance_db=-12` würden beide
sichtbar machen.

## Auflagen für die Abnahme

1. PO-15 beheben (Gate/max_gain im Betrieb nachführen oder teppichbezogenes Gate im Normalizer) und mit dem
   Szenario „kalibriert bei −50, Betrieb bei −42 und −38" testen (Gain in 20-s-Pause ≤ 1 dB, Pause ≤ −38 LUFS).
2. PO-16 beheben (Ruheschwelle des De-Essers aus der Bandspitze) und mit `sibilance_db=-12` testen.
3. Beauftragung §6.3: „≥ 20 LU" als Mindestabstand kennzeichnen; §6.4 um die Raumänderungs-Anforderung ergänzen.
4. Doku-Restposten PO-22 (docs/01:270, docs/03 §7/§10).

PO-17 bis PO-21 sind Empfehlungen zweiter Ordnung; sie können in die Abschlussdokumentation als bekannte
Grenzen aufgenommen werden, wenn sie nicht umgesetzt werden.
