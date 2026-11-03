# Parameter-Begründung: DSP-Kette voicechain

Stand: 2026-11-03 (nach Prüfrunde 4, ergänzt um Live-Bedienung: Tilt und Bypass) · Gilt für `config/chain.default.yaml` und die Umsetzung in `voicechain/dsp/`.

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
(Echtzeitfaktor 0,08). Anteile je Stufe (µs/Block): Input Trim 7, HPF 19, Expander 83, De-Esser 74,
EQ 19, Kompressor 45, Normalizer 61, Limiter 43. Keine Python-Schleife läuft über einzelne Samples;
die nichtlinearen Hüllkurven werden geschlossen vektorisiert gerechnet (siehe Abschnitt 9). Auf
einem M-Chip ist deutlich weniger zu erwarten; das Budget < 1 ms wird auch hier eingehalten.

**Meter und Robustheit (Prüfbefunde PO-5, SW-1):** Expander, De-Esser, Kompressor und Limiter
liefern in `meters()` neben `gain_reduction_db` (Mittel des letzten Blocks) auch
`gain_reduction_mean_db` und `gain_reduction_max_db` als Mittel bzw. Maximum **seit dem letzten
Auslesen**; das Auslesen (`Chain.get_meters()`) leert den Akkumulator. Für Statuszeile und
Zustandsabfragen gibt es `Chain.peek_meters()` (= `get_meters(drain=False)`): dieselbe Struktur
mit den laufenden Akkumulatorwerten, ohne sie zu leeren – nur die Regelung liest leerend. Die adaptive Regelung sieht
damit alle 2 s das ganze Intervall und nicht einen 5-ms-Schnappschuss. `Chain.process` prüft jeden
Block auf NaN/Inf: ein nicht-finiter Block wird durch Stille ersetzt, die Signalpfad-Zustände
aller Stufen werden geleert (`recover()`) und `get_meters()["input"]["nonfinite_blocks"]` zählt das
Ereignis. Der Normalizer behält dabei Gain, Sprachreferenz und Teppich-Schätzer – endliche
Skalare, die ein defektes Sample nicht vergiften kann; ein Reset des Gains hätte 15 s falsche
Lautstärke gekostet. Test: ein NaN-Sample im eingeschwungenen Zustand → Gain unverändert
(12,0 → 12,0 dB), Lautheit 1 s danach innerhalb 2 LU. Alle Pegelmeter benutzen denselben
Boden `voicechain.dsp.DB_FLOOR_DBFS = −150 dBFS` (6 dB unter dem Quantisierungsrauschen eines
24-Bit-Wandlers), damit Stille endliche Zahlen liefert und Stufen-, Analyse- und Statuswerte
vergleichbar sind.

**Bypass (A/B-Vergleich, `Chain.set_bypass(on)` / `get_bypass()`):** Im Bypass liefert die Kette nur
Input Trim → Limiter: der Trim, damit beide Pfade dieselbe Pegelreferenz haben, der Limiter, damit
auch rohe Sprache spitzenpegelsicher bleibt. Der Übergang ist ein linearer Crossfade von 30 ms
zwischen dem Ausgang der mittleren Stufen (HPF … Normalizer) und dem Trim-Signal, danach läuft der
gemeinsame Limiter. Die mittleren Stufen rechnen auch im Bypass weiter (gleiche Rechenzeit wie im
Normalbetrieb): So bleiben Filterzustände, Expander/Kompressor-Hüllkurven und das Gelernte des
Normalizers aktuell, und die Rückkehr hat keinen Einschwingknack – nach dem Zurückschalten ist der
Ausgang identisch mit dem einer nie unterbrochenen Kette (Test: < 1e-3). Meter im Bypass: die
mittleren Stufen melden `gain_reduction_*` = 0 und `bypassed` = 1 (das hört die Gegenseite),
`get_meters()["chain"]` = {`bypass` (0/1, angefordert), `bypass_mix` (Blendposition 0…1),
`processing` (0/1, Kill-Switch), `processing_mix` (Blendposition, 1 = aus), `latency_samples`}. Messwerte (Tests): Bypass-Ausgang bitgleich mit einer Referenz aus Trim +
Limiter nach der Blende; Umschalten auf einem 1-kHz-Ton ohne Blockpegelsprung > 0,5 dB
(gemessen 0,25 dB bei 1 dB Pfadunterschied) und ohne Transiente; die Blende dauert genau drei
480er-Blöcke. Die 30 ms sind lang genug, dass ein Pegelunterschied von einigen dB zwischen den
Pfaden ohne Knacken vergeht, und kurz genug, um als sofort zu gelten.

**Kill-Switch vs. Bypass (`Chain.set_processing(on)` / `get_processing()`):** Der Bypass ist zum
Hören gedacht (A/B-Vergleich): Die mittleren Stufen rechnen weiter, die Rückkehr ist bitgleich mit
einer nie unterbrochenen Kette. Der Kill-Switch ist zum Abschalten gedacht: Nach derselben
30-ms-Blende auf das Trim-Signal werden die mittleren Stufen (HPF … Normalizer) **nicht mehr
aufgerufen**; nur Input Trim und Limiter laufen (Sprache bleibt pegelsicher), Analyzer und Regelung
pausieren. Gemessen: Rechenzeit je 256er-Block im Aus-Zustand 0,07 ms statt 0,44 ms, also ~16 % des
Normalbetriebs (Median über 1000 Blöcke; Soll < 25 %). Beim Wiedereinschalten werden die mittleren Stufen wie bei der
NaN-Erholung zurückgesetzt (`recover()`: Filter- und Detektorzustände frisch, der Normalizer behält
Gain, Sprachreferenz und Teppich) und dann 30 ms eingeblendet. Hörbild beim Wiedereinschalten: Die
Einschwingvorgänge der frischen Filter (Hochpass, EQ) und der von null startenden Kompressor-
Reduktion fallen in die Blende; danach liegt die Lautstärke sofort wieder auf dem gelernten Wert,
weil der Normalizer-Gain erhalten bleibt – kein Hochregeln über Sekunden, kein Knack (Test: Spitze
der ersten 100 ms nach der Blende ≤ eingeschwungene Spitze + 0,5 dB, kein Blockpegelsprung
> 0,5 dB). Verarbeitung aus hat Vorrang vor dem Bypass: Beide Zustände sind kombinierbar, im
Aus-Zustand ist der Ausgang in jedem Fall Trim → Limiter. Meter:
`get_meters()["chain"]["processing"]` (0/1), `processing_mix` (Blendposition, 1 = aus); im
Aus-Zustand melden die mittleren Stufen `gain_reduction_*` = 0 und `bypassed` = 1. Wann welches:
Bypass, um zu prüfen, was die Kette tut; Kill-Switch, wenn die Kette für längere Zeit nicht
arbeiten soll (Prozessorlast, Batterie, fremde Quelle am Mikrofon).

