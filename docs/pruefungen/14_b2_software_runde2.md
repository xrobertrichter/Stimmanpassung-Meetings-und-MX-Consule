# Software-Prüfung Beauftragung 2 – Runde 2 (Zweitprüfung), voicechain Stand ae522ec

Prüfer: Software-Prüfer B2 · 2026-11-03 · Grundlage: docs/06 §3–§7 (inkl. Ergänzungen nach Runde 1), Erstbericht `pruefung_software_b2.md` (SW2-1…15) ·
Nur Prüfung, keine Repo-Änderung. Artefakte: `<pruefordner>/b2/sw/r2/`
(`pytest_std.log`, `pytest_slow.log`, `git_status_before.txt`/`…after_std.txt`/`…after_slow.txt`, `ctl_latency.log`, `measure_chain.log`,
`mutations/apply.py`, `mutations/run_mut.sh`, `mutations/summary.txt`, `mutations/*.log`, `n1a_demo.py`, Kopie `mut2/` = exakt ae522ec).

**Wichtiger Hinweis zum Prüfgegenstand:** Geprüft ist der Commit **ae522ec** (Kopie `mut2/` ist byte-gleich mit `git show HEAD:`).
Während der Prüfung sind im Arbeitsbaum des Repos **unversionierte Änderungen** entstanden (nicht von mir; mein Patcher arbeitet
ausschließlich in `mut2/`): `voicechain/adaptive.py` (09:54), `voicechain/livecontrol.py` (09:55), `tests/test_livecontrol.py` (09:57),
zusammen +106/−18 Zeilen (`SPEECH_CAP_DEADZONE_DB`, `_sync_room_offset`, `set_offset` → „unverändert“). Beide Suiten liefen **vorher**
(09:44–09:53) auf dem ae522ec-Stand. Diese Änderungen sind **nicht Gegenstand** dieser Prüfung und berühren genau den Bereich des
Befunds SW2-16 – nach Commit sind Suite und die Mutationen N1a/N1b/N4 zu wiederholen (siehe SW2-17).

## 1. Kurzurteil

**Abnahmefähig.** Alle Auflagen der Runde 1 sind umgesetzt und belegt: die beiden mittleren Befunde (SW2-1 Meter-Drain, SW2-2 Socket-Pfadlänge
auf macOS) sowie SW2-3, -4, -5, -6, -7, -9, -13, -14, -15 sind behoben; SW2-8, SW2-11 und ein Rest von SW2-10 bleiben als Hinweise offen
(keine Abnahmerelevanz). Standardsuite `-m "not slow"`: **489 bestanden, 1 übersprungen, 23 deselektiert, 270 s Wanduhr** (< 5 min, gemessen
teilweise unter paralleler Last); Slow-Suite: 23 bestanden, 249 s; `git status --porcelain --ignored` vor/nach beiden Läufen identisch.
Mutationen: **M14 und M20 jetzt gefangen**; von den 8 neuen Mutationen werden 7 gefangen, **N1a überlebt** (Kappe-vor-Offset im Regler: der
Mutant wird vom `speech_cap`-Pull-down maskiert, flattert aber ±0,5 dB je Intervall – neuer Befund SW2-16, niedrig). `ctl`-Latenz unverändert
Median **59 ms**. Neuer `livecontrol.py` (564 Zeilen) ohne Lazy-Coding-Befunde.

## 2. Statustabelle SW2-1 … SW2-15

Status: **behoben** / **teilweise** / **offen (H)** = Hinweis ohne Abnahmerelevanz.

