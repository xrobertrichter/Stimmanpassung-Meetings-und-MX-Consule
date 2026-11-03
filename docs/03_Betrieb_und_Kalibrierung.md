# Betrieb und Kalibrierung

Stand: 2026-11-02. Dieses Dokument beschreibt den tatsächlichen Stand von
`voicechain/calibrate.py`, `voicechain/adaptive.py`, `voicechain/meters.py` und
`voicechain/synth.py`. Voraussetzung: die Einrichtung aus `docs/01_macOS_Setup.md`
ist abgeschlossen (BlackHole installiert, Rode NT-USB auf 48 kHz, Python-Umgebung mit
`requirements.txt`). Die Bedeutung der einzelnen Parameter steht in
`docs/02_Parameter_Begruendung.md`.

Alle Kommandos werden im Repository-Verzeichnis ausgeführt; relative Pfade werden
von der CLI gegen das Repository-Verzeichnis aufgelöst.

---

## 1. Ablauf in Kürze

1. Sample aufnehmen (60 s, 48 kHz, WAV) oder ohne Mikrofon mit `make-sample` erzeugen.
2. `python -m voicechain calibrate --wav sample.wav --out config/chain.calibrated.yaml --report reports/calibration.md`
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
| Inhalt | Normal sprechen wie im Seminar, mit natürlichen Pausen zwischen Sätzen | Sprachpegel und Sprachanteil müssen typisch sein |
| Zischlaute | Mehrere Sätze mit s/sch/z-Lauten, z. B. „Sechs Schlösser stehen still am See", „Zwischen zwei Zweigen zischt es", „Sie sagte, sie sei seit sechs Stunden hier" | Zischanteil und Schwerpunkt des Zischbands für den De-Esser |
| Abstand | Wie im Seminar (empfohlen 15–25 cm, leicht seitlich) | Nahbesprechungseffekt und Pegel hängen stark vom Abstand ab |
| Pegel | Keine Übersteuerung (Spitzen unter −6 dBFS); Rode-Gain nicht nachträglich ändern | Trim wird aus dieser Aufnahme berechnet |

**Aufnahme unter macOS**, zwei Wege:

- QuickTime Player → Ablage → Neue Audioaufnahme → Quelle „RØDE NT-USB" wählen → danach
  als WAV exportieren (oder mit `ffmpeg -i aufnahme.m4a -ar 48000 -ac 1 sample.wav` wandeln).
- Mit dem Projekt selbst, wenn `sounddevice` installiert ist: `python -c "import sounddevice as sd, soundfile as sf; sr=48000; x=sd.rec(60*sr, samplerate=sr, channels=1, dtype='float32'); sd.wait(); sf.write('sample.wav', x, sr, subtype='PCM_24')"`
  (vorher mit `python -m voicechain devices` prüfen, dass das Rode der Standard-Eingang ist, sonst `device=` ergänzen).

**Ohne Mikrofon proben:** `python -m voicechain make-sample --out samples/synth.wav` erzeugt ein
60-s-Signal aus `voicechain/synth.py` (10 s Rauschen am Anfang, Sprachpegel −26 dBFS,
Rauschteppich −48 dBFS, deutliche Zischanteile). Es ist keine Sprache, hat aber die
Statistik, die die Kalibrierung misst, und eignet sich, um den Ablauf und die Berichte
kennenzulernen.

---

## 3. Kalibrierung ausführen

```
python -m voicechain calibrate --wav sample.wav \
    --out config/chain.calibrated.yaml \
    --report reports/calibration.md
```

Optional `--config <basis.yaml>`, sonst wird `config/chain.default.yaml` als Basis genommen.
Eine bereits kalibrierte YAML darf als Basis dienen (ihr `_calibration`-Block wird verworfen).

Was passiert (Funktionen in `voicechain/calibrate.py`):

1. **Lesen**: WAV über `soundfile`, Mischung auf Mono. Weicht die Samplerate von
   `sample_rate` der Basis-Konfiguration ab, bricht der Lauf ab und nennt beide Raten und
   einen `ffmpeg`-Befehl zum Umwandeln.
