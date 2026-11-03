# Kennzahlen und Items (Zielwerte)

| Item | Zielwert | Toleranz | Quelle |
|------|----------|----------|--------|
| Samplerate | 48000 Hz | fest (Rode NT-USB) | E5 |
| Blockgröße | 256 Frames (~5,3 ms) | 128–512 | Praxis Latenz vs. Stabilität |
| Ziel-Lautheit (Short-Term) | −18 LUFS | ± 2 LU | E3 |
| True-Peak-Obergrenze | −1,0 dBTP | nie überschritten | E3 |
| Rauschteppich-Absenkung in Pausen | ≥ 6 dB | — | Beauftragung §6 |
| Sprachpegel-Referenz nach Input Trim | −20 dBFS RMS (aktive Sprache) | ± 3 dB | Praxis |
| High-Pass | 80 Hz, 24 dB/Okt | 60–120 Hz | E1, Sprache-Grundfrequenz > 85 Hz |
| Expander | Schwelle Rauschteppich + 10 dB (Entscheidung PL 2026-11-03, vorher 8 dB, siehe F3), zusätzlich ≤ Sprachpegel − 12 dB; Ratio 2.5, Range 20 dB, Hold 100 ms, Release 250 ms | Schwelle −70…−30 dBFS | E1, PO Runde 1 |
| De-Esser | 6,5 kHz, 1 Okt, max. 8 dB Reduktion; Schwelle aus Band-Spitzenpegel p95; Ruhelage Bandspitze + 2 dB ohne Zischproblem (Zischanteil ≤ −15 dB) | Schwelle −45…−10 dBFS | E1, PO Runde 2 |
| Kompressor | Schwelle = Sprach-RMS (−20 dBFS Referenz), Ratio 3:1, Knie 6 dB, 10/100 ms, Makeup aus statischer Kennlinie | Schwelle −35…−10 | E1 |
| EQ (Kalibrierung) | Shelves 120 Hz / 10 kHz nur Absenkung (High-Shelf max. −3 dB), Peaks 250 Hz / 3 kHz ± 4 dB; kein LF-Eingriff bei f0 > 150 Hz; Referenz flach bis 500 Hz, −6 dB/Okt bis 4 kHz, −10 dB/Okt darüber (Annahme) | — | PO Runde 1/2 |
| Normalizer | Ziel −18 LUFS gegatet; Gate = max(gate_lufs, Teppich + 8 LU); Deckel: Teppich + Gain ≤ Ziel − 15 LU; low_snr Eintritt 13 / Austritt 15 LU; sichere Sprache ≥ Teppich + 13 LU; Aufwärtsrate 1 dB/s, Abwärtsrate 3 dB/s; Plateau-Teppich-Fenster 10 s | gate −40…Ziel−15; max_gain 0…12 dB | E2/E3, PO Runde 2 |
| Pausenkriterium (§6.3) | Pausenlautheit nach Kette ≤ Ziel − 20 LU in Pause ≥ 5 s nach erstem Satz | Mindestwert | PO Runde 2 |
| Raumänderung im Betrieb (§6.4) | Pause ≤ Sprachlautheit − 15 LU, nächster Satz ≤ +1,5 dB | — | PO Runde 2 |
| Limiter | Ceiling −1 dBTP, Lookahead 5 ms | — | E3 |
| Adaptive Lernphase | 30 s | 10–60 s | Beauftragung §3.2 |
| Adaptive max. Schritt | 0,5 dB je Intervall (2 s), je Parameter frühestens alle 2 Intervalle | — | Beauftragung §3.2 |
| Adaptive Plausibilität | Sprachpegel nach Trim −20 ± 6 dBFS, speech_ratio ≥ 0,3, Dynamik ≥ 15 dB; sonst implausible_speech; nach 60 s rest | — | PO Runde 1 |
| Konvergenz Expander | ca. 75–90 s bei +10 dB Teppichsprung | — | Blind Runde 1 |
| Testumfang | 380 Tests grün, 1 Skip (root), Mutationen 18/18 gefangen | — | Prüfrunde 2–5 |
| Rechenzeit je Block (256 @ 48 kHz) | < 1 ms | — | Beauftragung §4.3 |

Werte sind Startwerte; die Kalibrierung überschreibt sie innerhalb der Grenzen.
