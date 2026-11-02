# Parameter-Begründung: DSP-Kette voicechain

Stand: 2026-11-02 · Gilt für `config/chain.default.yaml` und die Umsetzung in `voicechain/dsp/`.

Alle Pegel in dBFS (0 dBFS = Vollaussteuerung). Für Sinussignale ist der Pegel als Spitzenwert
gemeint (AES17: Vollaussteuerungs-Sinus = 0 dBFS), für Rauschen als RMS. Die Startwerte sind für
ein **Rode NT-USB** (Nieren-Kondensatormikrofon, Abstand 15–25 cm) bei Sprache in Webseminaren
gewählt. Die Kalibrierung überschreibt die Werte innerhalb der angegebenen Bereiche; die
adaptive Regelung bewegt nur die drei Schwellen (Expander, De-Esser, Kompressor) innerhalb der
`adaptive.bounds`.

Die Bereiche in diesem Dokument sind dieselben, die `voicechain.config.validate_config` und
`Stage.set_param` erzwingen (`PARAM_RANGES` je Stufe); Loader und Laufzeit können daher nie
unterschiedliche Grenzen haben.

## 0. Globale Werte

| Parameter | Startwert | Bereich | Begründung |
|---|---|---|---|
| `sample_rate` | 48000 | 8000–192000 (ganzzahlig) | Das NT-USB arbeitet fest mit 48 kHz; keine Wandlung nötig. Alle Filter werden für die konfigurierte Rate entworfen (auch die K-Bewertung). |
| `block_size` | 256 | 16–8192 | 5,3 ms Blocklatenz. Kleiner erhöht das Dropout-Risiko, größer die Latenz. Alle Stufen sind blockgrößenunabhängig (Test: 64 vs. 4096 Samples → Abweichung ≤ 1e-5). |
| `input.device` / `input.channel` | "RØDE NT-USB" / 0 | Teilstring / 0–63 | Gerätesuche per Teilstring (Groß/Klein egal); das NT-USB liefert Mono auf Kanal 0. |
| `output.device` / `output.channels` | "BlackHole 2ch" / 2 | Teilstring / 1–8 | Mono wird auf beide Kanäle dupliziert. |

Rechenzeit (gemessen in diesem Container, 4 Kerne, Python 3.13, numpy 2.5, scipy 1.18): 10 s Audio in
Blöcken von 256 Samples laufen in 0,76–0,83 s durch die ganze Kette, also rund **400–450 µs je Block**
(Echtzeitfaktor 0,08). Anteile je Stufe (µs/Block): Input Trim 7, HPF 19, Expander 69, De-Esser 69,
EQ 19, Kompressor 41, Normalizer 55, Limiter 37. Keine Python-Schleife läuft über einzelne Samples;
die nichtlinearen Hüllkurven werden geschlossen vektorisiert gerechnet (siehe Abschnitt 9). Auf
einem M-Chip ist deutlich weniger zu erwarten; das Budget < 1 ms wird auch hier eingehalten.

## 1. Input Trim (`chain.input_trim`)

**Zweck:** Statische Verstärkung, die den Sprachpegel auf die Referenz −20 dBFS RMS (aktive Sprache)
bringt. Wird von der Kalibrierung gesetzt, nicht von der adaptiven Regelung.

| Parameter | Startwert | Bereich | Begründung |
|---|---|---|---|
| `gain_db` | 0.0 | −40…+40 | 0 dB, bis die Kalibrierung den tatsächlichen Pegel gemessen hat. ±40 dB deckt alles von einem heißen Line-Signal bis zu einem sehr leise eingestellten USB-Mikrofon ab. |

Hörbild: zu wenig → der Kompressor greift kaum, der Normalizer muss mit +12 dB nachziehen und hebt
Rauschen mit; zu viel → der Kompressor arbeitet dauerhaft, der Limiter drückt, Sprache klingt
"gepresst". Eine Änderung zur Laufzeit wird über 10 ms linear gerampt (kein Knacken).

## 2. High-Pass (`chain.highpass`)

