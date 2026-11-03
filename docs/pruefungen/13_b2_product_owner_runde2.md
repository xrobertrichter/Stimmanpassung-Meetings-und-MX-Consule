# PO-Zweitprüfung Beauftragung 2 (Live-Bedienung MX Creative Console)

Prüfer: Product Owner (audiofachkundig) · Repo `/home/user/Test`, Stand `ae522ec` · Datum 2026-11-03
Grundlage: `docs/06_Beauftragung_MX_Console.md` (§2 und §7 geändert), Erstprüfung `pruefung_po_b2.md` (Stand e680b56). Keine Repo-Datei geändert. Skripte und Rohdaten: `<pruefordner>/b2/po/` (`sim_cap_r2.py`, `sim_checks_r2.py`, `sim_meeting_r2.py` + `sim_meeting_r2.log` + `r2_sim_A_result.json`, `sim_tone.py`, `sim_loud.py`).

## 1. Kurzurteil

**Abnehmbar.** Alle 13 Befunde der Erstprüfung sind behoben oder (PO2-14) adressiert; die geänderte Beauftragung (§2 Belegung, Reset = Bedienzustand, Intensität nur Kompressor, Lautheit −24…−14 LUFS, Dial ohne Banner, Mute sichtbar E2 K8; §7 Anschlag/NaN/peek/Socket/slow) ist im Code, Manifest, docs/05 und README konsistent umgesetzt. Selbst nachgeprüft: Selftest 12/12, `pytest -m "not slow"` 489 bestanden / 1 übersprungen (slow-Lauf nicht wiederholt, Koordinator meldet 23 grün). Die 10-min-Simulation mit gleichem Drehbuch zeigt die neue Reset-Semantik wie beabsichtigt: Pausenabstand nach Reset im Lüfterraum 30,3 dB statt 22,1 dB, Freeze aus, Regelung läuft weiter; Kill-Switch pausiert die Lernphasen-Uhr (15,0 s verschoben) und hinterlässt keine Spuren.

Drei neue Befunde, alle niedrig (ein Antworttext auf dem häufigsten Griff, Mikroschritte der neuen `speech_cap`-Regel im Log, veraltete Socket-Pfad-Sätze in docs/05 §6 und README). Keiner blockiert die Abnahme; PO2-15 sollte vor der Übergabe an den Nutzer behoben werden, weil er bei jedem Dial-Druck in Neutralstellung „abgelehnt“ meldet.

## 2. Statustabelle Erstprüfung

