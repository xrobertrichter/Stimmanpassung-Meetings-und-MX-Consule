# Blindprüfung Beauftragung 2 (MX Creative Console) – Prüfer B2

Stand: 2026-11-03 · Repository `/home/user/Test` @ `e680b56` · Beauftragung `docs/06_Beauftragung_MX_Console.md` (Basis `docs/00_Beauftragung.md` §4).
Nicht gelesen: `memory/`, `docs/04_Pruefprotokoll.md`, `docs/pruefungen/`. Keine Repo-Änderung (`git status` leer, Socket/Snapshots/Logs nur unter `scratchpad/reviews/b2/blind/`).

## 1. Kurzurteil

**Abnehmbar mit Auflagen.** Alle 20 CLI-Kommandos sind über den echten Unix-Socket erreichbar (Exit 0, deutsche Antwortzeile, vollständiger `state`), Grenzen klemmen wie spezifiziert (20× louder → Ziel −12 LUFS, 20× quieter → −24, Raum/Zischen ±6, Intensität/Tilt ±3), alle 20 Aktionsskripte laufen (`bash -n` sauber, ausführbar, Git-Mode 100755), Manifest ist konsistent (Labels ≤ 10 Zeichen, beide Ebenen, Dial identisch). `pytest -q`: 491 bestanden, 1 übersprungen; Selftest 12/12. Eigene Simulationen bestätigen §6.3–§6.6: Lautheit ±1,00 LU, Raum ±4 → Pausen-RMS −5,6/+6,0 dB, Zischen −4 → De-Esser-GR ×1,9, Tilt ±2 → ±1,91 dB Bandbalance (Frequenzgang exakt wie docs/02), Intensität +2 → Kompressor-GR +1,6 dB; 300-s-Regelung hält Raum +4 exakt (kein Rückschritt); Reset = alle `get_params` identisch mit Konfig; Mute/Bypass/Kill-Switch außerhalb der 30-ms-Rampe max. 0,20 dB Blocksprung; Kill-Switch 15 % Rechenzeit.

Auflagen: **BL2-1 (mittel)** – die in docs/06 §2, docs/05 und docs/03 zugesicherte Sprach-Kappe („Weniger Raum“ bleibt ≤ Sprache − 12 dB) ist nicht implementiert: der Offset wird *nach* der Kappe addiert; gemessen liegt die Expander-Schwelle mit Raum +4 um 2,0 dB über der Kappe. **BL2-2 (niedrig–mittel)** – jedes Konsolenkommando leert über `LiveControl.state()` → `Chain.get_meters()` die Gain-Reduction-Statistik, die die Regelung am nächsten Takt auswertet. Rest: Doku-Widersprüche (Anschlag „abgelehnt“, `--value`-Durchreichung, „Zeitstempel im Kommentar“).

## 2. Anforderungsmatrix

Status: ✅ erfüllt · ◐ teilweise · ❌ nicht erfüllt · ℹ nicht prüfbar (macOS). Belege: Datei:Zeile oder Ausgabe aus `e2e_log.txt` / `sim_log.txt` / `sim_click_log.txt`.

### §2 Ebene 1 „Meeting“