| Nr. | Schwere R1 | Status | Beleg (Datei:Zeile bzw. Messung) |
|---|---|---|---|
| SW2-1 Meter-Drain durch Kommandos | M | **behoben** | `voicechain/livecontrol.py:174` `state()` liest `chain.peek_meters()`; `dsp/chain.py` `get_meters(drain=True)` + `peek_meters()` (Diff e680b56..HEAD), `dsp/envelope.py` `ReductionAccumulator.peek()`, `peek_meters` in compressor/deesser/expander/limiter/`dsp/__init__.py`. Tests: `tests/test_livecontrol.py:91`, `tests/test_dsp_chain.py:690`. **Messung** (Demo Runde 1 wiederholt): Tick-Statistik mit `status`+`offset` bei 7,9 s identisch zu ohne (Expander-Mittel 4,57 dB, Max 18,76 dB; vorher 0,0/0,0). Mutation M20 gefangen. |
| SW2-2 Socket-Pfade > 103 Byte auf macOS | M | **behoben** | Fixtures `tests/test_control.py:80-93` (`/tmp/vcXXXXXXXX/c.sock`), `tests/test_cli.py:48-58` (`short_tmp`), `tests/test_runtime.py:197-203`; `_run_config` `tests/test_cli.py:61-70`. Gemessene Länge des Musters **22 Byte** (macOS `/private/tmp`: 30). Default-Pfad `/tmp/voicechain-<user>.sock` = 25 Byte (`control.py:38-45, 62-69`). `tmp_path` kommt nur noch im Zu-lang-Test (`test_control.py:157`, nie gebunden) und bei reiner Pfadauflösung (`:183`) vor. |
| SW2-3 Zweitstart stiehlt Socket | N | **behoben** | `control.py` `_remove_stale_socket_file` mit `socket_is_alive()` (Liveness-Probe 0,2 s) und `_remove_own_socket_file` mit Inode-Vergleich; Test `tests/test_control.py:187`. Demo wiederholt: zweiter Server → `ControlError "voicechain run läuft bereits"`, Client erreicht weiter A, `A.stop()` entfernt nur den eigenen Socket. Mutation N5 gefangen. |
| SW2-4 NaN/Infinity im Protokoll | N | **behoben** | `livecontrol.py:124-130` `_finite()` (Typ + `isfinite`), `:558-564` `_finite_or_none()`, in `state()` für offsets/snr_lu/loudness_lufs (`:178,185,186`). Tests `tests/test_livecontrol.py:88,105,129-136` (`json.dumps(allow_nan=False)`). Demo: `set_offset NaN` → `ok:false "'value' muss endlich sein"`, Offset bleibt 0,0; Status bei Stille „Störabstand unbekannt“, kein NaN/Infinity auf der Leitung. Mutation N8 gefangen. Rest: `control.py:102` `_encode` ohne `allow_nan=False` (Sicherheitsnetz, SW2-18 H). |
| SW2-5 kein SIGTERM-Handler | N | **behoben** | `runtime.py` `handle_sigterm()` setzt `_stop_event`; Registrierung in `run_forever()` im Hauptthread, Rücksetzen im `finally`; Test `tests/test_runtime.py:1087`. Mutation N6: Testprozess wird ohne Handler per SIGTERM beendet (rc 143) → Regression kann nicht unbemerkt bleiben (siehe SW2-19 H zur Testform). |
| SW2-6 `stop()` bricht vor `stream.stop()` ab | N | **behoben** | `runtime.py:865-870` `try/except` um `control_server.stop()`, Zähler + stderr; Test `tests/test_runtime.py:1110`. |
| SW2-7 toter ENAMETOOLONG-Zweig | N | **behoben** | `control.py` `check_socket_path()` prüft `len(os.fsencode(path)) > 103` vor `bind()` (`start()`) und vor `connect()` (`send_command`, `:409`) mit Abhilfetext; Test `tests/test_control.py:156` prüft beide Seiten und den Hinweistext. Mutation N7 gefangen. |
| SW2-8 Client-Timeout je Operation, 64-KiB-Antwort | H | **offen (H)** | `control.py:414-426` unverändert; bewusst nicht priorisiert, kein Abnahmebezug. |
| SW2-9 AppleScript/PATH ohne `.venv` | H | **behoben** | `actions/*.sh:13-25` Reihenfolge `.venv` → `VOICECHAIN_PYTHON` → `/opt/homebrew/bin/python3` → `python3`; `cli.py:748-783` `_ctl_socket_path` fällt ohne PyYAML auf den Default zurück (Hinweis auf stderr) bzw. nennt bei `--config` die Abhilfe; Test `tests/test_cli.py:825`; README Zeile 65. |
| SW2-10 Kommentar Rampe, Snapshot-Zeitstempel, `--value`, Rundung | H | **teilweise** | Kommentar korrigiert (`runtime.py:66-68`); docs/06 §2 K6 nennt `_live.created`; `--value`-Hilfe erklärt die Schrittbegrenzung (`cli.py:900-905`). Offen: Banker's Rounding bei `intensity` (`livecontrol.py:250`, nur bei Bruch-Deltas, trivial). |
| SW2-11 zeitbasierte Tests ohne Marker | H | **offen (H)** | `tests/test_dsp_chain.py:572`, `tests/test_runtime.py:1071`, `tests/test_cli.py:839` ohne `timing`-Marker; in dieser Runde alle grün (auch unter Last). |
| SW2-12 positive Prüfpunkte | H | unverändert | Threadsicherheit, 0600 (`stat`: 600), Quoting, Exit-Codes 0/1/2 erneut verifiziert (`ctl room-less` am Anschlag → „abgelehnt … am Anschlag“, Exit 1). |
| SW2-13 Start-Race, Ctrl-C-Test umgebungsabhängig | N | **behoben** | `runtime.py` `run_forever()`: `self.start()` jetzt **im** `try`, Kommentar nennt den Grund. Ctrl-C-Test in der Kopie (dort in Runde 1 deterministisch rot) **5/5 grün**. Der Feeder unterbricht weiterhin sofort (`tests/test_cli.py:117`), was jetzt unschädlich ist. |
| SW2-14 Tests schreiben in `logs/` des Arbeitsbaums | N | **behoben** | `_run_config` setzt `log_path` und `control.socket_path` auf tmp (`tests/test_cli.py:61-70`), Zusicherung `tests/test_cli.py:538`. Nachweis: 186 Tests (cli/livecontrol/runtime/control) in der Kopie ohne `logs/` → kein `logs/`, kein `config/chain.live.yaml`; `git status --ignored` vor/nach beiden Suiten identisch. |
| SW2-15 Testlücken (Offset = Totzone; `recover()` ungetestet) | N | **behoben** | `tests/test_livecontrol.py:236-251`: Offset +4, Fenster −46,5…−44 dBFS (ein Offsets ignorierender Regler landet bei −48 → rot); `tests/test_dsp_chain.py:708` `test_processing_on_recovers_middle_stages`. Mutationen M14 und (sinngemäß) M05 gefangen. |