2. **`analyze`**: Rauschteppich, Sprachpegel, Silbenspitzen, Störabstand, Sprachanteil,
   Zischanteil (Energie 5–10 kHz minus 0,3–3 kHz in aktiven Rahmen), Schwerpunkt der
   Zischenergie 4–10 kHz, spektrale Neigung 200 Hz–8 kHz, True Peak, Lautheit
   (integriert, Short-Term-Maximum), Bandenergien für die EQ-Bänder.
3. **`recommend`**: leitet daraus alle Parameter ab (Abschnitt 4) und legt je Wert einen
   Begründungssatz unter `_calibration.reasons` ab.
4. **Kette**: das Sample wird blockweise durch `Chain.from_config(neue Konfiguration)` gerechnet.
5. **`evaluate`**: Messwerte nachher und Akzeptanzkriterien (Abschnitt 5).
6. **Schreiben**: YAML (Schema wie `config/chain.default.yaml`, zusätzlich der Metadaten-Block
   `_calibration` mit Messwerten und Begründungen) und Markdown-Bericht.

Der Rückgabewert `0` bedeutet „alle Kriterien bestanden", `1` „mindestens eines nicht
bestanden" (Details im Bericht).

---

## 4. Wie die Parameter abgeleitet werden

Referenzwerte sind Konstanten am Anfang von `voicechain/calibrate.py`; alles, was keine
Norm ist, ist dort und hier als **Annahme** markiert.

