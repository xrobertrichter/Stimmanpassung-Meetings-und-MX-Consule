# voicechain – Sprachaufbereitung Rode NT-USB → BlackHole → Zoom/Teams

voicechain ist eine Python-Echtzeit-Verarbeitungskette für Sprache in Webseminaren. Sie nimmt das
Signal eines **Rode NT-USB** entgegen, bereitet es in acht Stufen auf (0 Input Trim, 1 High-Pass,
2 Downward Expander, 3 De-Esser, 4 EQ, 5 Compressor, 5b Loudness-Normalizer, 6 Limiter) und gibt
es an das virtuelle Audiokabel **BlackHole 2ch** weiter. In Zoom oder Microsoft Teams wählt man dann
„BlackHole 2ch" als Mikrofon. Die anderen Teilnehmenden hören eine gleichmäßig laute, von
Raumrauschen und Zischlauten befreite Stimme.

Eine Kalibrierung aus einem kurzen Sprachsample setzt alle Parameter passend zu Raum, Stimme
und Mikrofonabstand; eine adaptive Regelung führt im Betrieb langsam und in engen Grenzen nach.

## Wichtig: BlackHole verarbeitet selbst nichts

BlackHole ist nur ein virtuelles Kabel zwischen Programmen. Es hat keine Filter, keine
Rauschunterdrückung, keine Pegelregelung. Die gesamte Verarbeitung passiert in dem Python-Prozess
`python -m voicechain run`. Läuft dieser Prozess nicht, kommt in Zoom/Teams **Stille** an, sobald
BlackHole als Mikrofon gewählt ist.

## Signalweg

```
 Rode NT-USB (USB, 48 kHz)
      │  Mikrofonsignal, 1 Kanal
      ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │  voicechain  (python -m voicechain run)                          │
 │                                                                  │
 │  0 Input Trim ─► 1 High-Pass ─► 2 Downward Expander ─► 3 De-Esser│
 │        ─► 4 EQ ─► 5 Compressor ─► 5b Loudness Normalizer ─►      │
 │        ─► 6 Limiter (True Peak ≤ −1 dBTP)                        │
 │                                                                  │
 │  Nebenläufig: Meter → adaptive Regelung (alle 2 s, kleine Schritte)│
 └──────────────────────────────────────────────────────────────────┘
      │  Mono wird auf 2 Kanäle dupliziert
      ▼
 BlackHole 2ch (virtuelles Audiokabel, keine Verarbeitung)
      │
      ▼
 Zoom / Microsoft Teams  (Mikrofon: „BlackHole 2ch")

 Monitoring: Kopfhörer direkt am Rode NT-USB (Direktabhören, latenzfrei).
 Der Zoom-/Teams-Ton der anderen kommt über das normale Ausgabegerät
 (Rode-Kopfhörerausgang oder beliebiges anderes Gerät).
```

## Systemvoraussetzungen

- macOS auf Apple Silicon (entwickelt für MacBook Pro M-Serie; Intel-Macs sind nicht geprüft)
- Python 3.11 oder neuer
- Homebrew (für BlackHole und PortAudio)
- Rode NT-USB, Kopfhörer am Mikrofon
- Zoom oder Microsoft Teams

Python-Pakete (siehe `requirements.txt`): numpy, scipy, soundfile, sounddevice, PyYAML, pytest.

## Schnellstart in 10 Schritten

1. **BlackHole und PortAudio installieren**
   ```bash
   brew install blackhole-2ch portaudio
   ```
   Danach abmelden und wieder anmelden (oder neu starten), damit macOS den Treiber lädt.
2. **Python 3.11+ und Repository**: Das mit macOS gelieferte `python3` ist oft älter. Ein
   aktuelles Python kommt über Homebrew:
   ```bash
   brew install python@3.12
   git clone <repo-url> voicechain && cd voicechain
   python3.12 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```
   Alternativ erledigen `scripts/run_live.sh` und `scripts/calibrate.sh` das Anlegen der
   Umgebung selbst; sie prüfen die Python-Version und nennen den Installationsbefehl, wenn sie
   zu alt ist (`PYTHON_BIN=python3.12 scripts/run_live.sh`, falls `python3` ein älteres ist).