**Parameteränderungen im Betrieb:** `set_param` validiert den Wert und entwirft die neuen
Koeffizienten *außerhalb* des Ketten-Locks; nur der Tausch läuft unter dem Lock (Mikrosekunden).
Schlägt Validierung oder Entwurf fehl, bleibt der alte Wert vollständig erhalten (Test:
`HighPass.set_param("order", 4.5)` → Fehler, Ordnung bleibt 4, Ausgang identisch). Zwei
Parameterwechsel sind **nicht knackfrei**, weil sich die Struktur ändert: `highpass.order` (die
Zahl der Filtersektionen wechselt, der Zustand wird genullt; Transient ≈ −45 dBFS bei einem
−14-dBFS-Sinus) und `limiter.lookahead_ms` (Latenz ändert sich). Beide gehören nicht zum adaptiven
Pfad und werden nur beim Einrichten gesetzt. `process`
hält das Lock für die Dauer eines Blocks (~0,4 ms von 5,3 ms Blockperiode). Ein Test lässt einen
Thread im Dauerfeuer Parameter setzen (inklusive Ordnungswechsel des HPF), während ein anderer
verarbeitet; ohne Lock entstehen dabei Formfehler, mit Lock keine.

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
| `order` | 4 | 1–8 (ganzzahlig) | 24 dB/Okt: 16,4 dB Dämpfung bei 50 Hz, 24 dB bei 40 Hz, 48 dB bei 20 Hz; 1 kHz bleibt unverändert (< 0,01 dB). Ein Wechsel im Betrieb ist nicht knackfrei (Sektionszahl ändert sich, Zustand wird genullt) – nur beim Einrichten ändern. |

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
| `threshold_db` | −45.0 | −90…0 (adaptiv −70…−30) | Rauschteppich eines ruhigen Büros mit NT-USB liegt bei etwa −55 dBFS; +10 dB Abstand, damit Rauschspitzen die Schwelle nicht erreichen und die Kennlinie am Teppich voll greift: 10 dB unter der Schwelle ergeben mit Ratio 2,5 und Knie 6 eine Absenkung von 15 dB (Knie wirkt nur ±3 dB um die Schwelle). Die Kalibrierung setzt Rauschteppich + 10 dB (Entscheidung PL 2026-11-03). |
| `ratio` | 2.5 | 1–10 | Unterhalb der Schwelle wird jedes dB zu 2,5 dB: 10 dB unter der Schwelle → 15 dB leiser. Sanfter als ein Gate (∞), aber hörbar wirksam. |
| `range_db` | 20.0 | 0–60 | Maximale Absenkung. Mehr als 20 dB klingt wie Ein-/Ausschalten des Raums ("Atmen" der Kette); 20 dB reicht, um Lüfter unter die Wahrnehmungsschwelle der Gegenseite zu bringen. |
| `attack_ms` | 5.0 | 0.1–500 | Zeit zum Öffnen (63 %). 5 ms + 10 ms Detektorfenster: der erste Konsonant einer Silbe kommt ungekappt durch. |
| `hold_ms` | 100.0 | 0–1000 | Überbrückt die 80–150 ms zwischen Wörtern, nicht nur Silbenpausen; verhindert Flattern ("Chattering"). Von 50 ms auf 100 ms gesetzt nach PO-Hörurteil ("lieber 100 ms mehr Raum als abgebissene Wortenden bei langsamen Sprechern"). Test: ein 20-ms-Loch im Signal verändert den Gain nicht, nach Signalende bleibt der Gain 100 ms bei 0 dB. |
| `release_ms` | 250.0 | 1–5000 | Zeitkonstante (63 %) des Schließens nach Hold und Detektorfenster; ~90 % der Absenkung nach 2,5 × release (gemessen −13,8 dB von −15 nach 735 ms). Von 150 ms auf 250 ms gesetzt (PO): Wortenden langsamer Sprecher bleiben vollständig, Pausen ab ~1 s werden trotzdem deutlich ruhiger. |
| `knee_db` | 6.0 | 0–24 | Weicher Übergang ±3 dB um die Schwelle: leise Silben und Atmer werden graduell leiser statt geschaltet. |
| `detector_ms` | 10.0 (optional, nicht in der YAML) | 0.5–100 | RMS-Fenster ≈ eine Periode der tiefsten Männerstimme (85 Hz = 11,8 ms), sodass der Pegel nicht mit der Tonhöhe wellt (< 1 dB), aber innerhalb einer Silbe reagiert. Ein Rechteckfenster "vergisst" lautes Signal vollständig nach 10 ms; ein Einpol-Mittler bräuchte für 25 dB Abfall 58 ms und würde das Schließen verzögern. |

Hörbild: Schwelle zu tief / Ratio zu klein → Rauschen bleibt in Pausen; Schwelle zu hoch → leise
Silbenenden und Atmer werden "abgeschnitten", Sprache wirkt abgehackt; Hold/Release zu kurz →
Flattern zwischen Wörtern; Release zu lang → Rauschen "fährt" hörbar hoch und runter.

Messwerte (Tests): stationäres Rauschen −55 dBFS → 12–20 dB leiser (statisch 15 dB, gemessen 14,2 dB
wegen der Pegelschwankung des 10-ms-Fensters); Sprache −20 dBFS → < 1 dB Änderung; in 1,5-s-Pausen
sind die letzten 0,5 s ≥ 12 dB leiser (gemessen 14,2 dB), die ganze Pause im Mittel ≥ 6 dB (7,5 dB).

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
| `ratio` | 4.0 | 1,1–20 | Kräftig genug für hörbare Wirkung; 15 dB über der Schwelle werden 11 dB gewünscht, begrenzt durch `max_reduction_db`. Hartes Knie, damit der De-Esser erst oberhalb der Schwelle entschieden eingreift. |
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
raus, Präsenz rein. Kaskade aus RBJ-Biquads (Low-Shelf, Peak, High-Shelf), Anzahl und
Typ der Bänder stehen mit der Konfiguration fest; Gain, Frequenz und Q sind zur Laufzeit
änderbar (`set_param("eq", "bands.<i>.<feld>", wert)`), Koeffizientenwechsel werden über 10 ms
zwischen altem und neuem Filter überblendet.

