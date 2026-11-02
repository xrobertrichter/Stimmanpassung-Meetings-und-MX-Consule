"""Deterministic generator for speech-like test signals.

The signal imitates the statistical properties that the calibration and the
adaptive controller measure (speech level, noise floor, sibilance ratio, pause
structure, spectral tilt) without being speech. It is used by the unit tests, by
``python -m voicechain selftest`` and by users without a microphone who want to
rehearse the calibration workflow (:func:`write_sample_wav`).

Structure of the generated signal (all random choices come from a seeded
``numpy.random.Generator``, so the output is reproducible):

* phrases of 1.5-3 s made of "syllables" (120-250 ms): harmonic tone with a
  pitch glide, a -6 dB/oct spectral envelope above 500 Hz and three formant
  resonances, shaped by a Tukey envelope;
* optional sibilant bursts (band-limited noise 4-10 kHz, 60-120 ms) after a
  syllable, with an RMS of ``sibilance_db`` relative to the voiced RMS;
* pauses between phrases sized so that the pause share equals ``pause_ratio``;
* white noise over the whole duration at ``noise_floor_dbfs`` RMS.

``level_dbfs`` is the RMS over the speech-active samples (syllables and bursts,
including their fades) before the noise is added.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from scipy import signal

# --- Timing constants (typical values for connected speech; assumptions) ---- #
PHRASE_DURATION_S = (1.5, 3.0)      # breath groups between pauses
SYLLABLE_DURATION_S = (0.12, 0.25)  # syllable rate ~4-8 per second
SIBILANT_DURATION_S = (0.06, 0.12)  # duration of /s/, /sh/ fricatives
SIBILANT_PROBABILITY = 0.30         # share of syllables followed by a fricative
PAUSE_JITTER = (0.7, 1.3)           # random spread of pause lengths around the mean

# --- Voice model ------------------------------------------------------------ #
PITCH_RANGE_HZ = (100.0, 140.0)     # male-to-neutral fundamental; above the 80 Hz high-pass
PITCH_GLIDE = 0.10                  # +-10 % pitch movement within a syllable
HARMONIC_MAX_HZ = 5000.0            # voiced energy above this is negligible
SPECTRAL_KNEE_HZ = 500.0            # envelope flat below, -6 dB/oct above (long-term speech spectrum, assumption)
FORMANTS_HZ = (500.0, 1500.0, 2500.0)   # neutral vowel formant positions
FORMANT_BANDWIDTH_HZ = 150.0
FORMANT_BOOST = 2.0                 # +6 dB amplitude peak at each formant
SYLLABLE_AMPLITUDE_RANGE = (0.7, 1.0)   # ~3 dB syllable-to-syllable variation
TUKEY_FADE_FRACTION = 0.3           # fraction of each segment used for fade in/out
SIBILANT_BAND_HZ = (4000.0, 10000.0)
SIBILANT_FILTER_ORDER = 4


def _tukey(n: int) -> np.ndarray:
    return signal.windows.tukey(n, alpha=TUKEY_FADE_FRACTION)


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x * x))) if x.size else 0.0


def _voiced_syllable(n: int, sr: int, rng: np.random.Generator) -> np.ndarray:
    """Unit-RMS harmonic tone with pitch glide, spectral tilt and formants."""
    f0 = rng.uniform(*PITCH_RANGE_HZ)
    glide = rng.uniform(-PITCH_GLIDE, PITCH_GLIDE)
    t = np.arange(n) / sr
    # Linear pitch glide: instantaneous f0(t) = f0 * (1 + glide * t / T)
    phase_f0 = 2.0 * math.pi * f0 * (t + 0.5 * glide * t * t / (n / sr))
    n_harm = max(1, int(HARMONIC_MAX_HZ // f0))
    k = np.arange(1, n_harm + 1)
    fk = k * f0
    amp = 1.0 / np.sqrt(1.0 + (fk / SPECTRAL_KNEE_HZ) ** 2)
    for fc in FORMANTS_HZ:
        amp *= 1.0 + FORMANT_BOOST * np.exp(-((fk - fc) / FORMANT_BANDWIDTH_HZ) ** 2)
    phases = rng.uniform(0.0, 2.0 * math.pi, size=n_harm)
    tone = np.sum(amp[:, None] * np.sin(k[:, None] * phase_f0[None, :] + phases[:, None]), axis=0)
    tone *= _tukey(n)
    r = _rms(tone)
    return tone / r if r > 0 else tone


def _sibilant_burst(n: int, sr: int, rng: np.random.Generator) -> np.ndarray:
    """Unit-RMS band-limited noise burst (4-10 kHz) with fades."""
    lo, hi = SIBILANT_BAND_HZ
    hi = min(hi, 0.45 * sr)
    sos = signal.butter(SIBILANT_FILTER_ORDER, [lo, hi], btype="bandpass", fs=sr, output="sos")
    # Extra lead-in samples let the filter settle before the burst starts.
    lead = int(0.01 * sr)
    noise = signal.sosfilt(sos, rng.standard_normal(n + lead))[lead:]
    burst = noise * _tukey(n)
    r = _rms(burst)
    return burst / r if r > 0 else burst


def make_speech_like(duration_s: float, sr: int, level_dbfs: float = -26.0,
                     noise_floor_dbfs: float = -50.0, sibilance_db: float = -6.0,
                     pause_ratio: float = 0.3, seed: int = 0,
                     lead_silence_s: float = 0.0) -> np.ndarray:
    """Generate a deterministic speech-like mono signal (float32, -1..1).

    Parameters
    ----------
    duration_s : total length in seconds.
    sr : sample rate in Hz.
    level_dbfs : RMS of the speech-active part (before noise is added).
    noise_floor_dbfs : RMS of the added white noise.
    sibilance_db : RMS of sibilant bursts relative to the voiced syllables.
    pause_ratio : share of the (non-lead) duration that is pause (0..0.9).
    seed : seed of the random generator.
    lead_silence_s : noise-only stretch at the start (mirrors the recording
        instruction "10 s silence first").
    """
    if duration_s <= 0 or sr <= 0:
        raise ValueError(f"duration_s and sr must be positive, got {duration_s}, {sr}")
    if not 0.0 <= pause_ratio < 0.9:
        raise ValueError(f"pause_ratio must be in [0, 0.9), got {pause_ratio}")
    if lead_silence_s < 0 or lead_silence_s >= duration_s:
        raise ValueError(f"lead_silence_s must be in [0, duration_s), got {lead_silence_s}")
    rng = np.random.default_rng(seed)
    n_total = int(round(duration_s * sr))
    speech = np.zeros(n_total)
    active = np.zeros(n_total, dtype=bool)
    pos = int(round(lead_silence_s * sr))
    pause_per_phrase = pause_ratio / (1.0 - pause_ratio)

    while pos < n_total:
        phrase_len = int(rng.uniform(*PHRASE_DURATION_S) * sr)
        phrase_end = min(pos + phrase_len, n_total)
        while pos < phrase_end:
            n_syl = int(rng.uniform(*SYLLABLE_DURATION_S) * sr)
            n_syl = min(n_syl, n_total - pos)
            if n_syl < 2:
                pos = n_total
                break
            seg = _voiced_syllable(n_syl, sr, rng) * rng.uniform(*SYLLABLE_AMPLITUDE_RANGE)
            speech[pos:pos + n_syl] += seg
            active[pos:pos + n_syl] = True
            pos += n_syl
            if rng.uniform() < SIBILANT_PROBABILITY and pos < n_total:
                n_sib = min(int(rng.uniform(*SIBILANT_DURATION_S) * sr), n_total - pos)
                if n_sib >= 2:
                    burst = _sibilant_burst(n_sib, sr, rng) * 10.0 ** (sibilance_db / 20.0)
                    speech[pos:pos + n_sib] += burst
                    active[pos:pos + n_sib] = True
                    pos += n_sib
        pause_len = int(phrase_len * pause_per_phrase * rng.uniform(*PAUSE_JITTER))
        pos += pause_len

    if np.any(active):
        current = _rms(speech[active])
        speech *= 10.0 ** (level_dbfs / 20.0) / current
    noise = rng.standard_normal(n_total) * 10.0 ** (noise_floor_dbfs / 20.0)
    out = speech + noise
    peak = float(np.max(np.abs(out)))
    if peak > 1.0:
        raise ValueError(
            f"generated signal clips (peak {20 * math.log10(peak):.1f} dBFS); "
            f"lower level_dbfs ({level_dbfs}) or sibilance_db ({sibilance_db})")
    return out.astype(np.float32)


def write_sample_wav(path, duration_s: float = 60.0, sr: int = 48000, seed: int = 0,
                     level_dbfs: float = -26.0, noise_floor_dbfs: float = -48.0,
                     sibilance_db: float = -3.0, pause_ratio: float = 0.3,
                     lead_silence_s: float = 10.0) -> Path:
    """Write a synthetic calibration sample as 16-bit WAV and return its path.

    The defaults imitate a slightly too quiet recording with audible room noise
    and pronounced sibilance, starting with 10 s of "silence" like the recording
    instructions in docs/03 ask for.
    """
    import soundfile as sf

    x = make_speech_like(duration_s, sr, level_dbfs=level_dbfs,
                         noise_floor_dbfs=noise_floor_dbfs, sibilance_db=sibilance_db,
                         pause_ratio=pause_ratio, seed=seed, lead_silence_s=lead_silence_s)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), x, sr, subtype="PCM_16")
    return path