**Zweck:** Trittschall, Rumpeln, Körperschall vom Schreibtisch und Plosiv-Energie unterhalb der
Sprache entfernen. Butterworth (maximal flacher Durchlassbereich) als SOS-Kaskade mit fortgeführtem
Zustand.

| Parameter | Startwert | Bereich | Begründung |
|---|---|---|---|
| `enabled` | true | – | Bypass nur zum A/B-Hören sinnvoll. |
| `freq_hz` | 80.0 | 20–400 | −3-dB-Frequenz. Männliche Grundfrequenzen beginnen bei ~85 Hz; 80 Hz lässt sie unangetastet (bei 100 Hz nur 0,5 dB Abfall). Unter 60 Hz kommt zu viel Rumpeln durch, über 120 Hz wird die Stimme dünn. |
| `order` | 4 | 1–8 (ganzzahlig) | 24 dB/Okt: 16,4 dB Dämpfung bei 50 Hz, 24 dB bei 40 Hz, 48 dB bei 20 Hz; 1 kHz bleibt unverändert (< 0,01 dB). |

Hörbild: zu wenig (zu tief/zu flach) → dumpfes Rumpeln, "Plopp" bei P- und B-Lauten, der
Kompressor reagiert auf Tiefbass statt auf Sprache; zu viel (zu hoch/zu steil) → dünne, "telefonige"
Stimme, Männerstimmen verlieren Fundament.

**Entscheidung zur Dämpfungsvorgabe:** Die Umsetzungsvorgabe nannte "≥ 20 dB bei 50 Hz" für 80 Hz /
Ordnung 4. Das ist physikalisch nicht erreichbar: Ein Butterworth-Hochpass 4. Ordnung mit
−3 dB bei 80 Hz dämpft 50 Hz um 10·log10(1 + (80/50)^8) = 16,4 dB. Fachlich wurde die
Standardsemantik (−3-dB-Frequenz, Butterworth) beibehalten, weil sie für die Sprachgrundfrequenz
die schonendste Wahl ist. Die Tests belegen 16–17 dB bei 50 Hz, ≥ 20 dB bei 40 Hz und ≥ 40 dB bei
20 Hz. Wer 20 dB bei 50 Hz braucht, setzt `freq_hz: 90` (20,5 dB) oder `order: 6` (24,5 dB); beides
ist konfigurierbar und wird ebenfalls getestet.

## 3. Downward Expander (`chain.expander`)

**Zweck:** Raumrauschen, Lüfter und Nachhall in Sprechpausen absenken, ohne die Sprache hart zu
schalten (sanftes Gate).

**Aufbau:** Kurzzeit-RMS-Detektor (Rechteckfenster `detector_ms`) → statische Kennlinie mit weichem
Knie → Hold (Gate-Timer: solange das Gate innerhalb der letzten `hold_ms` voll offen war, bleibt das
Ziel 0 dB) → Release-Abfall (exponentiell) → Attack-Glättung (Einpol). Der RMS-Detektor (statt
Spitzenwert) macht die Schwelle direkt mit dem Rauschteppich vergleichbar, den die Kalibrierung
als Rahmen-RMS misst. Der Hold wird nur durch ein voll geöffnetes Gate ausgelöst, nicht durch die
kleinen Schwankungen der statischen Kennlinie im stationären Rauschen; ein gleitendes Maximum des
Gains selbst hätte die Absenkung im Rauschen um ~1 dB Richtung "offen" verfälscht.

