"""Deterministic generators for test signals.

``make_speech_like`` imitates the statistical properties that the calibration
and the adaptive controller measure (speech level, noise floor, sibilance ratio,
pause structure, spectral tilt, fundamental frequency) without being speech. It
is used by the unit tests, by ``python -m voicechain selftest`` and by users
without a microphone who want to rehearse the calibration workflow
(:func:`write_sample_wav`). ``make_keyboard_clicks`` imitates typing during a
listening phase (test signal for the plausibility rule of the adaptive controller).

Structure of the speech-like signal (all random choices come from a seeded
``numpy.random.Generator``, so the output is reproducible):

* phrases of 1.5-3 s made of "syllables" (120-250 ms): harmonic tone with a
  pitch glide, a -6 dB/oct spectral envelope above 500 Hz and three formant
  resonances, shaped by a Tukey envelope;
* optional sibilant bursts (band-limited noise 4-10 kHz, 60-120 ms) after a
  syllable, with an RMS of ``sibilance_db`` relative to the voiced syllables;
* pauses between phrases sized so that the pause share equals ``pause_ratio``;
* optionally one long pause (``long_pause_s`` at ``long_pause_at_s``) like the
  recording instructions in docs/03 ask for;
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
HARMONIC_MAX_HZ = 8000.0            # voiced harmonics reach into the top octave of the speech band
SPECTRAL_KNEE_HZ = 500.0            # envelope flat below, -6 dB/oct above (long-term speech spectrum, assumption)
# Above this knee the voiced envelope falls at -12 dB/oct in total: real voices
# keep weak harmonics and aspiration noise up to ~8 kHz, which is what a
# microphone's top-octave response (high shelf) acts on. Assumption.
SPECTRAL_KNEE_HIGH_HZ = 4000.0
FORMANTS_HZ = (500.0, 1500.0, 2500.0)   # neutral vowel formant positions
FORMANT_BANDWIDTH_HZ = 150.0
FORMANT_BOOST = 2.0                 # +6 dB amplitude peak at each formant
SYLLABLE_AMPLITUDE_RANGE = (0.7, 1.0)   # ~3 dB syllable-to-syllable variation
TUKEY_FADE_FRACTION = 0.3           # fraction of each segment used for fade in/out
SIBILANT_BAND_HZ = (4000.0, 10000.0)
SIBILANT_FILTER_ORDER = 4

# --- Keyboard model ---------------------------------------------------------- #
CLICK_DURATION_S = (0.004, 0.010)   # a key stroke is a 4-10 ms broadband transient (assumption)
CLICK_DECAY_FRACTION = 0.3          # exponential decay reaches 1/e after 30 % of the click
CLICK_TIMING_JITTER = 0.4           # +-40 % jitter of the inter-click interval


def _tukey(n: int) -> np.ndarray:
    return signal.windows.tukey(n, alpha=TUKEY_FADE_FRACTION)


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x * x))) if x.size else 0.0


def _voiced_syllable(n: int, sr: int, rng: np.random.Generator,
                     pitch_range_hz: tuple[float, float]) -> np.ndarray:
    """Unit-RMS harmonic tone with pitch glide, spectral tilt and formants."""
    f0 = rng.uniform(*pitch_range_hz)
    glide = rng.uniform(-PITCH_GLIDE, PITCH_GLIDE)
    t = np.arange(n) / sr
    # Linear pitch glide: instantaneous f0(t) = f0 * (1 + glide * t / T)
    phase_f0 = 2.0 * math.pi * f0 * (t + 0.5 * glide * t * t / (n / sr))
    n_harm = max(1, int(HARMONIC_MAX_HZ // f0))
    k = np.arange(1, n_harm + 1)
    fk = k * f0
    amp = 1.0 / np.sqrt(1.0 + (fk / SPECTRAL_KNEE_HZ) ** 2)
    amp /= np.sqrt(1.0 + (fk / SPECTRAL_KNEE_HIGH_HZ) ** 2)   # extra -6 dB/oct above 4 kHz
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
                     lead_silence_s: float = 0.0,
                     long_pause_s: float = 0.0, long_pause_at_s: float | None = None,
                     pitch_range_hz: tuple[float, float] = PITCH_RANGE_HZ) -> np.ndarray:
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
    long_pause_s : length of one additional long pause (0 = none).
    long_pause_at_s : start of that pause; None = middle of the part after the
        lead silence. The running phrase is ended there (no syllable is cut).
    pitch_range_hz : range of the syllable fundamental frequency.
    """
    if duration_s <= 0 or sr <= 0:
        raise ValueError(f"duration_s and sr must be positive, got {duration_s}, {sr}")
    if not 0.0 <= pause_ratio < 0.9:
        raise ValueError(f"pause_ratio must be in [0, 0.9), got {pause_ratio}")
    if lead_silence_s < 0 or lead_silence_s >= duration_s:
        raise ValueError(f"lead_silence_s must be in [0, duration_s), got {lead_silence_s}")
    if long_pause_s < 0:
        raise ValueError(f"long_pause_s must be >= 0, got {long_pause_s}")
    if pitch_range_hz[0] <= 0 or pitch_range_hz[1] < pitch_range_hz[0]:
        raise ValueError(f"pitch_range_hz must be positive and ordered, got {pitch_range_hz}")
    if long_pause_at_s is None:
        long_pause_at_s = lead_silence_s + 0.5 * (duration_s - lead_silence_s - long_pause_s)
    if long_pause_s > 0 and not (lead_silence_s <= long_pause_at_s <= duration_s - long_pause_s):
        raise ValueError(
            f"long pause {long_pause_at_s}+{long_pause_s} s does not fit between lead silence and end")
    rng = np.random.default_rng(seed)
    n_total = int(round(duration_s * sr))
    speech = np.zeros(n_total)
    active = np.zeros(n_total, dtype=bool)
    pos = int(round(lead_silence_s * sr))
    pause_per_phrase = pause_ratio / (1.0 - pause_ratio)
    long_pause_start = int(round(long_pause_at_s * sr)) if long_pause_s > 0 else None
    long_pause_len = int(round(long_pause_s * sr))

    while pos < n_total:
        phrase_len = int(rng.uniform(*PHRASE_DURATION_S) * sr)
        phrase_end = min(pos + phrase_len, n_total)
        hit_long_pause = long_pause_start is not None and phrase_end > long_pause_start
        if hit_long_pause:
            phrase_end = long_pause_start
        while pos < phrase_end:
            n_syl = min(int(rng.uniform(*SYLLABLE_DURATION_S) * sr), phrase_end - pos)
            if n_syl < 2:
                pos = phrase_end
                break
            seg = _voiced_syllable(n_syl, sr, rng, pitch_range_hz) * rng.uniform(*SYLLABLE_AMPLITUDE_RANGE)
            speech[pos:pos + n_syl] += seg
            active[pos:pos + n_syl] = True
            pos += n_syl
            if rng.uniform() < SIBILANT_PROBABILITY and pos < phrase_end:
                n_sib = min(int(rng.uniform(*SIBILANT_DURATION_S) * sr), phrase_end - pos)
                if n_sib >= 2:
                    burst = _sibilant_burst(n_sib, sr, rng) * 10.0 ** (sibilance_db / 20.0)
                    speech[pos:pos + n_sib] += burst
                    active[pos:pos + n_sib] = True
                    pos += n_sib
        if hit_long_pause:
            pos = long_pause_start + long_pause_len
            long_pause_start = None
        else:
            natural_pause = int(phrase_len * pause_per_phrase * rng.uniform(*PAUSE_JITTER))
            # A natural pause must not merge with the long pause: speech runs
            # right up to its start so the long pause has the requested length.
            if long_pause_start is None or pos + natural_pause < long_pause_start:
                pos += natural_pause

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