| Nr. | Schwere (R1) | Status | Nachweis (Stand ae522ec) |
|---|---|---|---|
| PO2-1 Sprach-Kappe mit Raum-Offset | hoch | **behoben** | `sim_cap_r2.py` Fall 2 (Sprecher 5 dB leiser, Raum −50, Raum +6): Druck 1 „Raum +2“, Druck 2 „Raum +3 dB … durch Sprach-Kappe auf −37 dBFS begrenzt“ (Offset gespeichert 3,03 = wirksam), Druck 3 `ok=false` „Raum am Anschlag … durch Sprach-Kappe begrenzt“. Schwelle Ende −37,24 dBFS = Kappe −0,2 dB (R1: +4,5 dB). Leise Silben relativ −7,1 → −7,1 dB (R1: −8,6), Expander-GR max in Sprachintervallen 8,9 dB (R1: 15,1), `speech_cut` 0. Controller: `_track(include_offset=False)` mit `expander_threshold_target(floor + offset, speech)`, neue Regel `speech_cap` zieht über der Kappe liegende Schwellen herunter |
| PO2-2 Reset lässt Freeze, verwirft Nachführung | mittel | **behoben** (Beauftragung geändert) | Meeting-Sim: Reset bei 480 s → Expander −34,51 → −38,51 (nur Offset +4 abgezogen), `frozen True → False`, Regelung schreitet bei 482 s auf −38,01 (Ziel −35,95 innerhalb Totzone); tiefe Pausen nach Reset −48,8 dBFS, Pausenabstand **30,3 dB** (R1: −40,7 / 22,1 dB). Einzelcheck: Reset bei Freeze + Offsets + nachgeführter Schwelle −38 → −38 bleibt, Offsets 0, `user_offsets {}` |
| PO2-3 Reset ohne Limiter-Schutz-Rücknahme | mittel | **behoben** | `max_gain_db` 4,0 → 12,0, `limiter_protect_active` True → False nach Reset (`reset_protection()`) |
| PO2-4 Zisch +/− auf zwei Ebenen | mittel | **behoben** | Manifest E1 K3 „Zischen −“, K4 „Zischen +“; Freeze E1 K7, Reset K8; Status E2 K3 |
| PO2-5 Dial-Druck = unsichtbarer Mute | mittel | **behoben** | Dial-Druck `loudness-reset` („Lautheit 0“), Mute E2 K8 mit Display; docs/05 §2/§3 begründet |
| PO2-6 Mitteilung je Dial-Rastung | mittel | **behoben** | `louder.sh`/`quieter.sh` → `ctl … --notify-errors` (Banner nur bei Ablehnung/Fehler); am Anschlag kommt „abgelehnt: Lautheit am Anschlag (wirksam: Ziel −14 LUFS, +4 LU)“ – richtig so |
| PO2-7 Intensität koppelt De-Esser | niedrig | **behoben** | `dense`: De-Esser −30,0 → −30,0, Kompressor −20 → −22, `user_offsets` deesser 0. Pegelbuckel durch festes Makeup bleibt (Short-Term −19,5 → −17,9 LUFS, Normalizer korrigiert in ~1 s) – akzeptiert, siehe Restrisiken |
| PO2-8 Anschlag antwortet ok | niedrig | **behoben** | Basis −34: +2 → −32, +4 → −30, dritter Druck `ok=false` „Raum am Anschlag (wirksam: Expander −30 dBFS, Raum +4 dB) – durch Grenze …“, Offset bleibt 4; Rückweg wirkt sofort (mit und ohne Regelung) |
| PO2-9 Snapshot ohne Nachführung | niedrig | **behoben** | Kette −38 (nachgeführt) + Raum +2 → Datei −38,0; `_live` = {created, offsets, tracked, base}; `validate_config` ok |
| PO2-10 Labels | niedrig | **behoben** | „Zischen −/+“ (9), „DSP an/aus“ (10), alle ≤ 10 Zeichen; Hinweis: „−“ ist U+2212 (siehe Restrisiken) |
| PO2-11 README Laien/`.venv`/Zählung | niedrig | **behoben** | Tabelle „Wann drücke ich was (Symptom → Taste)“ in Laiensprache, Abschnitt „Voraussetzung: Python mit den Abhängigkeiten“ (Options+-PATH, `.venv`, `VOICECHAIN_PYTHON`, Homebrew), Zählung 20 = 16 Tasten + 3 Dial + marker in docs/05/06/README gleich; `ctl` ohne PyYAML fällt auf Standard-Socket zurück |
| PO2-12 Lernphasen-Uhr läuft im Aus-Zustand | niedrig | **behoben** | Aus bei 10 s, an bei 40 s: `t_start` 0 → 30, `learning` True bis 59 s, False bei 61 s; Meeting-Sim: 15,0 s Aus → `t_start` +15,003; Relearn im Aus-Zustand wird abgelehnt („erst einschalten“); Reset im Aus-Zustand schaltet ein und löst die Pause |
| PO2-13 Obergrenze −12 LUFS | niedrig | **behoben** | `LOUDNESS_TARGET_BOUNDS_LUFS = (−24, −14)`; `set_offset +6` → wirksam +4, Ziel −14, Ausgang −14,4 LUFS, True Peak −2,11 dBTP, Limiter 0,02 dB; 9. Rastung `ok=false` „am Anschlag“ |
| PO2-14 zeitabhängiger CLI-Test | Hinweis | adressiert | Standardlauf 489/489 grün in 4:29 min (keine parallele Last); `slow`-Marker eingeführt |

## 3. Neue Befunde

