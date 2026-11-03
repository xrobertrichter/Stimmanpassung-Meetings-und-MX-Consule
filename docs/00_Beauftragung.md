# Beauftragung: Sprachverbesserung Rode NT-USB → BlackHole → Zoom/Teams

Stand: 2026-11-02 · Status: verbindlich für Umsetzung und Prüfung

## 1. Ziel

Eine lauffähige, konfigurierbare und selbst nachsteuernde Audio-Verarbeitungskette auf einem
MacBook Pro (Apple Silicon, M5), die das Signal eines **Rode NT-USB** aufbereitet und über
**BlackHole 2ch** als virtuelles Mikrofon an **Zoom** oder **Microsoft Teams** liefert.
Zweck: bessere Sprachverständlichkeit und gleichmäßige Lautheit für die anderen
Teilnehmenden in Webseminaren.

## 2. Fachlicher Rahmen (aus dem Ausgangsbild "On Air"-Kette)

Verarbeitungsreihenfolge, unverändert zu übernehmen:

| # | Stufe               | Zweck                                                   |
|---|---------------------|---------------------------------------------------------|
| 0 | Input Trim          | Eingangspegel auf Referenz bringen (Kalibrierung)       |
| 1 | High-Pass           | Trittschall, Rumpeln, Plosiv-Energie unter Sprache weg  |
| 2 | Downward Expander   | Raumrauschen / Lüfter in Sprechpausen absenken          |
| 3 | De-Esser            | Zischlaute (s, sch, z) entschärfen                       |
| 4 | EQ                  | Mulm raus, Präsenz rein, Klangfarbe des NT-USB korrigieren |
| 5 | Compressor          | Dynamik der Sprache verdichten                           |
| 5b| Loudness Normalizer | Langsame Lautheitsregelung auf Zielwert (für die anderen TN) |
| 6 | Limiter             | Harte Obergrenze gegen Übersteuerung (True-Peak)         |

Begründung 5b: Die Beauftragung verlangt, dass der Ton "zunächst normalisiert" wird, damit die
anderen Teilnehmenden einen gleichmäßigen Pegel hören. Das wird zweistufig gelöst: statisch über
den Input Trim (Stufe 0, durch Kalibrierung gesetzt) und dynamisch über den Normalizer (5b), der
nach dem Kompressor und vor dem Limiter arbeitet.

Wichtige technische Tatsache: **BlackHole selbst verarbeitet kein Audio.** Es ist nur ein
virtuelles Audio-Kabel. Die Kette wird daher als Python-Echtzeitprozess umgesetzt
(Rode NT-USB → Python-Kette → BlackHole → Zoom/Teams). Alternativen (DAW, AU-Host) werden
in der Dokumentation nur als Option erwähnt.

## 3. Lieferumfang

### 3.1 Software (Python 3.11+, numpy, scipy, soundfile, sounddevice, PyYAML)

- Paket `voicechain/` mit den DSP-Stufen, Metern, Kalibrierung, adaptiver Regelung, Runtime, CLI.
- Konfiguration `config/chain.default.yaml` mit allen Parametern, jeder Wert mit Kommentar (warum).
- CLI (`python -m voicechain ...`):
  - `devices`       Audiogeräte auflisten
  - `run`           Live-Betrieb Rode → BlackHole, mit adaptiver Regelung
  - `calibrate`     Sample-WAV analysieren → Konfigurationsvorschlag + Bericht
  - `process`       WAV offline durch die Kette (A/B-Hören, Prüfung)
  - `selftest`      synthetische Signale durch die Kette, Prüfwerte ausgeben
- Tests (`pytest`) laufen **ohne Audiogeräte** (Container/CI). Jede Stufe hat Tests, die das
  erwartete Verhalten messbar belegen (z. B. HPF dämpft 50 Hz um ≥ X dB, Limiter überschreitet
  Ceiling nie, Expander senkt Rauschen in Pausen um ≥ Y dB, De-Esser senkt Zischband, Normalizer
  trifft Ziel-Lautheit ± Toleranz).

### 3.2 Prüfskripte / Kalibrierung (Kernanforderung)