def make_keyboard_clicks(duration_s: float, sr: int, rate_per_s: float = 3.0,
                         level_dbfs: float = -30.0, noise_floor_dbfs: float = -55.0,
                         seed: int = 0) -> np.ndarray:
    """Typing noise: short broadband transients at ``rate_per_s`` on white noise.

    ``level_dbfs`` is the RMS of each click over its own duration (4-10 ms), so
    a click reads about 10 dB lower in a 50 ms analysis frame. Deterministic.
    """
    if duration_s <= 0 or sr <= 0 or rate_per_s <= 0:
        raise ValueError(f"duration_s, sr and rate_per_s must be positive, got {duration_s}, {sr}, {rate_per_s}")
    rng = np.random.default_rng(seed)
    n_total = int(round(duration_s * sr))
    out = rng.standard_normal(n_total) * 10.0 ** (noise_floor_dbfs / 20.0)
    amp = 10.0 ** (level_dbfs / 20.0)
    t = 0.0
    while True:
        t += rng.uniform(1.0 - CLICK_TIMING_JITTER, 1.0 + CLICK_TIMING_JITTER) / rate_per_s
        start = int(t * sr)
        n = int(rng.uniform(*CLICK_DURATION_S) * sr)
        if start + n >= n_total:
            break
        envelope = np.exp(-np.arange(n) / (CLICK_DECAY_FRACTION * n))
        click = rng.standard_normal(n) * envelope
        out[start:start + n] += click / _rms(click) * amp
    peak = float(np.max(np.abs(out)))
    if peak > 1.0:
        raise ValueError(f"generated clicks clip (peak {20 * math.log10(peak):.1f} dBFS); lower level_dbfs")
    return out.astype(np.float32)


def write_sample_wav(path, duration_s: float = 60.0, sr: int = 48000, seed: int = 0,
                     level_dbfs: float = -26.0, noise_floor_dbfs: float = -48.0,
                     sibilance_db: float = -3.0, pause_ratio: float = 0.3,
                     lead_silence_s: float = 10.0, long_pause_s: float = 6.0) -> Path:
    """Write a synthetic calibration sample as 16-bit WAV and return its path.

    The defaults imitate a slightly too quiet recording with audible room noise
    and pronounced sibilance, starting with 10 s of "silence" and containing a
    6 s pause in the middle, like the recording instructions in docs/03 ask for.
    """
    import soundfile as sf

    x = make_speech_like(duration_s, sr, level_dbfs=level_dbfs,
                         noise_floor_dbfs=noise_floor_dbfs, sibilance_db=sibilance_db,
                         pause_ratio=pause_ratio, seed=seed, lead_silence_s=lead_silence_s,
                         long_pause_s=long_pause_s)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), x, sr, subtype="PCM_16")
    return path