| Nr. | Schwere | Fundstelle | Befund | Empfehlung |
|---|---|---|---|---|
| PO2-15 | niedrig | `voicechain/livecontrol.py` `_apply_offset()` (Zweig `loudness_lu`, allgemein für `set_offset`); `actions/loudness-reset.sh` mit `--notify` | Dial-Druck „Lautheit 0“ bei bereits neutraler Lautheit antwortet `ok=false` „Lautheit am Anschlag (wirksam: Ziel −18 LUFS, +0 LU)“ → Banner „abgelehnt: …“, Exit 1. Für ein „Zurück auf Standard“ ist „am Anschlag“ falsch und wirkt wie ein Fehler; das ist der häufigste Griff. Gleiches Muster für jedes `set_offset` auf den schon aktiven Wert | `set_offset` ohne Änderung mit `ok=true` „Lautheit unverändert (Ziel −18 LUFS)“ beantworten (nur `offset`-Schritte am Bereichsende sind „am Anschlag“) – oder `loudness-reset.sh` auf `--notify-errors` stellen; Test ergänzen |
| PO2-16 | niedrig | `voicechain/adaptive.py` `_propose()` Regel `speech_cap` (immediate, ohne Totzone) | Liegt die Schwelle auf der Kappe, folgt sie jeder Schwankung der Sprachpegelschätzung: `sim_cap_r2.py` Fall 2 schreibt 68–126 s neun `speech_cap`-Einträge mit Schritten von 0,0–0,1 dB (−37,0 → −37,24) ohne Abstandsregel. Hörbar nicht, aber Log-Rauschen, und die gespeicherte Raum-Offset-Zahl (3,03) entfernt sich langsam vom wirksamen Wert (2,8) | Kleine Totzone für die Kappe (z. B. 0,5 dB: nur eingreifen, wenn Schwelle > Kappe + 0,5) oder Mindestschritt 0,25 dB; optional Offset nach `speech_cap`-Schritten nachführen |
| PO2-17 | niedrig | `docs/05_MX_Creative_Console.md` §6 („Die mitgelieferte `config/chain.default.yaml` setzt derzeit `control.socket_path: logs/control.sock`“), `integrations/mx_console/README.md` Einrichtung Schritt 5 („die mitgelieferte … tut das“), `docs/06` §3 („Default `logs/control.sock`“) | Die YAML hat den Eintrag inzwischen nur noch auskommentiert (Zeile 89), Standard ist `/tmp/voicechain-<user>.sock` (README-Diagramm, docs/05 §6 Entscheidung, `control.default_socket_path`). Zwei Sätze widersprechen dem und schicken Nutzer auf eine falsche Fehlersuche | Beide Sätze streichen bzw. auf den neuen Standard umformulieren; docs/06 §3 Default im Lieferumfang angleichen (oder in §7 als Änderung nennen) |

## 4. Fachliche Endbeurteilung Belegung, Manifest, docs/05, README

- **Ebene 1** (Raum ±, Zischen ±, Natürlich/Dichter, Freeze, Reset; Dial Lautheit / Lautheit 0): jede Korrektur hat ihre Gegenrichtung auf derselben Ebene, der Notausgang liegt rechts außen, Freeze daneben – stimmig für Meetings. Dass Status auf Ebene 2 gewandert ist, ist vertretbar: jede Taste meldet ihren Zustand selbst, und die Statuszeile läuft mit; wer häufig „Was ist aktiv?“ braucht, findet es mit einem Ebenenwechsel.
- **Ebene 2** (Wärmer/Heller, Status, DSP an/aus, Neu lernen, Snapshot, Bypass, Mute): Mute sichtbar mit Display, Marker zu Recht unbelegt. „DSP an/aus“ ist verständlich; Beschreibungen nennen Wirkung in Laiensprache („lispelt“, „gleichmäßiger“).
- **Dial**: Lauter/Leiser mit `--notify-errors` – kein Banner je Rastung, Hinweis am Anschlag; Druck = Lautheit 0 mit `--notify` (siehe PO2-15).
- **Richtungen/Schrittweiten**: unverändert korrekt (Zischen − = Schwelle tiefer, Raum + = Schwelle höher, Wärmer = Tilt −1, Natürlich = Kompressor +2); `--value` wird auf eine Schrittweite begrenzt – sinnvolle Entscheidung, in docs/05 begründet.
- **docs/05**: Begründungen je Taste aktualisiert, §5 Reset/Neu lernen/Snapshot trennt sauber „Bedienzustand zurück“ von „Regelung zurück“; §6 enthält den veralteten YAML-Satz (PO2-17).
- **README** aus Laiensicht: Die Symptom-Tabelle ist das, was fehlte; Voraussetzungen und Fehlersuche sind jetzt vollständig für Options+-Nutzer ohne Audiovorwissen. Schritt 5 enthält den veralteten Halbsatz (PO2-17).

## 5. Simulationszahlen Zweitprüfung

### 5.1 10-min-Meeting, gleiches Drehbuch (`sim_meeting_r2.py`, Variante A)

Kalibrierung wie R1 (Trim +6,05, Expander −44,01, De-Esser −16,47, Kompressor −20,0, Makeup 6,21 dB, Ziel −18 LUFS); Teppich −60 dBFS, ab 300 s −52 dBFS.

