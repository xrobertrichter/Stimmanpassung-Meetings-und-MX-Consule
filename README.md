# voicechain – Sprachaufbereitung Rode NT-USB → BlackHole → Zoom/Teams

voicechain ist eine Python-Echtzeit-Verarbeitungskette für Sprache in Webseminaren. Sie nimmt das
Signal eines **Rode NT-USB** entgegen, bereitet es in sieben Stufen auf (Input Trim, High-Pass,
Downward Expander, De-Esser, EQ, Compressor mit Loudness-Normalizer, Limiter) und gibt es an das
virtuelle Audiokabel **BlackHole 2ch** weiter. In Zoom oder Microsoft Teams wählt man dann
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
2. **Repository holen und Umgebung anlegen**
   ```bash
   git clone <repo-url> voicechain && cd voicechain
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```
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
   Alle Zeilen müssen „bestanden" zeigen.
7. **Sprachsample aufnehmen** (30–120 s, normale Seminarstimme, mit Pausen von einigen
   Sekunden, am gewohnten Platz) – z. B. mit QuickTime Player (Neue Audioaufnahme, Quelle
   RØDE NT-USB) und als WAV exportieren, oder mit einem beliebigen Recorder. Ohne eigenes
   Sample lässt sich der Ablauf mit `python -m voicechain make-sample --out samples/demo.wav`
   proben.
8. **Kalibrieren**
   ```bash
   python -m voicechain calibrate --wav pfad/zum/sample.wav
   ```
   Erzeugt `config/chain.calibrated.yaml` und einen Bericht unter `reports/`. Der Bericht
   zeigt Messwerte vorher/nachher und ob die Akzeptanzkriterien erfüllt sind.
9. **Live starten**
   ```bash
   python -m voicechain run --config config/chain.calibrated.yaml
   ```
   (oder `scripts/run_live.sh --config config/chain.calibrated.yaml`). Beim ersten Start fragt
   macOS nach der Mikrofonberechtigung für das Terminal – erlauben. Jede Sekunde erscheint eine
   Statuszeile mit Pegeln, Gain-Reduction je Stufe, Xruns und der letzten adaptiven Änderung.
10. **Zoom/Teams einstellen:** Mikrofon „BlackHole 2ch", Rauschunterdrückung aus bzw. Original-
    Ton/High-Fidelity an, automatische Lautstärkeanpassung aus, Lautsprecher = Kopfhörer am Rode.
    Vor dem Seminar einen Zoom-Testanruf bzw. Teams-Testanruf machen (`docs/01_macOS_Setup.md`,
    Abschnitt „Testen vor dem Seminar").

Beenden mit `Ctrl-C` im Terminal. Danach in Zoom/Teams wieder das Rode-Mikrofon direkt wählen,
sonst bleibt es still.

## Kommandos im Überblick

| Kommando | Zweck |
|----------|-------|
| `python -m voicechain devices` | Audiogeräte mit Index, Kanälen und Samplerate auflisten |
| `python -m voicechain run [--config PFAD] [--input-device NAME] [--output-device NAME] [--no-adaptive] [--freeze]` | Live-Betrieb Rode → BlackHole |
| `python -m voicechain calibrate --wav PFAD [--config BASIS] [--out NEU.yaml] [--report BERICHT.md]` | Sprachsample analysieren → Konfigurationsvorschlag + Bericht |
| `python -m voicechain process --in PFAD --out PFAD [--config PFAD]` | WAV offline durch die Kette (A/B-Hören), Messzeile vorher/nachher |
| `python -m voicechain selftest [--config PFAD] [--report PFAD]` | Synthetische Signale durch die Kette, Prüftabelle Soll/Ist, Exit-Code 1 bei Nichtbestehen |
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