| Element | Funktion / Wirkung / Schritt / Grenze (Soll) | Status | Beleg |
|---|---|---|---|
| Dial drehen | Lautheit, Ziel-Lautheit Normalizer, ±0,5 LU je Rastung, −24…−12 LUFS | ✅ | `cli.py:676-677` (±0,5), `livecontrol.py:49,56` (Offset ±6, Ziel-Klemme −24…−12); E2E: 20× louder → `loudness_lu=6.0 target=-12.0`, 40× quieter → `-6.0/-24.0`; Sim: +1 LU → +1,00 LU gemessen |
| Dial drücken | Mute an/aus, 30-ms-Blende, Toggle, Statuszeile MUTE | ✅ | `runtime.py:68,430-457,460-466`; Statuszeile `… \| MUTE \|` (eigener Lauf); Sim: Rampe 5–6 Blöcke à 5,3 ms, danach exakt 0,0; außerhalb Fenster max. 0,195 dB |
| K1 Weniger Raum | Expander-Schwelle höher, +2 dB je Druck, Offset −6…+6; Sprach-Kappe (Sprache − 12 dB) bleibt | ◐ | Schritt/Grenze ✅ (`cli.py:678`, E2E 5× → `room_db=6.0 exp -45→-39`). **Sprach-Kappe ❌**: `adaptive.py:578` addiert Offset nach `min(floor+10, speech−12)`, `livecontrol.py:256` ohne Kappe; Sim C/B: Kappe −32,0, Schwelle −30,03 (= Kappe +2,0 dB) → BL2-1 |
| K2 Mehr Raum | Schwelle tiefer, −2 dB je Druck | ✅ | `cli.py:679`; E2E 8× → `room_db=-6.0 exp=-51.00`; Sim Raum −4: Pausen-RMS +5,95 dB |
| K3 Weniger Zischen | De-Esser-Schwelle tiefer, −2 dB, −6…+6 | ✅ | `cli.py:680` (`deess-more` → −2); E2E 5× → `deess_db=-6.0 des -30→-36`; Sim −4: GR 0,33 → 0,63 dB, Zischanteil −0,9 dB |
| K4 Natürlicher | Intensität −1: Komp +2, De-Esser +1; Stufe −3…+3 | ✅ | `livecontrol.py:58-59,235-242`; E2E `natural` → `comp -20.5→-18.5, des -29.5→-28.5`; 8× → −3 |
| K5 Dichter | Intensität +1: Komp −2, De-Esser −1 | ✅ | E2E 5× dense → `intensity=3.0 comp -20→-26 des -24→-27`; Sim +2: Komp-GR 3,72 → 5,30 dB |
| K6 Freeze/Weiter | Regelung einfrieren/freigeben, Toggle, Statuszeile FROZEN | ✅ | `livecontrol.py:319-331`; E2E on/off/toggle/an/aus korrekt; Statuszeile `adaptive FROZEN` |
| K7 Reset | Offsets 0, Lautheit Kalibrierwert, Regelung auf rest, Mute aus, Bypass aus, sofort mit Blende | ✅ | `livecontrol.py:300-317`; E2E nach allen Offsets + Drift (−44,5/−29,5/−20,5) → −45/−30/−20, Mute/Bypass aus; Sim 6.5: `param diffs after reset: none` (inkl. Tilt, Power off → an). Hinweis BL2-7: FROZEN bleibt |
| K8 Status | Mitteilung: Lautheit, Störabstand, aktive Offsets, Freeze/Mute, nur lesend | ✅ | `livecontrol.py:394-411`; E2E: `Lautheit -27,9 LUFS (Ziel -18 LUFS) \| Störabstand 25,2 LU \| Offsets: keine \| FROZEN` |

### §2 Ebene 2 „Feinabstimmung“

| Element | Soll | Status | Beleg |
|---|---|---|---|
| K1 Wärmer | Tilt −1 dB (LS 250 Hz +½, HS 4 kHz −½), −3…+3 | ✅ | `eq.py:98-102`; E2E 5× → `tone_db=-3.0 tilt=-3.0`; Sim Tilt −2: +0,97/+0,5/0/−0,5/−0,96 dB @100/250/1k/4k/8k |
| K2 Heller | Tilt +1 dB | ✅ | E2E 8× brighter → `+3.0`; Sim +2 → Bandbalance +1,91 dB |
| K3 Mehr Zischen | De-Esser-Schwelle höher, +2 dB | ✅ | `cli.py:681`; E2E 8× deess-less → `+6.0 des=-24.00` |
| K4 Verarbeitung an/aus | Nur Trim+Limiter rechnen, Analyzer+Regelung pausieren, Wiedereinschalten setzt Stufen zurück + 30 ms Blende, Statuszeile OFF, `loudness-reset` bleibt CLI | ✅ | `chain.py:143-175,198-210`, `runtime.py:400,476-487,581-583`; Sim: 0,070 ms statt 0,457 ms = 15 %; `processing_mix` 1,0 nach 5 Blöcken; Statuszeile `OFF … adaptive paused`; `loudness-reset` E2E rc=0 |
| K5 Neu lernen | Lernphase neu, Offsets bleiben | ✅ | `livecontrol.py:343-349`, `adaptive.py:459-471`; E2E `learn=True`, Offsets unverändert |
| K6 Snapshot | Basiswerte+Offsets → `config/chain.live.yaml`, Zeitstempel im Kommentar | ◐ | `livecontrol.py:63,351-375`; E2E snap2: `exp=-41 des=-31 comp=-22 tgt=-17.5 tilt=-1.0` (Offsets eingerechnet), Datei lädt per `load_config`. Zeitstempel steht als Datenschlüssel `_live.created`, nicht im Kommentar → BL2-5 |
| K7 Bypass A/B | Nur Trim+Limiter, 30-ms-Crossfade, Toggle, Statuszeile BYPASS | ✅ | `chain.py:135-141,160-163`; Sim: Pfaddifferenz 2,62 dB, außerhalb Fenster 0,195 dB; Statuszeile `BYPASS` |
| K8 Marker | Zeitmarke mit Text ins Regelungslog | ✅ | `livecontrol.py:377-392`; E2E `{"t": 5033.54, "event": "marker", "text": "blind marker ä"}` im Controller-JSONL; ohne `--text` → „Marker 08:49:39“ |
| Dial Ebene 2 | identisch Ebene 1 | ✅ | `functions.json` Seite 2 `dial` = louder/quieter/mute; Test `test_control.py:384` |

