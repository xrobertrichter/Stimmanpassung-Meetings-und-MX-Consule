# Beauftragung 2: Live-Bedienung über Logi MX Creative Console

Stand: 2026-11-03 · Status: verbindlich · Basis: `docs/00_Beauftragung.md` (gilt weiter)

## 1. Ziel

Die laufende Kette (`python -m voicechain run`) soll im Meeting ohne Terminal korrigierbar sein.
Bedienelement ist eine Logi MX Creative Console: Keypad mit 9 Tasten (eine Taste ist bei Logi
üblicherweise die Seitennavigation, daher 8 belegbare je Ebene) und Dialpad (Drehregler mit
Druckfunktion). Die Konsole startet pro Taste eine „Run“-Aktion (Shell-Befehl) oder ein
AppleScript. Das Profil selbst erzeugt der Nutzer mit einem eigenen Skript
(`mx_profile.py`, lokal auf dem Mac, hier nicht verfügbar). Geliefert werden daher:

1. die **Funktionen** (Kommandos mit Semantik, Schrittweiten, Grenzen, Verhalten gegenüber der
   adaptiven Regelung),
2. der **Steuerkanal** in die laufende Kette,
3. **aufrufbare Aktionen** je Taste (Shell-Skripte und AppleScript) mit Rückmeldung,
4. ein **Manifest** beider Ebenen, das `mx_profile.py` einlesen kann,
5. Dokumentation und Tests.

## 2. Fachliches Konzept (Toningenieur-Sicht)

Grundsatz: Im Meeting korrigiert man Symptome, nicht Parameter. Jede Taste ist ein **Makro** mit
hörbarem Effekt, kleiner Schrittweite, harter Grenze und Rücknahme per Reset. Live-Änderungen
sind **Offsets** auf die kalibrierten Werte; die adaptive Regelung arbeitet weiter und rechnet die
Offsets in ihre Sollwerte ein (sie darf sie nicht zurückregeln). Offsets gelten nur für die
laufende Sitzung; dauerhaft werden sie nur per „Snapshot speichern“.

Bewertung der Nutzervorschläge: Lauter/Leiser gehört auf den Drehregler (feinfühlig, häufigster
Griff), nicht auf Tasten. Weniger/mehr Raum, Natürlicher/Dichter, Reset und Freeze sind richtig
und bleiben auf Ebene 1. Ergänzt werden Zischen (zweithäufigstes Problem bei Kondensatormikros),
Mute (Stille ohne Zoom-Menü, mit sanfter Blende) und Status (man muss sehen, was aktiv ist).
Klangfarbe (wärmer/heller) ist seltener nötig und wandert auf Ebene 2.

### Ebene 1 „Meeting“

| Element | Funktion | Wirkung (Offset) | Schritt / Grenze |
|---|---|---|---|
| Dial drehen | Lautheit | Ziel-Lautheit des Normalizers | ±0,5 LU je Rastung, −24…−12 LUFS |
| Dial drücken | Mute an/aus | Ausgang blendet in 30 ms auf Stille / zurück | Toggle, Statuszeile „MUTE“ |
| K1 | Weniger Raum | Expander-Schwelle höher | +2 dB je Druck, Offset −6…+6 dB; Sprach-Kappe (Sprache − 12 dB) bleibt |
| K2 | Mehr Raum | Expander-Schwelle tiefer | −2 dB je Druck |
| K3 | Weniger Zischen | De-Esser-Schwelle tiefer | −2 dB je Druck, Offset −6…+6 dB |
| K4 | Natürlicher | Intensität −1: Kompressor-Schwelle +2 dB, De-Esser-Schwelle +1 dB | Stufe −3…+3 |
| K5 | Dichter | Intensität +1: Kompressor-Schwelle −2 dB, De-Esser-Schwelle −1 dB | Stufe −3…+3 |
| K6 | Freeze / Weiter | Adaptive Regelung einfrieren bzw. freigeben | Toggle, Statuszeile FROZEN |
| K7 | Reset | Alle Offsets 0, Lautheit auf Kalibrierwert, Regelung auf Kalibrierwerte (rest), Mute aus, Bypass aus | sofort, mit Blende |
| K8 | Status | macOS-Mitteilung: Lautheit, Störabstand, aktive Offsets, Freeze/Mute | nur lesend |

### Ebene 2 „Feinabstimmung“

