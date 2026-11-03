# Prüfung 1: Product Owner (audiofachkundig) – voicechain

Stand: 2026-11-03 · Geprüft gegen `docs/00_Beauftragung.md` und `memory/04_kennzahlen_items.md` · Commit f2345fb
Prüfer hat nur gelesen, ausgeführt und simuliert; keine Datei im Repo verändert.

Ausgeführt: `pytest -q` (227 bestanden, 58 s), `python -m voicechain selftest` (9/9 bestanden),
`make-sample` + `calibrate` (Bericht `po_report.md`, alle vier Kriterien bestanden, Exit 0). Zusätzlich
eigene Simulationen mit den Projektklassen (`sim_a.py`, `sim_c.py`, `sim_d.py`, `sim_e.py`, `sim_f.py`
im selben Verzeichnis), weil der Selbsttest und das synthetische Sample nur kurze Pausen (0,6–1,3 s)
und einen ruhigen Raum (SNR 22–24 dB) abdecken, ein Webseminar aber lange Pausen und Lüfter hat.

## Kurzurteil

**Abnehmbar mit Auflagen.**

Kette, Reihenfolge, Messtechnik, Tests, Kalibrierablauf und Berichtsform sind solide und ehrlich
dokumentiert; der Code ist deutlich besser als der Durchschnitt solcher Projekte. Nicht abnehmbar in
der jetzigen Form sind drei Verhaltensfehler, die im realen Seminar hörbar Schaden anrichten und die
weder Selftest noch Kalibrierbericht aufdecken, weil deren Testsignale sie nicht provozieren:

1. Der Normalizer zieht in jeder Pause ab ~3 s Länge den Rauschteppich hoch (PO-1).
2. Die adaptive Regelung hält Tastaturklicks und leise Fremdsprache für Sprache und verstellt alle
   drei Schwellen minutenlang in die falsche Richtung (PO-2).
3. Die EQ-Kalibrierung hebt bei höheren Stimmen (typisch weiblich) einen 120-Hz-Shelf um +4 dB an,
   wo keine Sprache, sondern nur Rumpeln liegt (PO-3).

Alle drei sind lokal begrenzt und ohne Architekturänderung behebbar. Nach Behebung von PO-1 bis PO-3
und Nachweis per Test (lange Pause, Tipp-Phase, hohe Stimme) ist das Projekt aus Sicht des PO
abnehmbar; PO-4 bis PO-14 sind Auflagen zweiter Ordnung bzw. Dokumentationskorrekturen.

## Befundtabelle