- `calibrate`: Nutzer spielt ein Sprachsample (WAV, 30–120 s, Sprache mit Pausen) ein. Das Skript
  misst Rauschteppich, Sprachpegel, Zischanteil, spektrale Neigung, Spitzen und leitet daraus
  **alle signalabhängigen** Parameter ab (mit Grenzen): Input Trim, Expander-Schwelle,
  De-Esser-Frequenz und -Schwelle, EQ-Gains, Kompressor-Schwelle und Makeup, Normalizer-Gate
  und -Maximalgewinn. Zeitkonstanten, Ratios, Knie, Filterordnung und Limiter-Ceiling sind
  Designkonstanten und bleiben fest; ihre Begründung steht in docs/02.
  _Präzisierung 2026-11-03 (Blindprüfung Runde 2, BL-2): „alle Parameter" war wörtlich nicht
  sinnvoll, da Zeitkonstanten nicht aus einem Sample ableitbar sind._ Es schreibt eine neue YAML und einen Bericht
  (Messwerte vorher/nachher, Akzeptanzkriterien bestanden/nicht bestanden).
- Adaptive Regelung (`adaptive.py`): im Live-Betrieb werden Meter alle N Sekunden ausgewertet;
  Parameter werden langsam, in kleinen Schritten, mit Hysterese und innerhalb harter Grenzen
  nachgeführt (Expander-Schwelle folgt Rauschteppich, De-Esser-Schwelle folgt Zischanteil,
  Kompressor-Schwelle folgt Sprachpegel, Normalizer hält Ziel-Lautheit). Verhalten wie ein
  Tontechniker: erst zuhören (Lernphase), dann vorsichtig nachregeln, bei Problemen
  (Pumpen, Übersteuern, zu starke Absenkung) zurücknehmen. Alle Änderungen werden mit Grund
  protokolliert (JSONL). "Freeze" muss möglich sein.

### 3.3 Dokumentation (Deutsch)

- `README.md`: Überblick, Schnellstart in 10 Schritten.
- `docs/01_macOS_Setup.md`: BlackHole-Installation, Audio-MIDI-Setup (ggf. Aggregate Device),
  Rode NT-USB Einstellungen, Zoom- und Teams-Einstellungen (Original Sound / High-Fidelity,
  Rauschunterdrückung, automatische Pegelanpassung aus), Kopfhörer vs. Lautsprecher (Echo),
  Fehlersuche (Dropouts, Samplerate, Latenz, Rechte für Mikrofonzugriff).
- `docs/02_Parameter_Begruendung.md`: jeder Parameter mit Startwert, Bereich, Begründung,
  Hörbild bei Fehlstellung.
- `docs/03_Betrieb_und_Kalibrierung.md`: Ablauf Kalibrierung, Live-Betrieb, A/B-Vergleich,
  was die adaptive Regelung tut und wie man sie liest.
- `docs/04_Pruefprotokoll.md`: Ergebnisse der drei Prüfungen (PO, Software, Blind) und Nacharbeit.

## 4. Schnittstellenvertrag (verbindlich für parallele Umsetzung)

### 4.1 Konfiguration (YAML, Schema)

```yaml
sample_rate: 48000          # Rode NT-USB arbeitet fest mit 48 kHz
block_size: 256             # Frames pro Block (~5,3 ms)
input:  {device: "RØDE NT-USB", channel: 0}      # Teilstring-Suche, Groß/Klein egal
output: {device: "BlackHole 2ch", channels: 2}   # Mono wird auf beide Kanäle dupliziert
chain:
  input_trim: {gain_db: 0.0}
  highpass:   {enabled: true, freq_hz: 80.0, order: 4}
  expander:   {enabled: true, threshold_db: -45.0, ratio: 2.5, range_db: 20.0,
               attack_ms: 5.0, hold_ms: 50.0, release_ms: 150.0, knee_db: 6.0}
  deesser:    {enabled: true, freq_hz: 6500.0, bandwidth_oct: 1.0, threshold_db: -30.0,
               ratio: 4.0, max_reduction_db: 8.0, attack_ms: 1.0, release_ms: 60.0}
  eq:         {enabled: true, bands: [{type: low_shelf|peak|high_shelf, freq_hz: .., gain_db: .., q: ..}, ...]}
  compressor: {enabled: true, threshold_db: -20.0, ratio: 3.0, knee_db: 6.0,
               attack_ms: 10.0, release_ms: 100.0, makeup_db: 0.0}
  normalizer: {enabled: true, target_lufs: -18.0, window_s: 3.0, max_gain_db: 12.0,
               min_gain_db: -12.0, rate_db_per_s: 1.0, gate_lufs: -40.0}
  limiter:    {enabled: true, ceiling_dbfs: -1.0, lookahead_ms: 5.0, release_ms: 50.0}
adaptive:
  enabled: true
  learn_s: 30.0               # Lernphase ohne Eingriff
  interval_s: 2.0             # Auswerteintervall
  max_step_db: 0.5            # maximale Änderung je Eingriff
  log_path: "logs/adaptive.jsonl"
  bounds:                     # harte Grenzen je nachgeführtem Parameter
    expander.threshold_db: [-70.0, -30.0]
    deesser.threshold_db:  [-45.0, -15.0]
    compressor.threshold_db: [-35.0, -10.0]
```

