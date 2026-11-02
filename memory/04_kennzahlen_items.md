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
| Expander | Schwelle Rauschteppich + 8 dB, Ratio 2.5, Range 20 dB | Schwelle −70…−30 dBFS | E1 |
| De-Esser | 6,5 kHz, 1 Okt, max. 8 dB Reduktion | 5–9 kHz | E1 |
| Kompressor | Schwelle −20 dBFS, Ratio 3:1, Knie 6 dB, 10/100 ms | Schwelle −35…−10 | E1 |
| Limiter | Ceiling −1 dBTP, Lookahead 5 ms | — | E3 |
| Adaptive Lernphase | 30 s | 10–60 s | Beauftragung §3.2 |
| Adaptive max. Schritt | 0,5 dB je Intervall (2 s) | — | Beauftragung §3.2 |
| Rechenzeit je Block (256 @ 48 kHz) | < 1 ms | — | Beauftragung §4.3 |

Werte sind Startwerte; die Kalibrierung überschreibt sie innerhalb der Grenzen.