| Nr | Schwere | Fundstelle | Befund | Empfehlung |
|---|---|---|---|---|
| PO-1 | hoch | `voicechain/dsp/normalizer.py:51,67-76,107`; `config/chain.default.yaml:71`; `docs/02_Parameter_Begruendung.md:212,215,229` | Das Gate des Normalizers (−40 LUFS absolut) wirkt auf die *geglättete* Lautheit (Einpol, τ = 1,5 s). Endet die Sprache, fällt diese Lautheit über ~7 s von −20 auf −40 LUFS; in dieser Zeit ist das Gate offen, das Ziel ist `−18 − L` und der Gain läuft mit 1 dB/s hoch. Danach wird der hohe Gain in der Pause eingefroren. Simulation (`sim_d.py`, kalibrierte Kette, 20-s-Pause): ruhiger Raum (Teppich −50 dBFS, SNR 24 dB) → Gain steigt von +2,2 auf +9,2 dB, Pausenrauschen am Ausgang −42,9 dBFS, also 7 dB **lauter** als am Mikrofon, obwohl der Expander 15 dB wegnimmt; Raum mit Lüfter (Teppich −42, SNR 16 dB) → Gain +12 dB (Maximum), Pause −24 dBFS / −21 LUFS, nur 3 LU unter Sprache; Teppich −38 → Pause −18,3 LUFS = Sprachlautheit. Beim nächsten Satz setzt die Stimme 5–12 dB zu laut ein und wird über Sekunden heruntergeregelt – genau das "Atmen auf Satzebene", das docs/02 ausschließt. Der Kalibrierbericht sieht das nicht, weil das Sample nur Pausen unter 1,3 s hat; der Unit-Test "Rauschen −60 dBFS bleibt unverändert" testet Rauschen ohne vorangehende Sprache. Das verletzt Beauftragung §1 (gleichmäßige Lautheit) und §6.3 im Geist. | (a) Gate auf einen schnellen Detektor legen (Momentary 400 ms) und den Gain einfrieren, sobald die momentane Lautheit unter `gate` fällt, nicht erst die Short-Term-Lautheit; (b) zusätzlich relatives Gate wie BS.1770: einfrieren, wenn Momentary < (aktuelle Sprachlautheit − 10 LU); (c) Gain nur erhöhen, solange Lautheit nicht fällt (keine Erhöhung auf einer abklingenden Hüllkurve); (d) `gate_lufs` in `calibrate.recommend` aus dem Pausenpegel nach Expander ableiten (z. B. Teppich nach Kette + 8 dB, mindestens −40) – Beauftragung §3.2 verlangt "alle Parameter"; (e) Test: 30 s Sprache, 20 s Pause, 20 s Sprache → Pausen-RMS am Ausgang ≤ Eingang − 6 dB, Gain-Änderung in der Pause ≤ 1 dB. |
| PO-2 | hoch | `voicechain/adaptive.py:395-444` (`_propose`), `:432`; `voicechain/meters.py:70` (`MIN_SPEECH_SNR_DB = 6`), `:61-70`; `docs/03_Betrieb_und_Kalibrierung.md:225-226` | Sprache gilt als erkannt, sobald 95.-Perzentil − 10.-Perzentil der 50-ms-Rahmen ≥ 6 dB ist – ohne Plausibilitätsprüfung des Pegels. Simulation (`sim_a.py`, `sim_c.py`): Tastatur mit 3 Anschlägen/s bei −30 dBFS auf −55 dBFS Rauschen → `speech_detected = True`, "Sprachpegel" −40 dBFS, "Zischanteil" +2,9 dB (Klicks sind breitbandig). Leise Fremdsprache (−42 dBFS: Kollege im Raum, Rückkanal über offene Lautsprecher, Nutzer spricht abgewandt mit jemandem) ebenso. Folge in 4 Minuten Zuhörphase (sehr häufig im Seminar: stumm geschaltet, mitschreiben): Kompressorschwelle −20 → −25,5, De-Esser −30 → −35,6 (bei Klicks Richtung Grenze −45), Expander −34 → −38; bei mehr Zeit bis an die `bounds`. Kehrt der Sprecher ans Mikro zurück, greift der De-Esser mit den vollen 8 dB auf jedem Laut (lispelnd/dumpf), der Kompressor 4–10 dB zu stark, der Expander lässt mehr Raum durch – und die Rückkehr dauert bei 0,5 dB je 4 s zwei bis vier Minuten. Beauftragung §3.2 verlangt "Verhalten wie ein Tontechniker"; ein Tontechniker regelt nicht nach Tastaturklicks. Reine Stille (S1) und Lüfter (S3) sind dagegen unkritisch (0–1 Änderungen). | Nachführen von De-Esser und Kompressor nur, wenn der gemessene Sprachpegel (nach Trim) innerhalb ±6 dB der Referenz −20 dBFS liegt **und** `speech_ratio` ≥ 0,3 **und** die Dynamik ≥ 15 dB ist; sonst Ereignis `implausible_speech` loggen und halten. Expander-Regel bei nicht plausibler Sprache nur mit der Floor-Regel (bereits so), aber `speech − 12` nur mit plausibler Sprache. Optional: Rückkehr zum kalibrierten Wert ("rest") statt weiterem Driften, wenn über > 60 s keine plausible Sprache. Tests: Tipp-Phase 3 min, Fremdsprache −42 dBFS 3 min → keine Änderung an De-Esser/Kompressor. |
| PO-3 | hoch | `voicechain/calibrate.py:95-100` (`EQ_TARGET_BANDS` low_shelf 120 Hz, Messband 80–170 Hz), `:264-268` (`eq_reference_db`), `:446-454`; `docs/03_Betrieb_und_Kalibrierung.md:102` | Die EQ-Referenzkurve "flach bis 500 Hz" unterstellt Sprachenergie im Band 80–170 Hz. Bei Stimmen mit Grundfrequenz ≥ 180 Hz (typisch weiblich, viele Kinder- und helle Männerstimmen) liegt dort keine Sprache. Simulation (`sim_e.py`, Synth mit f0 190–250 Hz): Band 80–170 Hz misst −21 dB zum Anker, "Abweichung −22,5 dB", Korrektur +4 dB (Obergrenze) auf einen **neu angelegten** Low-Shelf 120 Hz. Angehoben werden damit ausschließlich Trittschall, Lüftergrundton, Netzbrumm-Oberwellen und Plosivreste – der Hochpass bei 80 Hz wird teilweise zurückgenommen, Expander-Detektor und Kompressor sehen mehr Tiefbass (Pumpen bei Plosiven). Der Bericht begründet den Schritt wortreich, aber falsch. Dasselbe Prinzip (Referenz −6 dB/Okt bis 14 kHz) überschätzt bei echter Sprache den Hochtonanteil (LTASS fällt oberhalb 4 kHz deutlich steiler); auf dem synthetischen Sample ist das harmlos, bei realen Stimmen wird der 10-kHz-Shelf eher angehoben → Zischen und Preamp-Rauschen. | Low-Shelf 120 Hz und High-Shelf 10 kHz nur als **Cut** zulassen (Korrektur ≤ 0 dB), Boost nur für die Peak-Bänder 250 Hz / 3 kHz; Low-Shelf-Regel zusätzlich an eine gemessene Grundfrequenz koppeln (kein Boost, wenn f0 > 150 Hz; f0 ist per Autokorrelation der aktiven Rahmen billig messbar). Referenzkurve oberhalb 4 kHz auf −9…−12 dB/Okt setzen oder das 7–14-kHz-Band nur gegen Zischanteil bewerten. Test: hohe Stimme → kein LF-Boost. |
| PO-4 | mittel | `voicechain/calibrate.py:56-64,212-230` (`deesser_threshold_target`, `SIBILANCE_BURST_CREST_DB`), `voicechain/dsp/deesser.py:67-72`; `docs/03:101` | Das Modell "Zischpegel ≈ Sprach-RMS + Zischanteil + 6 dB" ist eine RMS-Größe; der De-Esser-Detektor misst den **Sample-Spitzenwert** des Bandsignals. Messung (`sim_e.py`): Band-Spitze in Zischrahmen liegt 11,8 dB über dem Modellwert. Die Regel "Schwelle so, dass ≥ 4 dB Reduktion entstehen" ist damit um ~12 dB zu konservativ; real steht der De-Esser bei jedem Zischlaut sofort am `max_reduction_db` (8 dB). Dass es im Bericht trotzdem stimmig aussieht, liegt nur daran, dass die Regel nie über den Standardwert hinaus darf und `max_reduction_db` deckelt. Die adaptive Regel (`sibilance_track`) erbt dieselbe Verschiebung. | Modell auf Spitzenwert umstellen: Band-Spitzenpegel in Zischrahmen direkt messen (`analyze`: Bandpass mit den De-Esser-Parametern, 95. Perzentil der 50-ms-Spitzen aktiver Rahmen) und Schwelle = Spitzenpegel − Overshoot. Dann kann die Schwelle auch nach oben wandern und `max_reduction_db` ist nicht mehr der eigentliche Regler. |
| PO-5 | mittel | `voicechain/adaptive.py:12-20,352-376` (Docstring "mean over an interval", `_accumulate`/`_drain`); `voicechain/runtime.py:448-466` (`_service_loop`) | Die Schutzregeln (Limiter-GR-Mittel > 3 dB, Expander-GR bei Sprache > 6 dB) sind als Intervallmittel beschrieben und getestet (`test_limiter_mean_is_averaged_over_calls_within_an_interval`), aber die Runtime ruft `update()` genau einmal je 2 s auf. Die Meter der Kette sind Blockmittel (5,3 ms). Effektiv entscheidet ein 5-ms-Schnappschuss alle 2 s: ein Lacher von 1,5 s wird mit 25 % Wahrscheinlichkeit nicht gesehen, ein einzelner Plosiv-Block kann `limiter_protect` auslösen. Auf dem Synth-Signal trat beides nicht auf (`sim_a.py`: P(Exp-GR > 6 | Sprache) = 0, Limiter-GR 0), bei echter Sprache mit harten Konsonanten ist die Rate anders. | Im Audio-Callback je Block Max/Mittel der GR-Meter akkumulieren (wie `_in_peak`) und beim Tick an den Controller geben, oder `_accumulate` aus dem Service-Thread alle 100–200 ms aufrufen (billig: nur `get_meters()`). Docstring und Test an das tatsächliche Verhalten angleichen. |
| PO-6 | mittel | `docs/03_Betrieb_und_Kalibrierung.md:46-47`, `README.md:86-87` | "QuickTime Player … danach als WAV exportieren" ist falsch: QuickTime exportiert Audioaufnahmen nur als `.m4a` (AAC). Der genannte `ffmpeg`-Weg setzt voraus, dass ffmpeg installiert ist (nicht Teil von macOS, nicht in `requirements.txt`, nicht im Setup). Für die Zielperson ohne Audio-Vorwissen ist das ein Stopper im Schritt 7 des Schnellstarts – Beauftragung §6.5. Außerdem verändert AAC den Rauschteppich und das Band > 16 kHz, was die Messung verfälscht. | Primär den eigenen Aufnahmeweg anbieten: CLI-Kommando `record --seconds 60 --out sample.wav` (gleicher Gerätepfad wie `run`, 48 kHz, PCM), QuickTime nur als Ausweichweg mit dem macOS-Bordmittel `afconvert -f WAVE -d LEI16@48000 aufnahme.m4a sample.wav` (kein ffmpeg nötig). Hinweis, dass verlustbehaftete Zwischenformate zu vermeiden sind. |
| PO-7 | mittel | `voicechain/calibrate.py:497-505` (Normalizer/Limiter "bleiben"), `docs/03:105-107`, Beauftragung §3.2 | `gate_lufs`, `max_gain_db`, `window_s`, `rate_db_per_s` werden nicht aus der Messung abgeleitet, obwohl der Pausenpegel nach Kette gemessen wird und das Gate davon abhängen muss (siehe PO-1). Beauftragung verlangt "**alle** Parameter (mit Grenzen)". Für HPF, Zeitkonstanten und Ratios ist "bleibt, Begründung in docs/02" akzeptabel; für das Gate nicht. | Siehe PO-1 (d). Zusätzlich `max_gain_db` begrenzen auf `−18 − (Pausenlautheit nach Kette + 6)`, damit der Normalizer rechnerisch nie Rauschen auf Sprachlautheit heben kann. |
| PO-8 | mittel | `voicechain/calibrate.py:537-565` (`evaluate`, Kriterium `noise_floor_reduction`); `docs/03:283-285` | Bei kleinem Störabstand (Sample SNR 16 dB, `sim_f.py`) fällt das Rauschkriterium korrekt durch (+0,4 dB), aber der Bericht sagt nicht, dass die Kette den absoluten Pausenpegel um 9 dB **angehoben** hat (−42 → −33 dBFS, in längeren Pausen noch mehr, PO-1) und damit schlechter ist als das rohe Mikrofon. docs/03 §11 formuliert "die Kette macht das hörbar besser, aber das Kriterium fällt durch" – das Gegenteil ist der Fall. | Zweites, absolutes Kriterium "Pausenpegel nach Kette ≤ Pausenpegel vorher" (bzw. ≤ −45 dBFS) ergänzen; bei Verfehlung klare Handlungsanweisung im Bericht ("Kette in diesem Raum nicht einsetzen / Normalizer-Gain begrenzen / Lüfter weg"). docs/03 §11 korrigieren. |
| PO-9 | niedrig | `config/chain.default.yaml:30,86`; `docs/02:85` vs. `memory/04:12`, `calibrate.py:46`; `voicechain/calibrate.py:44`, `docs/03:99` | Zwei Inkonsistenzen: (1) YAML-Kommentare und docs/02 nennen weiterhin "+8 dB" Expander-Abstand, Entscheidung PL vom 2026-11-03 ist 10 dB (memory/04, calibrate.py). (2) calibrate.py:44 und docs/03:99 behaupten "(10 − 3) · 1,5 = 10,5 dB Absenkung am Teppich"; die Kennlinie (`expander_gain_db`, Giannoulis) liefert bei 10 dB unter Schwelle 15 dB (geprüft: −15,0 dB); docs/02:98 sagt richtig 15 dB. | Kommentare und docs/02/03 auf 10 dB bzw. 15 dB statisch korrigieren; Beauftragung §5 ("Doku beschreibt tatsächlichen Stand"). |
| PO-10 | niedrig | `docs/01_macOS_Setup.md:46-48` | "Systemerweiterung bzw. Treiber erlauben" trifft auf BlackHole nicht zu: BlackHole ist ein CoreAudio-HAL-Plug-in (`/Library/Audio/Plug-Ins/HAL`), keine System-/Kernel-Extension; ein Freigabedialog unter Datenschutz & Sicherheit erscheint nicht. Was tatsächlich kommt: `brew install --cask blackhole-2ch` fragt nach dem **Administratorpasswort** (pkg-Installer) – das steht nirgends und irritiert die Zielperson. | Absatz ersetzen: "Homebrew fragt nach dem Mac-Passwort (Installationspaket). Ein Sicherheitsdialog ist nicht nötig." Abmelden/Anmelden-Hinweis kann bleiben. |
| PO-11 | niedrig | `docs/01_macOS_Setup.md:176` | "Mikrofonlautstärke auf ca. 75–100 %" – der Zoom-Mikrofonregler ist ein Pegelsteller vor dem Codec; bei 75 % kommt das auf −18 LUFS normalisierte Signal ca. 2–3 dB leiser an, die Normalisierung läuft damit ins Leere. | "auf 100 %" (Normalisierung macht voicechain); gleiches bei Teams, falls ein Regler sichtbar ist. |
| PO-12 | niedrig | `README.md:4-5` | "sieben Stufen (… Compressor mit Loudness-Normalizer …)" – es sind acht Stufen, Normalizer ist eine eigene Stufe 5b laut Beauftragung §2 und `STAGE_ORDER`. | "acht Stufen" und Normalizer getrennt nennen. |
| PO-13 | niedrig | `voicechain/cli.py:243-244`, `docs/03:266-274` | "Freeze" existiert nur als Startoption `--freeze`; im laufenden Seminar lässt sich die Regelung nicht einfrieren oder wieder freigeben (z. B. vor einer Präsentation, nach einer Tipp-Phase). Beauftragung §3.2: "Freeze muss möglich sein" ist formal erfüllt, praktisch nur halb. | Tastatur-Toggle im Service-Thread (z. B. `f` + Enter auf stdin) oder Signal (`SIGUSR1`) für Freeze an/aus; Status in der Statuszeile anzeigen. |
| PO-14 | niedrig | `docs/02:207`, `docs/03:296-297`, `voicechain/runtime.py:355-356` | Lautheitsziel −18 LUFS wird intern mono gemessen; das Mono-Signal wird auf zwei Kanäle dupliziert. Eine Stereo-Messung des BlackHole-Signals (z. B. Aufzeichnung des Meetings) ergibt nach BS.1770 +3 LU, also −15 LUFS. Für Zoom/Teams (Mono-Sprachpfad) ohne Belang, aber die Dokumentation sollte die Konvention nennen, sonst "stimmt" ein Nachmessen mit einem Stereo-Meter nicht. | Dual-Mono beibehalten (Zoom/Teams erwarten das), aber in docs/02 §7 und docs/03 §11 einen Satz ergänzen: "Ziel gilt für die Mono-Messung; ein Stereo-Meter auf dem BlackHole-Signal zeigt +3 LU." |