## 3. Mutationstabelle Runde 2

Verfahren wie Runde 1 (Kopie `mut2/` = ae522ec, exakter Textersatz mit Revert-Kontrolle, Lauf der zuständigen Testdateien ohne Marker-Filter).

| Nr. | Mutation (Fundstelle) | Gefangen | Fehlschlagende Tests / Befund |
|---|---|---|---|
| M14 | kein `recover()` der Stufen beim Wiedereinschalten (`dsp/chain.py` `_processing_weights`) | **ja** | test_dsp_chain :: test_processing_on_recovers_middle_stages |
| M20 | `state()` liest `get_meters()` statt `peek_meters()` (`livecontrol.py:174`) | **ja** | test_livecontrol :: test_status_and_answers_read_meters_without_draining_the_accumulators |
| N1a | Kappe vor Offset im Regler entfernt: `_track(expander_threshold_target(floor, …), include_offset=True)` statt `floor + room_offset … include_offset=False` (`adaptive.py` `_propose`) | **nein** | test_adaptive (inkl. `test_room_offset_enters_the_floor_rule_before_the_speech_cap`) und test_livecontrol grün. **Verhalten des Mutanten** (`n1a_demo.py`, Kappe −37, Offset +6): Schwelle pendelt −36,5/−37,0/−37,0/−36,5 …, **19 Änderungen in 30 Intervallen** (`noise_floor_track` ↑ und `speech_cap` ↓ im Wechsel); Original: 0 Änderungen, Schwelle ruhig bei −39. Der Test erlaubt `≤ Kappe + Totzone` und zählt keine Änderungen → SW2-16 |
| N1b | Sofort-Schritt ignoriert Sprach-Kappe (`livecontrol.py:323` `if cap is not None and new > max(cap, current)`) | **ja** | test_livecontrol :: test_room_offset_respects_the_speech_cap_in_controller_and_immediate_step, test_quiet_speaker_with_room_offset_stays_under_the_speech_cap |
| N2 | `reset` ruft `reset_protection()` nicht (`livecontrol.py:372`) | **ja** | test_livecontrol :: test_reset_restores_normalizer_gain_limit_after_limiter_protection |
| N3 | `resume()` ohne Zeitverschiebung (`adaptive.py` `gap = 0.0`) | **ja** | test_adaptive :: test_pause_and_resume_stop_the_controller_clock; test_livecontrol :: test_processing_off_pauses_the_controller_clock_and_restores_freeze |
| N4 | Anschlag liefert `ok:true` (alle drei `abs(effective - previous) <= EPS`-Zweige in `_apply_offset`) | **ja** | test_livecontrol :: test_offset_steps_move_one_step_and_stop_with_ok_false_at_the_range_end (5 Parametrisierungen) + 4 weitere (9 Tests) |
| N5 | Liveness-Check entfernt (`control.py` `_remove_stale_socket_file`) | **ja** | test_control :: test_second_server_refuses_to_replace_a_live_socket |
| N6 | SIGTERM-Handler entfernt (`runtime.py` `run_forever`) | **ja** (rc 143) | `tests/test_runtime.py::test_sigterm_requests_an_orderly_stop` sendet SIGTERM an den eigenen Prozess; ohne Handler stirbt der pytest-Prozess (Exit 143, kein `FAILED`-Eintrag, Log endet mitten im Lauf). Regression fällt sicher auf, aber nicht als Testergebnis → SW2-19 |
| N7 | `check_socket_path` entfernt (`control.py`) | **ja** | test_control :: test_socket_path_too_long_is_checked_before_bind_and_connect |
| N8 | `_finite`-Prüfung entfernt (`livecontrol.py:128`) | **ja** | test_livecontrol :: test_invalid_commands_return_ok_false_without_raising_or_changing_anything[cmd10, cmd11] (NaN-Delta, Infinity-Wert) |