| Parameter | Startwert | Bereich | Begründung |
|---|---|---|---|
| `enabled` | true | – | |
| `threshold_db` | −45.0 | −90…0 (adaptiv −70…−30) | Rauschteppich eines ruhigen Büros mit NT-USB liegt bei etwa −53 dBFS; +8 dB Abstand, damit Rauschspitzen die Schwelle nicht erreichen. Die Kalibrierung setzt Rauschteppich + 8 dB. |
| `ratio` | 2.5 | 1–10 | Unterhalb der Schwelle wird jedes dB zu 2,5 dB: 10 dB unter der Schwelle → 15 dB leiser. Sanfter als ein Gate (∞), aber hörbar wirksam. |
| `range_db` | 20.0 | 0–60 | Maximale Absenkung. Mehr als 20 dB klingt wie Ein-/Ausschalten des Raums ("Atmen" der Kette); 20 dB reicht, um Lüfter unter die Wahrnehmungsschwelle der Gegenseite zu bringen. |
| `attack_ms` | 5.0 | 0.1–500 | Zeit zum Öffnen (63 %). 5 ms + 10 ms Detektorfenster: der erste Konsonant einer Silbe kommt ungekappt durch. |
| `hold_ms` | 50.0 | 0–1000 | Überbrückt Mikropausen zwischen Wörtern und Silben; verhindert Flattern ("Chattering"). Test: ein 20-ms-Loch im Signal verändert den Gain nicht. |
| `release_ms` | 150.0 | 1–5000 | Zeitkonstante (63 %) des Schließens nach Hold und Detektorfenster; ~80 % der Absenkung nach 2,5 × release. Schnell genug, dass Pausen ab ~0,4 s hörbar ruhiger werden, langsam genug, dass Wortenden nicht abreißen. |
| `knee_db` | 6.0 | 0–24 | Weicher Übergang ±3 dB um die Schwelle: leise Silben und Atmer werden graduell leiser statt geschaltet. |
| `detector_ms` | 10.0 (optional, nicht in der YAML) | 0.5–100 | RMS-Fenster ≈ eine Periode der tiefsten Männerstimme (85 Hz = 11,8 ms), sodass der Pegel nicht mit der Tonhöhe wellt (< 1 dB), aber innerhalb einer Silbe reagiert. Ein Rechteckfenster "vergisst" lautes Signal vollständig nach 10 ms; ein Einpol-Mittler bräuchte für 25 dB Abfall 58 ms und würde das Schließen verzögern. |

Hörbild: Schwelle zu tief / Ratio zu klein → Rauschen bleibt in Pausen; Schwelle zu hoch → leise
Silbenenden und Atmer werden "abgeschnitten", Sprache wirkt abgehackt; Hold/Release zu kurz →
Flattern zwischen Wörtern; Release zu lang → Rauschen "fährt" hörbar hoch und runter.

Messwerte (Tests): stationäres Rauschen −55 dBFS → 12–20 dB leiser (statisch 15 dB, gemessen 14,2 dB
wegen der Pegelschwankung des 10-ms-Fensters); Sprache
−20 dBFS → < 1 dB Änderung; in 1-s-Pausen sind die letzten 0,4 s ≥ 12 dB leiser, die ganze Pause im
Mittel ≥ 6 dB.

## 4. De-Esser (`chain.deesser`)

**Zweck:** Zischlaute (s, sch, z) entschärfen, die das präsenzbetonte Kondensatormikrofon und die
Codecs von Zoom/Teams hervorheben.

**Designentscheidung (split-band mit Band-Subtraktion):** Ein paralleler RBJ-Bandpass
(`freq_hz`, `bandwidth_oct`, Spitzenverstärkung exakt 0 dB) liefert sowohl das Detektorsignal als
auch den Anteil, der abgezogen wird: `y = x − (1 − 10^(−GR/20)) · Bandpass(x)`. Bei der
Mittenfrequenz ist der Bandpass reell gleich 1, dort wird exakt `GR` dB abgesenkt; außerhalb des
Bandes verschwindet der Bandpass und das Signal bleibt unberührt. Gegen einen dynamisch
nachgestimmten Peak-EQ sprach, dass dessen Koeffizienten je Sample neu zu berechnen wären (Python-
Schleife oder blockweise Sprünge = Zipper-Rauschen und Blockgrößenabhängigkeit); gegen Wideband-
De-Essing sprach, dass dabei bei jedem "s" die ganze Stimme dumpf wird. Die Band-Subtraktion ist
sampelgenau, blockgrößenunabhängig und braucht keine Koeffizientenänderung im Betrieb. Grenze des
Verfahrens: An den Bandrändern (Phase ±45°) ist die Absenkung geringer als in der Mitte (bei 8 dB
Mitte etwa 2,4 dB am −3-dB-Rand), die Form entspricht etwa einem Glockenfilter.