Positiv und ohne Befund (damit die Auflagen nicht den Blick verstellen):

- **Reihenfolge (Beauftragung §6.6):** `chain.py:20` STAGE_ORDER = input_trim → highpass → expander → deesser → eq → compressor → normalizer → limiter, exakt wie §2. Normalizer vor Limiter ist richtig (Limiter muss das letzte Wort haben), nach Kompressor ist richtig begründet (sonst arbeitet er gegen die Schwellen-Nachführung).
- **Messtechnik:** K-Bewertung reproduziert die 48-kHz-Tabelle, Gating nach BS.1770 (−70 absolut, −10 relativ), True Peak 4× oversampled mit Sample-Peak als Untergrenze. Sauber.
- **Limiter:** Beweis der Ceiling-Garantie ist korrekt; True-Peak-Marge 1,5 dB statt Oversampling ist für Sprache ein vertretbarer, ehrlich dokumentierter Kompromiss (gemessen −1,97 / −2,48 dBTP).
- **Dynamikstufen:** Giannoulis-Struktur, log-domain Smoothing, vektorisiert, blockgrößenunabhängig; Expander mit RMS-Detektor passend zur Teppichmessung; Hold nur bei voll offenem Gate – das ist durchdacht.
- **Kalibrierbericht:** Jede Ableitung mit Zwischenwerten nachrechenbar, Annahmen als solche markiert. Für die Zielperson sind "Crest-Faktor", "10. Perzentil", "K-Bewertung", "LUFS" nicht erklärt – ein Glossar-Absatz (memory/05 existiert bereits) am Berichtsende würde reichen.
- **Rechenzeit:** 0,43 ms je 256er-Block im Container; Budget < 1 ms eingehalten.
- **Dokumentation Zoom/Teams/BlackHole/Aggregate/Mikrofonrechte:** Inhaltlich korrekt (Original Sound mit Unteroptionen, In-Meeting-Schalter, Rauschunterdrückung "Niedrig" weil es kein "Aus" gibt, Teams-Musikmodus, Terminal als Berechtigungsträger, Stille statt Fehler ohne Berechtigung, Aggregate mit Taktquelle = Rode und Drift-Korrektur auf BlackHole, Warnung vor BlackHole als Systemausgabe). Versionsabhängigkeit ist oben ehrlich gekennzeichnet. Die 30-Minuten-Vorgabe ist mit Homebrew und Python 3.11+ realistisch – wenn PO-6 behoben ist.