| Element | Funktion | Wirkung | Schritt / Grenze |
|---|---|---|---|
| K1 | Wärmer | Klang-Tilt −1 dB (Low-Shelf 250 Hz +½, High-Shelf 4 kHz −½) | Tilt −3…+3 dB |
| K2 | Heller | Klang-Tilt +1 dB | |
| K3 | Mehr Zischen | De-Esser-Schwelle höher (De-Esser zurücknehmen) | +2 dB je Druck |
| K4 | Verarbeitung an/aus (Kill-Switch) | Kette wirklich abschalten: nur Input Trim + Limiter werden gerechnet, Analyzer und Regelung pausieren; spart Prozessorlast. Wiedereinschalten setzt die Stufen zurück und blendet 30 ms ein | Toggle, Statuszeile OFF; `Lautheit zurück` bleibt als CLI-Kommando `loudness-reset` |
| K5 | Neu lernen | Regelung: Lernphase neu starten (nach Platz- oder Raumwechsel), Offsets bleiben | |
| K6 | Snapshot speichern | aktuelle Basiswerte + Offsets als `config/chain.live.yaml` schreiben (mit Zeitstempel im Kommentar) | |
| K7 | Bypass A/B | Kette umgehen (nur Input Trim + Limiter) zum Vergleich; Blende 30 ms | Toggle, Statuszeile BYPASS |
| K8 | Marker | Zeitmarke mit Text ins Regelungslog (für spätere Analyse) | |

Dial auf Ebene 2: identisch zu Ebene 1 (Lautheit / Mute), damit der häufigste Griff immer gleich ist.

## 3. Lieferumfang

- `voicechain/livecontrol.py` (KAL): `LiveControl(cfg, chain, controller=None, hooks=None)` mit
  `handle(cmd: dict) -> dict`; hält die Offsets, rechnet sie in Sollwerte um (mit Regelung: Offsets
  gehen in die Controller-Ziele und in `rest`; ohne Regelung: direkt auf die Kalibrierwerte),
  Grenzen, Reset, Freeze, Relearn, Snapshot, Marker, Status. `hooks` = {"mute": fn(bool), "bypass": fn(bool)}
  für Funktionen, die außerhalb der Regelung liegen.
- `voicechain/control.py` (INT): Steuerkanal als Unix-Domain-Socket (Pfad `cfg.control.socket_path`,
  Default `logs/control.sock`), JSON-Zeilen-Protokoll, Server im Service-Thread der Runtime
  (nicht blockierend, `selectors`), Client-Funktion `send_command(cmd, socket_path, timeout)`.
- CLI `python -m voicechain ctl <kommando> [--value X] [--text ...] [--notify] [--socket PFAD]`
  (INT): Kommandos siehe §4; `--notify` zeigt die Antwort als macOS-Mitteilung (osascript), Exit 0/1/2.
- Runtime (INT): Mute-Blende im Audio-Callback (Ausgangsgain-Rampe 30 ms), Bypass über
  `Chain.set_bypass`, Statuszeile mit MUTE/BYPASS/Offsets-Kurzform.
- DSP: `Chain.set_processing(on)` / `get_processing()`: aus = nur Input Trim → Limiter werden gerechnet
  (mittlere Stufen werden nicht aufgerufen; Rechenzeit je Block muss messbar auf < 25 % fallen);
  Ausblenden 30 ms vor dem Abschalten, beim Einschalten Stufen zurücksetzen (recover) und 30 ms
  einblenden; Meter `chain.processing` 0/1.
- DSP: `Equalizer` erhält optionalen Parameter `tilt_db` (−3…+3, Default 0, zwei zusätzliche
  Shelves 250 Hz / 4 kHz, knackfrei); `Chain.set_bypass(on)` mit 30-ms-Crossfade, Bypass =
  Input Trim + Limiter; Validator kennt `eq.tilt_db` optional und `control.socket_path` optional.
- `integrations/mx_console/` (INT): `actions/*.sh` (ein Skript je Funktion, ruft `ctl … --notify`),
  `applescript/*.applescript` (Alternative), `functions.json` (Manifest beider Ebenen: id, label
  (max. 10 Zeichen für das Tastendisplay), beschreibung, kommando, dial), `README.md` (Verdrahtung
  in Logi Options+ „Run“-Aktion, Pfade, Rechte, Test ohne Konsole).
- `docs/05_MX_Creative_Console.md`: Konzept, Begründung je Taste, Verhalten gegenüber der Regelung,
  Grenzen, Fehlersuche.