| Parameter | Startwert | Bereich | Begründung |
|---|---|---|---|
| `enabled` | true | – | |
| `freq_hz` | 6500.0 | 2000–12000 (< fs/2) | Energiemaximum von "s"/"z" bei Sprache über ein Kondensatormikrofon; Frauenstimmen eher 7–8 kHz, Männer 5–6 kHz. Die Kalibrierung kann nachführen. |
| `bandwidth_oct` | 1.0 | 0.25–3 | 4,6–9,2 kHz (−3 dB): deckt s, sch, z ab; schmaler lässt "sch" durch, breiter greift in Konsonanten wie "t" und in die Präsenz ein. |
| `threshold_db` | −30.0 | −60…0 (adaptiv −45…−15) | Spitzenpegel des Bandsignals, ab dem Zischen bei −20 dBFS Sprachpegel störend wird (Zischspitzen liegen 5–15 dB unter dem Sprachspitzenpegel). |
| `ratio` | 4.0 | 1–20 | Kräftig genug für hörbare Wirkung; 15 dB über der Schwelle werden 11 dB gewünscht, begrenzt durch `max_reduction_db`. Hartes Knie, damit der De-Esser erst oberhalb der Schwelle entschieden eingreift. |
| `max_reduction_db` | 8.0 | 0–24 | Deckel gegen "Lispeln": mehr als 8 dB lässt "s" wie "th" klingen. Tests: 6,5-kHz-Ton −15 dBFS wird um exakt 8,0 dB abgesenkt, mit `max_reduction_db: 3` um 3,0 dB. |
| `attack_ms` | 1.0 | 0.1–50 | Zischlaute dauern 50–150 ms: der Eingriff muss innerhalb weniger Millisekunden stehen. |
| `release_ms` | 60.0 | 1–1000 | Löst innerhalb des Lauts wieder; die nächste Silbe (Vokal) bleibt unbeeinflusst. |

Hörbild: zu wenig → scharfe, "spitze" S-Laute, besonders nach dem Opus-Codec; zu viel → Lispeln,
die Stimme verliert Artikulation; Band zu breit → Stimme wird bei jedem "s" dumpf.

Messwerte (Tests): Zischbursts −15 dBFS (5–9 kHz) ≥ 4 dB leiser (gemessen ~5 dB im 5–9-kHz-Band, weil
das Band breiter ist als die 1-Oktav-Glocke), 1-kHz-Ton gleichen Pegels < 0,5 dB verändert (gemessen
0,0 dB), Reduktion nie über `max_reduction_db`.

## 5. EQ (`chain.eq`)

**Zweck:** Klangfarbe des NT-USB auf Sprachverständlichkeit trimmen: Nahbesprechungs-Mulm
raus, Präsenz rein, etwas Luft. Kaskade aus RBJ-Biquads (Low-Shelf, Peak, High-Shelf), Anzahl und
Typ der Bänder stehen mit der Konfiguration fest; Gain, Frequenz und Q sind zur Laufzeit
änderbar (`set_param("eq", "bands.<i>.<feld>", wert)`), Koeffizientenwechsel werden über 10 ms
zwischen altem und neuem Filter überblendet.

| Band | Typ | `freq_hz` | `gain_db` | `q` | Begründung |
|---|---|---|---|---|---|
| 0 | peak | 220 | −2.0 | 1.0 | Nahbesprechungseffekt der Niere bei 15–25 cm hebt 150–300 Hz an ("Mulm", "Brustigkeit"). −2 dB bei Q 1 (Bandbreite ~1,4 Okt) nimmt das weg, ohne die Stimme dünn zu machen. |
| 1 | peak | 3200 | +2.0 | 1.2 | Präsenzbereich 2,5–4 kHz trägt Konsonanten und damit Verständlichkeit; +2 dB reicht, mehr wird nach dem Codec hart. |
| 2 | high_shelf | 10000 | +1.5 | 0.707 | Dezente "Luft" > 10 kHz gegen den dumpfen Eindruck mancher Codecs; der De-Esser davor verhindert, dass dabei Zischen verstärkt wird. |