### §3 Lieferumfang

| Punkt | Status | Beleg |
|---|---|---|
| `livecontrol.py` `LiveControl(cfg, chain, controller=None, hooks=None)`, `handle(cmd)->dict`, Offsets→Sollwerte (mit Regelung in Ziele+rest, ohne direkt), Grenzen, Reset, Freeze, Relearn, Snapshot, Marker, Status; hooks mute/bypass | ✅ | `livecontrol.py:100,128,244-261` (Controller: `set_user_offsets`), `adaptive.py:423-457` (Ziele + `apply_rest_values`); `_frozen_before_off`-Parken geprüft (E2E „offsets while power off“) |
| `control.py`: Unix-Socket `cfg.control.socket_path` (Default `logs/control.sock`), JSON-Zeilen, Server im Service-Thread, nicht blockierend (`selectors`), `send_command(cmd, socket_path, timeout)` | ✅ | `control.py:34,53-61,172-208,326`; `runtime.py:747-770` (Poll alle 20 ms); Rohprotokoll-Fehler (`nix`, `[1]`, unbekanntes cmd/Feld) → `ok:false`, Verbindung bleibt |
| CLI `ctl <kommando> [--value] [--text] [--notify] [--socket]`, Exit 0/1/2 | ✅ | `cli.py:879-892,795-818`; Exit 2 ohne Server („voicechain run läuft nicht …“), Exit 2 bei falschem `--value`, Exit 1 bei `ok:false` (Code `EXIT_FAILED`, Pfad `cli.py:814`) |
| Runtime: Mute-Rampe im Callback (30 ms), Bypass über `Chain.set_bypass`, Statuszeile MUTE/BYPASS/Offsets-Kurzform | ✅ | `runtime.py:405,430-457,468-474,679-684,709-734`; eigener Lauf: `… \| OFF \| MUTE \| BYPASS \| cpu 0% … \| off L+0.5 R+2 S-2 T0 I+1 \| target -17.5 LUFS` |
| DSP `Chain.set_processing/get_processing`, mittlere Stufen nicht aufgerufen, < 25 %, Aus-/Einblenden 30 ms, recover, Meter `chain.processing` | ✅ | `chain.py:143-149,165-175,198-209`; Sim 15 % (Median), `chain.processing=0.0`, Einschwingen nach Einblendung −11,48 vs. −11,57 dBFS |
| `Equalizer.tilt_db` (−3…+3, Default 0, Shelves 250 Hz/4 kHz, knackfrei), `set_bypass` 30 ms, Validator `eq.tilt_db`/`control.socket_path` optional | ✅ | `eq.py:33-36,80-102,137-143`, `config.py:142-146,221-224`; `test_dsp_eq.py:112` (Umschalten knackfrei) |
| `integrations/mx_console/`: `actions/*.sh` (je Funktion, `ctl … --notify`), `applescript/*.applescript`, `functions.json` (id, label ≤ 10, beschreibung, kommando, dial), README (Verdrahtung, Pfade, Rechte, Test ohne Konsole) | ✅ | 20 Skripte/20 AppleScripts, alle Namen stimmen überein (eigener Abgleich); Labels 4–10 Zeichen; README §Einrichtung 1–5. Socket-Fund: nur per `--socket`/`--config`-Durchreichung, keine Umgebungsvariable (README Z. 59-61 sagt das) |
| `docs/05_MX_Creative_Console.md`: Konzept, Begründung je Taste, Verhalten ggü. Regelung, Grenzen, Fehlersuche | ✅ (inhaltlich ◐, s. BL2-1/3/5) | docs/05 §1–§8 vorhanden |
| Tests ohne Audiogeräte: Socket real, alle Kommandos, Grenzen, Reset, Offsets/Regelung 300 s, Mute-Rampe, Bypass-Crossfade, Tilt, Manifest-Konsistenz | ✅ | `test_control.py:159-360,378-437`, `test_livecontrol.py:91-130,190-215,417-540`, `test_runtime.py:870`, `test_dsp_chain.py:531,562,600`, `test_dsp_eq.py:92,112` |

