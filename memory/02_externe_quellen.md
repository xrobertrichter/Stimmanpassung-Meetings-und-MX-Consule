# Externe Quellen (Log)

| Nr | Quelle | Inhalt / Verwendung | Stand der Evidenz |
|----|--------|---------------------|-------------------|
| E1 | Instagram-Post patchdstudio "On Air"-Kette (Bild vom 2026-11-02) | Reihenfolge: High-Pass → Downward Expander → De-Esser → EQ → Compressor → Limiter | Praxisempfehlung, keine Norm |
| E2 | ITU-R BS.1770 (Lautheitsmessung) | K-Bewertung (Hochpass ~38 Hz + Höhenshelf +4 dB), Momentary 400 ms, Short-Term 3 s, Integrated mit Gating (−70 LUFS absolut, −10 LU relativ), True Peak mit 4x Oversampling | Norm, stabil |
| E3 | EBU R128 | Zielwert −23 LUFS Broadcast; für Konferenz/Streaming praxisüblich −16 bis −18 LUFS, True Peak ≤ −1 dBTP | Norm/Praxis |
| E4 | Robert Bristow-Johnson "Audio EQ Cookbook" | Biquad-Koeffizienten für Low-Shelf, Peak, High-Shelf, Butterworth HPF | Standardreferenz |
| E5 | Rode NT-USB Herstellerangaben | Kondensator-Nierenmikrofon, USB, 48 kHz / 16 Bit, Kopfhörerausgang mit Direktmonitoring, Mix-Regler Mikro/Computer | Herstellerangabe (aus Trainingswissen, nicht live geprüft) |
| E6 | BlackHole (Existential Audio) | Virtueller Audiotreiber macOS, 2ch/16ch/64ch, kein Processing, Installation über Homebrew `brew install blackhole-2ch` | Herstellerangabe (aus Trainingswissen, nicht live geprüft) |
| E7 | Zoom Hilfe "Original sound for musicians" / Teams "High fidelity music mode" | Deaktiviert Rauschunterdrückung und Pegelautomatik; bei Original Sound in Zoom entfällt auch Echounterdrückung → Kopfhörer nötig | Produkthilfe (Stand Trainingswissen, UI kann abweichen) |

Hinweis: E5–E7 wurden nicht live im Netz verifiziert. Bei Abweichung der Oberfläche ist die
jeweilige Produkthilfe maßgeblich.