Bereiche je Band: `freq_hz` 20 Hz bis unterhalb fs/2, `gain_db` −24…+24, `q` 0.1–20. Alle
Startwerte bleiben bescheiden (≤ 3 dB), weil ein Konferenzsystem keine Hi-Fi-Wiedergabe ist und
starke EQ-Eingriffe nach dem Codec unnatürlich klingen. Bänder mit 0 dB sind bitgenau transparent.

Hörbild: Band 0 zu stark → dünn, "Telefon"; zu schwach → mulmig, undeutlich. Band 1 zu stark →
nasal, aggressiv, Zischen nimmt zu; zu schwach → "hinter einem Vorhang". Band 2 zu stark →
Rauschen und Zischen werden hörbar; zu schwach → dumpf.

Messwerte (Tests): Zeitbereichsmessung je Band stimmt mit Soll ±0,5 dB überein (analytisch ±0,1 dB),
Nullgain-Bänder sind exakt transparent.

## 6. Kompressor (`chain.compressor`)

**Zweck:** Dynamik der Sprache verdichten, damit leise und laute Passagen näher beieinander liegen.

**Aufbau:** Feedforward, Spitzenwertdetektor (|x| in dB), statische Kennlinie mit quadratischem
weichem Knie, Glättung der Gain-Reduktion im dB-Bereich nach Giannoulis/Massberg/Reiss (JAES 2012):
Release-Abfall (exponentiell zum Ziel) gefolgt von Einpol-Attack. Makeup als statische Verstärkung
mit 10-ms-Rampe bei Änderung.

| Parameter | Startwert | Bereich | Begründung |
|---|---|---|---|
| `enabled` | true | – | |
| `threshold_db` | −20.0 | −60…0 (adaptiv −35…−10) | Entspricht dem Referenzpegel aktiver Sprache nach Input Trim (−20 dBFS RMS); die Spitzen darüber werden verdichtet. |
| `ratio` | 3.0 | 1–20 | 3:1 ist der Sprachstandard: hörbar gleichmäßiger, noch natürlich. 2:1 wirkt kaum, > 4:1 klingt "gepresst". |
| `knee_db` | 6.0 | 0–24 | Weiches Knie ±3 dB: der Einsatz ist nicht hörbar; am Schwellenpunkt beträgt die Reduktion 0,5 dB. |
| `attack_ms` | 10.0 | 0.1–500 | Lässt die Transienten von Konsonanten durch (Verständlichkeit) und fängt Silbenspitzen trotzdem ein. |
| `release_ms` | 100.0 | 1–5000 | Folgt dem Silbenrhythmus (~4/s), pumpt nicht. Gemessener 63-%-Punkt ~110 ms, weil die Attack-Glättung dem Release nachläuft (siehe Abschnitt 9). |
| `makeup_db` | 0.0 | −12…+24 | Die Lautheit stellt der Normalizer ein; Makeup bleibt 0, damit zwei Regler nicht gegeneinander arbeiten. |

Hörbild: Schwelle zu tief / Ratio zu hoch → "gepresst", Atmer und Raum werden hochgezogen,
Rauschteppich steigt relativ; zu wenig → ungleichmäßige Lautstärke, der Normalizer muss ständig
nachführen; Attack zu kurz → Konsonanten werden stumpf; Release zu kurz → Verzerrung bei tiefen
Stimmen, zu lang → Pumpen.

Messwerte (Tests): Sinus −10 dBFS → −16,67 dBFS ± 0,3 dB (Ratio 3, Knie 6); Sinus an der Schwelle →
−20,5 dBFS (Knie); Attack-63-%-Punkt 10 ms ± 30 %, Release 100 ms ± 30 % (gemessen 10,1 / 110 ms).

## 7. Loudness Normalizer (`chain.normalizer`)

**Zweck:** Langsame automatische Verstärkungsregelung (AGC) auf eine Ziel-Lautheit, damit die
anderen Teilnehmenden unabhängig von Sprechabstand und Tagesform einen gleichmäßigen Pegel hören.

