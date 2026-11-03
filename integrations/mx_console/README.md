# Logi MX Creative Console → voicechain

Dieses Verzeichnis enthält alles, was die Konsole aufrufen kann, um die laufende Kette
(`python -m voicechain run`) im Meeting zu bedienen. Fachliches Konzept, Begründung je Taste und
Fehlersuche: `docs/05_MX_Creative_Console.md`.

```
Taste / Dial ──► Logi Options+ Aktion „Run“ ──► actions/<kommando>.sh
                                  (oder AppleScript) ──► python -m voicechain ctl <kommando> --notify
                                                                  │
                                                      Unix-Socket /tmp/voicechain-<user>.sock
                                                                  ▼
                                                    python -m voicechain run (LiveControl)
                                                                  │
                                                   macOS-Mitteilung mit der Antwort
```

## Wann drücke ich was (Symptom → Taste)

| Symptom im Meeting | Taste | Ebene |
|---|---|---|
| Teilnehmende hören Lüfter, Raum oder Tippen in meinen Pausen | **Wenig Raum** (mehrmals möglich) | Meeting K1 |
| Wortenden werden abgeschnitten, der Raum „atmet“ | **Mehr Raum** | Meeting K2 |
| S- und Sch-Laute stechen | **Zischen −** | Meeting K3 |
| Es lispelt, S-Laute klingen stumpf (zu viel entzischt) | **Zischen +** | Meeting K4 |
| Stimme klingt gepresst, komprimiert, „nah am Ohr“ | **Natürlich** | Meeting K5 |
| Pegel schwankt, man rückt vom Mikrofon weg und wieder hin | **Dichter** | Meeting K6 |
| Ich bin zu leise / zu laut für die anderen | **Dial drehen** (rechts lauter, links leiser), Druck = zurück auf Kalibrierwert | beide Ebenen |
| Gleich kommt eine Präsentation, nichts soll sich mehr ändern | **Freeze** (noch einmal: wieder freigeben) | Meeting K7 |
| Ich habe mich verstellt | **Reset** | Meeting K8 |
| Klingt dumpf / zu spitz über die Verbindung | **Wärmer** / **Heller** | Feinabstimmung K1/K2 |
| Was ist gerade aktiv? | **Status** | Feinabstimmung K3 |
| Laptop wird heiß oder die Verarbeitung selbst stört | **DSP an/aus** | Feinabstimmung K4 |
| Platz- oder Raumwechsel, anderer Abstand | **Neu lernen** | Feinabstimmung K5 |
| So soll es bleiben | **Snapshot** | Feinabstimmung K6 |
| Vergleich mit/ohne Verarbeitung | **Bypass A/B** | Feinabstimmung K7 |
| Kurz stumm ohne Zoom-Menü | **Mute** (noch einmal: wieder an) | Feinabstimmung K8 |

Jede Taste ist ein Schritt; mehrfaches Drücken geht bis zur Grenze, dann antwortet die Kette
„am Anschlag“. Reset nimmt alle Live-Korrekturen zurück.

## Inhalt

| Pfad | Zweck |
|------|-------|
| `functions.json` | Manifest beider Ebenen („Meeting“, „Feinabstimmung“): je Taste `slot`, `id`, `label` (≤ 10 Zeichen für das Tastendisplay), `description`, `action_script`, `applescript`, `cli`; je Ebene `dial` mit `rotate_cw` (Lauter), `rotate_ccw` (Leiser), `press` (Lautheit 0); `unassigned` für Funktionen ohne Taste (derzeit `marker`). Ein eigenes Profilskript (z. B. das `mx_profile.py` des Nutzers) kann die Datei einlesen und daraus das Options+-Profil erzeugen. |
| `actions/<kommando>.sh` | Eine Shell-Aktion je Funktion (20 Stück = alle 20 `ctl`-Kommandos). Findet das Repository relativ zum eigenen Pfad, wählt den Python-Interpreter (siehe unten) und ruft `python -m voicechain ctl <kommando> --notify`. Die Dial-Aktionen `louder.sh`/`quieter.sh` rufen `--notify-errors`: keine Mitteilung je Rastung, nur wenn die Kette ablehnt (Grenze erreicht) oder nicht läuft. Zusätzliche Argumente werden durchgereicht (`actions/louder.sh --value 0.25`). |
| `applescript/<kommando>.applescript` | Dieselbe Funktion als AppleScript (`do shell script` auf das Shell-Skript), für Options+-Aktionen vom Typ AppleScript. Der Repository-Pfad steht am Anfang der Datei (`~/voicechain`) und ist anzupassen, wenn das Repository woanders liegt. |