## Antworten auf die expliziten Fragen

### 1. Rauschkriterium relativ (Störabstandsgewinn) statt absolut – vertretbar?

**Ja, vertretbar und fachlich sogar die richtigere Formulierung – aber nur zusammen mit einer
absoluten Schranke.** Begründung: Die Kette ändert absichtlich den absoluten Pegel (Trim + Normalizer
auf −18 LUFS). Was die Gegenseite stört, ist der Pausenpegel *im Verhältnis* zum Sprachpegel, den sie
gewohnt ist; da der Sprachpegel nach der Kette fest ist, ist "Störabstandsgewinn ≥ 6 dB" gleichbedeutend
mit "Pausenpegel liegt 6 dB tiefer unter −18 LUFS als vorher unter dem Rohpegel". Ein absoluter Vergleich
würde eine leise Aufnahme, die um 9 dB angehoben wird, als Verschlechterung werten, obwohl sie besser
ist. Die Umdeutung ist im Fehlerlog (F3) und im Bericht transparent gemacht; die absoluten Werte stehen
daneben. Das genügt mir.

Zwei Auflagen: (a) Die Beauftragung §6.3 muss auf die relative Lesart angepasst werden (Status
"verbindlich"; derzeit widerspricht der Text der Umsetzung). (b) Ein absolutes Zweitkriterium ist
nötig, weil der Störabstandsgewinn allein den Fall PO-1/PO-8 nicht erkennt: In Räumen mit SNR ≤ 16 dB
hebt die Kette den Pausenpegel absolut um 9 dB und mehr an; der relative Wert fällt dann zwar
durch, aber der Bericht sagt der Zielperson nicht, dass die Kette den Ton *verschlechtert*. Vorschlag:
"Pausenpegel nach Kette ≤ Pausenpegel vorher" als Pflicht und "≤ −45 dBFS" als Hinweis. Außerdem
muss die Messung "nachher" Pausen ≥ 5 s enthalten (Sample-Vorgabe in docs/03 §2 entsprechend
ergänzen: "10 s Stille am Anfang **und** zwei Pausen von je 5 s in der Mitte"), sonst misst das
Kriterium den Normalizer nie in dem Zustand, den er im Seminar hat.