Alle dB-Werte sind dBFS (Vollaussteuerung = 0 dBFS). Alle Zeiten in ms bzw. s wie benannt.

### 4.2 Python-API

```python
# voicechain/dsp/chain.py
class Chain:
    @classmethod
    def from_config(cls, cfg: dict) -> "Chain": ...
    def process(self, x: np.ndarray) -> np.ndarray      # float32, mono, shape (n,), -1..1; blockweise, Zustand bleibt erhalten
    def set_param(self, stage: str, name: str, value: float) -> None   # z. B. ("expander", "threshold_db", -42.0), threadsicher (Zuweisung + Neuberechnung der Koeffizienten)
    def get_param(self, stage: str, name: str) -> float
    def get_meters(self) -> dict        # zuletzt gemessene Werte je Stufe, z. B. {"expander": {"gain_reduction_db": ...}, "deesser": {...}, "compressor": {...}, "normalizer": {"gain_db": ...}, "limiter": {"gain_reduction_db": ...}, "output": {"peak_dbfs": ..., "rms_dbfs": ...}}
    def reset(self) -> None

# Jede Stufe in voicechain/dsp/<stufe>.py:
class <Stage>:
    def __init__(self, sample_rate: int, **params): ...
    def process(self, x: np.ndarray) -> np.ndarray      # gleiche Vertragsregeln wie Chain.process
    def set_param(self, name: str, value: float) -> None
    def get_param(self, name: str) -> float
    def meters(self) -> dict
    def reset(self) -> None

# voicechain/meters.py
class LoudnessMeter:          # ITU-R BS.1770 K-Bewertung, mono
    def __init__(self, sample_rate: int): ...
    def push(self, x: np.ndarray) -> None
    def momentary_lufs(self) -> float      # 400 ms
    def short_term_lufs(self) -> float     # 3 s
    def integrated_lufs(self) -> float     # gated, gesamte Historie
def true_peak_dbfs(x: np.ndarray, sample_rate: int) -> float   # 4x Oversampling
def rms_dbfs(x: np.ndarray) -> float
def noise_floor_dbfs(x: np.ndarray, sample_rate: int, frame_ms: float = 100.0) -> float   # Perzentil der Rahmen-RMS
def speech_level_dbfs(x: np.ndarray, sample_rate: int) -> float                           # Pegel der aktiven Sprachrahmen
def sibilance_ratio_db(x: np.ndarray, sample_rate: int) -> float   # Energie 5–10 kHz minus Energie 0.3–3 kHz, nur aktive Rahmen
def spectral_tilt_db_per_oct(x: np.ndarray, sample_rate: int) -> float
def band_energies_db(x: np.ndarray, sample_rate: int, edges_hz: list[float]) -> list[float]

# voicechain/calibrate.py
def analyze(x: np.ndarray, sample_rate: int) -> dict                      # Messwerte
def recommend(measurements: dict, base_cfg: dict) -> dict                # neue Konfiguration (Kopie)
def evaluate(x_in, x_out, sample_rate, cfg) -> dict                       # vorher/nachher + Akzeptanzkriterien
def run_calibration(wav_path, base_cfg_path, out_cfg_path, report_path) -> dict

# voicechain/adaptive.py
class AdaptiveController:
    def __init__(self, cfg: dict, chain: Chain, clock=time.monotonic, log_path=None): ...
    def update(self, meters: dict, now: float | None = None) -> list[dict]   # gibt vorgenommene Änderungen zurück ({stage, name, old, new, reason})
    def freeze(self, on: bool) -> None
```

### 4.3 Verhaltensregeln für alle Stufen

- Blockweise Verarbeitung mit beliebiger Blockgröße; Ergebnis muss unabhängig von der
  Blockgröße sein (Test: 64er-Blöcke vs. ein großer Block → identisch bis 1e-5).
- Filterzustände (sosfilt `zi`) werden über Blöcke hinweg fortgeführt.
- Keine Allokation von großen Puffern pro Block (Echtzeit!), keine Python-Schleifen über
  Samples in der Kette außer in Dynamik-Stufen, dort dann mit numba-freiem, aber
  vektorisiertem oder zumindest einfachem Code; Laufzeitbudget: 48 kHz Block 256 muss in
  < 1 ms auf einem M-Chip rechnen. Im Zweifel die Hüllkurve per `scipy.signal.lfilter`
  (Einpol-Glättung) rechnen, nicht per Python-Schleife.