| Parameter | Regel | Begründung / Annahme |
|---|---|---|
| `input_trim.gain_db` | −20 dBFS − gemessener Sprachpegel, begrenzt auf −12…+24 dB | Referenzpegel aktiver Sprache −20 dBFS RMS (memory/04); lässt ~14 dB Headroom für Sprachspitzen |
| `expander.threshold_db` | min(Rauschteppich + 10 dB, Sprachpegel − 12 dB), jeweils nach Trim; Grenzen aus `adaptive.bounds` | Übliche Praxis für Gate/Expander-Schwellen sind 8–12 dB über dem Rauschteppich; 10 dB ergeben bei Ratio 2,5 und 6 dB Knie ≈ 10,5 dB Absenkung am Teppich und damit Reserve gegen die ~6 dB Kompressor-Reduction (mit 8 dB maß der Selbsttest nur 5,8 dB Störabstandsgewinn). 12 dB unter der Sprache bleiben leise Silben erhalten. Bei Störabstand < 22 dB greift die Sprach-Regel, der Expander kann dann wenig tun |
| `deesser.freq_hz` | Schwerpunkt der Zischenergie 4–10 kHz, begrenzt auf 5–9 kHz | Bandzentrum der gemessenen Zischlaute |
| `deesser.threshold_db` | nur wenn Zischanteil > −15 dB (**Annahme**, aus dem Langzeit-Sprachspektrum abgeleitet): Zischpegel ≈ Sprache + Zischanteil + 6 dB Burst-Crest (**Annahme**); Schwelle so weit darunter, dass bei Ratio R mindestens 4 dB Reduktion entstehen (4 / (1 − 1/R)); nie über der Standardschwelle | Liegt der Standardwert bereits tiefer, bleibt er (er liefert dann mehr als 4 dB, begrenzt durch `max_reduction_db`). Darunter: Standard |
| EQ | Dichte je Messband relativ zum Ankerband 350–1000 Hz gegen Referenzkurve „flach bis 500 Hz, dann −6 dB/Okt" (**Annahme**: typisches Langzeit-Sprachspektrum); 50 % der Abweichung werden korrigiert (**Annahme**, Sprecherklang soll erhalten bleiben), Totzone ±1 dB, Korrektur wird auf den Basiswert des Bands **addiert**, Ergebnis auf ±4 dB begrenzt | Nur vier Bänder: Low-Shelf 120 Hz (Messband 80–170 Hz), Peak 250 Hz (170–350), Peak 3 kHz (2–4,5 kHz), High-Shelf 10 kHz (7–14 kHz). Vorhandene Basis-Bänder innerhalb ±20 % der Frequenz werden genutzt (z. B. 220 Hz statt 250 Hz), sonst wird ein Band angelegt |
| `compressor.threshold_db` | Sprachpegel nach Trim (−20 dBFS), Grenzen aus `adaptive.bounds` | Der Kompressor der Kette misst Sample-Spitzen; Silbenspitzen liegen ~10 dB über dem RMS (wird gemessen: „Crest-Faktor"). Schwelle auf RMS-Höhe verdichtet nur die Spitzen (~6 dB bei 3:1). Eine Schwelle 6 dB darunter ergab in der Messung ~10 dB Reduktion und machte den Störabstandsgewinn des Expanders zunichte |
| `compressor.makeup_db` | statische Kennlinie (weiches Knie) am mittleren Silbenspitzenpegel, 0…12 dB | Modell stimmt mit der echten Kette auf ±0,6 dB überein; Ziel „Ausgang ≈ Eingang", den Rest regelt der Normalizer |
| `normalizer.target_lufs` | bleibt (Basis: −18 LUFS) | Zielwert aus der Konfiguration (E3) |
| `limiter.ceiling_dbfs` | bleibt (Basis: −1 dBTP) | True-Peak-Obergrenze (E3) |
| `highpass`, Zeitkonstanten, Ratios | bleiben | Begründung in docs/02 |

---

## 5. Bericht lesen

Der Bericht (`reports/calibration.md`) hat fünf Abschnitte:

1. **Messwerte vorher** – Tabelle aller Messgrößen plus Bandenergien. Plausibilitätsprüfung:
   Rauschteppich sollte deutlich unter dem Sprachpegel liegen (Störabstand ≥ 20 dB ist gut,
   < 15 dB ist ein Raum-/Abstandsproblem, das keine Kette löst). Sprachanteil 50–80 % heißt,
   die Pausen waren da. Steht in den Hinweisen „keine Sprache erkannt", war die Aufnahme zu
   leise oder nur Rauschen.
2. **Abgeleitete Parameter** – Parameter, Wert vorher, Wert nachher, Begründung mit allen
   Zwischenwerten. Jede Zeile lässt sich mit den Messwerten nachrechnen.
3. **Messwerte nachher** – dieselben Messgrößen nach der Kette, mit Differenz.
4. **Akzeptanzkriterien** (Beauftragung §6.3):

   | Kriterium | Ziel | Hinweis |
   |---|---|---|
   | Rauschteppich gesenkt | Störabstand Sprache/Rauschen um ≥ 6 dB verbessert | Bewertung bei gleichem Sprachpegel, weil der Normalizer leise Aufnahmen insgesamt anhebt; die absoluten Werte stehen daneben |
   | Zischanteil | gesunken (wenn De-Esser nötig), sonst nicht schlechter als +1 dB | |
   | Lautheit im Ziel | integriert −18 LUFS ± 2 LU | Die Lautheitsregelung braucht einige Sekunden, das geht in den integrierten Wert ein |
   | True Peak unter Ceiling | ≤ −1 dBTP (+0,1 dB Messtoleranz) | 4x-Oversampling-Messung |

5. **Annahmen und Grenzen** – die oben markierten Annahmen.

Ist ein Kriterium „nicht bestanden", steht in der Spalte „Hinweis" der Grund. Typische Fälle:
Störabstand der Aufnahme zu klein (Expander darf nicht tiefer, sonst schneidet er Sprache),
Aufnahme ohne Pausen (Rauschteppich wird überschätzt), Aufnahme übersteuert.

---

## 6. A/B-Vergleich

```
python -m voicechain process --in sample.wav --out sample.default.wav --config config/chain.default.yaml
python -m voicechain process --in sample.wav --out sample.calibrated.wav --config config/chain.calibrated.yaml
```

Beide Dateien und das Original im Wechsel mit Kopfhörer anhören. Worauf achten:

- Pausen: Raumrauschen soll leiser werden, Sprachanfänge dürfen nicht „abgebissen" klingen.
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
`--freeze` lässt sie mitlaufen und protokollieren, aber nichts verändern (Abschnitt 9).

---

## 8. Was die adaptive Regelung tut

Klasse `AdaptiveController` in `voicechain/adaptive.py`; Einstellungen unter `adaptive:` in der
YAML (`learn_s`, `interval_s`, `max_step_db`, `bounds`, `log_path`).

**Messgrundlage.** Die Runtime füttert `LiveAnalyzer.push()` mit jedem Eingangsblock (rohes
Mikrofonsignal, vor der Kette). Der Analyzer hält 20 s Historie aus 50-ms-Rahmen und liefert
mit `snapshot()`:

```
{"ready": bool,               # mindestens 2 s Historie
 "speech_detected": bool,     # Abstand leise/laute Rahmen >= 6 dB
 "noise_floor_dbfs": float,   # 10. Perzentil der Rahmenpegel
 "speech_level_dbfs": float,  # Energiemittel der aktiven Rahmen
 "sibilance_ratio_db": float, # 5-10 kHz minus 0,3-3 kHz (aktive Rahmen)
 "speech_active": bool,       # letzter Rahmen war Sprache
 "speech_ratio": float,       # Anteil aktiver Rahmen
 "frames": int, "history_s": float}
```

Die Runtime legt diesen Schnappschuss alle `interval_s` als `meters["analysis"]` neben die
Meter der Kette (`chain.get_meters()`) und ruft `controller.update(meters)` auf. Da der
Analyzer vor dem Input Trim misst, rechnet der Controller `input_trim.gain_db` auf die Pegel
auf.

**Lernphase.** In den ersten `learn_s` Sekunden (Standard 30 s) wird nur gemessen und je
Intervall ein `learning`-Eintrag geschrieben. Erst danach wird geregelt.

**Regelgesetze** (dieselben Zielwerte wie die Kalibrierung, Abschnitt 4):

| Parameter | Ziel | Totzone | Grund im Log |
|---|---|---|---|
| `expander.threshold_db` | min(Rauschteppich + 10, Sprache − 12) (Konstante `EXPANDER_FLOOR_MARGIN_DB` in calibrate.py, von adaptive.py importiert) | ±2 dB | `noise_floor_track` |
| `deesser.threshold_db` | wie Kalibrierung aus Sprachpegel und Zischanteil; unterhalb der Referenz zurück zum kalibrierten Ruhewert | ±1,5 dB | `sibilance_track`, `sibilance_rest` |
| `compressor.threshold_db` | Sprachpegel (nach Trim) | ±2 dB | `speech_level_track` |

Je Intervall höchstens ein Schritt von `max_step_db` (Standard 0,5 dB) in Richtung Ziel, nur
wenn der Fehler außerhalb der Totzone liegt, nur innerhalb der `bounds`, und nur, wenn der
letzte Eingriff an diesem Parameter mindestens zwei Intervalle zurückliegt. Ein Wert, der
außerhalb der Grenzen startet, wird in Schritten zurückgeholt, nie gesprungen. Weil die Ziele
aus dem Eingangssignal berechnet werden (nicht aus dem Ausgang), gibt es keine Rückkopplung
und kein Pendeln: die Richtung ändert sich nur, wenn sich das Signal ändert.

**Schutzregeln**

| Situation | Reaktion | Grund im Log |
|---|---|---|
| Limiter-Gain-Reduction im Intervall im Mittel > 3 dB | Kompressor-Schwelle wird nicht angehoben; `normalizer.max_gain_db` wird auf den aktuellen Normalizer-Gain gedeckelt (mehr Pegel gibt es nicht); Ereignis `limiter_protect` mit `hold_normalizer_gain: true` | `limiter_overload` |
| Limiter-Reduction wieder < 1,5 dB | `normalizer.max_gain_db` zurück auf den konfigurierten Wert | `limiter_recovered` |
| Expander-Gain-Reduction während Sprachaktivität im Mittel > 6 dB (Sprache wird abgeschnitten) | Expander-Schwelle sofort um `max_step_db` senken, ohne Totzone und ohne Wartezeit | `speech_cut` |

Keine Sprache erkannt (`speech_detected` false): nur die Expander-Regel läuft (Rauschteppich
folgt), De-Esser und Kompressor bleiben.

---

## 9. JSONL-Log lesen

Datei aus `adaptive.log_path` (Standard `logs/adaptive.jsonl`), eine JSON-Zeile je Ereignis,
`t` ist die monotone Uhr des Prozesses in Sekunden.

Änderungen:

```
{"t": 48.0, "stage": "expander", "name": "threshold_db", "old": -34.0, "new": -34.5, "reason": "noise_floor_track"}
```

Ereignisse (Schlüssel `event`):

| event | Bedeutung |
|---|---|
| `learning` | Lernphase, `remaining_s` Restzeit |
| `learn_done` | Lernphase beendet, Regelung aktiv |
| `no_analysis` | Kein Analyzer-Schnappschuss oder noch nicht `ready` (< 2 s Historie) |
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
zurücknehmen, neu kalibrieren).