| Band | Typ | `freq_hz` | `gain_db` | `q` | Begründung |
|---|---|---|---|---|---|
| 0 | peak | 220 | −2.0 | 1.0 | Nahbesprechungseffekt der Niere bei 15–25 cm hebt 150–300 Hz an ("Mulm", "Brustigkeit"). −2 dB bei Q 1 (Bandbreite ~1,4 Okt) nimmt das weg, ohne die Stimme dünn zu machen. |
| 1 | peak | 3200 | +2.0 | 1.2 | Präsenzbereich 2,5–4 kHz trägt Konsonanten und damit Verständlichkeit; +2 dB reicht, mehr wird nach dem Codec hart. |
| 2 | high_shelf | 10000 | 0.0 | 0.707 | Ursprünglich +1,5 dB "Luft"; nach PO-Urteil auf 0 dB gesetzt: Das NT-USB ist oben herum bereits hell, Zoom/Teams übertragen Sprache codecbedingt höhenbetont, und ein Boost oberhalb des De-Esser-Bands (4,6–9,2 kHz) hebt vor allem Preamp-Rauschen und Restzischen an. Das Band bleibt angelegt, damit die Kalibrierung dort absenken kann (nur Cut). |

Bereiche je Band: `freq_hz` 20 Hz bis unterhalb fs/2, `gain_db` −24…+24, `q` 0.1–20. Alle
Startwerte bleiben bescheiden (≤ 2 dB), weil ein Konferenzsystem keine Hi-Fi-Wiedergabe ist und
starke EQ-Eingriffe nach dem Codec unnatürlich klingen. Bänder mit 0 dB sind bitgenau transparent.

Hörbild: Band 0 zu stark → dünn, "Telefon"; zu schwach → mulmig, undeutlich. Band 1 zu stark →
nasal, aggressiv, Zischen nimmt zu; zu schwach → "hinter einem Vorhang". Band 2 (High-Shelf) angehoben →
Rauschen und Zischen werden hörbar; abgesenkt → bei sehr hellen Mikrofonpositionen angenehmer, zu
stark → dumpf.

Messwerte (Tests): Zeitbereichsmessung je Band stimmt mit Soll ±0,5 dB überein (analytisch ±0,1 dB),
Nullgain-Bänder (auch der 0-dB-Shelf der Startkonfiguration) sind exakt transparent.

**Klang-Tilt (`eq.tilt_db`, Live-Bedienung „wärmer / heller“):** Im Meeting korrigiert man die
Klangfarbe mit einem Griff, nicht mit drei Bändern. Der Tilt sind zwei feste zusätzliche Shelves
hinter den konfigurierten Bändern: Low-Shelf 250 Hz mit −tilt/2 dB und High-Shelf 4 kHz mit
+tilt/2 dB (Q 0,707). Warum diese Eckfrequenzen: Unter 250 Hz liegen Grundton und „Brust“ einer
Stimme, über 4 kHz Konsonanten und „Luft“; dazwischen, um 1 kHz, sitzt die Sprachverständlichkeit,
und genau dort bleibt der Tilt bei 0 dB (Drehpunkt). Positiv = heller, negativ = wärmer.

| Parameter | Startwert | Bereich | Begründung |
|---|---|---|---|
| `tilt_db` | 0.0 (optional) | −3…+3 | ±3 dB gesamt (±1,5 dB je Shelf) ist das Meiste, was eine Meeting-Stimme verträgt, ohne „falsch“ zu klingen; alles darüber gehört in die kalibrierten Bänder. Schrittweite der Konsole 1 dB. |

Hörbild: +1…+2 → präsenter, „näher am Mikrofon“, bei zu viel spitz und zischig (der De-Esser
sitzt davor und sieht die Anhebung nicht); −1…−2 → runder, „Radio-Stimme“, bei zu viel dumpf
und verhangen. Messwerte (Tests): Tilt +2 → −0,98 dB bei 100 Hz, −0,5 dB bei 250 Hz, 0,0 dB bei
1 kHz, +0,5 dB bei 4 kHz, +0,96 dB bei 8 kHz; Tilt 0 ist bitgenau transparent (beide Sektionen
sind dann exakte Identität und bleiben in der Kaskade, damit ein Wechsel die Sektionszahl nicht
ändert); Umschalten 0 → +3 → −3 auf einem 1-kHz-Ton ohne Blockpegelsprung > 0,5 dB und ohne
Transiente (Crossfade 10 ms wie bei allen Filterwechseln). Zugriff: `set_param("eq", "tilt_db", v)`,
Meter `eq.tilt_db`.

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
| `ratio` | 3.0 | 1,1–20 | 3:1 ist der Sprachstandard: hörbar gleichmäßiger, noch natürlich. 2:1 wirkt kaum, > 4:1 klingt "gepresst". |
| `knee_db` | 6.0 | 0–24 | Weiches Knie ±3 dB: der Einsatz ist nicht hörbar; am Schwellenpunkt beträgt die Reduktion 0,5 dB. |
| `attack_ms` | 10.0 | 0.1–500 | Lässt die Transienten von Konsonanten durch (Verständlichkeit) und fängt Silbenspitzen trotzdem ein. |
| `release_ms` | 100.0 | 1–5000 | Folgt dem Silbenrhythmus (~4/s), pumpt nicht. Gemessener 63-%-Punkt ~110 ms, weil die Attack-Glättung dem Release nachläuft (siehe Abschnitt 9). |
| `makeup_db` | 0.0 | −12…+24 | Startwert der unkalibrierten Datei. Die Kalibrierung setzt das Makeup auf die statische Reduktion an den gemessenen Sprachspitzen (typisch 4–7 dB, begrenzt auf 0…12 dB), damit der Pegel nach dem Kompressor dem vor dem Kompressor entspricht und der Normalizer nur noch die langsame Feinregelung übernimmt. Makeup ist statisch (10-ms-Rampe bei Änderung), der Normalizer dynamisch; sie arbeiten deshalb nicht gegeneinander. |