3. **Rode NT-USB anschließen, Kopfhörer am Mikrofon einstecken.** Mix-Regler am Mikrofon
   mittig, Kopfhörerlautstärke angenehm.
4. **Samplerate prüfen:** Audio-MIDI-Setup öffnen (Programme → Dienstprogramme), bei
   „RØDE NT-USB" und „BlackHole 2ch" jeweils **48.000 Hz** einstellen. Details und die Variante
   mit Aggregate Device stehen in `docs/01_macOS_Setup.md`.
5. **Geräte prüfen**
   ```bash
   python -m voicechain devices
   ```
   In der Liste müssen „RØDE NT-USB" (Eingang) und „BlackHole 2ch" (Ausgang) erscheinen.
6. **Selbsttest** (kein Mikrofon nötig, prüft die Kette mit synthetischen Signalen)
   ```bash
   python -m voicechain selftest
   ```
   In der Spalte „bestanden" muss überall „ja" stehen, die letzte Zeile „Ergebnis: … BESTANDEN"
   (Exit-Code 0).
7. **Sprachsample aufnehmen** (am gewohnten Platz, mit dem eingebauten Kommando; kein
   zusätzliches Programm nötig)
   ```bash
   python -m voicechain record --out samples/sample.wav --seconds 60
   ```
   Ablauf während der 60 s: **zuerst 10 s still sein** (nur Raumgeräusch), dann in normaler
   Seminarlautstärke sprechen, **in der Mitte eine Pause von mindestens 5 s** machen, dann
   weitersprechen. Das Kommando zeigt Restzeit und Spitzenpegel; steht dort „ZU LAUT", Abstand
   vergrößern und wiederholen. Es entsteht ein 48-kHz-Mono-WAV (24 Bit) ohne jede Bearbeitung.
   Liegt bereits eine Aufnahme als `.m4a` vor, wandelt das macOS-Bordmittel
   `afconvert -f WAVE -d LEI24@48000 aufnahme.m4a sample.wav` sie um (verlustbehaftete
   Zwischenformate möglichst vermeiden, sie verfälschen den Rauschteppich). Ohne Mikrofon lässt
   sich der Ablauf mit `python -m voicechain make-sample --out samples/demo.wav` proben.
8. **Kalibrieren**
   ```bash
   python -m voicechain calibrate --wav pfad/zum/sample.wav
   ```
   Erzeugt `config/chain.calibrated.yaml` und den Bericht `reports/calibration_report.md`
   (Pfade über `--out`/`--report` änderbar). Der Bericht zeigt Messwerte vorher/nachher und ob
   die Akzeptanzkriterien erfüllt sind.
9. **Live starten**
   ```bash
   python -m voicechain run --config config/chain.calibrated.yaml
   ```
   (oder `scripts/run_live.sh --config config/chain.calibrated.yaml`). Beim ersten Start fragt
   macOS nach der Mikrofonberechtigung für das Terminal – erlauben. Jede Sekunde erscheint eine
   Statuszeile mit Pegeln, Gain-Reduction je Stufe, Xruns, Zähler nicht-finiter Eingangsblöcke,
   Zustand der adaptiven Regelung (`on`/`FROZEN`/`off`), dem Störabstand des Normalizers
   (`snr 24.0 LU`) und der letzten adaptiven Änderung. Erscheint stattdessen der Marker
   **`SNR LOW (12.5 LU)`**, ist der Abstand zwischen Sprache und Sprechpausen zu gering: Der
   Normalizer hebt die Sprache dann bewusst nicht weiter an (sonst würden die Pausen mit
   hochgezogen), die Pausen bleiben aber hörbarer als gewünscht. Abhilfe: Raum leiser machen
   (Lüfter, Fenster, Abstand zum Mikrofon verringern) oder mit einem neuen Sample kalibrieren.
   Die Regelung lässt sich im laufenden Betrieb einfrieren und wieder freigeben (z. B. vor
   einer Präsentation), aus einem zweiten Terminal:
   ```bash
   kill -USR1 $(pgrep -f "voicechain run")
   ```
   Jeder Aufruf schaltet um; die Statuszeile zeigt dann `adaptive FROZEN`.