| t | Ereignis | Expander | Kompressor | Antwort / Zustand |
|---|---|---|---|---|
| 120 s | Weniger Raum ×2 | −44,01 → −40,01 | – | „Raum +4 dB (weniger Raum, Expander −40 dBFS)“; Kappe zu dem Zeitpunkt −31,9 dBFS (nicht bindend) |
| 240 s | Natürlich ×1 | – | −20,00 → −18,00 | De-Esser **unverändert** −16,47 (R1: −15,47) |
| 300–360 s | Lüfter +8 | 11 Schritte à +0,5 dB auf −34,51 | – | kein Schritt abwärts, Offset gehalten |
| 360 s | Freeze | – | – | FROZEN, 360–480 s keine Änderung |
| 420/440 s | Mute | – | – | Ausgang −240 dBFS; Normalizer-Gain 0,17 → 0,14 → 0,23 dB |
| 480 s | Reset | −34,51 → **−38,51** | −18,00 → −20,00 | „Reset: Offsets 0 … Freeze/Mute/Bypass aus, Verarbeitung an; nachgeführte Schwellen bleiben“; `frozen True → False`; 482 s Regelschritt auf −38,01 (Ziel −35,95, Totzone 2) |
| 540 s | Kill-Switch aus | unverändert | – | `paused True`, Freeze geparkt; Ausgang −20,1 LUFS roh |
| 555 s | Kill-Switch an | unverändert | – | `t_start` 0 → 15,003 s (Uhr-Pause), `paused False`, `frozen False` wiederhergestellt; 555–600 s keine Nachholschritte |

| Phase | t [s] | Ausgang LUFS | tiefe Pause ein → aus [dBFS] | Pausenabstand [dB] | R1-Wert | Expander Ende |
|---|---|---|---|---|---|---|
| P0 Start | 30–120 | −18,3 | −60,0 → −59,5 | 41,0 | 41,0 | −44,01 |
| P1 Raum +4 | 120–240 | −18,3 | −60,0 → −64,0 | 45,5 | 45,5 | −40,01 |
| P2 + Natürlich | 240–300 | −18,2 | −60,0 → −65,0 | 46,7 | 46,7 | −40,01 |
| P3 Lüfter, Regelung | 300–360 | −18,3 | −52,0 → −49,2 | 30,7 | 30,7 | −36,01 |
| P4 Freeze | 360–420 | −18,3 | −52,0 → −54,4 | 35,9 | 35,9 | −34,51 |
| P5 Mute | 420–440 | Stille | – | – | – | −34,51 |
| P6 nach Mute | 440–480 | −18,2 | −52,0 → −54,5 | 36,0 | 36,0 | −34,51 |
| P7 nach Reset | 480–540 | −18,3 | −52,0 → **−48,8** | **30,3** | 22,1 | −38,01 |
| P8 Kill-Switch aus | 540–555 | −20,1 | −52,0 → −46,0 | 25,6 | 25,6 | −38,01 |
| P9 Kill-Switch an | 555–600 | −18,3 | −52,0 → −48,5 | 30,0 | 22,1 | −38,01 |

Lesart: Bis 480 s identisch mit R1 (bis auf den entkoppelten De-Esser). Nach Reset bleibt die Lüfter-Nachführung erhalten, nur der Offset +4 geht ab: 30,3 dB Pausenabstand statt 22,1 dB, und die Regelung läuft (ein Schritt bei 482 s). Die verbleibende Differenz zu P6 (36,0 dB) ist der zurückgenommene Raum-Offset – gewollt.

### 5.2 Sprach-Kappe (`sim_cap_r2.py`, Sprecher 5 dB leiser, Raum −50 dBFS, Raum +6 bei 60 s)

| | R1 (e680b56) | R2 (ae522ec) |
|---|---|---|
| Antworten auf drei Drücke | +2 / +4 / +6, alle `ok` | +2 / **+3 (durch Sprach-Kappe auf −37 dBFS begrenzt)** / **`ok=false` am Anschlag** |
| gespeicherter Offset | 6,0 | 3,03 (= wirksam) |
| Schwelle Ende vs. Kappe (−37,0) | −32,5 (**+4,5 dB**) | −37,24 (−0,2 dB) |
| leise/laute Silben vorher → nachher | −7,1 → −8,6 dB | −7,1 → −7,1 dB |
| Expander-GR max in Sprachintervallen | 15,1 dB | 8,9 dB |
| `speech_cut` | 0 | 0 |
| Regelungseinträge nach dem Druck | – | `speech_cap` 68 s (−37,0) und 108–126 s neun Mikroschritte 0,0–0,1 dB (PO2-16) |

Fall 1 (kalibrierter Sprecher, Kappe −32,1 nicht bindend): +2/+4/+6 wie bisher, keine Regelungseingriffe. Fall 3 (leiser Sprecher ohne Offset): Regelung endet −38,5 = Kappe −1,5 dB.