Hörbild: Schwelle zu tief / Ratio zu hoch → "gepresst", Atmer und Raum werden hochgezogen,
Rauschteppich steigt relativ; zu wenig → ungleichmäßige Lautstärke, der Normalizer muss ständig
nachführen; Attack zu kurz → Konsonanten werden stumpf; Release zu kurz → Verzerrung bei tiefen
Stimmen, zu lang → Pumpen.

Messwerte (Tests): Sinus −10 dBFS → −16,67 dBFS ± 0,3 dB (Ratio 3, Knie 6); Sinus an der Schwelle →
−20,5 dBFS (Knie); Attack-63-%-Punkt 10 ms ± 30 %, Release 100 ms ± 30 % (gemessen 10,1 / 110 ms).

## 7. Loudness Normalizer (`chain.normalizer`)

**Zweck:** Langsame automatische Verstärkungsregelung (AGC) auf eine Ziel-Lautheit, damit die
anderen Teilnehmenden unabhängig von Sprechabstand und Tagesform einen gleichmäßigen Pegel hören.

**Aufbau:** K-Bewertung (BS.1770: Pre-Filter + RLB, lokal in `biquad.k_weighting_sos`
entworfen, reproduziert die 48-kHz-Tabelle des Standards auf 1e-9) → Quadrat → Einpol-Mittler mit
τ = 0,2 s (varianzäquivalent zum 400-ms-Rechteckfenster der **Momentary**-Lautheit), mit
Bias-Korrektur ab dem ersten Sample → Momentary-Lautheit L_m = −0,691 + 10·log10(Mittelwert) LUFS.
Alle 10 ms (absolutes Sampleraster, deshalb blockgrößenunabhängig) wird entschieden:

1. **Teppich-Schätzer** L_T: Beleg für den Raumteppich ist ein *Plateau*, d. h. 0,5 s
   10-ms-Intervall-Lautheiten mit einer Spanne ≤ 3 LU. Stationäres Rauschen ist flach (am
   Normalizer-Eingang gemessen: weiß 1,0–1,9 LU, rosa im Median 2,7 LU), Sprache nie: selbst das
   synthetische Testsignal mit nur 6 dB Silbenmodulation liegt nach dem Kompressor noch bei 4,4 LU,
   echte Sprache weit darüber. L_T ist das Minimum der Plateau-Pegel der letzten 10 s (Sätze
   dauern 2–5 s, ein hörbarer Raum zeigt sich in jeder Pause ≥ 0,6 s). Nach unten folgt er
   sofort, nach oben mit höchstens 1 LU/s (ein Lüfter ist in ~10–20 s nachgeführt); ist der
   Schätzwert ein ganzes Fenster lang von keinem Plateau bestätigt, wird das Fenster-Minimum
   übernommen. **Ohne Plateau – ein Sprecher, der ohne Pausen spricht – behält L_T seinen letzten
   Wert**; dichter Vortrag (Test: 1,8 s Sprache / 0,2 s Lücke, 10 % Pausen) kann deshalb nie als
   lauter Raum gelten. Der Schätzer wird nicht an die Sprachlautheit geklemmt (ein zu naher
   Teppich wird nicht in einen tieferen umgedeutet, siehe Punkt 3) und überlebt die
   NaN-Erholung (`recover()`). Gate, Deckel und Punkt 3 nutzen L_T erst, nachdem modulierte
   Sprache gelernt wurde – ein stationärer Testton ist selbst ein Plateau und würde sich sonst
   selbst gaten.
2. **Wirksames Gate** G = max(`gate_lufs`, L_T + `gate_margin_lu`), sobald Sprache gelernt ist
   (davor nur `gate_lufs`, damit auch ein Signal ohne Pausen die Regelung starten kann).
3. **Was als Sprache gelernt wird:** Die Sprachreferenz L_s wird nur aus Intervallen gelernt, die
   (a) in einem *modulierten* Signal liegen – Spanne der 10-ms-Intervall-Lautheit innerhalb der
   letzten 2 s ≥ 6 LU (stationäres Rauschen: weiß < 2 LU, rosa < 4 LU; Sprache ≥ 7 LU selbst beim
   glattesten Testsignal, typisch 15–30 LU; die 400-ms-Momentary würde die Silbenmodulation auf
   ~1 LU glätten, deshalb die Intervall-Lautheit) – und (b), sobald ein Teppich bekannt ist,
   mindestens `gate_margin_lu` + 5 LU = 13 LU über L_T liegen (ein Raumplateau schwankt ≤ 3 LU,
   13 LU darüber ist nie der Raum). Vorlaufrauschen vor dem ersten Wort kann so nicht zur
   Referenz werden, und ohne Referenz wird der Gain gehalten (Test: 10 s weißes Rauschen −38 dBFS,
   20 LU über dem Gate → Gain 0,00 dB, Referenz leer; erst die Sprache danach wird gelernt). Die
   5 LU statt der zunächst vorgesehenen 10 LU: Mit 18 LU Abstand würde das Lernen genau am
   Betriebspunkt eines 5–8 dB lauteren Raums (Sprache 15–17 LU über dem Teppich) enden und ein
   solcher Raum dauerhaft ungeregelt bleiben (gemessen: Raum −45 dBFS, Gain 0,00 statt 2,6 dB).