### §4 Schnittstellenvertrag

| Anforderung | Status | Beleg |
|---|---|---|
| Kommandos `offset(name,delta)`, `set_offset(name,value)`, `reset`, `freeze(on)`, `mute(on)`, `bypass(on)`, `relearn`, `snapshot(path?)`, `marker(text)`, `status`, `processing(on)` | ✅ | `livecontrol.py:65-77`; alle 11 roh über Socket geprüft; unbekannte Felder abgelehnt (`"foo"` → `ok:false`) |
| `on`: true/false/"toggle" | ✅ | `livecontrol.py:414-420`; `"maybe"` → `ok:false` |
| `loudness_lu` −6…+6 / 0,5 | ✅ | `livecontrol.py:49`; E2E Klemmung |
| `room_db` −6…+6 / 2, positiv = weniger Raum = Schwelle höher | ✅ | `livecontrol.py:50,239`; E2E `+6 → exp -39` |
| `deess_db` −6…+6 / 2, negativ = weniger Zischen = Schwelle tiefer | ✅ | `livecontrol.py:51,240`; E2E `-6 → des -36` |
| `tone_db` −3…+3 / 1, positiv = heller | ✅ | `livecontrol.py:52,211-217`; Sim +2 → Höhen +0,96 dB |
| `intensity` −3…+3 / 1, positiv = dichter | ✅ | `livecontrol.py:53`; `set_offset 2.4` → 2,0 (gerundet) |
| Antwort `{"ok":true,"state":{…},"message":"<kurz, deutsch>"}` / `{"ok":false,"error":…}` | ✅ | E2E: Schlüssel `['message','ok','state']`; Fehler `{"ok": false, "error": "…"}` |
| `state` ⊇ offsets, target_lufs, frozen, muted, bypass, low_snr, snr_lu, loudness_lufs, adaptive_enabled | ✅ | E2E state keys: zusätzlich processing, learning, tilt_db, 3 Schwellen |
| CLI-Namen: louder, quieter, mute, room-less, room-more, deess-more, deess-less, natural, dense, freeze, reset, status, warmer, brighter, loudness-reset, relearn, snapshot, bypass, marker, power | ✅ | `cli.py:689-693`; alle 20 im E2E rc=0 |

### §5 Qualitätsregeln

| Anforderung | Status | Beleg |
|---|---|---|
| Tastendruck blockiert Audio-Callback nicht; alles über Service-Thread und `set_param`-Pfade | ✅ | `runtime.py:747-770` (Poll im Service-Thread), `chain.py:215-217` (Design außerhalb des Locks); Mute nur als Gain-Ziel (`runtime.py:460-466`) |
| Antwortzeit `ctl` < 300 ms auf dem Mac, messen und dokumentieren | ◐ / ℹ | Container: Median 80 ms, Max 82 ms, Min 60 ms (15 Aufrufe `ctl status`); Skripte 60–103 ms. docs/05 §6 nennt 61/62 ms Container und **schätzt** den Mac (ehrlich gekennzeichnet, nicht gemessen) |
| Kein numpy/scipy-Import in `ctl` | ✅ | `python -X importtime -m voicechain ctl status --socket …`: kein numpy/scipy/sounddevice |

### §6 Abnahmekriterien