**Aufbau:** K-Bewertung (BS.1770: Pre-Filter + RLB, lokal in `biquad.k_weighting_sos` entworfen,
reproduziert die 48-kHz-Tabelle des Standards auf 1e-9) → Quadrat → Einpol-Mittler mit
τ = `window_s`/2 (varianzäquivalent zum 3-s-Rechteckfenster der Short-Term-Lautheit), mit
Bias-Korrektur ab dem ersten Sample → Lautheit = −0,691 + 10·log10(Mittelwert) LUFS. Alle 10 ms (auf
einem absoluten Sampleraster, deshalb blockgrößenunabhängig) wird das Gain-Ziel
`clip(target − L, min_gain, max_gain)` neu bestimmt, sofern L über dem Gate liegt; der angewandte
Gain läuft mit höchstens `rate_db_per_s` auf das Ziel zu, je Sample in geschlossener Form. Die
Regelung ist feedforward (sie misst ihren eigenen Eingang) und kann daher nicht schwingen.

| Parameter | Startwert | Bereich | Begründung |
|---|---|---|---|
| `enabled` | true | – | Bei Bypass läuft die Messung weiter (Meter bleiben gültig, Wiedereinschalten ohne Sprung). |
| `target_lufs` | −18.0 | −40…−6 | Konferenzsysteme und ihre automatische Pegelung erwarten Sprache um −18…−16 LUFS; −18 lässt 17 dB Crest-Faktor bis zum Limiter. |
| `window_s` | 3.0 | 0.4–10 | Short-Term-Fenster nach BS.1770. Kürzer regelt Silben statt Sätze, länger reagiert träge auf einen Wechsel des Sprechabstands. |
| `max_gain_db` | 12.0 | 0–30 | Mehr würde in leisen Passagen Rauschen und Raum hochziehen; 12 dB fängt einen deutlich zu leisen Sprecher auf. |
| `min_gain_db` | −12.0 | −30…0 | Schutz gegen "Reinbrüllen"; weiter unten würde der Limiter ohnehin arbeiten. |
| `rate_db_per_s` | 1.0 | 0.1–20 | So langsam wie eine Hand am Fader: keine hörbare Regelbewegung innerhalb eines Satzes. Ein 12-dB-Versatz ist nach 12 s ausgeglichen. |
| `gate_lufs` | −40.0 | −80…−20 | Unterhalb gilt "Pause": der Gain wird eingefroren, Stille und Rauschteppich werden nicht hochgezogen. 20 LU unter dem Ziel entspricht dem relativen Gate von BS.1770 (−10 LU) mit Reserve. |

Hörbild: Rate zu hoch → hörbares Atmen/Pumpen auf Satzebene; Ziel zu hoch → Limiter arbeitet
dauerhaft; Gate zu tief → in langen Pausen fährt der Raum hoch; Gate zu hoch → leise Sprecher werden
nicht geregelt.

**Position in der Kette (5b, nach Kompressor, vor Limiter):** Der Normalizer sieht das bereits
verdichtete Signal, dessen Lautheit stabiler ist als die der Rohsprache; die Regelung muss deshalb
weniger und seltener eingreifen. Vor dem Kompressor platziert würde er dessen Schwelle laufend
"verschieben" (die adaptive Regelung führt die Kompressorschwelle dem Sprachpegel nach – beide
würden gegeneinander arbeiten). Nach dem Limiter darf er nicht stehen, weil er sonst das Ceiling
wieder überschreiten könnte. Eine Konsequenz dieser Reihenfolge: Der Normalizer hebt in absoluten
Zahlen auch den Rauschteppich an. Entscheidend für die Gegenseite ist aber der *Abstand* zwischen
Sprache und Rauschen, und den verbessert der Expander um ≥ 6 dB (Chain-Test, gemessen als
10. Perzentil der 100-ms-Rahmen in Pausen wie in der Kalibrierung).

Messwerte (Tests): −30-LUFS-Signal erreicht −18 ± 1 LU, Gain steigt mit exakt 1 dB/s (nach 5 s
5,0 dB), monoton; Rauschen −60 dBFS bleibt unverändert (Gate), `max_gain_db`/`min_gain_db` werden nie
überschritten; die Lautheitsschätzung stimmt nach 0,5 s auf 0,1 LU (Bias-Korrektur).