4. **Zustand `low_snr` (Störabstand zu gering):** Liegt L_T weniger als 13 LU unter L_s, kann kein
   Gate Pausen von Sprache trennen, ohne Sprache abzuschneiden. Der Normalizer hält das Gate auf
   L_T + `gate_margin_lu`, lernt Referenz und Gain weiter – aber ausschließlich aus den sicheren
   Sprachintervallen (Punkt 3 b), Pausen liegen darunter und können den Gain nicht hochziehen –
   und setzt den Pausen-Deckel (Punkt 7) außer Kraft; es gilt nur `max_gain_db`, sonst bliebe die
   Sprache im lauten Raum dauerhaft zu leise. Der Zustand endet mit Hysterese, wenn L_T wieder
   ≥ 15 LU unter L_s liegt (2 LU gegen Flattern): Ein Raum mit 15 LU Abstand erfüllt §6.4 a und
   darf nicht als „Störabstand zu gering“ gemeldet werden – mit dem früheren Fenster 15/17 LU
   rastete der Zustand im +8-dB-Rosa-Raum (15,5–16,5 LU) ein und erzeugte alle 30 s einen
   Fehlalarm. Durch das Weiterlernen ist der Zustand kein Riegel mehr. Ein leiser gewordener
   Sprecher wird darin nur so lange nachgeführt, wie seine Sprache ≥ Teppich + 13 LU liegt
   (Punkt 3 b): im +8-dB-Raum ja bei −6 dB (Ausgang −19,3 LUFS), nein bei −10 dB – dann bleibt
   der Gain stehen; das ist ein Neukalibrierfall oder ein Fall für einen leiseren Raum. Meter: `low_snr` (0/1) und `snr_lu` (L_s − L_T, ∞ solange unbekannt); die
   Regelung protokolliert daraus das Ereignis „Störabstand zu gering“, die Statuszeile zeigt es.
   Test: Teppich −50 → −36 → −39 → −41 → −50 dBFS unter Sprache −20 dBFS: Eintritt bei ~10 LU,
   bleibt im Hysteresefenster 13–15 LU, Austritt ab 15 LU; Pausen heben den Gain nicht
   (≤ 0,5 dB), Referenz bleibt innerhalb 1,5 LU der des ruhigen Raums.
5. **Absolutes Gate:** L_m < G → Pause, der Gain wird eingefroren.
6. **Relatives Gate (wie BS.1770):** L_m < Sprachlautheit − 10 LU → ebenfalls eingefroren. Die
   Sprachlautheit L_s ist die exponentiell geglättete Lautheit (τ = `window_s`/2, entspricht dem
   3-s-Short-Term-Fenster) nur der Intervalle, die beide Gates passieren **und nicht fallen** – also
   die Lautheit der Sprache selbst, nicht der Pausen und nicht der Abklingvorgänge in Pausen hinein.
   Liegt ein steigendes oder stetiges Intervall über G, aber mehr als 10 LU unter L_s (ein
   leiser gewordener Sprecher; Abklingvorgänge in Pausen zählen nicht), klingt L_s mit 2 LU/s zur
   Momentary-Lautheit hin ab, nie unter G + 10 LU: ein Sprecher, der 20 LU leiser wird (größerer
   Abstand, zweiter Sprecher), ist nach ~10–20 s neu referenziert statt dauerhaft eingefroren.
   Auch das Abklingen gilt nur für sichere Sprachintervalle (Punkt 3).
7. **Gain-Ziel** = `clip(target_lufs − L_s, min_gain_db, min(max_gain_db, target_lufs − 15 LU − L_T))`
   (Deckel nur außerhalb von `low_snr`): der Gain darf einen gatebaren Teppich nie näher als 15 LU
   an das Ziel heben (Beauftragung §6.4). Intervalle, die nicht sicher Sprache sind, dürfen den
   Gain nicht anheben (Ziel ≤ aktueller Gain).
8. **Fallende Hüllkurve:** Fällt L_m schneller als 10 LU/s und liegt unter L_s (Wort- oder
   Satzende), darf das Ziel nicht über dem aktuellen Gain liegen – der Gain steigt nie auf einer
   abklingenden Hüllkurve.
9. **Asymmetrische Rate:** Der angewandte Gain läuft je Sample mit höchstens `rate_db_per_s`
   aufwärts und `release_rate_db_per_s` abwärts auf das Ziel zu.

Die Regelung ist feedforward (sie misst ihren eigenen Eingang) und kann daher nicht schwingen.
Warum Momentary statt Short-Term für das Gate: Mit dem Gate auf der geglätteten Lautheit blieb es
nach Satzende ~7 s offen, der Gain lief mit 1 dB/s hoch, Pausenrauschen wurde bis 12 dB lauter und
der nächste Satz setzte zu laut ein. Der 400-ms-Detektor schließt das Gate innerhalb von 0,1–0,5 s
nach dem letzten Wort. Warum ein eigener Teppich-Schätzer: Das kalibrierte `gate_lufs` gilt für den
Raum zur Kalibrierzeit; wird der Raum danach lauter (Lüfter, Fenster), läge der Teppich über dem
Gate und würde als Sprache gelernt – das relative Gate rutscht dann mit bis zum Teppich. Mit dem
Schätzer hängt der Schutz nicht an der Kalibrierung.

**Raumänderung im Betrieb – systemtreu gemessen (Beauftragung §6.4 a/b):** Kette kalibriert auf
einen Teppich von −50 dBFS (Sprache −26 dBFS, Trim +6, Makeup 6, Gate −40), danach Teppich −42
(+8 dB) bzw. −38 dBFS (+12 dB), 30 s Sprache / 20 s Pause / 20 s Sprache, Expander-Schwelle so,
wie die Regelung sie setzt: min(Teppich + 10, Sprache − 12) nach Trim = −32 dBFS (die Sprach-Kappe
greift in beiden Fällen). Rosa Rauschen ist Standard-Rosa (−3 dB/Okt von 20 Hz bis 20 kHz; das
frühere 1/√f-Rauschen ab FFT-Bin 1 verlor 3 dB im 80-Hz-Hochpass und las 1,7 LU leiser), weißes
Rauschen ist der härtere Fall für die K-Bewertung (Chain-Test `test_room_gets_louder_after_calibration`):

| Fall | Abstand Sprache−Pause Eingang → Ausgang | Gain-Anstieg in der Pause | Einsatz nächster Satz | `low_snr` in der Pause | Sprache am Ausgang |
|---|---|---|---|---|---|
| +8 dB rosa | 12,3 → 12,5 LU | 0,00 dB | +0,04 dB | ja | −18,8 LUFS |
| +8 dB weiß | 10,1 → 9,8 LU | 0,14 dB | +0,30 dB | ja | −18,8 LUFS |
| +12 dB rosa | 8,7 → 4,3 LU | 0,12 dB | +0,34 dB | ja | −18,2 LUFS |
| +12 dB weiß | 6,7 → 2,2 LU | 0,16 dB | +0,52 dB | ja | −18,0 LUFS |