### 2. Normalisierungskonzept "für die anderen Teilnehmenden"

**Konzeptionell richtig gelöst, in der Implementierung des dynamischen Teils an einer Stelle falsch.**
Richtig: zweistufig (statischer Trim aus der Kalibrierung = Tagesform/Abstand grob, langsamer AGC
= Drift), AGC nach dem Kompressor und vor dem Limiter, feedforward (kann nicht schwingen), 1 dB/s wie
eine Hand am Fader, ±12 dB Fenster, Ziel −18 LUFS / −1 dBTP als sinnvoller Konferenz-Praxiswert
(keine Norm, das sagt die Doku auch). Die Kopplung "Makeup stellt Ausgang ≈ Eingang her, Rest macht
der Normalizer" verhindert, dass zwei Regler gegeneinander arbeiten – gut.

Falsch: das Gate (PO-1). Ein AGC für Sprache braucht zwingend eine Pausenerkennung, die *schneller*
ist als die Pegelmessung, und darf auf einer abklingenden Hüllkurve den Gain nicht erhöhen. So wie es
jetzt ist, bekommt die Gegenseite nach jeder Frage-Pause einen 5–10 dB zu lauten Satzanfang und ein
Rauschen, das in der Pause hochfährt – das ist das Gegenteil von "gleichmäßiger Pegel für die anderen".
Zwei weitere Punkte zweiter Ordnung: Die Rate 1 dB/s symmetrisch ist für "zu laut" zu langsam (ein
Sprecher, der plötzlich 8 dB lauter ist, hängt 8 s im Limiter); üblich ist asymmetrisch (runter 3 dB/s,
hoch 1 dB/s). Und das Gate sollte relativ zur aktuellen Sprachlautheit liegen (−10…−15 LU) statt
absolut bei −40 LUFS, dann funktioniert es in lauten und leisen Räumen gleichermaßen.

