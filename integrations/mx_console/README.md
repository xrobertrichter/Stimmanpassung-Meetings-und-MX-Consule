# Logi MX Creative Console → voicechain

Dieses Verzeichnis enthält alles, was die Konsole aufrufen kann, um die laufende Kette
(`python -m voicechain run`) im Meeting zu bedienen. Fachliches Konzept, Begründung je Taste und
Fehlersuche: `docs/05_MX_Creative_Console.md`.

```
Taste / Dial ──► Logi Options+ Aktion „Run“ ──► actions/<kommando>.sh
                                  (oder AppleScript) ──► python -m voicechain ctl <kommando> --notify
                                                                  │
                                                      Unix-Socket logs/control.sock
                                                                  ▼
                                                    python -m voicechain run (LiveControl)
                                                                  │
                                                   macOS-Mitteilung mit der Antwort
```

## Inhalt

| Pfad | Zweck |
|------|-------|
| `functions.json` | Manifest beider Ebenen („Meeting“, „Feinabstimmung“): je Taste `slot`, `id`, `label` (≤ 10 Zeichen für das Tastendisplay), `description`, `action_script`, `applescript`, `cli`; je Ebene `dial` mit `rotate_cw`, `rotate_ccw`, `press`. Ein eigenes Profilskript (z. B. das `mx_profile.py` des Nutzers) kann die Datei einlesen und daraus das Options+-Profil erzeugen. |
| `actions/<kommando>.sh` | Eine Shell-Aktion je Funktion (20 Stück, alle 20 `ctl`-Kommandos). Findet das Repository relativ zum eigenen Pfad, nutzt `.venv/bin/python`, falls vorhanden, sonst `python3` (oder `PYTHON_BIN`), ruft `python -m voicechain ctl <kommando> --notify`. Zusätzliche Argumente werden durchgereicht (`actions/louder.sh --value 1`). |
| `applescript/<kommando>.applescript` | Dieselbe Funktion als AppleScript (`do shell script` auf das Shell-Skript), für Options+-Aktionen vom Typ AppleScript. Der Repository-Pfad steht am Anfang der Datei (`~/voicechain`) und ist anzupassen, wenn das Repository woanders liegt. |

Die Kommandonamen sind die der CLI (`python -m voicechain ctl --help`): `louder`, `quieter`, `mute`,
`room-less`, `room-more`, `deess-more`, `deess-less`, `natural`, `dense`, `freeze`, `reset`,
`status`, `warmer`, `brighter`, `power` (Kill-Switch, Ebene 2 Taste 4), `relearn`, `snapshot`,
`bypass`, `marker` sowie `loudness-reset` (hat Skript, AppleScript und CLI, aber keine Taste;
im Manifest unter `unassigned`, frei belegbar).

## Einrichtung

1. **Ausführbar machen** (einmalig, nach dem Klonen):
   ```bash
   chmod +x integrations/mx_console/actions/*.sh
   ```
2. **Test ohne Konsole** (Terminal), bei laufendem `python -m voicechain run`:
   ```bash
   integrations/mx_console/actions/status.sh
   integrations/mx_console/actions/room-less.sh
   integrations/mx_console/actions/reset.sh
   ```
   Jede Aktion gibt eine Zeile aus und zeigt dieselbe Zeile als Mitteilung. Läuft `run` nicht,
   lautet die Antwort „voicechain run läuft nicht …“ (Exit 2).
3. **Verdrahtung in Logi Options+** (Bezeichnungen je Version; Stand Kenntnis der Autoren):
   Options+ öffnen → MX Creative Console → Keypad → Ebene/Seite anlegen („Meeting“, dann
   „Feinabstimmung“) → Taste anklicken → Aktion suchen → **„Run“** (bzw. „Shell-Skript
   ausführen“) → Pfad des Skripts eintragen, z. B.
   `/Users/<name>/voicechain/integrations/mx_console/actions/room-less.sh`. Alternativ die Aktion
   **„AppleScript“** mit der Datei aus `applescript/`. Tastenbeschriftung aus `label` des Manifests.
   Dialpad → Drehen rechts: `louder.sh`, Drehen links: `quieter.sh`, Drücken: `mute.sh` – auf
   beiden Ebenen gleich.
4. **Rechte für Mitteilungen:** Die Mitteilung kommt aus `osascript`. Beim ersten Mal fragt macOS,
   ob das Terminal bzw. Logi Options+ Mitteilungen senden darf → erlauben. Nachträglich:
   Systemeinstellungen → Mitteilungen → „Script Editor“/„osascript“ bzw. das startende Programm.
   Ohne `osascript` (oder ohne Recht) läuft die Aktion trotzdem; die Antwort steht dann nur in der
   Konsole des aufrufenden Programms.
5. **Socket-Pfad:** Standard `logs/control.sock` im Repository (`control.socket_path` in der YAML).
   Startet man `run` mit einer anderen Konfiguration, die einen anderen Pfad setzt, den Skripten
   `--socket PFAD` mitgeben (Options+ erlaubt Argumente im Run-Feld) oder `--config DATEI`.

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
```

Pfade im Manifest sind relativ zum Repository-Wurzelverzeichnis.

## Fehlersuche (kurz)

| Symptom | Maßnahme |
|---------|----------|
| Mitteilung „voicechain run läuft nicht“ | `python -m voicechain run` starten; alte Socket-Datei wird beim Start ersetzt |
| Keine Mitteilung, aber Terminal zeigt die Antwort | Mitteilungsrecht für osascript/Terminal/Options+ erteilen (Systemeinstellungen → Mitteilungen) |
| Options+ meldet „Permission denied“ | `chmod +x integrations/mx_console/actions/*.sh` |
| Antwort „abgelehnt: …“ | Die Kette hat das Kommando verweigert (z. B. Grenze erreicht); Text der Mitteilung lesen |
| AppleScript findet das Skript nicht | `repoRoot` in der `.applescript`-Datei an den tatsächlichen Pfad anpassen |