Lesart: Der Normalizer hält in allen Fällen §6.4 b ein – Gain-Anstieg in der 20-s-Pause ≤ 0,2 dB
(statt bis zu 12 dB im Fehlerbild PO-1), nächster Satz ≤ +0,5 dB lauter, `low_snr` gemeldet,
Sprache auf dem Ziel, Pause nie auf Sprachlautheit (schlechtester Fall 2,2 LU Abstand). Der
Abstand Sprache−Pause schrumpft nach der Nachführung um höchstens 5 LU (gemessen +0,2 / −0,3 /
−4,4 / −4,5 LU), weil der Kompressor (Schwelle auf dem Sprach-RMS) die Sprache um 3–6 dB
verdichtet, während der an der Sprach-Kappe stehende Expander den Teppich nur um 1–6 dB
absenkt; vor der Nachführung (Expander noch auf dem alten Teppich) sind es bis zu 7 LU. Das ist
eine Eigenschaft von Kompressor plus gekappter Expander-Schwelle, nicht des Normalizers. §6.4 a
(Pause ≤ Sprache − 15 LU bei bis zu +8 dB) wird in diesem synthetischen Szenario mit
Standard-Rosa nicht erreicht: Der Eingangsabstand beträgt dort nur 12,3 LU (Sprache −26 dBFS,
Teppich −42 dBFS), und die Kette gewinnt netto 0,2 LU; mit dem früheren tieffrequenzlastigen
Rosa lagen Eingang 15,5 und Ausgang 20,9 LU. Ob reale Lüftergeräusche näher am einen oder
anderen Spektrum liegen, entscheidet der Hörtest am Arbeitsplatz. Der Teppich-Schätzer folgt
einem Raum, der um 10 dB lauter wird, innerhalb von ~20 s (gemessen −50 → −41,6 LUFS, Soll −41,9).

**Vorlaufrauschen und leiser werdender Sprecher (System):** Beginnt der Stream mit 3 s oder 10 s
Rauschen vor dem ersten Wort (weiß, −55/−50/−45 dBFS, kalibrierte −50-Kette), steigt der Gain im
Vorlauf um 0,00 dB, `low_snr` bleibt in den Räumen −55/−50 bei 0,0 % über 180 s, und die aktive
Sprache liegt 30 s nach dem ersten Wort bei −16,7 LUFS (−45: −17,0 LUFS, `low_snr` 94–98 %, Gain
regelt trotzdem auf 2,6 dB). Wird der Sprecher im +8-dB-Rosa-Raum nach der Nachführung um 6 dB
leiser, liegt der Ausgang 30–40 s danach bei −19,3 LUFS (−16,8 davor): der Kompressor fängt den
Großteil ab; die Sprache des leiseren Sprechers liegt dort nur ~13 LU über dem Teppich, sodass
der Normalizer sie nicht mehr als sichere Sprache lernt und den Gain bei 3,4 dB hält – innerhalb
der Toleranz, aber ohne Regelung; ein Raum mit Störabstand < 15 LU bleibt ein Fall für
Neukalibrierung oder leiseren Raum.

| Parameter | Startwert | Bereich | Begründung |
|---|---|---|---|
| `enabled` | true | – | Bei Bypass läuft die Messung weiter (Gate-Zustand, Sprachlautheit), der Gain bleibt stehen; Wiedereinschalten ohne Sprung. |
| `target_lufs` | −18.0 | −40…−6 | Konferenzsysteme und ihre automatische Pegelung erwarten Sprache um −18…−16 LUFS; −18 lässt 17 dB Crest-Faktor bis zum Limiter. Das Ziel gilt für die **gegatete Sprachlautheit** (BS.1770-Logik), nicht für 3-s-Fenster mit Pausen: Enthält ein Fenster 40 % Pause, liest ein ungegatetes Short-Term-Meter 2–3 LU weniger (Chain-Test: aktive Anteile −16,7, 3-s-Fenster −20,6 LUFS bei synthetischer Sprache mit 40 % Pause). **Mono-Konvention (PO-14):** Das Ziel wird am Mono-Signal gemessen; der Ausgang ist Dual-Mono auf BlackHole. Ein Stereo-Meter nach BS.1770 summiert beide Kanäle und zeigt **+3 LU**, also −15 LUFS – für den Mono-Sprachpfad von Zoom/Teams ohne Belang, beim Nachmessen einer Meeting-Aufzeichnung zu beachten. |
| `window_s` | 3.0 | 0.4–10 | Glättung der Sprachlautheit (Short-Term-Fenster nach BS.1770). Kürzer regelt Silben statt Sätze, länger reagiert träge auf einen Wechsel des Sprechabstands. Das Gate benutzt unabhängig davon das 400-ms-Momentary-Fenster. |
| `max_gain_db` | 12.0 | 0–30 | Mehr würde in leisen Passagen Rauschen und Raum hochziehen; 12 dB fängt einen deutlich zu leisen Sprecher auf. Die Kalibrierung leitet den Wert aus Sprachpegel und Rauschteppich ab und kann ihn zur Laufzeit per `set_param` ändern (Test: der neue Deckel greift sofort mit der Abwärtsrate). |
| `min_gain_db` | −12.0 | −30…0 | Schutz gegen "Reinbrüllen"; weiter unten würde der Limiter ohnehin arbeiten. |
| `rate_db_per_s` | 1.0 | 0.1–20 | Aufwärtsrate, so langsam wie eine Hand am Fader: keine hörbare Regelbewegung innerhalb eines Satzes. Bei stationärem Signal exakt 1 dB/s (Test); bei bewegter Sprache zählen Pausen und fallende Silbenhüllkurven nicht, die effektive Aufwärtsrate liegt dann bei etwa 0,4 dB/s (Chain-Test: 7,9 dB nach 20 s). Auf Systemebene (kalibrierte Kette, Sprecher wird um 10 dB leiser, z. B. Abstand 20 → 60 cm) ist die Lautheit nach 2 s noch −2,8 dB, nach 10 s −0,4 dB vom Ziel entfernt – der Kompressor nimmt den Großteil ab, die Rate ist dort nicht der begrenzende Faktor; 2 oder 3 dB/s liefern bis auf 0,7 dB identische Verläufe, erhöhen aber das Risiko, dass ein Fehlurteil des Gates hörbar wird. Ein Sprecher, der 20 dB leiser wird, ist kein Regel-, sondern ein Neukalibrierfall (`max_gain_db` und Expander begrenzen, nicht die Rate). |
| `release_rate_db_per_s` | 3.0 (optional, Default 3) | 0.1–30 | Abwärtsrate. Ein Satz, der 6 dB zu laut einsetzt, ist nach 2 s gezähmt, ohne dass die Bewegung als Pumpen auffällt; ein zu leiser Einsatz darf dagegen langsam kommen (PO: 1 dB/s hoch, 3 dB/s runter). |
| `gate_lufs` | −40.0 | −80…−20 | Konfiguriertes absolutes Gate auf die Momentary-Lautheit: darunter gilt "Pause", der Gain wird eingefroren, Stille und Rauschteppich werden nicht hochgezogen. 22 LU unter dem Ziel reicht für ruhige Räume; die Kalibrierung legt es auf Pausenpegel nach Kette + 8 dB. Wirksam ist immer max(`gate_lufs`, Teppich-Schätzer + `gate_margin_lu`), Meter `effective_gate_lufs`. |
| `gate_margin_lu` | 8.0 (optional, Default 8) | 0–30 | Abstand des wirksamen Gates über dem intern geschätzten Teppich. Die Schwankung eines stationären Raumteppichs im 400-ms-Fenster bleibt innerhalb ±3 LU, 8 LU gaten die Pause deshalb sicher, während leise Sprache 15 LU über dem Teppich noch passiert. Gleicher Wert wie der Kalibrier-Abstand des Gates. |