### 3. Startwerte – wo ich anders entscheiden würde

Die Startwerte sind insgesamt plausibel für NT-USB, 15–25 cm, Sprache; die Begründungen in docs/02
sind nachvollziehbar und messtechnisch belegt. Konkret anders:

| Parameter | Jetzt | Meine Wahl | Warum |
|---|---|---|---|
| `eq` High-Shelf 10 kHz | +1,5 dB | 0 dB (Kalibrierung nur Cut) | Das NT-USB hat bereits eine Präsenz-/Höhenbetonung; +1,5 dB oberhalb 10 kHz hebt vor allem Preamp-Rauschen und den Rest der Zischlaute an, den der De-Esser (4,6–9,2 kHz) nicht mehr sieht. Zoom/Teams übertragen Sprache codecbedingt ohnehin mit Betonung der Höhen; "Luft" ist ein Hi-Fi-Argument, kein Konferenz-Argument. |
| `eq` Low-Shelf 120 Hz (von der Kalibrierung angelegt) | Boost bis +4 | nur Cut | PO-3. |
| `deesser.max_reduction_db` | 8 | 6 | 8 dB Dauer-Deckel auf einem immer laufenden Sprach-De-Esser ist am Rand zum Lispeln; mit PO-4 (Schwelle auf Spitzenpegel) kann der Wert wieder 8 sein, weil dann nicht jeder Laut den Deckel trifft. |
| `expander.hold_ms` / `release_ms` | 50 / 150 | 100 / 250 | Bei Ratio 2,5 und Knie 6 ist das Gate weich genug; aber 50 ms Hold überbrückt nur Silbenpausen, nicht die 80–150 ms zwischen Wörtern. Ich höre lieber 100 ms mehr Raum als "abgebissene" Wortenden bei langsamen Sprechern. Nachrangig, Hörvergleich entscheidet. |
| `normalizer.gate_lufs` | −40 absolut | relativ (Sprachlautheit − 12 LU), Momentary-Detektor; kalibriert | PO-1. |
| `normalizer.rate_db_per_s` | 1,0 symmetrisch | 1,0 hoch / 3,0 runter | siehe oben. |
| `highpass.freq_hz` | 80 | 80 (ok), für hohe Stimmen 100 | Nicht ändern; nur als Kalibrier-Option, wenn f0 gemessen wird (PO-3). |
| `compressor` | −20 / 3:1 / 6 / 10 / 100 ms | unverändert | Richtig. Schwelle auf Sprach-RMS bei Spitzenwertdetektor ist ein kluger Kompromiss, die Begründung (Kompression frisst Störabstand) stimmt. |
| `limiter` | −1 dBTP / 5 ms / 50 ms, Marge 1,5 | unverändert | Richtig. |
| `adaptive` | 30 s / 2 s / 0,5 dB | unverändert, plus Plausibilitätsregel | PO-2. Die Schrittweite ist bewusst langsam; dass eine Lüfteränderung ~2 min braucht, ist akzeptabel ("vorsichtiger Tontechniker"). |
| `expander.threshold_db` Startwert | −45 (Kommentar "+8") | −45 (Kommentar "+10", Teppich −55) | Nur Kommentar; PO-9. |