Die 20 Kommandonamen sind die der CLI (`python -m voicechain ctl --help`): Ebene Meeting
`room-less`, `room-more`, `deess-more`, `deess-less`, `natural`, `dense`, `freeze`, `reset`; Ebene
Feinabstimmung `warmer`, `brighter`, `status`, `power`, `relearn`, `snapshot`, `bypass`, `mute`;
Dial `louder`, `quieter`, `loudness-reset`; ohne Taste `marker`.

## Voraussetzung: Python mit den Abhängigkeiten

Logi Options+ und AppleScripts (`do shell script`) starten Skripte mit einem minimalen `PATH`
(`/usr/bin:/bin:/usr/sbin:/sbin`) und ohne Login-Profil. `python3` ist dort Apples System-Python
ohne PyYAML und numpy; `python -m voicechain ctl` würde damit scheitern. Die Skripte wählen deshalb
in dieser Reihenfolge:

1. `.venv/bin/python` im Repository – **empfohlen**; entsteht mit docs/01 Schritt 2
   (`python3.12 -m venv .venv && pip install -r requirements.txt`) oder beim ersten
   `scripts/run_live.sh`.
2. `VOICECHAIN_PYTHON` – absoluter Pfad zu einem Python mit installierten Abhängigkeiten
   (z. B. `/opt/homebrew/bin/python3.12`). In Options+ lässt sich die Variable nicht setzen;
   sie ist für eigene Wrapper gedacht. Ohne `.venv` genügt es daher, den Homebrew-Interpreter zu
   haben:
3. `/opt/homebrew/bin/python3` (Apple Silicon) bzw. `/usr/local/bin/python3` (Intel), wenn dort
   `pip install -r requirements.txt` ausgeführt wurde,
4. sonst `python3` aus dem `PATH` (`PYTHON_BIN` überschreibt).

`ctl` selbst braucht nur die Standardbibliothek plus PyYAML (nur zum Lesen von
`control.socket_path` aus der YAML); fehlt PyYAML, nutzt `ctl` den Standard-Socket
`/tmp/voicechain-<user>.sock` und sagt das in der Konsole.

## Einrichtung

1. **Ausführbar machen** (einmalig, nach dem Klonen):
   ```bash
   chmod +x integrations/mx_console/actions/*.sh
   ```
2. **Test ohne Konsole** (Terminal), bei laufendem `python -m voicechain run`:
   ```bash
   integrations/mx_console/actions/status.sh
   integrations/mx_console/actions/room-less.sh
   integrations/mx_console/actions/louder.sh      # keine Mitteilung, nur Konsolenzeile
   integrations/mx_console/actions/reset.sh
   ```
   Jede Aktion gibt eine Zeile aus; Tasten zeigen dieselbe Zeile als Mitteilung. Läuft `run`
   nicht, lautet die Antwort „voicechain run läuft nicht …“ (Exit 2, als Mitteilung).
3. **Verdrahtung in Logi Options+** (Bezeichnungen je Version; Stand Kenntnis der Autoren):
   Options+ öffnen → MX Creative Console → Keypad → Ebene/Seite anlegen („Meeting“, dann
   „Feinabstimmung“) → Taste anklicken → Aktion suchen → **„Run“** (bzw. „Shell-Skript
   ausführen“) → Pfad des Skripts eintragen, z. B.
   `/Users/<name>/voicechain/integrations/mx_console/actions/room-less.sh`. Alternativ die Aktion
   **„AppleScript“** mit der Datei aus `applescript/`. Tastenbeschriftung aus `label` des Manifests.
   Dialpad → Drehen rechts: `louder.sh`, Drehen links: `quieter.sh`, Drücken: `loudness-reset.sh`
   – auf beiden Ebenen gleich.