| Nr. | Kriterium | Status | Beleg |
|---|---|---|---|
| 1 | `pytest -q` grün ohne Audiogeräte; Selftest 12/12 | ✅ | `491 passed, 1 skipped in 566.71s`; Selftest `12/12 bestanden -> BESTANDEN` |
| 2 | Jede Funktion über `ctl`, Skript, AppleScript, Manifest, Doku-Zeile, Test | ✅ | E2E alle 20 Skripte rc=0; `test_control.py:390-437` prüft Skript/AppleScript/CLI/docs-05-Zeile; (Spec sagt „17“, geliefert 20 → BL2-8) |
| 3 | Raum −/+ ändert Pausenabsenkung | ✅ | Sim: Pausen-RMS −50,00 → −55,58 (+4) / −44,05 (−4) dBFS |
| 3 | Zischen −/+ ändert De-Esser-Reduktion | ✅ | Sim: GR-Mittel 0,33 → 0,63 (−4) / 0,08 (+4) dB; Zischanteil −10,12 → −11,01 / −9,18 dB |
| 3 | Lautheit ±1 LU → Ausgang ±1 LU | ✅ | Sim: −18,23 → −17,23 / −19,23 LUFS (Δ +1,00 / −1,00) |
| 3 | Tilt ±2 dB messbar | ✅ | Sim: sosfreqz +2: −0,97/−0,5/0/+0,5/+0,96 dB; Bandbalance ±1,91 dB |
| 3 | Intensität ändert Kompressor-Reduktion | ✅ | Sim: 3,72 → 5,30 (+2) / 2,19 (−2) dB |
| 4 | 300-s-Simulation Raum +4: Expander bleibt Ziel + 4 ± Totzone | ✅ | Sim A/B: Ende −34,03 vs. −30,03 → B − A = +4,00 dB; 0 Schritte nach unten nach dem Druck; Spur konstant 82–282 s |
| 5 | Reset = Kalibrierzustand (alle Parameter identisch, Regelung auf rest) | ✅ | Sim 6.5 (Offsets, Drift −2,5/+1,0 dB, Mute, Bypass, Freeze, Power aus): `param diffs after reset: none`, `user_offsets {}`; E2E reset nach Drift → −45/−30/−20 |
| 6a | Kill-Switch: < 25 % Rechenzeit, knackfrei, Regelung/Analyzer pausieren, Reset schaltet ein | ✅ | Sim: 15 % (Median) / 16 % (Mittel); Aus/Ein außerhalb Fenster ≤ 0,20 dB; `runtime.py:400,581`; Reset → `processing=true` (E2E „state after loop“ → reset) |
| 6 | Mute/Bypass knackfrei (kein Sprung > 0,5 dB je Block außerhalb der 30-ms-Rampe), Statuszeile | ✅ | Sim-Click: Mute 0,195 dB, Bypass 0,195 dB, Processing 0,202 dB außerhalb; Sprache Mute: 0,000 dB Zusatzsprung; Statuszeile `MUTE`/`BYPASS`. Innerhalb der Rampe: Bypass 0,63 dB, Processing-Ein 0,71 dB je Block bei 2,6 dB Pfaddifferenz (zulässig laut Wortlaut; docs/02 nennt 0,25 dB bei 1 dB Pfaddifferenz) |
| 7 | PO-/SW-/Blindprüfung | – | diese Prüfung |

## 3. Befundtabelle