10. **Zoom/Teams einstellen:** Mikrofon „BlackHole 2ch", Rauschunterdrückung aus bzw. Original-
    Ton/High-Fidelity an, automatische Lautstärkeanpassung aus, Lautsprecher = Kopfhörer am Rode.
    Vor dem Seminar einen Zoom-Testanruf bzw. Teams-Testanruf machen (`docs/01_macOS_Setup.md`,
    Abschnitt „Testen vor dem Seminar").

Beenden mit `Ctrl-C` im Terminal. Danach in Zoom/Teams wieder das Rode-Mikrofon direkt wählen,
sonst bleibt es still.

## Bedienung im Meeting (MX Creative Console)

Die laufende Kette lässt sich ohne Terminal korrigieren. Alle Tasten sind Offsets auf die
kalibrierten Werte; die adaptive Regelung arbeitet weiter und regelt sie nicht zurück. Ein Druck
ist ein Schritt (`--value` ändert nur den Betrag bis zur Schrittweite), Reset nimmt alles zurück.

| Symptom | Taste (Ebene) |
|---|---|
| Teilnehmende hören Lüfter/Raum in meinen Pausen | Wenig Raum (Meeting) |
| Wortenden abgeschnitten | Mehr Raum (Meeting) |
| S-Laute stechen / es lispelt | Zischen − / Zischen + (Meeting) |
| Klingt gepresst / Pegel schwankt | Natürlich / Dichter (Meeting) |
| Zu leise oder zu laut | Dial drehen; Druck = Kalibrierwert (beide Ebenen) |
| Gleich Präsentation, nichts mehr ändern | Freeze (Meeting) |
| Ich habe mich verstellt | Reset (Meeting) |
| Dumpf / spitz über die Verbindung | Wärmer / Heller (Feinabstimmung) |
| Was ist aktiv? | Status (Feinabstimmung) |
| Laptop heiß, Verarbeitung aus | DSP an/aus (Feinabstimmung) |
| Raumwechsel | Neu lernen (Feinabstimmung) |
| So soll es bleiben / Vergleich / kurz stumm | Snapshot / Bypass A/B / Mute (Feinabstimmung) |

Jede Funktion ist auch im Terminal erreichbar:

```bash
python -m voicechain ctl room-less --notify   # weniger Raum, Antwort als macOS-Mitteilung
python -m voicechain ctl status               # Lautheit, Störabstand, Offsets, Freeze/Mute
python -m voicechain ctl power                # Kill-Switch: Verarbeitung aus/an (Statuszeile OFF)
python -m voicechain ctl reset                # Bedienzustand zurück (nachgeführte Schwellen bleiben)
python -m voicechain ctl relearn              # Regelung neu lernen (nach Raumwechsel)
```

Voraussetzung für die Konsole: das `.venv` im Repository (Schritt 2), denn Logi Options+ startet
Skripte mit minimalem PATH und Apples System-Python ohne die Abhängigkeiten; die Skripte nehmen
`.venv/bin/python`, sonst `VOICECHAIN_PYTHON` oder das Homebrew-Python. Konzept, Begründung je
Taste und Fehlersuche: `docs/05_MX_Creative_Console.md`; Skripte, Manifest (20 Kommandos) und
Verdrahtung in Logi Options+: `integrations/mx_console/README.md`.

## Kommandos im Überblick

| Kommando | Zweck |
|----------|-------|
| `python -m voicechain devices` | Audiogeräte mit Index, Kanälen und Samplerate auflisten |
| `python -m voicechain record --out PFAD [--seconds 60] [--input-device NAME] [--config PFAD]` | Kalibriersample aufnehmen (rohes Mikrofonsignal, 48 kHz mono, 24-Bit-WAV), mit Restzeit und Pegelanzeige |
| `python -m voicechain run [--config PFAD] [--input-device NAME] [--output-device NAME] [--no-adaptive] [--freeze] [--no-control]` | Live-Betrieb Rode → BlackHole; öffnet den Steuer-Socket für `ctl`/Konsole (`--no-control` lässt ihn weg); `kill -USR1 <pid>` schaltet Freeze um |
| `python -m voicechain calibrate --wav PFAD [--config BASIS] [--out NEU.yaml] [--report BERICHT.md]` | Sprachsample analysieren → Konfigurationsvorschlag + Bericht |
| `python -m voicechain process --in PFAD --out PFAD [--config PFAD]` | WAV (48 kHz) offline durch die Kette (A/B-Hören), Messzeilen vorher/nachher |
| `python -m voicechain selftest [--config PFAD] [--report PFAD]` | Synthetische Signale durch die Kette, Prüftabelle Soll/Ist, Exit-Code 1 bei Nichtbestehen |
| `python -m voicechain ctl KOMMANDO [--value X] [--text ...] [--path PFAD] [--socket PFAD] [--notify] [--notify-errors]` | Laufende Kette steuern (20 Kommandos der Konsole, z. B. `louder`, `room-less`, `power`, `reset`, `status`); Socket Standard `/tmp/voicechain-<user>.sock` bzw. `control.socket_path`; Exit 0/1/2 |
| `python -m voicechain make-sample --out PFAD` | Synthetisches Sprach-WAV erzeugen, um den Kalibrierablauf ohne Mikrofon zu proben |

Relative Pfade werden vom Repository-Wurzelverzeichnis aus aufgelöst; Ausgabeverzeichnisse
werden angelegt. `--input-device`/`--output-device` akzeptieren einen Namensteil
(Groß/Klein und „Ø/O" egal) oder den Index aus `devices`. Für ein Aggregate Device gibt man
beiden Optionen denselben Namen.

## Dokumentation

- `docs/01_macOS_Setup.md` – BlackHole, Audio-MIDI-Setup (zwei Varianten), Rode NT-USB,
  Zoom, Teams, Test vor dem Seminar, Fehlersuche, Latenz
- `docs/02_Parameter_Begruendung.md` – jeder Parameter mit Startwert, Bereich, Begründung, Hörbild
- `docs/03_Betrieb_und_Kalibrierung.md` – Kalibrierung, Live-Betrieb, A/B-Vergleich, adaptive Regelung
- `docs/04_Pruefprotokoll.md` – Ergebnisse der Prüfungen
- `docs/05_MX_Creative_Console.md` – Live-Bedienung im Meeting (Konsole, `ctl`, Offsets)
- `docs/00_Beauftragung.md` – Beauftragung und Schnittstellenvertrag

## Entwicklung und Tests

```bash
pytest -q                      # läuft ohne Audiogeräte (Container/CI)
python -m voicechain selftest  # messbare Prüfwerte der Kette
```

Die Tests ersetzen `sounddevice` durch Attrappen; der Live-Teil (`voicechain/runtime.py`)
importiert PortAudio erst beim Aufruf, alle anderen Module sind geräteunabhängig.

## Alternativen (nur als Hinweis)

Statt des Python-Prozesses kann die gleiche Kette auch in einer DAW oder einem AU-Host
(z. B. mit Audio-Units-Plugins) aufgebaut und deren Ausgang auf BlackHole geroutet werden. Dieses
Projekt dokumentiert und prüft ausschließlich den Python-Weg; Kalibrierung und adaptive Regelung
gibt es nur dort.