4. **Rechte für Mitteilungen:** Die Mitteilung kommt aus `osascript`. Beim ersten Mal fragt macOS,
   ob das Terminal bzw. Logi Options+ Mitteilungen senden darf → erlauben. Nachträglich:
   Systemeinstellungen → Mitteilungen → „Script Editor“/„osascript“ bzw. das startende Programm.
   Ohne `osascript` (oder ohne Recht) läuft die Aktion trotzdem; die Antwort steht dann nur in der
   Konsole des aufrufenden Programms.
5. **Socket-Pfad:** Standard `/tmp/voicechain-<user>.sock` (kurz, unabhängig vom Repository-Pfad;
   Unix-Sockets erlauben auf macOS nur 103 Byte). In `config/chain.default.yaml` ist
   `control.socket_path` nur auskommentiert, der Standard gilt. Setzt die verwendete YAML den
   Schlüssel auf etwas anderes, den Skripten `--config DATEI` oder `--socket PFAD` mitgeben
   (Options+ erlaubt Argumente im Run-Feld); `run` nennt den Pfad beim Start („Control socket: …“).

## `--value`

Ein Druck ist ein Schritt (0,5 LU Lautheit, 2 dB Raum/Zischen, 1 dB Tilt, 1 Stufe Intensität).
`--value` bestimmt nur den Betrag dieses einen Schritts und wird von der Kette auf höchstens eine
Schrittweite begrenzt: `louder.sh --value 0.25` wirkt mit 0,25 LU, `louder.sh --value 1` wirkt
trotzdem mit 0,5 LU (die Antwort nennt den wirksamen Wert). Größere Änderungen sind mehrere
Drücke – so bleibt jede Taste vorhersehbar.

## Profilskript des Nutzers

`functions.json` ist so aufgebaut, dass ein lokales Skript (der Nutzer hat ein eigenes
`mx_profile.py` auf seinem Mac; es ist hier nicht enthalten) es einlesen kann:

```python
import json
manifest = json.load(open("integrations/mx_console/functions.json", encoding="utf-8"))
for page in manifest["pages"]:
    for key in page["keys"]:
        print(page["name"], key["slot"], key["label"], key["action_script"])
    print(page["name"], "dial", {k: v["id"] for k, v in page["dial"].items()})
print("ohne Taste:", [u["id"] for u in manifest["unassigned"]])
```

Pfade im Manifest sind relativ zum Repository-Wurzelverzeichnis.

## Fehlersuche (kurz)

| Symptom | Maßnahme |
|---------|----------|
| Mitteilung „voicechain run läuft nicht“ | `python -m voicechain run` starten; eine alte Socket-Datei wird beim Start ersetzt |
| Mitteilung „voicechain run läuft bereits“ beim Start | Es läuft schon eine Instanz; erst beenden (Ctrl-C oder `kill <pid>`) |
| Mitteilung „ModuleNotFoundError … yaml/numpy“ | Falsches Python: `.venv` anlegen (docs/01 Schritt 2) oder Homebrew-Python mit `pip install -r requirements.txt` ausstatten |
| Keine Mitteilung, aber Terminal zeigt die Antwort | Mitteilungsrecht für osascript/Terminal/Options+ erteilen (Systemeinstellungen → Mitteilungen) |
| Options+ meldet „Permission denied“ | `chmod +x integrations/mx_console/actions/*.sh` |
| Antwort „abgelehnt: … am Anschlag“ | Grenze erreicht; Gegenrichtung oder Reset |
| AppleScript findet das Skript nicht | `repoRoot` in der `.applescript`-Datei an den tatsächlichen Pfad anpassen |
| „control socket path is … bytes long“ | `control.socket_path` auf einen kurzen absoluten Pfad setzen (z. B. `~/.voicechain/ctl.sock`) oder den Eintrag entfernen (Standard `/tmp/voicechain-<user>.sock`) |