Hörbild: Rate zu hoch → hörbares Atmen/Pumpen auf Satzebene; Ziel zu hoch → Limiter arbeitet
dauerhaft; Gate zu tief → in langen Pausen fährt der Raum hoch (genau der Befund PO-1); Gate zu
hoch → leise Sprecher werden nicht geregelt; Abwärtsrate zu hoch → der erste Satz nach einer Pause
"duckt" hörbar weg.

**Position in der Kette (5b, nach Kompressor, vor Limiter):** Der Normalizer sieht das bereits
verdichtete Signal, dessen Lautheit stabiler ist als die der Rohsprache; die Regelung muss deshalb
weniger und seltener eingreifen. Vor dem Kompressor platziert würde er dessen Schwelle laufend
"verschieben" (die adaptive Regelung führt die Kompressorschwelle dem Sprachpegel nach – beide
würden gegeneinander arbeiten). Nach dem Limiter darf er nicht stehen, weil er sonst das Ceiling
wieder überschreiten könnte. Eine Konsequenz dieser Reihenfolge: Der Normalizer hebt in absoluten
Zahlen auch den Rauschteppich an (Pause am Ausgang = Pause am Eingang + Trim + Makeup +
Normalizer-Gain − Expander-Absenkung). Entscheidend für die Gegenseite ist der *Abstand* zwischen
Sprache und Rauschen (Beauftragung §6.3, Störabstandsgewinn), und den verbessert der Expander um
≥ 6 dB (Chain-Tests: 10. Perzentil der 100-ms-Rahmen in Pausen wie in der Kalibrierung, und die
20-s-Pause des PO-Szenarios).

Meter: `gain_db`, `loudness_lufs` (Sprachreferenz L_s), `momentary_lufs`, `noise_floor_lufs` (L_T),
`effective_gate_lufs` (G), `snr_lu` (L_s − L_T), `low_snr` (0/1), `gated`.

Messwerte (Tests): −30-LUFS-Sinus erreicht −18 ± 1 LU, Gain steigt mit exakt 1 dB/s (nach 5 s
5,0 dB), monoton; Rauschen −60 dBFS bleibt unverändert (Gate); `max_gain_db`/`min_gain_db` werden nie
überschritten; die Momentary-Schätzung stimmt nach 50 ms auf 0,1 LU (Bias-Korrektur). PO-1-Szenario
(30 s Sprache, 20 s Pause, 20 s Sprache; Teppich −50 dBFS mit Gate −40, Teppich −42 dBFS mit
kalibriertem Gate −30): Gain-Änderung in der Pause 0,000 dB, erster Satz danach +0,04 dB bzw.
+0,03 dB gegenüber dem letzten Satz davor. Relatives Gate: Teppich −45 dBFS (≈ −41,5 LUFS, über
einem absoluten Gate von −60) wird eingefroren, weil er 13 LU unter der Sprachlautheit liegt.
Fallende Hüllkurve (−30 dB/s): Gain steigt höchstens 0,25 dB (Detektorverzögerung) statt 2 dB.
Komplette kalibrierungsnahe Kette im selben Szenario: Störabstandsgewinn ≥ 6 dB, Gain in der Pause
unverändert, Satz danach ≤ +1 dB, Sprache bei −18 ± 2 dBFS RMS. Teppich-Schätzer: Pausen −55 dBFS →
−50 LUFS geschätzt (Soll −51,9, ±3 LU), nach Raumwechsel auf −45 dBFS innerhalb 25 s bei −41,6
(Soll −41,9). Sprecher 20 dB leiser, Stufe allein (−6 → −26 LUFS, Pausen 0,8 s): Referenz nach 20 s
um > 11 LU abgeklungen, aktive Sprache am Ausgang mit 2 dB/s Aufwärtsrate < 40 s wieder bei
−18 ± 2,5 – der Nachweis, dass die Referenz nicht mehr einrastet; die Systemzahlen für einen
leiser werdenden Sprecher stehen bei `rate_db_per_s`. Gain-Deckel: Teppich −42 dBFS bei `max_gain_db` 30 → Gain ≤ Ziel − 15 − L_T.

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
Top-Level-Schlüssel mit führendem Unterstrich (`_calibration` mit Messwerten und Begründungen der
Kalibrierung) sind Metadaten: nicht validiert, von `Chain.from_config` ignoriert, beim Speichern
erhalten. Optionale Stufenparameter (`expander.detector_ms`, `limiter.true_peak_margin_db`,
`normalizer.release_rate_db_per_s`) dürfen fehlen; dann gilt der im Code begründete Default. Die
Untergrenze von `deesser.ratio` und `compressor.ratio` ist 1,1, weil die Kalibrierung bei Ratio 1
durch null teilen würde (SW-7). Optionale Abschnitte: `control: {socket_path}` (Unix-Socket des Steuerkanals; in der
Default-YAML nur als auskommentiertes Beispiel, ohne Eintrag wählt die Runtime
`/tmp/voicechain-<user>.sock`, weil macOS Socket-Pfade auf 103 Byte begrenzt) und `eq.tilt_db`. `save_config` schreibt über `yaml.safe_dump`; **Kommentare der
Quelldatei gehen dabei verloren**
(PyYAML erhält sie nicht). Das ist für erzeugte Konfigurationen (Kalibrierungsausgabe) akzeptiert;
die kommentierte Referenz `config/chain.default.yaml` wird von `save_config` nie überschrieben.
Laden → Speichern → Laden liefert ein identisches Dictionary (Test).

