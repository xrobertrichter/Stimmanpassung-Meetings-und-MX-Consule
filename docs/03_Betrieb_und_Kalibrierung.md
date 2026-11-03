# Betrieb und Kalibrierung

Stand: 2026-11-03 (Nacharbeit Runde 1). Dieses Dokument beschreibt den tatsächlichen Stand von
`voicechain/calibrate.py`, `voicechain/adaptive.py`, `voicechain/meters.py` und
`voicechain/synth.py`. Voraussetzung: die Einrichtung aus `docs/01_macOS_Setup.md`
ist abgeschlossen (BlackHole installiert, Rode NT-USB auf 48 kHz, Python-Umgebung mit
`requirements.txt`). Die Bedeutung der einzelnen Parameter steht in
`docs/02_Parameter_Begruendung.md`.

Alle Kommandos werden im Repository-Verzeichnis ausgeführt; relative Pfade werden
von der CLI gegen das Repository-Verzeichnis aufgelöst.

---

## 1. Ablauf in Kürze

1. Sample aufnehmen (60 s, 48 kHz, WAV: 10 s Stille am Anfang, eine Pause ≥ 5 s in der Mitte)
   oder ohne Mikrofon mit `make-sample` erzeugen.
2. `python -m voicechain calibrate --wav sample.wav` (Standardausgaben
   `config/chain.calibrated.yaml` und `reports/calibration_report.md`; andere Pfade mit
   `--out` und `--report`).
3. Bericht lesen: Messwerte vorher, abgeleitete Parameter mit Begründung, Messwerte nachher,
   Akzeptanzkriterien.
4. A/B hören: `python -m voicechain process --in sample.wav --out sample.processed.wav --config config/chain.calibrated.yaml`
5. Live: `python -m voicechain run --config config/chain.calibrated.yaml`
6. Im Betrieb das JSONL-Log der adaptiven Regelung beobachten; bei Bedarf `--freeze`.

---

## 2. Sample aufnehmen

Die Kalibrierung braucht eine Aufnahme, die den späteren Betrieb abbildet. Gleiche
Position, gleicher Raum, gleiche Mikrofoneinstellung wie im Seminar.

**Anforderungen an die Aufnahme**

| Punkt | Vorgabe | Warum |
|---|---|---|
| Dauer | 60 s (zulässig 30–120 s, Minimum 10 s) | Rauschteppich und Sprachpegel brauchen Statistik über viele Rahmen |
| Format | WAV, 48 kHz, mono oder stereo (wird gemischt), 16 oder 24 Bit | Die Kette läuft fest mit 48 kHz; die Kalibrierung resampelt **nicht** und bricht bei anderer Rate mit Fehlermeldung ab |
| Anfang | 10 s Stille (nicht sprechen, nicht bewegen, Rechnerlüfter wie im Betrieb) | Daraus wird der Rauschteppich geschätzt (10. Perzentil der Rahmenpegel) |
| Mitte | **eine Pause von mindestens 5 s nach einigen Sätzen** (nicht sprechen, nicht tippen) | Pflicht nach Beauftragung §6.3: nur in einer Pause *nach* Sprache ist messbar, wie leise die Kette in Pausen bleibt (Expander-Release, Normalizer-Gate). Ohne diese Pause bricht `calibrate` mit einem Hinweis ab |
| Inhalt | Normal sprechen wie im Seminar, mit natürlichen Pausen zwischen Sätzen | Sprachpegel und Sprachanteil müssen typisch sein |
| Zischlaute | Mehrere Sätze mit s/sch/z-Lauten, z. B. „Sechs Schlösser stehen still am See", „Zwischen zwei Zweigen zischt es", „Sie sagte, sie sei seit sechs Stunden hier" | Zischanteil, Schwerpunkt und Spitzenpegel des Zischbands für den De-Esser |
| Abstand | Wie im Seminar (empfohlen 15–25 cm, leicht seitlich) | Nahbesprechungseffekt und Pegel hängen stark vom Abstand ab |
| Pegel | Keine Übersteuerung (Spitzen unter −3 dBFS, wie README und `record`-Prüfung); Rode-Gain nicht nachträglich ändern | Trim wird aus dieser Aufnahme berechnet |

**Aufnahme unter macOS:** siehe `README.md` und `docs/01_macOS_Setup.md` (eigenes
Kommando bzw. `afconvert` als Bordmittel für die Umwandlung nach 48 kHz WAV). Verlustbehaftete
Zwischenformate (AAC/m4a) verfälschen Rauschteppich und Höhen und sind zu vermeiden.

**Ohne Mikrofon proben:** `python -m voicechain make-sample --out samples/synth.wav` erzeugt ein
60-s-Signal aus `voicechain/synth.py` (10 s Rauschen am Anfang, 6 s Pause bei 32–38 s,
Sprachpegel −26 dBFS, Rauschteppich −48 dBFS, deutliche Zischanteile). Es ist keine Sprache,
hat aber die Statistik, die die Kalibrierung misst, und eignet sich, um den Ablauf und die
Berichte kennenzulernen.

---

## 3. Kalibrierung ausführen

```
python -m voicechain calibrate --wav sample.wav \
    --out config/chain.calibrated.yaml \
    --report reports/calibration_report.md
```

Optional `--config <basis.yaml>`, sonst ist `config/chain.default.yaml` die Basis (einzige
Quelle der Startwerte; geladen und geprüft über `voicechain.config`). Eine bereits kalibrierte
YAML darf als Basis dienen (ihr `_calibration`-Block wird verworfen).