| Nr. | Schwere | Fundstelle | Befund | Empfehlung |
|---|---|---|---|---|
| BL2-1 | **mittel** | `voicechain/adaptive.py:573-579` (`_track`), `voicechain/livecontrol.py:244-261`; zugesichert in `docs/06 §2 K1`, `docs/05 §2 K1 + §4 (Z. 87-88)`, `docs/03 §11 (Z. 382)` | Die Sprach-Kappe des Expanders („Schwelle höchstens Sprache − 12 dB, auch mit Offset“) gilt mit Raum-Offset nicht: `_track` berechnet `min(floor+10, speech−12)` und addiert den Offset **danach**; `_apply_threshold_offsets` schiebt den Schritt ohne Kappe direkt auf die Stufe. Messung (kalibriert −34,0, Sprache nach Trim −20 → Kappe −32,0): Raum +4 → Schwelle −30,03 = Kappe + 2,0 dB (Raum +6 wird erst von `bounds` bei −30 gestoppt). Bei anderen Kalibrierungen bis +6 dB über der Kappe möglich; leise Silben können beschnitten werden, und die `speech_cut`-Regel würde den Offset dann schrittweise zurückregeln (Widerspruch zu §6.4). | Offset nur auf den Teppich-Zweig anwenden: `target = min(floor + 10 + offset, speech − 12)` (und dieselbe Kappe in `_apply_threshold_offsets` mit dem letzten Analyzer-Sprachpegel), oder die Zusicherung in docs/05/03/06 streichen und das Verhalten (Kappe kann überschritten werden) dokumentieren. Test ergänzen: Raum +6 bei Sprache−12 < Teppich+10+6. |
| BL2-2 | niedrig–mittel | `voicechain/livecontrol.py:136-156` (`state()` → `chain.get_meters()`), aufgerufen aus jedem `_ok` (Z. 175-176); Kontrast `voicechain/runtime.py:513-525` (`control_state` vermeidet genau das) | Jedes Konsolenkommando – auch jede Dial-Rastung – leert die `gain_reduction_mean/max`-Akkumulatoren aller Stufen. Die Regelung liest sie am 2-s-Takt für `limiter_protect` (Limiter-GR-Mittel) und `speech_cut` (Expander-GR-Max bei Sprache). Gemessen: nach 200 Blöcken Komp-GR mean 5,63/max 6,82 dB; mit `state()` unmittelbar davor 0,00/0,00. Folge: Schutzregeln sehen nach einem Tastendruck nur ein verkürztes Fenster; ein schnelles Drehen am Dial setzt sie ständig zurück. | `state()` nicht entwässernd lesen: z. B. `Chain.get_meters(drain=False)`/`last_mean`-Werte, oder die Runtime reicht `_last_meters` an LiveControl (Hook/Callable). Alternativ `loudness_lufs/snr_lu/low_snr` direkt aus dem Normalizer-Meter ohne Akkumulator-Reset holen. |
| BL2-3 | niedrig | `docs/05 §4 Z. 85-86`, `docs/05 §8 Z. 141`, `integrations/mx_console/README.md Z. 86` vs. `livecontrol.py:179-185`, `cli.py:808-818` | Doku: am Anschlag antworte die Kette mit „abgelehnt“ (Grenze erreicht). Tatsächlich: `ok:true`, Exit 0, Mitteilung nennt nur den unveränderten Wert („Lautheit +6 LU (Ziel −12 LUFS)“), kein Hinweis „Grenze“. „abgelehnt“ entsteht nur bei Protokoll-/Zustandsfehlern. | Entweder Mitteilung um „(Grenze erreicht)“ ergänzen, wenn der Offset nicht geändert wurde, oder die drei Doku-Stellen korrigieren. |
| BL2-4 | niedrig | `integrations/mx_console/README.md Z. 23` („`actions/louder.sh --value 1`“), `cli.py:881-885` (Help „step size“) vs. `livecontrol.py:183-184` | `--value` wird serverseitig auf eine Schrittweite geklemmt: `louder --value 1` ergibt +0,5 LU (E2E), `--value 0.25` wirkt. Das README-Beispiel suggeriert größere Schritte. | Dokumentieren („höchstens eine Schrittweite“) oder größere Werte zulassen (z. B. Vielfache bis zur Bereichsgrenze). |
| BL2-5 | niedrig | `docs/06 §2 K6`, `docs/05 §3 K6 + §5 Z. 96` („Zeitstempel im Kommentar“) vs. `livecontrol.py:371-374`, `config.py save_config` (Kommentarkopf ohne Zeitstempel); `docs/03 §11 Z. 423` beschreibt `_live: {created, offsets}` korrekt | Zeitstempel steht als Datenschlüssel `_live.created`, nicht als Kommentar; Datei lädt (`load_config` akzeptiert `_`-Schlüssel), Offsets sind eingerechnet. Reiner Doku-Widerspruch zwischen docs/05 und Umsetzung/docs/03. | docs/05 an docs/03 angleichen oder Zeitstempel zusätzlich in den Kommentarkopf schreiben. |
| BL2-6 | niedrig | `livecontrol.py:201-230`, `adaptive.py:578` (bounds-Klemme) | Wird der Sollwert von `adaptive.bounds` geklemmt (kalibriert −34, Grenze −30), zeigt `status` „Raum +6 dB“, obwohl +6 dieselbe Schwelle wie +4 ergibt (Sim: Pausen-RMS identisch −55,6 dBFS). Die Einzelantwort nennt zwar „Expander −30 dBFS“, der Offset-Wert täuscht aber Wirkung vor. | Bei Klemmung an `bounds` in Mitteilung/`status` „(an bounds-Grenze)“ anzeigen oder den Offset nicht weiter erhöhen. |
| BL2-7 | info | `livecontrol.py:300-317`; `docs/05 §5` | Reset lässt FROZEN unverändert (E2E: `frozen=true` nach reset). Spec §2 K7 verlangt das nicht, „Kalibrierzustand“ kann aber anders gelesen werden; docs/05 §5 erwähnt Freeze bei Reset nicht. | In docs/05 §5 einen Satz „Freeze bleibt“ ergänzen (oder bewusst entfrieren). |
| BL2-8 | info | `docs/06 §6.2` („17 Funktionen“), `tests/test_cli.py:630` (Name „…all_19…“, prüft 20) | Zählungen inkonsistent: Spec 17, Doku/README 20 (19 + `power`). Umsetzung liefert 20. | Spec-Text auf 20 (bzw. 19 Tasten + `loudness-reset`) berichtigen; Testnamen anpassen. |
| BL2-9 | info / ℹ | `integrations/mx_console/actions/*.sh Z. 12-16`, `applescript/*.applescript` (`do shell script`), `cli.py:748-765` (`import yaml`) | Ohne `.venv` fällt das Skript auf `python3` zurück. Logi Options+ / `do shell script` starten mit minimalem PATH; auf macOS ist das dann meist `/usr/bin/python3` ohne PyYAML → `ctl` ohne `--socket` scheitert am `yaml`-Import. Nicht im Container prüfbar. Mac-Antwortzeit < 300 ms ist nur geschätzt (docs/05 §6 sagt das offen). | README: Hinweis auf `.venv` als Pflicht bzw. `PYTHON_BIN`-Export in Options+, oder `yaml`-Import in `_ctl_socket_path` mit Fallback auf Default-Pfad absichern. Messung auf dem Mac nachliefern. |
| BL2-10 | info | `docs/02 Z. 54-57` | Meterliste `get_meters()["chain"]` nennt nur `bypass, bypass_mix, latency_samples`; `processing, processing_mix` kommen erst im Kill-Switch-Absatz (Z. 77-78). | Liste ergänzen. |