## 11. Adaptive Parameter (`adaptive.*`)

Die Regelung selbst ist in `voicechain/adaptive.py` umgesetzt und in `docs/03_Betrieb_und_
Kalibrierung.md` (Abschnitt Regelung) beschrieben; hier stehen die Startwerte und ihre Wirkung
auf die Kette. Die Regelung verändert die drei Schwellen und – über die Limiter-Schutzregel –
`normalizer.max_gain_db`, alle innerhalb von `adaptive.bounds`; alle anderen Parameter bleiben,
wie Datei oder Kalibrierung sie gesetzt haben.

| Parameter | Startwert | Bereich | Begründung und Wirkung |
|---|---|---|---|
| `enabled` | true | – | false: Parameter bleiben wie in der Datei; die Kette läuft mit den kalibrierten Werten. |
| `learn_s` | 30.0 | 0–600 | Lernphase ohne Eingriff ("erst zuhören"): 30 s enthalten bei normaler Sprache mehrere Sätze und Pausen, sodass Rauschteppich, Sprachpegel und Zischanteil belastbar gemessen sind, bevor eine Schwelle bewegt wird. Kürzer riskiert Eingriffe auf Basis eines Räusperns, länger verzögert die erste Korrektur unnötig. |
| `interval_s` | 2.0 | 0.1–60 | Auswerteintervall der Meter. 2 s sind lang genug, dass die Mittel/Maxima der Gain-Reduction (siehe Abschnitt 0, Meter) ein Intervall mit Sprache und Pause enthalten, und kurz genug, dass eine Lüfteränderung in Minuten nachgeführt ist. |
| `max_step_db` | 0.5 | 0.01–6 | Maximale Änderung je Eingriff. 0,5 dB sind unterhalb der Hörschwelle für Pegelsprünge in Sprache; zusammen mit `interval_s` ergibt sich eine maximale Drift von 15 dB/min, in der Praxis (Hysterese, Totzone) deutlich weniger – das Verhalten eines vorsichtigen Tontechnikers. In der Kette landet jeder Schritt über `set_param` in den Attack/Release-Glättern der Dynamikstufen und ist deshalb knackfrei. |
| `bounds.expander.threshold_db` | [−70, −30] | innerhalb −90…0 | Harte Grenzen, in denen die Schwelle dem Rauschteppich (+10 dB) folgen darf. Über −30 dBFS würde der Expander leise Silben beschneiden, unter −70 dBFS ist kein realer Teppich mehr. |
| `bounds.deesser.threshold_db` | [−45, −10] | innerhalb −60…0 | Folgt dem Zischanteil. Unter −45 dBFS würde jeder Konsonant de-esst (Lispeln). Obergrenze −10 dBFS, weil die Bandspitzen stark zischender Stimmen nach dem Trim nahe der Vollaussteuerung liegen können; bei −15 klemmte die Regel und `max_reduction_db` wurde wieder zum Regler. |
| `bounds.compressor.threshold_db` | [−35, −10] | innerhalb −60…0 | Folgt dem Sprachpegel. Unter −35 dBFS komprimiert der Kompressor den Raum mit, über −10 dBFS verdichtet er nichts mehr. |
| `bounds.normalizer.max_gain_db` | [0, 12] | innerhalb 0…30 | Die Limiter-Schutzregel senkt den Gain-Deckel, wenn der Limiter im Mittel arbeitet; nie über den kalibrierten Wert hinaus und nie unter 0 dB. |
| `log_path` | `logs/adaptive.jsonl` | Pfad | Jede Änderung wird mit Grund protokolliert (JSONL); Voraussetzung dafür, Regelverhalten im Nachhinein zu lesen und zu prüfen. |

Der Validator prüft, dass jeder Bounds-Schlüssel eine existierende numerische Stufenparameter
bezeichnet, dass die Grenzen innerhalb des Stufenbereichs liegen und dass der aktuelle Wert in der
`chain`-Sektion innerhalb der Grenzen liegt.

## 12. Offene Punkte und Risiken

- Die Startwerte sind Erfahrungswerte für das NT-USB; die Kalibrierung mit einem echten
  Sprachsample ist der eigentliche Abgleich.
- Der De-Esser reagiert auf Spitzen im Band; bei sehr hellen Stimmen kann die Kalibrierung
  `freq_hz` und `threshold_db` nachführen müssen.
- Der Normalizer regelt die gegatete Sprachlautheit auf das Ziel; ungegatete 3-s-Fenster mit
  vielen Pausen lesen 2–3 LU weniger (siehe 7). Die effektive Aufwärtsrate bei bewegter Sprache
  liegt bei etwa 0,4 dB/s; ein Start weit unter dem Ziel braucht entsprechend länger – die
  Kalibrierung (Input Trim) soll den Startpunkt in die Nähe des Ziels legen.
- Raum wird nach der Kalibrierung lauter: Der Normalizer meldet `low_snr`, hält Pausen vom Gain
  fern (Anstieg ≤ 0,2 dB, Einsatz ≤ +0,5 dB) und regelt die Sprache weiter aus sicheren
  Sprachintervallen; der Abstand Sprache−Pause schrumpft durch Kompressor und gekappte
  Expander-Schwelle um bis zu 7 LU vor und 5 LU nach der Nachführung (§6.4 b, §7 Tabelle). Die
  15 LU von §6.4 a werden mit Standard-Rosa bei +8 dB im synthetischen Szenario nicht erreicht
  (12,5 LU); den Abstand verbessern kann nur ein leiserer Raum oder eine Neukalibrierung.
- True-Peak-Sicherheit beruht auf der Marge, nicht auf überabgetasteter Detektion (siehe 8).
- Zusammenspiel mit der Kalibrierung: Die Kompressorschwelle auf dem Sprach-RMS senkt den
  Sprach-RMS um ~6 dB; von den 15 dB statischer Expander-Absenkung (10 dB Abstand zum
  Rauschteppich, Ratio 2,5) bleiben relativ zur Sprache ~8–9 dB Störabstandsgewinn, also Reserve
  gegenüber den geforderten ≥ 6 dB.
