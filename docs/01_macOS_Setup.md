# macOS-Einrichtung: Rode NT-USB → voicechain → BlackHole 2ch → Zoom/Teams

Stand: 2026-11-02. Zielgruppe: technisch versierte Person ohne Audio-Vorwissen. Geplante Dauer
für die Ersteinrichtung: unter 30 Minuten (ohne Aufnahme des Kalibriersamples).

> **Hinweis zu Menüpfaden:** Die Bezeichnungen in macOS, Zoom und Microsoft Teams ändern sich
> mit den Versionen. Die hier genannten Menüpfade entsprechen dem Kenntnisstand der Autoren und
> wurden nicht gegen die aktuelle Version der jeweiligen Programme geprüft. Wenn ein Menüpunkt
> anders heißt oder woanders sitzt, ist die Hilfe des jeweiligen Programms maßgeblich; gesucht
> wird immer nach den Begriffen „Mikrofon", „Rauschunterdrückung", „Originalton/Original Sound",
> „High-Fidelity" und „Lautstärke automatisch anpassen".

## 1. Überblick: Was wird gebaut

```
Rode NT-USB ──► voicechain (Python, Kette 0–6) ──► BlackHole 2ch ──► Zoom / Teams
     ▲                                                                  │
     └──── Kopfhörer am Rode (Direktabhören der eigenen Stimme)         │
           Ton der anderen Teilnehmenden ◄──────────────────────────────┘
```

- **Rode NT-USB**: USB-Kondensatormikrofon, arbeitet fest mit 48 kHz. Hat einen
  Kopfhörerausgang mit latenzfreiem Direktabhören und einem Mix-Regler (Mikrofon ↔ Computer).
- **voicechain**: der Python-Prozess `python -m voicechain run`. Er öffnet das Rode als Eingang
  und BlackHole als Ausgang und schickt jeden Block (256 Samples ≈ 5,3 ms) durch die Kette.
- **BlackHole 2ch**: virtueller Audiotreiber. Was ein Programm in BlackHole hineinspielt, sehen
  andere Programme als Mikrofonsignal. **BlackHole verarbeitet nichts**; ohne laufenden
  voicechain-Prozess kommt in Zoom/Teams Stille an.
- **Zoom / Teams**: wählt „BlackHole 2ch" als Mikrofon. Die eigene Klangverarbeitung der
  Konferenzsoftware wird abgeschaltet, damit sie nicht gegen die Kette arbeitet.

## 2. Installation

### 2.1 Homebrew, BlackHole, PortAudio

```bash
brew install blackhole-2ch portaudio
```

- `blackhole-2ch` ist der virtuelle Treiber (2 Kanäle reichen; 16ch/64ch sind nicht nötig).
- `portaudio` ist die Audiobibliothek, die das Python-Paket `sounddevice` benutzt. Fehlt sie,
  meldet `python -m voicechain devices` eine sprechende Fehlermeldung mit genau diesem
  Installationsbefehl.
- Nach der BlackHole-Installation **abmelden und wieder anmelden** (oder Neustart). Erst danach
  erscheint „BlackHole 2ch" in Audio-MIDI-Setup und in den Systemeinstellungen → Ton.