---

## 10. Wann `--freeze`

- Während einer Präsentation, bei der nichts „wandern" darf (z. B. wenn der Raum während
  der Pause absichtlich still ist und der Expander sonst die Schwelle absenkt, die beim
  Zurückkommen zu tief liegt). Freeze protokolliert weiter, so dass man hinterher sieht,
  was die Regelung getan hätte.
- Zum Prüfen einer manuell geänderten YAML: `run --freeze` zeigt im Log die Abweichung der
  Regelziele vom festen Wert.
- Dauerhaft fest: `--no-adaptive` oder `adaptive.enabled: false` in der YAML.

---

## 11. Grenzen des Verfahrens

- **Ein Sample, ein Raum.** Die Kalibrierung gilt für die Aufnahmesituation. Anderer Raum,
  anderer Abstand oder anderes Mikrofon-Gain: neu kalibrieren. Die Regelung gleicht nur
  langsame Drift innerhalb der `bounds` aus.
- **Störabstand.** Liegt der Rauschteppich näher als 22 dB an der Sprache, kann der Expander
  die geforderten 6 dB nicht ohne Sprachverlust bringen; die Kette macht das hörbar besser,
  aber das Kriterium fällt durch. Abhilfe: leiserer Raum, geringerer Abstand, Lüfter weg.