**Ergebnis: 10 von 11 gefangen; M14/M20 beide gefangen; neue Mutationen 7 von 8 (N1a überlebt).**

## 4. Neue Befunde (Runde 2)

| Nr. | Schwere | Fundstelle | Befund | Empfehlung |
|---|---|---|---|---|
| SW2-16 | N (Testqualität) | `tests/test_adaptive.py:336-348`, `voicechain/adaptive.py` `_propose` (Block `speech_cap` / `_track(... include_offset=False)`) | Die Anforderung „Sprach-Kappe gilt **nach** dem Offset“ (docs/06 §2 K1) ist nur bis auf die Totzone geprüft: ein Regler, der den Offset nach der Kappe addiert, wird vom `speech_cap`-Pull-down maskiert und **flattert ±0,5 dB in jedem Intervall** (19 Änderungen/30 Intervalle, Log-Spam, hörbar als Pumpen der Pausenabsenkung); der Test bleibt grün (`≤ −37 + 2`). | Im Kappen-Szenario zusätzlich zusichern: nach dem Einschwingen (z. B. Intervalle 20–40) **keine** Expander-Änderung und keine `speech_cap`-Einträge; Endwert `≤ Kappe` statt `≤ Kappe + Totzone`. Achtung: die unversionierte Arbeitsbaum-Änderung (`SPEECH_CAP_DEADZONE_DB`) verändert genau diese Stelle – Test dort mit einbauen. |
| SW2-17 | H (Prüfprozess) | Arbeitsbaum: `voicechain/adaptive.py`, `voicechain/livecontrol.py`, `tests/test_livecontrol.py` (unversioniert, 09:54–09:57) | Nach Abschluss der Suiten entstanden unversionierte Änderungen (+106/−18) an Regler/LiveControl/Tests. Sie sind nicht geprüft; Mutationen N1a/N1b/N4 und beide Suiten gelten nur für ae522ec. | Änderungen committen, Standard- und Slow-Suite erneut laufen lassen, N1a/N1b/N4 (`r2/mutations/apply.py`, Strings ggf. anpassen) wiederholen; danach Kurzabnahme. |
| SW2-18 | H | `voicechain/control.py:102` `_encode` | Kein `allow_nan=False` als letztes Sicherheitsnetz; aktuell unkritisch, weil `state()` alle Werte filtert. | `json.dumps(obj, ensure_ascii=False, allow_nan=False)` mit Fallback auf `{"ok": false, "error": …}`. |
| SW2-19 | H (Testform) | `tests/test_runtime.py:1087` | Der SIGTERM-Test tötet bei fehlendem Handler den Testprozess (rc 143) statt rot zu werden; in CI erscheint das als abgebrochener Lauf ohne Testname. | Handler-Installation prüfen (`signal.getsignal(SIGTERM) is engine.handle_sigterm` während `run_forever`, z. B. über den Fake-Stream) oder SIGTERM aus einem Kindprozess senden. |
| SW2-20 | H (Doku) | `README.md`, `docs/03_Betrieb_und_Kalibrierung.md` | Der Standardlauf `python -m pytest -q -m "not slow"` steht nur in `pytest.ini` (Markertext) und docs/06 §7; `docs/04_Pruefprotokoll.md:262` erwähnt ihn knapp. | Einen Satz in README/docs/03 („Entwicklerlauf … / Abnahmelauf `pytest -q`“). |