Positiv hervorzuheben: Protokoll-Robustheit (alle Fehlpfade → `ok:false`, Verbindung bleibt), saubere Exit-Codes, Snapshot wieder ladbar, Kill-Switch-Rückkehr ohne Pegelsprung (Normalizer-Gain bleibt: Sprache −20,0 / −19,8 / −18,2 LUFS vor/während/nach), Offsets im Aus-Zustand werden geparkt und beim Einschalten angewendet (E2E `exp=-43.00` nach Power an), Reset arbeitet auch bei eingefrorener Regelung sofort.

## 4. Kurzausgaben

```
$ python -m pytest -q
491 passed, 1 skipped in 566.71s (0:09:26)        # SKIPPED tests/test_cli.py:332: root ignores file permissions

$ python -m voicechain selftest
… Rechenzeit je 256er-Block < 1.0 ms: 0.448 ms | ja … Ergebnis: 12/12 bestanden -> BESTANDEN   (Exit 0)

$ python -m voicechain ctl status            # ohne Server
voicechain: voicechain run läuft nicht (kein Steuer-Socket unter /home/user/Test/logs/control.sock). Erst 'python -m voicechain run' starten.
EXIT=2
```

E2E (eigener ControlServer + LiveControl + echte Chain/AdaptiveController/LiveAnalyzer, 256er-Blöcke in Echtzeit, Poll 20 ms; `e2e_log.txt`):