## 8. Limiter (`chain.limiter`)

**Zweck:** Harte Obergrenze gegen Übersteuerung des Codecs und der Gegenseite.

**Aufbau:** Verzögerungsleitung von `lookahead_ms`; benötigte Reduktion `GR = max(0, |x|_dB −
Ceiling_intern)`; exponentieller Release (im dB-Bereich); gleitendes Maximum über Lookahead + 1
Samples; gleitender Mittelwert über Lookahead Samples (linearer Attack-Anstieg). Beweis, dass das
Ceiling nie überschritten wird: Jeder Summand des Mittelwerts ist ein Maximum über ein Fenster, das
das Sample enthält, das gerade ausgegeben wird; der Mittelwert ist also mindestens so groß wie die
dort benötigte Reduktion. Die Reduktion erreicht ihren vollen Wert genau dann, wenn die Spitze am
Ausgang ankommt.

| Parameter | Startwert | Bereich | Begründung |
|---|---|---|---|
| `enabled` | true | – | Achtung: Bypass entfernt die Lookahead-Latenz (Sprung im Zeitbezug). |
| `ceiling_dbfs` | −1.0 | −20…0 | −1 dBTP ist der übliche Sicherheitsabstand für verlustbehaftete Codecs (Opus/SILK in Zoom/Teams) und die D/A-Wandler der Gegenseite. |
| `lookahead_ms` | 5.0 | 0.5–20 | 240 Samples: lang genug für einen weichen, unhörbaren Eingriff, kurz genug, dass die Gesamtlatenz der Kette (5,3 ms Block + 5 ms) im Gespräch nicht stört. Dies ist die einzige Latenz der Kette; eine Änderung zur Laufzeit ist der einzige nicht knackfreie Parameterwechsel (Puffer werden neu angelegt). |
| `release_ms` | 50.0 | 1–1000 | Schnell genug, dass die Sprache nach einer Spitze nicht "eingedrückt" bleibt; langsam genug, dass keine Verzerrung durch Gain-Modulation entsteht. |
| `true_peak_margin_db` | 1.5 (optional, nicht in der YAML) | 0–3 | Siehe unten. |

**True-Peak-Marge:** Der Limiter arbeitet auf Sample-Spitzen. Zwischen den Samples kann das
rekonstruierte Analogsignal höher liegen (Inter-Sample-Peaks). Statt den Sidechain 4-fach
überabzutasten (zusätzliche Latenz und Rechenzeit) sitzt das interne Sample-Ceiling um
`true_peak_margin_db` unter `ceiling_dbfs`, also bei −2,5 dBFS. Messung mit 4-facher Überabtastung
(Tests): sprachähnliches, bandbegrenztes Material überschwingt < 0,1 dB; stark limitiertes rosa
Rauschen (ungünstigster realistischer Fall hinter einem Mikrofon) etwa 1,0 dB; mit 1,5 dB Marge
bleibt 0,5 dB Reserve, und der Chain-Test belegt True Peak ≤ −1 dBTP. Nicht abgedeckt sind Signale
mit voller Energie bis zur Nyquist-Frequenz (weißes Rauschen, Rechteck mit Flanken von einem Sample):
sie überschwingen 2–3 dB, kommen hinter einem Mikrofon mit Anti-Aliasing-Filter aber nicht vor. Die
Marge kostet 1,5 dB Spitzenreserve; bei −18 LUFS Ziel und 15 dB Crest-Faktor nach dem Kompressor
ist das ohne Belang.

Hörbild: Ceiling zu hoch / Marge 0 → Clipping im Codec, "kratzende" Spitzen; Lookahead zu kurz →
hörbares Zerren an Transienten; Release zu kurz → Verzerrung tiefer Stimmen, zu lang → Pegel
"hängt" nach einer Spitze.

Messwerte (Tests): 0-dBFS-Rechteck und +6-dBFS-Impulse → Ausgang nie > Ceiling (Sample-Peak),
Latenz = 240 Samples, −20-dBFS-Signal unverändert (±0,01 dB), nach einer Spitze innerhalb
6 × Release wieder innerhalb 0,1 dB.