- Tests ohne Audiogeräte: Socket-Server/Client real (Unix-Socket im tmp_path), alle Kommandos,
  Grenzen, Reset, Zusammenspiel Offsets/Regelung (Regelung regelt Offsets nicht zurück), Mute-Rampe,
  Bypass-Crossfade, Tilt-Frequenzgang, Manifest-Konsistenz (jede Funktion hat Skript, AppleScript,
  Doku-Zeile, CLI-Kommando).

## 4. Schnittstellenvertrag

Kommandos (JSON-Objekt je Zeile, Antwort JSON-Objekt je Zeile):

| cmd | Felder | Wirkung |
|---|---|---|
| `offset` | `name`, `delta` | Offset um delta ändern (geklemmt) |
| `set_offset` | `name`, `value` | Offset setzen |
| `reset` | — | alles auf Kalibrierzustand |
| `freeze` | `on`: true/false/"toggle" | Regelung |
| `mute` | `on`: true/false/"toggle" | Hook |
| `bypass` | `on`: true/false/"toggle" | Hook |
| `relearn` | — | Lernphase neu |
| `snapshot` | `path` (optional) | YAML schreiben |
| `marker` | `text` | Logeintrag |
| `status` | — | Zustand |
| `processing` | `on`: true/false/"toggle" | Kill-Switch (Ergänzung 2026-11-03 auf Nutzerwunsch) |

Offset-Namen und Bereiche: `loudness_lu` (−6…+6, Schritt 0,5), `room_db` (−6…+6, Schritt 2;
positiv = weniger Raum = Expander-Schwelle höher), `deess_db` (−6…+6, Schritt 2; negativ =
weniger Zischen = Schwelle tiefer), `tone_db` (−3…+3, Schritt 1; positiv = heller),
`intensity` (−3…+3, Schritt 1; positiv = dichter).

Antwort: `{"ok": true, "state": {...}, "message": "<kurz, deutsch, für Mitteilung>"}` oder
`{"ok": false, "error": "..."}`. `state` enthält mindestens: offsets, target_lufs, frozen, muted,
bypass, low_snr, snr_lu, loudness_lufs, adaptive_enabled.

CLI-Kommandonamen (1:1 zu den Tasten): `louder`, `quieter`, `mute`, `room-less`, `room-more`,
`deess-more` (weniger Zischen), `deess-less`, `natural`, `dense`, `freeze`, `reset`, `status`,
`warmer`, `brighter`, `loudness-reset`, `relearn`, `snapshot`, `bypass`, `marker`, `power` (Kill-Switch).

## 5. Qualitätsregeln

Wie `docs/00_Beauftragung.md` §5. Zusätzlich: Ein Tastendruck darf im Audio-Callback nichts
blockieren; alle Änderungen laufen über den Service-Thread und die knackfreien `set_param`-Pfade.
Antwortzeit `ctl` < 300 ms auf dem Mac (Python-Start eingerechnet; messen und dokumentieren).

## 6. Abnahmekriterien

1. `pytest -q` grün ohne Audiogeräte; Selftest unverändert 12/12.
2. Jede der 17 Funktionen ist über `ctl` erreichbar, hat Skript, AppleScript, Manifest-Eintrag,
   Doku-Zeile und einen Test (Manifest-Konsistenztest).
3. Offsets wirken hörbar und messbar (Simulation): Raum −/+ ändert die Pausenabsenkung, Zischen
   −/+ ändert die De-Esser-Reduktion, Lautheit ±1 LU ändert die Ausgangslautheit um ±1 LU,
   Tilt ±2 dB ändert den Frequenzgang messbar, Intensität ändert die Kompressor-Reduktion.
4. Die adaptive Regelung regelt Offsets nicht zurück (300-s-Simulation mit Offset room +4: Expander
   bleibt Ziel + 4 ± Totzone).
5. Reset stellt den Kalibrierzustand wieder her (alle Parameter identisch mit der geladenen Konfig,
   Regelung auf rest-Werten).
6a. Kill-Switch: Rechenzeit je Block im Aus-Zustand < 25 % des Normalbetriebs (gemessen), Aus- und
   Einschalten knackfrei (Rampen), Regelung und Analyzer pausieren, Reset schaltet wieder ein.
6. Mute und Bypass sind knackfrei (kein Sprung > 0,5 dB je Block außerhalb der 30-ms-Rampe) und
   werden in der Statuszeile angezeigt.
7. PO-, Software- und Blindprüfung nach `memory/01_rolle_routinen.md`.