### 5.3 Einzelchecks (`sim_checks_r2.py`)

- Reset nach Limiter-Schutz + Freeze + Offsets (Raum +2, Intensität +1) + nachgeführter Expander −38: danach Expander −38,0, Kompressor −20,0, `max_gain_db` 12,0, Schutz aus, Freeze aus, Offsets 0.
- Neu lernen (Raum +2 aktiv): Expander −43,0 (= Kalibrierwert −45 + 2), `learning True`, Offset bleibt 2; im Aus-Zustand abgelehnt.
- Anschlag (Basis −34, Bound −30) mit und ohne Regelung: dritter Druck `ok=false`, Offset bleibt 4, Rückweg sofort −32.
- Lautheit: 9 × Lauter → 8 wirksam (+4, Ziel −14), 9. `ok=false`; „Lautheit 0“ von +4 → `ok` „Lautheit +0 LU (Ziel −18 LUFS)“; „Lautheit 0“ bei 0 → `ok=false` „am Anschlag“ (PO2-15); NaN abgelehnt.
- Intensität +1: De-Esser unverändert, Kompressor −22, `user_offsets` deesser 0.
- Snapshot: Ist-Werte (Expander −38 = Kette), `_live.tracked/base`, validiert.
- Uhr-Pause: aus 10 s / an 40 s → Lernphase endet bei 60 s statt 30 s; Reset im Aus-Zustand schaltet ein, löst Pause und Freeze.
- Status leert Akkumulatoren nicht (`peek` 6,65 dB = `drain` 6,65 dB danach).

### 5.4 Knackfreiheit und Lautheits-Offset (unverändert zu R1)

Ton 1 kHz: max. Blocksprung in der 30-ms-Rampe ≤ 0,37 dB (Kill-Switch an), außerhalb ≤ 0,21 dB; Mute aus 0,19 dB; Reset 0,19 dB. Lautheits-Offset 0/+3/+6(→+4)/−6 LU → −18,4/−15,3/−14,4/−24,4 LUFS, True Peak −4,35/−2,48/−2,11/−10,35 dBTP, Limiter-GR ≤ 0,02 dB.

### 5.5 Testläufe

`python -m voicechain selftest`: 12/12 bestanden. `python -m pytest -q -m "not slow"`: 489 bestanden, 1 übersprungen (root), 23 `slow` deselektiert, 4:29 min, ohne parallele Last. Der `slow`-Lauf wurde nicht wiederholt (Koordinator: 23 grün); die hier relevanten Langläufe (Kappe 3 × 240 s, Meeting 600 s) sind über die eigenen Skripte abgedeckt.

## 6. Restrisiken (keine Befunde)

1. **Kappe ohne Regelung und in der Lernphase:** `_expander_cap()` liefert `None` ohne Controller (`run --no-adaptive`) und vor der ersten Regelungsentscheidung (erste 30 s, nach Neu lernen). In dieser Zeit begrenzen nur die `bounds` den Sofortschritt; mit Regelung holt die `speech_cap`-Regel das ab der ersten Entscheidung in 0,5-dB-Schritten (≤ 24 s für +6) zurück. docs/05 §4 sollte den Fall „ohne Regelung: keine Kappe“ nennen.
2. **Pegelbuckel bei Intensitätsstufen:** festes Makeup (kalibriert 6,2 dB) → ±1,5 dB Short-Term für ~1 s bis der Normalizer nachzieht. Im Meeting unauffällig; wer es vermeiden will, führt das Makeup um ⅔ des Schwellenschritts mit.
3. **Reset mitten im Satz:** Offsets springen sofort (bis 6 dB Expander/Kompressor, 6 dB De-Esser), knackfrei, mit kurzem Lautheitsbuckel – Notausgang-Charakter, dokumentiert.
4. **Label „Zischen −“ mit U+2212:** Ob das Tastendisplay der Konsole den typografischen Minus darstellt, ist hier nicht prüfbar; bei Problemen ASCII-„-“ verwenden (Länge bleibt 9).
5. **Kill-Switch nach langer Pause:** Analyzer-Historie (20 s) stammt von vor dem Aus-Zustand; die erste Entscheidung nach dem Einschalten nutzt sie. Bei verändertem Raum folgt die Regelung innerhalb von ~20 s. Akzeptabel.
6. **Hörprüfung** mit echtem Mikrofon steht wie in Beauftragung 1 aus (Auflage aus docs/04).