- **Kompression kostet Störabstand.** Jede Dynamikverdichtung hebt die Pausen relativ zur
  Sprache an; der Störabstandsgewinn der Kette ist Expander-Absenkung minus
  Kompressor-Reduction. Deshalb bleibt die Kompressor-Schwelle auf Höhe des Sprach-RMS.
- **Referenzen sind Annahmen.** Zischanteil-Referenz (−15 dB), Burst-Crest (6 dB),
  EQ-Referenzkurve und 50-%-Korrektur stammen aus dem typischen Langzeit-Sprachspektrum,
  nicht aus einer Norm. Für sehr tiefe oder sehr hohe Stimmen kann die EQ-Korrektur
  verschieben; der Bericht zeigt, welches Band warum angefasst wurde.
- **EQ und De-Esser überlappen.** Die EQ-Messung umfasst die Zischlaute; ein Hochshelf-Cut
  und der De-Esser wirken bei zischenden Stimmen beide. Die Begrenzung auf ±4 dB und
  50 % Korrektur halten das im Rahmen.
- **Lautheit.** Die Lautheitsmessung ist ITU-R BS.1770 (K-Bewertung, Gating), das Ziel
  −18 LUFS ist ein Praxiswert für Konferenzsysteme (E3), keine Norm.
- **Synthetisches Sample.** `make-sample` ersetzt keine echte Aufnahme; es dient zum Proben
  des Ablaufs und für die Tests.
- **Metadaten in der YAML.** Der Block `_calibration` (Messwerte, Begründungen) steht in der
  kalibrierten YAML; Werkzeuge, die die Datei lesen, müssen Schlüssel mit führendem
  Unterstrich als Metadaten behandeln.