- Stille rein → Stille raus (kein DC, kein Rauschen, keine NaN).
- Parameteränderungen zur Laufzeit dürfen nicht knacken (Koeffizienten neu berechnen,
  Zustände behalten).

## 5. Qualitätsregeln (gelten für alle Umsetzenden)

- Keine Platzhalter, keine `TODO`, keine `pass`-Rümpfe, keine `NotImplementedError`, keine
  auskommentierten Funktionen, keine "später"-Hinweise.
- Keine stummen `except:`-Blöcke. Fehler werden mit Kontext gemeldet.
- Tests prüfen Verhalten mit Zahlen, nicht nur "läuft ohne Exception".
- Jede Konstante im Code ist entweder konfigurierbar oder im Kommentar begründet.
- Dokumentation beschreibt den tatsächlichen Stand, nicht einen geplanten.
- Code-Sprache: Englisch (Bezeichner, Kommentare). Dokumentation: Deutsch.

## 6. Abnahmekriterien (für die Prüfungen)

1. `pytest -q` grün im Container ohne Audiogeräte.
2. `python -m voicechain selftest` gibt einen Bericht mit messbaren Prüfwerten aus, alle bestanden.
3. `python -m voicechain calibrate --wav <sample>` erzeugt YAML + Bericht; die Kette verbessert das
   Sample messbar: Störabstandsgewinn (Sprachpegel minus Rauschteppich, nachher minus vorher)
   ≥ 6 dB **und** Pausenlautheit nach der Kette ≤ Ziel-Lautheit − 20 LU (Mindestabstand; im
   ruhigen Raum werden ca. 30 LU erreicht), gemessen in einer Sprechpause ≥ 5 s **nach** dem
   ersten Satz (Vorlaufstille zählt nicht), Zischanteil ↓, Lautheit innerhalb Ziel ± 2 LU,
   True Peak ≤ Ceiling. Das Sample muss mindestens eine solche Pause enthalten.
   _Änderung 2026-11-03 (Blindprüfung BL-1, PO-8): ursprünglich "Rauschteppich ↓ ≥ 6 dB"
   absolut; ein absoluter Wert ist bei Lautheitsnormalisierung nicht sinnvoll, daher relativ
   plus absolute Pausenobergrenze._
4. Adaptive Regelung: in Simulation konvergiert sie innerhalb der Grenzen, oszilliert nicht,
   greift in der Lernphase nicht ein, protokolliert jede Änderung mit Grund. **Raumänderung im
   Betrieb** (Ergänzung 2026-11-03, PO Runde 2, präzisiert nach PO Runde 3):
   a) Wird der Rauschteppich nach der Kalibrierung um bis zu 8 dB lauter, liegt die Pausenlautheit
      nach der Kette mindestens 15 LU unter der Sprachlautheit oder, wenn der Abstand am Eingang
      bereits geringer ist, mindestens so weit wie am Eingang; der nächste Satz setzt nicht
      lauter als +1,5 dB ein. (Präzisierung nach Runde 5: Standard-Rosa +8 dB ergibt am Eingang
      nur 12,3 LU K-bewerteten Abstand; die Kette hält ihn, kann ihn aber ohne Sprachverlust
      nicht auf 15 LU vergrößern.)
   b) Bei größeren Anstiegen (Störabstand am Eingang unter ca. 16 dB) kann der Expander Pausen
      nicht weiter absenken, ohne Sprache abzuschneiden (Kappe Sprachpegel − 12 dB). Dann gilt:
      der Normalizer-Gain steigt in Pausen um höchstens 1 dB, der nächste Satz setzt höchstens
      +1,5 dB lauter ein, der Abstand Sprache–Pause verringert sich gegenüber dem Eingang um
      höchstens 7 LU vor und 5 LU nach der Nachführung der Expander-Schwelle (ca. 1,5 min;
      Ursache: der auf den Sprecher kalibrierte Kompressor hebt leise Anteile; gemessen
      −4,5 bis −6,9 LU bei +12 dB, rosa wie weiß), und die Regelung protokolliert ein Ereignis
      „Störabstand zu gering“ mit dem
      Hinweis, den Raum zu verbessern oder neu zu kalibrieren. Der Zustand ist in der Statuszeile
      sichtbar. Eine Pause auf Sprachlautheit (Fehlerbild PO-1) darf in keinem Fall entstehen.
5. Dokumentation ermöglicht einer technisch versierten Person ohne Audio-Vorwissen die
   Einrichtung auf dem Mac in unter 30 Minuten.
6. Die Reihenfolge der Kette entspricht dem Ausgangsbild.