Was passiert (Funktionen in `voicechain/calibrate.py`):

1. **Lesen** (`read_mono_wav`): WAV über `soundfile`, Mischung auf Mono. Abbruch mit klarer
   Meldung bei unlesbarer Datei, leerer Datei, nicht-finiten Werten („Sample enthält nicht-finite
   Werte") oder abweichender Samplerate (nennt beide Raten und `afconvert`/`ffmpeg`-Befehl).
2. **`analyze`**: Rauschteppich, Sprachpegel, Silbenspitzen (Crest), Störabstand, Dynamik,
   Sprachanteil, Grundfrequenz (Autokorrelation der aktiven Rahmen), Zischanteil (Energie
   5–10 kHz minus 0,3–3 kHz in aktiven Rahmen), Schwerpunkt der Zischenergie 4–10 kHz,
   **Spitzenpegel der Zischlaute im De-Esser-Band** (Bandpass mit den De-Esser-Parametern,
   95. Perzentil der 50-ms-Rahmenspitzen aktiver Rahmen), spektrale Neigung 200 Hz–8 kHz,
   True Peak, Lautheit (integriert, Short-Term-Maximum), Bandenergien für die EQ-Bänder,
   längste Pause ≥ 5 s nach Sprache mit ihrer Momentary-Lautheit.
3. **`recommend`**: leitet daraus Trim, Expander, De-Esser, EQ und Kompressor ab (Abschnitt 4)
   und legt je Wert einen Begründungssatz unter `_calibration.reasons` ab.
4. **Erster Durchlauf** durch `Chain.from_config(...)`, dann **`refine_normalizer`**:
   Gate und Gainbegrenzung des Normalizers aus der Pausenlautheit *nach* der Kette.
5. **Zweiter Durchlauf** und **`evaluate`**: Messwerte nachher und Akzeptanzkriterien (Abschnitt 5).
6. **Schreiben**: YAML über `voicechain.config.save_config` (Schema wie
   `config/chain.default.yaml`, zusätzlich der Metadaten-Block `_calibration` mit Messwerten,
   Messwerten des ersten Durchlaufs und Begründungen) und Markdown-Bericht.

Der Rückgabewert `0` bedeutet „alle Kriterien bestanden", `1` „mindestens eines nicht
bestanden" (Details im Bericht).

---

## 4. Wie die Parameter abgeleitet werden

Referenzwerte sind Konstanten am Anfang von `voicechain/calibrate.py`; alles, was keine
Norm ist, ist dort und hier als **Annahme** markiert.

| Parameter | Regel | Begründung / Annahme |
|---|---|---|
| `input_trim.gain_db` | −20 dBFS − gemessener Sprachpegel, begrenzt auf −12…+24 dB | Referenzpegel aktiver Sprache −20 dBFS RMS (Projektreferenz, docs/02); lässt ~14 dB Headroom für Sprachspitzen |
| `expander.threshold_db` | min(Rauschteppich + 10 dB, Sprachpegel − 12 dB), jeweils nach Trim; Grenzen aus `adaptive.bounds` | Übliche Praxis für Gate/Expander-Schwellen sind 8–12 dB über dem Rauschteppich. Die Kennlinie der Kette (Ratio 2,5, Knie 6 dB) senkt ein Signal 10 dB unter der Schwelle um 15 dB; das lässt Reserve gegen die ~6 dB Kompressor-Reduction (mit 8 dB Abstand maß der Selbsttest 5,8 dB Störabstandsgewinn, mit 10 dB 8,7 dB). 12 dB unter der Sprache bleiben leise Silben erhalten. Bei Störabstand < 22 dB greift die Sprach-Regel, der Expander kann dann wenig tun |
| `deesser.freq_hz` | Schwerpunkt der Zischenergie 4–10 kHz, begrenzt auf 5–9 kHz | Bandzentrum der gemessenen Zischlaute |
| `deesser.threshold_db` | nur wenn Zischanteil > −15 dB (**Annahme**, aus dem Langzeit-Sprachspektrum abgeleitet): Schwelle = Spitzenpegel der lauten Zischlaute im De-Esser-Band (nach Trim) − Overshoot, mit Overshoot = 4 dB / (1 − 1/Ratio) (hartes Knie des De-Essers); Grenzen aus `adaptive.bounds` | Der De-Esser misst Sample-Spitzen des Bandsignals; das Modell misst dieselbe Größe (95. Perzentil der 50-ms-Spitzen aktiver Rahmen) und setzt die Schwelle so, dass die lauten Zischlaute mindestens 4 dB Reduktion erhalten, aber nicht jeder Laut am Deckel `max_reduction_db` hängt (gemessen: Median 4,9 dB, 0 % am Deckel). Die Schwelle darf dabei über oder unter dem Startwert liegen. **Unterhalb der Referenz (kein Zischproblem): Schwelle = Spitzenpegel + 2 dB** (**Annahme**), der De-Esser ruht und fasst nur Ausreißer an (gemessen: 0 % der aktiven Rahmen > 1 dB Reduktion statt 100 % mit dem Startwert −30). Der Startwert −30 der YAML gilt nur bis zur Kalibrierung |
| EQ | Dichte je Messband relativ zum Ankerband 350–1000 Hz gegen die Referenzkurve „flach bis 500 Hz, −6 dB/Okt bis 4 kHz, darüber −10 dB/Okt" (**Annahme**: typisches Langzeit-Sprachspektrum, das oberhalb 4 kHz steiler fällt); 50 % der Abweichung werden korrigiert (**Annahme**, Sprecherklang soll erhalten bleiben), Totzone ±1 dB, Korrektur wird auf den Basiswert des Bands **addiert**, Ergebnis auf ±4 dB begrenzt. **Low-Shelf 120 Hz und High-Shelf 10 kHz nur als Absenkung** (nie > 0 dB), **High-Shelf höchstens −3 dB** (**Annahme**: zusammen mit dem De-Esser klänge mehr dumpf) und sein Messband 7–14 kHz wird **ohne Zischrahmen** gemessen (aktive Rahmen, deren Bandspitze mehr als 6 dB unter dem Zisch-p95 liegt; der Anteil ausgeschlossener Rahmen steht im Bericht); **Low-Shelf nur bei Grundfrequenz ≤ 150 Hz** (**Annahme**), bei höheren Stimmen enthält das Band 80–170 Hz keine Sprache, sondern Trittschall und Lüfter | Vier Bänder: Low-Shelf 120 Hz (Messband 80–170 Hz), Peak 250 Hz (170–350), Peak 3 kHz (2–4,5 kHz), High-Shelf 10 kHz (7–14 kHz). Vorhandene Basis-Bänder innerhalb ±20 % der Frequenz werden genutzt (z. B. 220 Hz statt 250 Hz), sonst wird ein Band angelegt; ein Band mit 0 dB wird nicht angelegt |
| `compressor.threshold_db` | Sprachpegel nach Trim (−20 dBFS), Grenzen aus `adaptive.bounds` | Der Kompressor der Kette misst Sample-Spitzen; Silbenspitzen liegen ~10 dB über dem RMS (wird gemessen: „Crest-Faktor"). Schwelle auf RMS-Höhe verdichtet nur die Spitzen (~6 dB bei 3:1). Eine Schwelle 6 dB darunter ergab in der Messung ~10 dB Reduktion und machte den Störabstandsgewinn des Expanders zunichte |
| `compressor.makeup_db` | statische Kennlinie (weiches Knie) am mittleren Silbenspitzenpegel, 0…12 dB | Modell stimmt mit der echten Kette auf ±0,6 dB überein; Ziel „Ausgang ≈ Eingang", den Rest regelt der Normalizer |
| `normalizer.gate_lufs` | Pausenlautheit nach Kette **ohne Normalizer** (erster Durchlauf mit `normalizer.enabled: false`, Median der Momentary-Lautheit in der Pause) + 8 dB (**Annahme**), mindestens −40 LUFS, höchstens Ziel − 15 LU | Gate und Gainbegrenzung gelten für den Pegel am Eingang des Normalizers; würde der erste Durchlauf mit laufendem AGC gemessen, enthielte die Pause dessen noch unkalibrierten Gain (Grenzfall Teppich −38 dBFS: max_gain 0 statt > 0). Unterhalb des Gates hält der Normalizer seinen Gain: Raumrauschen darf das Gate nie öffnen, leise Sprache muss es öffnen |
| `normalizer.max_gain_db` | Ziel − (Pausenlautheit nach Kette + 6 LU), begrenzt auf 0…12 dB | Der Normalizer kann Rauschen rechnerisch nie auf Sprachlautheit anheben. 0 dB heißt: die Pausen sind in diesem Raum bereits zu laut (Bericht warnt) |
| `normalizer.target_lufs` | bleibt (Basis: −18 LUFS) | Zielwert aus der Konfiguration (E3) |
| `limiter.ceiling_dbfs` | bleibt (Basis: −1 dBTP) | True-Peak-Obergrenze (E3) |
| `highpass`, Zeitkonstanten, Ratios | bleiben | Begründung in docs/02 |

---

## 5. Bericht lesen

Der Bericht (Pfad aus `--report`, Standard `reports/calibration_report.md`) hat fünf Abschnitte:

1. **Messwerte vorher** – Tabelle aller Messgrößen, Pausenwerte und Bandenergien.
   Plausibilitätsprüfung: Rauschteppich sollte deutlich unter dem Sprachpegel liegen
   (Störabstand ≥ 22 dB ist gut, < 15 dB ist ein Raum-/Abstandsproblem, das keine Kette löst).
   Sprachanteil 50–80 % heißt, die Pausen waren da. Grundfrequenz 100–140 Hz tiefe,
   180–250 Hz hohe Stimme. Steht in den Hinweisen „keine Sprache erkannt", war die Aufnahme zu
   leise oder nur Rauschen.
2. **Abgeleitete Parameter** – Parameter, Wert vorher, Wert nachher, Begründung mit allen
   Zwischenwerten. Jede Zeile lässt sich mit den Messwerten nachrechnen.
3. **Messwerte nachher** – dieselben Messgrößen nach der Kette, mit Differenz, einschließlich
   der **absoluten Pausenwerte** (Momentary-Lautheit maximal und Median, RMS) vorher/nachher.
4. **Akzeptanzkriterien** (Beauftragung §6.3, Stand 2026-11-03):

   | Kriterium | Ziel | Hinweis |
   |---|---|---|
   | Störabstand verbessert | Sprachpegel minus Rauschteppich, nachher minus vorher, ≥ 6 dB | Bewertung bei gleichem Sprachpegel, weil der Normalizer leise Aufnahmen insgesamt anhebt; die absoluten Werte stehen daneben |
   | Pausenlautheit nach Kette | Maximum der Momentary-Lautheit in der längsten Pause ≥ 5 s (die ersten 0,5 s werden übersprungen) ≤ Ziel − 20 LU, also −38 LUFS bei −18 LUFS Ziel | Prüft, dass die Kette Pausen nicht anhebt (Normalizer-Gate). Bei Verfehlung steht die Abhilfe im Hinweis: Raum leiser (Lüfter, Abstand) oder `normalizer.max_gain_db` senken |
   | Zischanteil | bestanden, wenn nachher ≤ −15 dB, oder Δ ≤ −1 dB, oder (vorher ≤ −10 dB und Δ ≤ +1 dB), oder im Ruhezweig (vorher ≤ −15 dB, De-Esser nicht engagiert) Δ ≤ +3 dB bei nachher ≤ −12 dB. Begründung: Kompressor und Normalizer heben das 5–10-kHz-Band leiser Frikative relativ zu lauten Vokalen um 2–2,5 dB an (gemessen an der echten Kette); ein engagierter De-Esser nimmt das zurück, ein ruhender nicht. Nur klar zischende Stimmen (> −10 dB) müssen messbar besser werden | zusätzlich Zischspitzen im De-Esser-Band vorher/nachher |
   | Lautheit im Ziel | integriert −18 LUFS ± 2 LU | Die Lautheitsregelung braucht einige Sekunden, das geht in den integrierten Wert ein |
   | True Peak unter Ceiling | ≤ −1 dBTP (+0,1 dB Messtoleranz) | 4x-Oversampling-Messung |

5. **Annahmen und Grenzen** – die oben markierten Annahmen.

Ist ein Kriterium „nicht bestanden", steht in der Spalte „Hinweis" der Grund. Typische Fälle:
Störabstand der Aufnahme zu klein (Expander darf nicht tiefer, sonst schneidet er Sprache; die
Kette hebt dann die Pausen an und ist in diesem Raum schlechter als das rohe Mikrofon),
Aufnahme ohne Pausen (Rauschteppich wird überschätzt), Aufnahme übersteuert.

---

## 6. A/B-Vergleich

```
python -m voicechain process --in sample.wav --out sample.default.wav --config config/chain.default.yaml
python -m voicechain process --in sample.wav --out sample.calibrated.wav --config config/chain.calibrated.yaml
```

Beide Dateien und das Original im Wechsel mit Kopfhörer anhören. Worauf achten:

- Pausen: Raumrauschen soll leiser werden und in längeren Pausen leise *bleiben*;
  Sprachanfänge dürfen nicht „abgebissen" klingen.
- Zischlaute: weicher, aber nicht lispelnd (zu starke De-Esser-Reduktion klingt wie „th").
- Lautstärke: gleichmäßig über Sätze hinweg, keine hörbare „Atmung" des Pegels.
- Klangfarbe: nicht dumpf (zu viel Höhenabsenkung), nicht nasal (Peak-Bänder zu stark).

`process` gibt dieselben Messgrößen wie der Kalibrierungsbericht aus, so dass sich
Änderungen an der YAML sofort beziffern lassen.

---

## 7. Live-Betrieb

```
python -m voicechain run --config config/chain.calibrated.yaml
```

Die Kette läuft Rode → Python → BlackHole; in Zoom/Teams wird „BlackHole 2ch" als Mikrofon
gewählt (docs/01). Die Statuszeile zeigt einmal pro Sekunde Pegel, Normalizer-Gain,
Limiter-Eingriff, Xruns und die letzten Änderungen der adaptiven Regelung.

Optionen: `--no-adaptive` schaltet die Regelung ab (feste Werte aus der YAML),
`--freeze` startet sie eingefroren (mitlaufen und protokollieren, nichts verändern, Abschnitt 10).
Im laufenden Betrieb schaltet das Signal SIGUSR1 den Freeze um, ohne den Stream zu
unterbrechen: `kill -USR1 <pid>` (die PID steht in der Startmeldung bzw. über `pgrep -f "voicechain run"`);
der aktuelle Zustand steht in der Statuszeile und als Ereignis `freeze` im JSONL-Log.

---

## 8. Was die adaptive Regelung tut

Klasse `AdaptiveController` in `voicechain/adaptive.py`; Einstellungen unter `adaptive:` in der
YAML (`learn_s`, `interval_s`, `max_step_db`, `bounds`, `log_path`).

**Messgrundlage.** Die Runtime füttert `LiveAnalyzer.push()` mit jedem Eingangsblock (rohes
Mikrofonsignal, vor der Kette). Der Analyzer hält 20 s Historie aus 50-ms-Rahmen und liefert
mit `snapshot()`:

```
{"ready": bool,                # mindestens 2 s Historie
 "speech_detected": bool,      # Abstand leise/laute Rahmen >= 6 dB
 "noise_floor_dbfs": float,    # 10. Perzentil der Rahmenpegel
 "speech_level_dbfs": float,   # Energiemittel der aktiven Rahmen
 "dynamic_range_db": float,    # 95. minus 10. Perzentil der Rahmenpegel
 "sibilance_ratio_db": float,  # 5-10 kHz minus 0,3-3 kHz (aktive Rahmen)
 "band_peak_p95_dbfs": float,  # 95. Perzentil der Rahmenspitzen im De-Esser-Band
 "band_hz": (freq, oct),       # Band, in dem gemessen wurde
 "speech_active": bool,        # letzter Rahmen war Sprache
 "speech_ratio": float,        # Anteil aktiver Rahmen in der Historie
 "recent_speech_ratio": float, # Anteil aktiver Rahmen in den letzten 2 s
 "frames": int, "history_s": float}
```

**Ergänzung zum Schnittstellenvertrag (Beauftragung §4.2):** `AdaptiveController.update(meters)`
erwartet neben den Metern aus `chain.get_meters()` den zusätzlichen Schlüssel
`meters["analysis"]` mit genau diesem Schnappschuss; fehlt er oder ist er noch nicht `ready`,
regelt der Controller nicht und schreibt `no_analysis` ins Log. Die Runtime legt diesen
Schnappschuss alle `interval_s` als `meters["analysis"]` neben die
Meter der Kette (`chain.get_meters()`, deren Stufen Mittel und Maximum der Gain-Reduction
seit dem letzten Lesen akkumulieren) und ruft `controller.update(meters)` auf. Da der
Analyzer vor dem Input Trim misst, rechnet der Controller `input_trim.gain_db` auf die Pegel
auf. Das De-Esser-Band des Analyzers (`LiveAnalyzer(sr, deesser_freq_hz=…, deesser_bandwidth_oct=…)`
bzw. `set_deesser_band`) sollte dem der Kette entsprechen; Standard ist 6,5 kHz / 1 Okt wie in
der Basis-Konfiguration.

**Lernphase.** In den ersten `learn_s` Sekunden (Standard 30 s, gezählt ab Start des
Controllers) wird nur gemessen und je Intervall ein `learning`-Eintrag geschrieben.

**Plausibilitätsprüfung** (ein Tontechniker regelt nicht nach Tastaturklicks): Die
Sprach-Regeln für De-Esser und Kompressor laufen nur, wenn das Gemessene *der Sprecher am
Mikrofon* sein kann: Sprache erkannt, Sprachpegel nach Trim innerhalb ±6 dB der Referenz
−20 dBFS (dort hat ihn die Kalibrierung hingestellt), Sprachanteil ≥ 30 % der Historie,
Dynamik ≥ 15 dB. Tastaturklicks (3/s, −30 dBFS) lesen sich als „Sprache" bei −39 dBFS mit
15 % Anteil, ein Kollege im Raum oder der Rückkanal über Lautsprecher (−42 dBFS) fallen am Pegel
durch. Ist die Prüfung negativ, schreibt der Controller je Intervall ein Ereignis
`implausible_speech` mit den Gründen und ändert De-Esser und Kompressor nicht; der Expander
folgt weiter dem Rauschteppich. Die Kappe „12 dB unter Sprache" bleibt dabei aktiv, solange
das Gehörte Sprache ist (also auch bei einem Sprecher mit abweichendem Pegel: leiser Sprecher
−28 dBFS in einem −46-dBFS-Raum, Dynamik dann nur ~14 dB → Expander höchstens −40, nachgemessen
−38 mit Totzone, nicht −36); für die Kappe genügen Sprachaktivität, ein sprachähnlicher Anteil
aktiver Rahmen und eine Dynamik ≥ 10 dB, nur bei Tippen (Sprachanteil) oder gleichförmigem
Geräusch (Dynamik < 10 dB) entfällt sie. Nach **mehr als
60 s ohne plausible Sprache** wandern De-Esser- und Kompressor-Schwelle in `max_step_db`-Schritten
zu ihren kalibrierten Startwerten zurück (Grund `rest`), so dass der Sprecher nach einer
Zuhörphase die kalibrierte Kette vorfindet.

**Regelgesetze** (dieselben Zielwerte wie die Kalibrierung, Abschnitt 4):

| Parameter | Ziel | Totzone | Grund im Log |
|---|---|---|---|
| `expander.threshold_db` | min(Rauschteppich + 10, Sprache − 12) (Konstante `EXPANDER_FLOOR_MARGIN_DB` in calibrate.py, von adaptive.py importiert); ohne plausible Sprache nur Rauschteppich + 10 | ±2 dB | `noise_floor_track` |
| `deesser.threshold_db` | Zischspitzen im De-Esser-Band (nach Trim) − Overshoot, wenn Zischanteil über der Referenz; sonst Zischspitzen + 2 dB (Ruhelage, dieselbe Formel wie die Kalibrierung) | ±1,5 dB | `sibilance_track`, `sibilance_rest` |
| `normalizer.max_gain_db` | nur durch die Limiter-Schutzregel (unten): Deckel auf den aktuellen Gain, später zurück auf den kalibrierten Wert; innerhalb des `bounds`-Eintrags `normalizer.max_gain_db` (fehlt er, 0 … kalibrierter Wert) | – | `limiter_overload`, `limiter_recovered` |
| `compressor.threshold_db` | Sprachpegel (nach Trim) | ±2 dB | `speech_level_track` |

Je Intervall höchstens ein Schritt von `max_step_db` (Standard 0,5 dB) in Richtung Ziel, nur
wenn der Fehler außerhalb der Totzone liegt, nur innerhalb der `bounds`, und nur, wenn der
letzte Eingriff an diesem Parameter mindestens zwei Intervalle zurückliegt. Weil die Ziele
aus dem Eingangssignal berechnet werden (nicht aus dem Ausgang), gibt es keine Rückkopplung
und kein Pendeln: die Richtung ändert sich nur, wenn sich das Signal oder seine Statistik
ändert. Der Rauschteppich der Regelung wird aus den Pausenrahmen geschätzt (5. Perzentil der
inaktiven Rahmen, sobald mindestens 5 % der 20-s-Historie inaktiv sind; sonst 5. Perzentil
aller Rahmen), damit ein dichter Vortrag mit wenig Pausen (10 %) nicht als lauterer Raum gelesen
wird (Simulation 300 s dichter Vortrag im unveränderten Raum: 0 Expander-Schritte). Die
De-Esser-Regel hält ihren Zustand „engagiert/ruhend" mit 1 dB Hysterese um die Referenz, damit
eine Stimme nahe −15 dB nicht zwischen beiden Zielwerten pendelt.

**Raumänderung im Betrieb (Beauftragung §6.4), gemessener Ablauf.** Wird der Rauschteppich nach
der Kalibrierung 8–12 dB lauter, passiert Folgendes: Der Normalizer schätzt seinen Teppich selbst
(Meter `normalizer.noise_floor_lufs`, wirksames Gate `normalizer.effective_gate_lufs`, Abstand
`gate_margin_lu`; docs/02, Normalizer) und friert seinen Gain in Pausen sofort ein – eine Pause
auf Sprachlautheit entsteht nicht, der nächste Satz setzt höchstens +1,5 dB lauter ein. Der
Pausenabstand ist in den ersten Minuten aber kleiner als kalibriert: Der Expander steht noch auf
der alten Schwelle und senkt den lauteren Teppich kaum, der Abstandsverlust beträgt bis zu 7 LU
(Messung docs/02 §7: Pause 1 bei +8 dB 13 LU, bei +12 dB 4,8 LU unter der Sprache). Den vollen
Abstand ≥ 15 LU gibt es erst, wenn die Regelung die Expander-Schwelle nachgezogen hat (ca. 1,5 min,
Konvergenzzeit unten). Reicht der Störabstand am Eingang nicht mehr (unter ~16 dB), bleibt die
Kappe „Sprache − 12" maßgeblich, die Regelung schreibt `low_snr` (§9) und die Statuszeile zeigt
den Zustand; dann hilft nur der Raum oder eine neue Kalibrierung. Die Begrenzung von
`max_gain_db` aus der Kalibrierung gilt für den kalibrierten Raum; in einem lauter gewordenen Raum
begrenzt sie den Gain, hält den Teppich aber nicht automatisch von der Sprachlautheit fern.

**Konvergenzzeit.** 0,5 dB je zwei Intervalle = 0,5 dB je 4 s, dazu ~10 s, bis die 20-s-Historie
eine Änderung des Raums abbildet. Beispiel: Lüfter geht an, Rauschteppich +10 dB → das Ziel
verschiebt sich um 10 dB, die Totzone lässt 2 dB stehen → 16 Schritte × 4 s + 10 s ≈ 75–90 s
(Simulation mit echter Kette: Sprung bei 100 s, Ziel ± Totzone bei etwa 190 s erreicht). Das ist
bewusst langsam („vorsichtiger Tontechniker"); wer schneller will, erhöht `max_step_db`
(hörbare Sprünge ab etwa 1 dB) oder verkürzt `interval_s`.

**Rückholung außerhalb der Grenzen.** Der Loader lehnt Konfigurationsdateien ab, deren Werte
außerhalb der `bounds` liegen. Gerät ein Wert zur Laufzeit trotzdem dorthin (z. B. über
`set_param` von außen), wird er in `max_step_db`-Schritten zurückgeholt, nie gesprungen, und darf
sich nicht weiter entfernen (Laufzeitschutz, getestet).

**Schutzregeln** (aus den Stufen-Metern `gain_reduction_mean_db` / `gain_reduction_max_db`, die
seit dem letzten `get_meters()` akkumuliert sind, also über das ganze Intervall):

| Situation | Reaktion | Grund im Log |
|---|---|---|
| Limiter-Gain-Reduction im Intervall im Mittel > 3 dB | Kompressor-Schwelle wird nicht angehoben; `normalizer.max_gain_db` wird auf den aktuellen Normalizer-Gain gedeckelt (mehr Pegel gibt es nicht); Ereignis `limiter_protect` mit `hold_normalizer_gain: true` | `limiter_overload` |
| Limiter-Reduction wieder < 1,5 dB | `normalizer.max_gain_db` zurück auf den konfigurierten Wert | `limiter_recovered` |
| Maximum der Expander-Gain-Reduction > 6 dB in **zwei aufeinanderfolgenden** Intervallen, die jeweils vollständig Sprache enthielten (`recent_speech_ratio` ≥ 98 %, also höchstens ein inaktiver 50-ms-Rahmen; eine Pause, die 70 ms nach Intervallbeginn endet, lässt das Gate noch geschlossen und zählt daher nicht). Was die Stufen während der Lernphase akkumuliert haben, wird am Ende der Lernphase verworfen | Expander-Schwelle sofort um `max_step_db` senken, ohne Totzone und ohne Wartezeit, aber nie unter die untere Grenze (Simulation 300 s normale Sprache: 0 Fehlalarme) | `speech_cut` |

Nicht enthalten ist ein **Pump-Detektor** (§3.2 nennt „Pumpen" als Rücknahmegrund): Der
Normalizer ist feedforward mit höchstens 1 dB/s aufwärts und kann nicht schwingen, der
Kompressor sitzt mit der Schwelle auf RMS-Höhe und 100 ms Release im unkritischen Bereich;
eine Schwankungsmessung der Gain-Reduction hätte derzeit keinen Fall, in dem sie eingreifen
müsste. Hört man dennoch Pumpen, ist `--freeze`/SIGUSR1 das Mittel der Wahl, danach die
Kompressor-Zeitkonstanten in docs/02 prüfen.

---

## 9. JSONL-Log lesen

Datei aus `adaptive.log_path` (Standard `logs/adaptive.jsonl`), eine JSON-Zeile je Ereignis,
`t` ist die monotone Uhr des Prozesses in Sekunden.

Änderungen:

```
{"t": 48.0, "stage": "expander", "name": "threshold_db", "old": -34.0, "new": -34.5, "reason": "noise_floor_track"}
```

Gründe: `noise_floor_track`, `sibilance_track`, `sibilance_rest`, `speech_level_track`,
`speech_cut`, `limiter_overload`, `limiter_recovered`, `rest`.

Ereignisse (Schlüssel `event`):

| event | Bedeutung |
|---|---|
| `learning` | Lernphase, `remaining_s` Restzeit |
| `learn_done` | Lernphase beendet, Regelung aktiv |
| `no_analysis` | Kein Analyzer-Schnappschuss oder noch nicht `ready` (< 2 s Historie) |
| `implausible_speech` | Gemessenes ist nicht der Sprecher (`reasons`: Pegel, Sprachanteil, Dynamik, keine Sprache); `since_s` Sekunden seit der letzten plausiblen Sprache. De-Esser/Kompressor werden gehalten, ab 60 s zurückgeholt (`rest`) |
| `low_snr` | Störabstand zu gering (Beauftragung §6.4 b): der Normalizer meldet `low_snr` oder der eigene Teppich nach Trim liegt weniger als 16 dB unter dem Sprachpegel; `snr_lu`, `input_snr_db`, `source` und `hint` („Störabstand zu gering: Raum verbessern oder neu kalibrieren"). Höchstens alle 30 s, kein Parametereingriff |
| `limiter_protect` | Schutz ein/aus (`active`), mit gemessener mittlerer Limiter-Reduction |
| `frozen` | Vorgeschlagene Änderung (`proposed`) wurde wegen Freeze nicht angewandt |
| `freeze` | Freeze ein-/ausgeschaltet (`on`) |

Schnell auswerten:

```
python -c "import json,sys; [print(e['t'], e['stage'], e['name'], e['old'], '->', e['new'], e['reason']) for e in map(json.loads, open('logs/adaptive.jsonl')) if 'stage' in e]"
```

Lesart: Viele `noise_floor_track`-Schritte in eine Richtung = der Raum ist leiser/lauter
geworden als beim Kalibrieren. `speech_cut` = die Expander-Schwelle war zu hoch (zu nah an
der Sprache) – nach einigen Schritten sollte es aufhören; wenn nicht, ist der Störabstand im
Raum zu klein. `limiter_protect` über längere Zeit = Eingangspegel zu hoch (Rode-Gain
zurücknehmen, neu kalibrieren). Lange Folgen von `implausible_speech` sind in Zuhörphasen
normal; stehen sie während des eigenen Sprechens im Log, stimmt der Abstand oder der Pegel
nicht mehr mit der Kalibrierung überein (neu kalibrieren).

---

## 10. Wann `--freeze`

- Während einer Präsentation, bei der nichts „wandern" darf (z. B. wenn der Raum während
  der Pause absichtlich still ist und der Expander sonst die Schwelle absenkt, die beim
  Zurückkommen zu tief liegt). Freeze protokolliert weiter, so dass man hinterher sieht,
  was die Regelung getan hätte.
- Zum Prüfen einer manuell geänderten YAML: `run --freeze` zeigt im Log die Abweichung der
  Regelziele vom festen Wert.
- Während des Betriebs umschalten: `kill -USR1 <pid>` friert ein bzw. gibt wieder frei
  (Ereignis `freeze` im Log, Zustand in der Statuszeile); so lässt sich vor einer
  Präsentation einfrieren und nach einer Tipp-Phase wieder freigeben, ohne neu zu starten.
- Dauerhaft fest: `--no-adaptive` oder `adaptive.enabled: false` in der YAML.

---

## 11. Grenzen des Verfahrens

- **Ein Sample, ein Raum.** Die Kalibrierung gilt für die Aufnahmesituation. Anderer Raum,
  anderer Abstand oder anderes Mikrofon-Gain: neu kalibrieren. Die Regelung gleicht nur
  langsame Drift innerhalb der `bounds` aus.
- **Störabstand.** Liegt der Rauschteppich näher als 22 dB an der Sprache, kann der Expander
  die geforderten 6 dB nicht ohne Sprachverlust bringen; Makeup und Normalizer heben die
  Pausen dann absolut an, und die Kette ist in diesem Raum **schlechter** als das rohe
  Mikrofon. Der Bericht zeigt das in den Absolutwerten und im Pausenkriterium. Abhilfe:
  leiserer Raum, geringerer Abstand, Lüfter weg.
- **Kompression kostet Störabstand.** Jede Dynamikverdichtung hebt die Pausen relativ zur
  Sprache an; der Störabstandsgewinn der Kette ist Expander-Absenkung minus
  Kompressor-Reduction. Deshalb bleibt die Kompressor-Schwelle auf Höhe des Sprach-RMS.
- **Referenzen sind Annahmen.** Zischanteil-Referenz (−15 dB), EQ-Referenzkurve, 50-%-Korrektur,
  f0-Grenze 150 Hz, Gate-Abstand 8 dB und Gain-Abstand 6 LU stammen aus dem typischen
  Langzeit-Sprachspektrum bzw. Tonpraxis, nicht aus einer Norm. Der Bericht zeigt, welches
  Band warum angefasst wurde.
- **Plausibilitätsprüfung.** Ein Sprecher, der dauerhaft 6 dB leiser oder lauter als bei der
  Kalibrierung spricht, gilt als „nicht plausibel"; De-Esser und Kompressor werden dann nicht
  mehr nachgeführt. Das ist gewollt (sonst folgt die Regelung Fremdgeräuschen) und steht im
  Log; Abhilfe ist eine neue Kalibrierung.
- **Sprachabschneide-Erkennung.** Das Expander-Maximum ist nur in Intervallen aussagekräftig,
  die vollständig Sprache enthielten, und erst nach zwei solchen Intervallen in Folge; bei
  vielen kurzen Pausen wird die Regel selten geprüft. Ein zu hoch stehender Expander fällt dann
  über die normale Nachführung (Rauschteppich + 10, Sprache − 12) zurück.
- **Kein Pump-Detektor** (siehe §8): Pumpen wird nicht automatisch erkannt; Abhilfe ist Freeze
  (`--freeze` oder SIGUSR1) und die Prüfung der Zeitkonstanten nach docs/02.
- **Monotone, pausenlose Sprache und Dauertöne.** Der Teppich-Schätzer des Normalizers
  (Plateau-Erkennung, docs/02) und die Regelung lesen ein Signal, das über Sekunden kaum moduliert
  (gesungene Töne, Summen, extrem monotones Vorlesen ohne Pausen, Testtöne), als Raum: der
  Normalizer hält dann seinen Gain, und es kann ein `low_snr`-Hinweis („Raum verbessern") erscheinen,
  obwohl der Raum in Ordnung ist. Echte Sprache moduliert um mehr als 7 LU je halbe Sekunde und
  ist nicht betroffen; bei Dauertönen (Jingles, Musik über das Mikrofon) Freeze nutzen.
- **Dichter Vortrag mit geringer Dynamik.** Liest die Regelung über 20 s eine Dynamik
  (95.–10. Perzentil der Rahmenpegel) unter 15 dB – typisch bei dichtem Vortrag mit nur 10 %
  Pausen –, gilt das Gehörte als nicht plausibel: De-Esser und Kompressor bleiben auf den
  kalibrierten Werten (`implausible_speech` im Log), nur der Expander folgt dem Teppich mit der
  Sprach-Kappe (Dynamik ≥ 10 dB). Das ist gewollt konservativ: die kalibrierte Kette ist in diesem
  Fall die sichere Einstellung, und die Nachführung soll lieber aussetzen als einem unsicheren
  Maß folgen. An der ±6-dB-Pegelkante kann der Expander um ±0,5 dB zwischen Kappe und Totzone
  wechseln (unhörbar).
- **Leiser oder lauterer Sprecher.** Weicht der Pegel dauerhaft um mehr als 6 dB vom
  kalibrierten ab, bekommt der Sprecher nach 60 s die kalibrierte Kette plus den Ausgleich des
  Normalizers; Kompressor und De-Esser werden nicht nachgeführt, der Expander behält die
  Sprach-Kappe. Dauerhaft anderer Abstand: neu kalibrieren.
- **EQ und De-Esser überlappen.** Die EQ-Messung umfasst die Zischlaute; ein Hochshelf-Cut
  und der De-Esser wirken bei zischenden Stimmen beide. Die Begrenzung auf ±4 dB, nur
  Absenkung für Shelves und 50 % Korrektur halten das im Rahmen.
- **Lautheit.** Die Lautheitsmessung ist ITU-R BS.1770 (K-Bewertung, Gating), das Ziel
  −18 LUFS ist ein Praxiswert für Konferenzsysteme (E3), keine Norm; es gilt für die
  Mono-Messung (ein Stereo-Meter auf dem BlackHole-Signal zeigt +3 LU).
- **Raum wird nach der Kalibrierung lauter** (Beauftragung §6.4, zwei Stufen):
  a) bis etwa +8 dB Rauschteppich: der Normalizer hält seinen Gain in Pausen (Gate auf den
  Teppich, Gainbegrenzung aus der Kalibrierung), die Pausen liegen weiter ≥ 15 LU unter der
  Sprache, der nächste Satz setzt höchstens +1,5 dB lauter ein; der volle Pausenabstand kehrt
  erst zurück, wenn die Regelung die Expander-Schwelle nachgezogen hat (etwa 1–1,5 min, §8).
  b) größere Anstiege (Störabstand am Eingang unter ~16 dB): der Expander darf wegen der Kappe
  „Sprache − 12 dB" nicht weiter schließen, die Pausen bleiben nur so weit unter der Sprache wie
  am Eingang; die Regelung schreibt `low_snr` mit dem Hinweis, den Raum zu verbessern oder neu zu
  kalibrieren, die Statuszeile zeigt den Zustand. Praktische Abhilfe: Freeze, Lüfter/Fenster,
  näher ans Mikrofon, dann neu kalibrieren.
- **Synthetische Nachweise.** Alle Zahlen in diesem Dokument und alle Tests stammen aus
  synthetischen Sprachsignalen (`synth.py`): sie haben die Pegelstatistik von Sprache, aber
  keine leisen Konsonanten 15–20 dB unter dem RMS, keine echten Zischlaute und keine
  Mikrofonfärbung. Vor einer Freigabe sind eine Kalibrierung mit einer echten Aufnahme, das Lesen
  des Berichts und ein Hördurchlauf (§6) nötig; `make-sample` ersetzt keine echte Aufnahme, es
  dient zum Proben des Ablaufs und für die Tests.
- **Metadaten in der YAML.** Der Block `_calibration` (Messwerte, Begründungen) steht in der
  kalibrierten YAML; `voicechain.config` behandelt Schlüssel mit führendem Unterstrich als
  Metadaten.