```
louder   rc=0 | Lautheit +0,5 LU (Ziel -17,5 LUFS) | 80 ms   state: off={'loudness_lu': 0.5,…} tgt=-17.5 … exp=-44.50 des=-29.50 comp=-20.50 lufs=-27.86 snr=25.25
mute     rc=0 | MUTE an: Ausgang stumm              → mute=True
room-less rc=0 | Raum +2 dB (weniger Raum, Expander -42,5 dBFS)
natural  rc=0 | Intensität -1 (natürlicher, Kompressor -18,5 dBFS)   des -29.5→-28.5
freeze   rc=0 | Regelung eingefroren (FROZEN)
reset    rc=0 | Reset: Kalibrierzustand, Offsets 0, Mute und Bypass aus   → exp=-45.00 des=-30.00 comp=-20.00 mute=False (frz bleibt True)
status   rc=0 | Lautheit -27,9 LUFS (Ziel -18 LUFS) | Störabstand 25,2 LU | Offsets: keine | FROZEN
relearn  rc=0 | Lernphase neu gestartet (6 s), Offsets bleiben   → learn=True
snapshot rc=0 | Snapshot gespeichert: …/snap_e2e.yaml            → load_config OK, _live={'created': '2026-11-03T08:49:27', …}
marker   rc=0 | Marker gesetzt: blind marker ä                    → {"t": 5033.54, "event": "marker", "text": "blind marker ä"}
power    rc=0 | Verarbeitung AUS (nur Trim + Limiter, Prozessorlast gesenkt)   → proc=False
20x louder : rcs={0} loudness_lu=6.0 target=-12.0  |  40x quieter: loudness_lu=-6.0 target=-24.0
louder --value 1 → Lautheit +0,5 LU   (geklemmt, BL2-4)
5x room-less: room_db=6.0 exp -45→-39 | 8x room-more: -6.0 / -51 | 5x deess-more: -6 / -36 | 5x dense: +3, comp -26, des -27 | 5x warmer: tilt -3
power off → room-less: "Raum +2 dB … – Verarbeitung AUS: wirkt beim Einschalten"; power on → exp=-43.00, frozen wiederhergestellt
Fehlpfade: --value abc → rc=2; bogus → rc=2 (argparse); 'nix' → {"ok": false, "error": "invalid JSON…"}; unbekanntes Feld → ok:false
ctl status x15: median 80 ms, max 82 ms
actions/*.sh --socket …: 20/20 rc=0 (60–103 ms); ohne --socket: rc=2 „voicechain run läuft nicht (kein Steuer-Socket unter …/logs/control.sock)“
```

Simulation (`sim_log.txt`, `sim_click_log.txt`; Konfig per `calibrate.recommend` aus synthetischer Sprache: exp −34,0, des −13,8, comp −20,0, trim +6):

```
loudness_lu +1.0: delta +1.00 LU | -1.0: -1.00 LU | +6: +5.45 | -6: -6.00
room_db +4: pause rms -55.58 (Δ -5.57) | -4: -44.05 (Δ +5.95) | +6: -55.60 (= +4, bounds) | -6: -41.08
deess_db -4: GR 0.63 dB (ref 0.33), sibilance -11.01 (ref -10.12) | +4: 0.08 / -9.18
intensity +2: comp GR 5.30 (ref 3.72) | -2: 2.19 | des GR 0.48 / 0.20
tilt +2 @100/250/1k/4k/8k: [-0.97, -0.5, -0.0, 0.5, 0.96] dB ; Bandbalance ±1.91 dB
300 s: A -34.03 | B (+4 @60 s) -30.03, 0 Abwärtsschritte | C (+6) -30.00 (bounds) ; B-A = +4.00
       Kappe speech-12 = -32.0 → B: Schwelle - Kappe = +1.97 dB  (BL2-1)
reset: param diffs after reset: none; user_offsets {}
mute: Rampe 5-6 Blöcke, Stille exakt 0; außerhalb Fenster 0.195 dB | bypass (Pfad +2.62 dB): 0.195 außen / 0.63 innen | processing: 0.202 außen / 0.71 innen
processing on: Peak 100 ms nach Blende -11.48 vs steady -11.57 dBFS; Sprache: |yp-ref| max 0.010
CPU: on 0.457 ms, off 0.070 ms = 15 % (Median), bypass 0.071 ms (= off? nein: bypass misst hier bei processing off)
state()-Drain: Komp-GR mean 5.63/max 6.82 → nach state() 0.00/0.00   (BL2-2)
```

Hinweis zur letzten CPU-Zeile: der Bypass-Wert wurde bei bereits ausgeschalteter Verarbeitung gemessen und ist daher kein eigenständiger Bypass-Messwert; docs/02 Z. 51 (Bypass rechnet weiter, gleiche Rechenzeit) wurde nicht separat nachgemessen.

Eigene Dateien: `<pruefordner>/b2/blind/` (`e2e_server.py`, `e2e_driver.py`, `e2e_log.txt`, `sim.py`, `sim_log.txt`, `sim_click.py`, `sim_click_log.txt`, `snap_*.yaml`, `ctrl.jsonl`, `hooks.jsonl`).