## 9. Gemeinsame Hüllkurven-Implementierung (`voicechain/dsp/envelope.py`)

Die Dynamikstufen glätten ihre Steuergröße im dB-Bereich mit einer entkoppelten Struktur:
`y1[n] = max(x[n], a·y1[n−1] + (1−a)·x[n])` (Release-Abfall) gefolgt von einem Einpol-Attack. Die
Release-Rekursion enthält ein `max` und ist deshalb kein lineares Filter; `scipy.signal.lfilter`
kann sie nicht direkt rechnen. Statt einer Python-Schleife über Samples wird sie geschlossen
vektorisiert: Mit `u = y1 − x` wird sie zur Lindley-Rekursion `u[n] = max(0, a·(u[n−1] + x[n−1] −
x[n]))`, deren Lösung eine kumulierte Summe minus ihr laufendes Minimum ist (`numpy.cumsum`,
`numpy.minimum.accumulate`). Der Faktor `a^−n` wird in Chunks von 256 Samples gehalten, damit er
im float64-Bereich bleibt; gegenüber der Sample-Schleife beträgt die Abweichung < 1e-13 (geprüft).
Eine Folge der Struktur: Der Einpol-Attack läuft auch dem Release nach, der gemessene 63-%-Punkt
des Release liegt deshalb bei etwa `release + attack` (Kompressor: 110 ms statt 100 ms, innerhalb
der Toleranz von ±30 %).

Hold (Expander) ist ein kausales gleitendes Maximum der "Gate offen"-Flagge
(`scipy.ndimage.maximum_filter1d` mit Schwanzpuffer), der RMS-Detektor und der Limiter-Attack sind gleitende Mittelwerte per
kumulierter Summe. Gain-Parameter (Input Trim, Makeup) werden über 10 ms linear gerampt,
Filterkoeffizienten (HPF, EQ, De-Esser-Bandpass) über 10 ms zwischen altem und neuem Filter
überblendet, beide mit fortgeführtem Zustand. Laufzeit des Glätters: ~16 µs je 256er-Block.

## 10. Konfigurationsdatei und Speichern

`voicechain.config.load_config` liest und validiert streng (unbekannte, fehlende, falsch typisierte
und außerhalb des Bereichs liegende Werte werden gesammelt und mit vollem Schlüsselpfad gemeldet).
`save_config` schreibt über `yaml.safe_dump`; **Kommentare der Quelldatei gehen dabei verloren**
(PyYAML erhält sie nicht). Das ist für erzeugte Konfigurationen (Kalibrierungsausgabe) akzeptiert;
die kommentierte Referenz `config/chain.default.yaml` wird von `save_config` nie überschrieben.
Laden → Speichern → Laden liefert ein identisches Dictionary (Test).

## 11. Offene Punkte und Risiken

- Die Startwerte sind Erfahrungswerte für das NT-USB; die Kalibrierung mit einem echten
  Sprachsample ist der eigentliche Abgleich.
- Der De-Esser reagiert auf Spitzen im Band; bei sehr hellen Stimmen kann die Kalibrierung
  `freq_hz` und `threshold_db` nachführen müssen.
- Der Normalizer regelt die gefensterte Lautheit; bei sehr vielen kurzen Pausen liegt die aktive
  Sprache etwas über dem Ziel (Chain-Test: Short-Term ±2 LU erreicht).
- True-Peak-Sicherheit beruht auf der Marge, nicht auf überabgetasteter Detektion (siehe 8).
- Zusammenspiel mit der Kalibrierung: Setzt `calibrate.recommend` die Kompressorschwelle auf den
  Sprach-RMS, senkt der Kompressor den Sprach-RMS um ~6 dB; von den ~12 dB Expander-Absenkung
  (8 dB Abstand zum Rauschteppich, Ratio 2,5) bleiben dann relativ zur Sprache ~6 dB übrig
  (Selftest misst 5,8 dB). Mehr Abstand (10 dB) oder Ratio 3 bringt die geforderten ≥ 6 dB
  mit Reserve; das ist eine Entscheidung der Kalibrierung, nicht der Stufe.