### 4. Adaptive Regelung – Verhalten in realen Situationen (Simulation `sim_c.py`)

| Situation | Verhalten | Bewertung |
|---|---|---|
| Lange Stille (4 min, nur Raumrauschen) | 1 Änderung, Expander folgt dem Teppich, De-Esser/Kompressor bleiben | gut |
| Lüfter geht an (Teppich −50 → −38 dBFS, 3 min) | Expander-Ziel durch `Sprache − 12` begrenzt, 1 Schritt; Lüfter liegt über der Schwelle, Expander öffnet in Pausen, Normalizer hebt die Pause auf Sprachlautheit (PO-1) | Regelung ok, Normalizer schädlich |
| Nutzer rückt näher (+8 dB) | Kompressorschwelle in 13 Schritten auf −13,5; Expander/De-Esser unverändert; Normalizer gleicht den Pegel aus | gut |
| Nutzer rückt weg (−8 dB) | Alle drei Schwellen folgen nach unten (36 Schritte), Expander auf `Sprache − 12`; Störabstand sinkt gewollt zugunsten der Sprache | gut, so soll es sein |
| Zuhörphase mit Tippen (3 Anschläge/s) | Klicks gelten als Sprache mit "Zischanteil +2,9 dB": De-Esser-Schwelle wandert Richtung −45, Kompressor Richtung −35; nach Rückkehr 2–4 min lispelnd und überkomprimiert | **Schaden** (PO-2) |
| Kollege im Raum / Rückkanal über Lautsprecher (−42 dBFS) | wie oben: alle Schwellen folgen der Fremdsprache | **Schaden** (PO-2) |
| Lachen / Rufen (1–2 s) | Limiter fängt es; `limiter_protect` nur, wenn der 5-ms-Schnappschuss den Lacher trifft (PO-5); Normalizer senkt mit 1 dB/s zu langsam, Limiter arbeitet derweil | akzeptabel, Protect unzuverlässig |
| Sprecherwechsel am selben Mikro (anderer Pegel) | Normalizer ±6 dB in 6 s, Kompressorschwelle folgt in Minuten; De-Esser-Frequenz wird nicht nachgeführt (nur Schwelle) | akzeptabel |
| Bildschirmteilung mit Systemton | Kein Einfluss auf den Mikrofonpfad, solange die macOS-Standardausgabe nicht BlackHole ist (docs/01 warnt korrekt); mit Kopfhörer am Rode kein Übersprechen | gut, dokumentiert |
| Lernphase / Hysterese / Schrittweite / Grenzen / Protokoll | Lernphase 30 s ohne Eingriff, Totzonen 1,5–2 dB, 0,5 dB je 4 s, Grenzen nie verletzt, jede Änderung mit Grund im JSONL (Tests belegen das) | gut |

Fazit zur Regelung: Der Regler selbst ist vorsichtig und sauber gebaut; sein Schwachpunkt ist die
Messgrundlage (Sprachdetektion ohne Pegel-Plausibilität), nicht die Regelgesetze.

### 5. Bericht aus Schritt 6 (`po_report.md`)

Fachlich korrekt für das Sample: Trim +6 dB, Expander −32,1 (Teppich-Regel), De-Esser 6748 Hz /
−30,1, Kompressor −20 / Makeup +6,5, Störabstandsgewinn +9,9 dB, Zischanteil −2,8 dB, −17,1 LUFS,
−2,48 dBTP, Rechenpfad je Zeile nachvollziehbar. Für die Zielperson verständlich mit zwei Einschränkungen:
Fachbegriffe ohne Erklärung (siehe oben) und die EQ-Zeilen, die eine Annahme auf eine Annahme stapeln
("Referenzkurve erwartet +1,5 dB … Abweichung −2,7 dB … 50 % … neues Band") – das liest sich wie Beleg,
ist aber die Stelle, an der PO-3 entsteht. Hinweis "Annahme" ist korrekt gesetzt.