Lazy-Coding-Scan `voicechain/livecontrol.py` (neu geschrieben, 564 Zeilen): keine `TODO`/`FIXME`/`pass`-Rümpfe/`NotImplementedError`, kein stummes
`except:`, keine `print`; ein `except Exception` an der API-Grenze (`handle`, begründet). Konstanten mit Begründung (`LOUDNESS_TARGET_BOUNDS_LUFS` mit
PO-Verweis, `EPS`). Fachlogik stimmig mit docs/06 §7: Offsets spiegeln den wirksamen Wert (`_apply_threshold_offset` liest den geklemmten Parameter zurück),
Anschlag → `ok:false`, Reset behält nachgeführte Schwellen und zieht nur die Offsets ab (`_cmd_reset:357-369`), `relearn` setzt Ruhewerte + Lernphase,
Kill-Switch pausiert die Regler-Uhr (`_set_processing:438/450`).

## 5. Messwerte (Prüf-Container Linux x86, 4 Kerne, Python 3.13.18)

| Messung | Runde 2 (ae522ec) | Runde 1 |
|---|---|---|
| `ctl status` Ende-zu-Ende, 10 Aufrufe, ruhige Maschine (Server = echte `Chain`+`LiveControl` aus `mut2/`) | 62, 58, 58, 60, 59, 59, 58, 59, 59, 58 ms → **Median 59 ms**, Max 62 | Median 59, Max 72 |
| `ctl louder`, 5 Aufrufe | 58–59 ms | 57–59 |
| dieselbe Messung **unter Last** (Standardsuite + Mutationen parallel) | 67–184 ms (Median 97) – nur zur Einordnung, kein Abnahmewert | – |
| Standardsuite `-m "not slow"` | **270 s** Wanduhr (489 bestanden, 1 übersprungen, 23 deselektiert), teilweise unter Last | 574 s gesamt |
| Slow-Suite `-m slow` | 249 s (23 bestanden) | – |
| Chain an / aus / Bypass je 256er-Block (Median, `measure_chain.log`) | 0,449 / 0,068 (**15,1 %**) / 0,454 ms; p95 0,514 / 0,086 / 0,539 ms | 0,444 / 0,068 (15,4 %) / 0,455 ms |
| Idle `poll()` | 0,5 µs je Aufruf (0,002 % eines Kerns bei 50 Polls/s) | 0,5 µs |
| Socket-Pfade der Tests | 22 Byte (`/tmp/vcXXXXXXXX/c.sock`), Default 25 Byte | 110–124 Byte (macOS) |
| Rechte Socket | 600 (`stat`) | 600 |

Ergänzungen:
- Die Fixture `sock_path` in `tests/test_control.py:88` sichert die geforderte Grenze explizit zu (`assert len(os.fsencode(str(path))) <= 90`); `short_tmp`
  (`test_cli.py`) und `short_sock_path` (`test_runtime.py`) nutzen dasselbe Muster ohne eigene Zusicherung (22 Byte gemessen).
- Reste unter `/tmp` nach der Prüfung: `/tmp/vcvpsketh3` (leer, aus meinem SW2-3-Demo-Skript) und `/tmp/vc8deyhsm_/c.sock` (09:56:37 – der durch SIGTERM
  beendete Mutationslauf N6, dessen Fixture-Finalizer nicht mehr lief; illustriert SW2-19). Beide sowie mein Mess-Socket wurden von mir entfernt; im Repo
  liegt nichts davon.
- Alle Mutationen wurden rückgängig gemacht; `mut2/` ist nach dem Lauf wieder byte-gleich mit ae522ec (`git show HEAD:` je Datei), der Arbeitsbaum des Repos
  wurde von mir nicht angefasst (`git status` zeigt ausschließlich die drei fremden, unversionierten Änderungen aus SW2-17).