- Homebrew kann beim Installieren von BlackHole darauf hinweisen, dass eine Systemerweiterung
  bzw. ein Treiber erlaubt werden muss; dem Hinweis folgen (Systemeinstellungen →
  Datenschutz & Sicherheit → „Erlauben").

Kontrolle: Audio-MIDI-Setup öffnen (Programme → Dienstprogramme → Audio-MIDI-Setup oder per
Spotlight „Audio-MIDI-Setup"). In der linken Geräteliste müssen „BlackHole 2ch" und – bei
eingestecktem Mikrofon – „RØDE NT-USB" stehen.

### 2.2 Python-Umgebung

```bash
cd <repo>
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m voicechain devices
```

Die Skripte `scripts/run_live.sh` und `scripts/calibrate.sh` legen die virtuelle Umgebung bei
Bedarf selbst an und installieren die Abhängigkeiten.

### 2.3 Mikrofonberechtigung für das Terminal

macOS fragt beim ersten Zugriff eines Programms auf das Mikrofon nach. Für voicechain ist das
Programm das **Terminal** (oder iTerm, VS Code – das Programm, aus dem `python` gestartet
wird), nicht Python selbst.

- Beim ersten `python -m voicechain run` erscheint ein Dialog „Terminal möchte auf das Mikrofon
  zugreifen" → **Erlauben**.
- Nachträglich prüfen/ändern: Systemeinstellungen → Datenschutz & Sicherheit → Mikrofon →
  Schalter bei „Terminal" (bzw. dem verwendeten Terminalprogramm) einschalten.
- Ohne diese Berechtigung liefert das Rode nur Stille (kein Fehler!). Die Statuszeile zeigt dann
  dauerhaft `in -120.0 dBFS`.

## 3. Audio-MIDI-Setup: Zwei Varianten

voicechain braucht einen Eingang (Rode) und einen Ausgang (BlackHole). Unter CoreAudio geht das
auf zwei Wegen. **Variante A ist die Standardempfehlung**, Variante B die Rückfallebene, falls es
in Variante A zu Dropouts kommt.

### Variante A: Getrennte Geräte im Duplex-Stream (Standard)

voicechain öffnet einen PortAudio-Duplex-Stream mit zwei verschiedenen Geräten: Eingang
„RØDE NT-USB", Ausgang „BlackHole 2ch". PortAudio unter CoreAudio unterstützt das; intern
gleicht es die beiden Taktgeber mit Ringpuffern aus.

Einstellungen in Audio-MIDI-Setup:

1. „RØDE NT-USB" auswählen → Reiter **Eingang** → Format **48.000 Hz**, 1 Kanal (oder 2 Kanäle,
   je nach Firmware; voicechain nutzt Kanal `input.channel`, Standard 0).
2. „BlackHole 2ch" auswählen → Reiter **Ausgang** und **Eingang** → Format **48.000 Hz**,
   2 Kanäle (32-Bit Float ist Standard).
3. Beide Geräte müssen dieselbe Samplerate haben, sonst resampelt CoreAudio heimlich und die
   Latenz steigt bzw. es knackt.

Konfiguration (`config/chain.default.yaml`, Vorgabe):

```yaml
input:  {device: "RØDE NT-USB", channel: 0}
output: {device: "BlackHole 2ch", channels: 2}
```

Start: `python -m voicechain run`. Die Gerätenamen werden als Teilstring gesucht, Groß-/
Kleinschreibung und „Ø" gegen „O" spielen keine Rolle (`"rode nt-usb"` findet „RØDE NT-USB").

### Variante B: Aggregate Device „Rode+BlackHole" mit Drift-Korrektur

Ein Aggregate Device fasst mehrere Geräte zu einem einzigen CoreAudio-Gerät zusammen. Vorteil:
Es gibt nur einen Taktgeber, CoreAudio übernimmt den Abgleich (Drift-Korrektur), und der
Duplex-Stream läuft mit einem einzigen Geräteindex. Nachteil: eine Einrichtung mehr, und die
Kanalnummern verschieben sich.

1. Audio-MIDI-Setup → unten links **„+"** → **Aggregate Device erstellen**.
2. Name vergeben: `Rode+BlackHole`.
3. Rechts in der Liste **„Verwenden"** anhaken bei **BlackHole 2ch** und **RØDE NT-USB**.
4. Reihenfolge: **BlackHole 2ch nach oben ziehen**, Rode darunter. Die Reihenfolge bestimmt die
   Kanalnummern des Aggregats (siehe Tabelle unten).
5. **Taktquelle (Clock Source)**: `RØDE NT-USB` (das echte Gerät gibt den Takt vor).
6. **Drift-Korrektur** anhaken bei **BlackHole 2ch** (nicht beim Taktgeber). Ohne
   Drift-Korrektur laufen die Geräte langsam auseinander und es gibt regelmäßig Knackser.
7. Samplerate des Aggregats: 48.000 Hz.

Kanalbelegung bei dieser Reihenfolge (BlackHole zuerst, dann Rode):

| Richtung | Kanal 0–1 | Kanal 2 (und ggf. 3) |
|----------|-----------|----------------------|
| Eingang  | BlackHole 2ch (Loopback, uninteressant) | RØDE NT-USB Mikrofon |
| Ausgang  | BlackHole 2ch → Zoom/Teams | RØDE NT-USB Kopfhörerausgang |

Konfiguration für Variante B:

```yaml
input:  {device: "Rode+BlackHole", channel: 2}   # Rode-Mikrofon liegt hinter den 2 BlackHole-Kanälen
output: {device: "Rode+BlackHole", channels: 2}  # die ersten 2 Ausgangskanäle sind BlackHole
```

Alternativ ohne Änderung der YAML:
`python -m voicechain run --input-device "Rode+BlackHole" --output-device "Rode+BlackHole"`
(dann muss `input.channel` in der YAML trotzdem 2 sein). voicechain erkennt den gleichen Index
für Ein- und Ausgang und öffnet einen einzigen Duplex-Stream auf dem Aggregat. Steht das Rode in
der Liste oben, wäre `channel: 0` richtig und die BlackHole-Ausgänge lägen auf 2–3 – deshalb die
Empfehlung, BlackHole nach oben zu ziehen.

## 4. Rode NT-USB

- **Samplerate fest 48 kHz.** Das Mikrofon kann nichts anderes; die ganze Kette arbeitet deshalb
  mit 48 kHz (`sample_rate: 48000`). Bitte in Audio-MIDI-Setup nicht auf 44,1 kHz stellen.
- **Mix-Regler** (am Mikrofon, „Monitor Mix"): regelt im Kopfhörer das Verhältnis zwischen der
  eigenen Stimme (direkt, latenzfrei) und dem Computerton. Für Seminare: mittig bis leicht
  Richtung Computer, damit man die anderen gut hört und die eigene Stimme trotzdem kontrolliert.
  Dieser Regler beeinflusst **nicht**, was an voicechain/Zoom geht.
- **Kopfhörermonitoring:** Kopfhörer immer am Rode einstecken (3,5-mm-Buchse am Mikrofon). Man
  hört sich selbst ohne Verzögerung, und es gibt kein Echo in der Konferenz, weil kein
  Lautsprecher offen ist. In Zoom/Teams als **Lautsprecher** ebenfalls „RØDE NT-USB" wählen,
  dann kommt der Konferenzton über denselben Kopfhörer.
- **Abstand und Ausrichtung:** 15–25 cm vor dem Mund, leicht seitlich (nicht frontal
  angepustet). Das Rode ist eine Niere: von vorne besprechen (Rode-Logo zeigt zum Mund).
- **Popschutz:** den mitgelieferten Popschutz montieren. Er reduziert Plosive (p, b, t), die
  sonst den Expander/Kompressor zum Pumpen bringen.
- **Pegel:** Das NT-USB hat keinen Gain-Regler; den Eingangspegel setzt die Kalibrierung über
  Stufe 0 (Input Trim). In Systemeinstellungen → Ton → Eingang die Eingangslautstärke auf
  Maximum lassen, falls der Regler aktiv ist.
- **Stativ/Arm:** Trittschall über den Tisch geht in den High-Pass, aber ein Mikrofonarm oder eine
  Spinne ist besser als der Tischfuß.

## 5. Zoom einstellen

Pfade nach Kenntnisstand (siehe Hinweis oben): **Zoom → Einstellungen (⌘,) → Audio**.

1. **Mikrofon:** „BlackHole 2ch". Den Haken „Mikrofonlautstärke automatisch anpassen"
   **entfernen**, Mikrofonlautstärke auf ca. 75–100 % stellen. Die Pegelregelung macht
   voicechain (Normalizer).
2. **Lautsprecher:** „RØDE NT-USB" (Kopfhörer am Mikrofon) oder ein anderes Gerät mit Kopfhörer.
   Keine offenen Lautsprecher (siehe Echo unten).
3. **Hintergrundgeräusche unterdrücken / Audioprofil:** auf **„Niedrig"** stellen (oder
   „Originalton für Musiker" aktivieren, siehe 4). Zooms eigene Rauschunterdrückung würde die
   bereits bearbeitete Stimme verfälschen.
4. **Originalton für Musiker** („Original sound for musicians"): im Abschnitt Audioprofil
   **aktivieren**, darunter „Hochauflösendes Musikaudio" an, „Echounterdrückung" an lassen oder
   aus, „Stereo-Audio" aus. Im Meeting erscheint oben links ein Schalter „Originalton für
   Musiker: Ein/Aus" – er muss auf **Ein** stehen (Zoom merkt sich das pro Gerät).
   Hinweis: Bei Originalton **ohne** Echounterdrückung ist ein Kopfhörer zwingend, sonst hören
   die anderen sich selbst.
5. **Testen:** „Mikrofon testen" aufnehmen und abhören – klingt es wie die eigene Stimme ohne
   Rauschen, passt es. Zeigt der Pegelbalken nichts, läuft `python -m voicechain run` nicht oder
   ist noch nicht auf BlackHole geschaltet.

## 6. Microsoft Teams einstellen

Pfade nach Kenntnisstand (siehe Hinweis oben): **Teams → „…" (neben dem Profilbild) →
Einstellungen → Geräte**.

1. **Mikrofon:** „BlackHole 2ch".
2. **Lautsprecher:** „RØDE NT-USB" (Kopfhörer am Mikrofon).
3. **Mikrofonempfindlichkeit automatisch anpassen:** **aus** (falls vorhanden).
4. **Rauschunterdrückung:** auf **„Aus"** stellen (Optionen sind je nach Version „Automatisch /
   Niedrig / Hoch / Aus"). Teams' eigene Rauschunterdrückung läuft sonst auf dem schon
   bearbeiteten Signal und frisst Konsonanten.
5. **High-Fidelity-Musikmodus** (falls in der Version vorhanden): in den Geräteeinstellungen
   aktivieren; im Meeting dann über „Weitere Optionen" den Musikmodus einschalten. Er schaltet
   Rauschunterdrückung, Echounterdrückung und Pegelautomatik ab und überträgt mit höherer
   Bandbreite. Auch hier gilt: nur mit Kopfhörer.
6. **Testanruf:** In den Geräteeinstellungen „Testanruf tätigen" – Teams spielt die eigene
   Aufnahme zurück.

## 7. Testen vor dem Seminar

Reihenfolge, jedes Mal vor einem Termin (dauert 3 Minuten):

1. Rode anschließen, Kopfhörer einstecken, Terminal öffnen.
2. `python -m voicechain selftest` – alle Zeilen „bestanden". Das prüft die Kette an
   synthetischen Signalen und braucht kein Mikrofon.
3. `python -m voicechain run --config config/chain.calibrated.yaml` starten. In der Statuszeile
   muss beim Sprechen `in` sichtbar ausschlagen (typisch −30 … −15 dBFS) und `out` um −20 … −6
   dBFS liegen; `xruns` bleibt 0.
4. **Zoom:** Einstellungen → Audio → „Mikrofon testen" (oder `zoom.us/test` für ein
   Testmeeting). **Teams:** Einstellungen → Geräte → „Testanruf tätigen". Eigene Stimme
   aufnehmen und anhören: gleichmäßig laut, kein Rauschen in Pausen, Zischlaute weich,
   kein Echo.
5. Zur Kontrolle einmal auf das rohe Rode-Mikrofon umschalten und zurück – der Unterschied
   sollte hörbar sein (A/B).
6. Terminalfenster während des Seminars offen lassen (Statuszeile). Nach dem Seminar `Ctrl-C`
   und Zoom/Teams wieder auf das Rode direkt stellen.

## 8. Fehlersuche

| Symptom | Ursache | Maßnahme |
|---------|---------|----------|
| `python -m voicechain devices` meldet „PortAudio is not available" | PortAudio-Bibliothek fehlt | `brew install portaudio`, danach `pip install --force-reinstall sounddevice` |
| „BlackHole 2ch" fehlt in der Geräteliste | Treiber noch nicht geladen oder nicht erlaubt | Ab- und wieder anmelden bzw. Neustart; Systemeinstellungen → Datenschutz & Sicherheit → Treiber erlauben; `brew reinstall blackhole-2ch` |
| „RØDE NT-USB" fehlt | USB-Kabel/Hub, Mikrofon nicht erkannt | Direkt am Mac anschließen (kein passiver Hub), Kabel tauschen, Systeminformationen → USB prüfen |
| `run` meldet „No input device matches …" | Name in YAML passt nicht | `python -m voicechain devices` aufrufen, Namensteil oder Index übernehmen (`--input-device 3`) |
| `run` meldet „2 input devices match …" | Mehrere Geräte enthalten den Teilstring (z. B. Aggregat und Einzelgerät) | Längeren Namen oder Index verwenden |
| Statuszeile zeigt dauerhaft `in -120.0 dBFS` | Keine Mikrofonberechtigung für das Terminal, oder falscher Eingangskanal | Systemeinstellungen → Datenschutz & Sicherheit → Mikrofon → Terminal erlauben; bei Aggregat `input.channel` prüfen (Abschnitt 3, Variante B) |
| In Zoom/Teams kommt Stille an | voicechain läuft nicht, oder Mikrofon in Zoom/Teams ist nicht BlackHole | Prozess starten; Mikrofonauswahl prüfen; in macOS Systemeinstellungen → Ton darf BlackHole **nicht** als Standardausgabe gewählt sein (sonst landet Systemton im Meeting) |
| `xruns` zählt hoch, Knackser, Aussetzer (Dropouts) | Blockgröße zu klein, Samplerate-Mismatch, CPU-Last, Energiesparmodus | `block_size: 512` in der YAML; beide Geräte auf 48 kHz; Variante B (Aggregate mit Drift-Korrektur) nutzen; Browser-Tabs/Video schließen; Netzteil anschließen |
| Ton klingt „unter Wasser", stark verzerrt oder zu tief/hoch | Samplerate der Geräte ≠ 48 kHz (CoreAudio resampelt oder Takt stimmt nicht) | In Audio-MIDI-Setup Rode und BlackHole auf 48.000 Hz; `run` neu starten |
| Regelmäßiges Knacken alle paar Sekunden | Aggregate Device ohne Drift-Korrektur | Drift-Korrektur bei BlackHole anhaken, Taktquelle = Rode |
| Die anderen hören sich selbst (Echo) | Lautsprecher offen und Echounterdrückung durch Originalton/Musikmodus aus | Kopfhörer am Rode benutzen; oder Echounterdrückung in Zoom wieder einschalten |
| Stimme pumpt, wird in Pausen hörbar leiser/lauter | Expander/Kompressor zu scharf, kein Popschutz, Abstand schwankt | Neu kalibrieren mit realistischem Sample; Popschutz; `--freeze` setzen, wenn die adaptive Regelung nachführt, während man testet |
| Pegel in Zoom springt | Zooms/Teams' automatische Lautstärkeanpassung noch aktiv | Haken entfernen (Abschnitt 5/6), Rauschunterdrückung aus |
| Hohe Latenz, man hört sich im Kopfhörer verzögert | Kopfhörer am Mac statt am Rode, oder Zoom-Rückkanal mit eigenem Mikro | Kopfhörer am Rode, Direktmonitoring per Mix-Regler; in Zoom keine „Mikrofon abhören"-Funktion |
| `run` bricht mit „audio callback failed" ab | Fehler in der Kette (z. B. ungültige YAML-Werte) | Fehlermeldung lesen, YAML mit `python -m voicechain selftest --config <datei>` prüfen |
| Nach Schlafmodus kein Ton mehr | USB-Gerät wurde neu aufgezählt, Stream ist tot | `Ctrl-C`, `run` neu starten |

## 9. Latenz

- Blockgröße 256 Frames bei 48 kHz = **5,3 ms** Rechenblock. PortAudio arbeitet mit
  `latency="low"`, dazu kommen Ein- und Ausgangspuffer von CoreAudio (typisch 1–2 Blöcke je
  Richtung) und die Lookahead-Verzögerung des Limiters (5 ms).
- In Summe liegt die Verzögerung vom Mikrofon bis BlackHole in der Größenordnung **10–25 ms**,
  also deutlich unter 30 ms. Die konkreten Werte zeigt `run` beim Start an
  (`latency in … ms / out … ms`).
- Für Sprache in Konferenzen ist das unkritisch: Zoom/Teams selbst fügen durch Kodierung und
  Netz ein Vielfaches davon hinzu (üblich 100–300 ms), und die eigene Stimme hört man über das
  Direktmonitoring des Rode ohne jede Verzögerung.
- Wer die Latenz weiter senken will, kann `block_size: 128` setzen (2,7 ms); das erhöht das
  Risiko von Xruns. Bei Dropouts umgekehrt `block_size: 512` (10,7 ms) – immer noch unhörbar.
- Die Rechenzeit der Kette je 256er-Block liegt weit unter der Blockdauer (Budget < 1 ms, wird
  im `selftest` gemessen und ausgewiesen).
