"""Shared fixtures: deterministic signal generators and block-processing helpers.

Level conventions used throughout the tests:
* ``peak_dbfs`` for sines (AES17: a full-scale sine has peak 1.0 = 0 dBFS),
* ``rms_dbfs`` for noise-like signals.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy import signal

FS = 48000


def db_to_amp(db: float) -> float:
    return 10.0 ** (db / 20.0)


def rms_db(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=np.float64)
    return 10.0 * np.log10(max(np.mean(x * x), 1e-30))


def sine(freq_hz: float, peak_dbfs: float, seconds: float, fs: int = FS, phase: float = 0.0) -> np.ndarray:
    n = int(round(seconds * fs))
    t = np.arange(n) / fs
    return db_to_amp(peak_dbfs) * np.sin(2.0 * np.pi * freq_hz * t + phase)


def white_noise(rms_dbfs: float, seconds: float, fs: int = FS, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(int(round(seconds * fs)))
    return x / np.sqrt(np.mean(x * x)) * db_to_amp(rms_dbfs)


def pink_noise(rms_dbfs: float, seconds: float, fs: int = FS, seed: int = 0) -> np.ndarray:
    """White noise shaped by 1/sqrt(f) in the frequency domain (-3 dB/oct)."""
    rng = np.random.default_rng(seed)
    n = int(round(seconds * fs))
    spectrum = np.fft.rfft(rng.standard_normal(n))
    freqs = np.fft.rfftfreq(n, 1.0 / fs)
    weights = np.zeros_like(freqs)
    weights[1:] = 1.0 / np.sqrt(freqs[1:])     # DC bin stays zero
    x = np.fft.irfft(spectrum * weights, n)
    return x / np.sqrt(np.mean(x * x)) * db_to_amp(rms_dbfs)


def bandlimited_noise(lo_hz: float, hi_hz: float, rms_dbfs: float, seconds: float,
                      fs: int = FS, seed: int = 0) -> np.ndarray:
    """Noise restricted to [lo_hz, hi_hz] by a brick-wall in the frequency domain."""
    rng = np.random.default_rng(seed)
    n = int(round(seconds * fs))
    spectrum = np.fft.rfft(rng.standard_normal(n))
    freqs = np.fft.rfftfreq(n, 1.0 / fs)
    spectrum[(freqs < lo_hz) | (freqs > hi_hz)] = 0.0
    x = np.fft.irfft(spectrum, n)
    return x / np.sqrt(np.mean(x * x)) * db_to_amp(rms_dbfs)


def speech_like(rms_dbfs: float, seconds: float, fs: int = FS, seed: int = 0,
                noise_rms_dbfs: float = -55.0, talk_s: float = 0.6, pause_s: float = 0.4,
                f0_hz: float = 120.0, max_harmonic_hz: float = 3000.0,
                syllable_hz: float = 4.0) -> tuple[np.ndarray, np.ndarray]:
    """Synthetic "speech": harmonic multi-tone 120..3000 Hz, syllable AM (0.5..1), pauses, noise floor.

    Returns ``(signal, active_mask)``; the active parts have exactly ``rms_dbfs`` RMS
    (before the noise floor is added), the noise floor has ``noise_rms_dbfs`` RMS.
    """
    n = int(round(seconds * fs))
    t = np.arange(n) / fs
    harmonics = np.arange(1, int(max_harmonic_hz // f0_hz) + 1)
    voice = np.zeros(n)
    rng = np.random.default_rng(seed)
    for k in harmonics:
        voice += np.sin(2.0 * np.pi * k * f0_hz * t + rng.uniform(0, 2 * np.pi)) / k
    voice *= 0.75 + 0.25 * np.sin(2.0 * np.pi * syllable_hz * t)       # AM between 0.5 and 1.0
    period = talk_s + pause_s
    active = (t % period) < talk_s
    # 10 ms raised-cosine edges so the pauses do not start with a click
    edge = int(0.01 * fs)
    envelope = active.astype(np.float64)
    ramp = 0.5 - 0.5 * np.cos(np.pi * np.arange(edge) / edge)
    starts = np.flatnonzero(np.diff(envelope, prepend=0.0) > 0)
    stops = np.flatnonzero(np.diff(envelope, append=0.0) < 0)
    for s in starts:
        envelope[s:s + edge] = ramp[: n - s]
    for e in stops:
        envelope[e - edge + 1:e + 1] = ramp[::-1][-min(edge, e + 1):]
    voice *= envelope
    voice *= db_to_amp(rms_dbfs) / np.sqrt(np.mean(voice[active] ** 2))
    noise = white_noise(noise_rms_dbfs, seconds, fs, seed + 1)
    return voice + noise, active


def sibilance_bursts(rms_dbfs: float, seconds: float, fs: int = FS, seed: int = 0,
                     burst_s: float = 0.1, period_s: float = 0.4,
                     lo_hz: float = 5000.0, hi_hz: float = 9000.0) -> tuple[np.ndarray, np.ndarray]:
    """Band-limited noise bursts (5..9 kHz) with 5 ms raised-cosine edges; returns (signal, mask)."""
    n = int(round(seconds * fs))
    noise = bandlimited_noise(lo_hz, hi_hz, 0.0, seconds, fs, seed)
    t = np.arange(n) / fs
    mask = (t % period_s) < burst_s
    edge = int(0.005 * fs)
    envelope = mask.astype(np.float64)
    ramp = 0.5 - 0.5 * np.cos(np.pi * np.arange(edge) / edge)
    for s in np.flatnonzero(np.diff(envelope, prepend=0.0) > 0):
        envelope[s:s + edge] = ramp[: n - s]
    for e in np.flatnonzero(np.diff(envelope, append=0.0) < 0):
        envelope[e - edge + 1:e + 1] = ramp[::-1][-min(edge, e + 1):]
    x = noise * envelope
    x *= db_to_amp(rms_dbfs) / np.sqrt(np.mean(x[mask] ** 2))
    return x, mask


def process_blocks(stage, x: np.ndarray, block: int) -> np.ndarray:
    """Run ``stage.process`` over ``x`` in blocks of ``block`` samples and concatenate."""
    return np.concatenate([stage.process(x[i:i + block]) for i in range(0, len(x), block)])


def band_rms_db(x: np.ndarray, lo_hz: float, hi_hz: float, fs: int = FS) -> float:
    """RMS level (dB) of the energy inside [lo_hz, hi_hz] measured via FFT."""
    spectrum = np.fft.rfft(np.asarray(x, dtype=np.float64))
    freqs = np.fft.rfftfreq(len(x), 1.0 / fs)
    sel = (freqs >= lo_hz) & (freqs <= hi_hz)
    power = np.sum(np.abs(spectrum[sel]) ** 2) * 2.0 / (len(x) ** 2)
    return 10.0 * np.log10(max(power, 1e-30))


def tone_level_db(x: np.ndarray, freq_hz: float, fs: int = FS) -> float:
    """Peak level (dBFS) of a single tone in ``x`` by complex demodulation (immune to other components)."""
    x = np.asarray(x, dtype=np.float64)
    t = np.arange(len(x)) / fs
    window = np.hanning(len(x))
    amp = 2.0 * np.abs(np.sum(x * window * np.exp(-2j * np.pi * freq_hz * t))) / np.sum(window)
    return 20.0 * np.log10(max(amp, 1e-15))


def true_peak_dbfs(x: np.ndarray, oversample: int = 4) -> float:
    """Inter-sample peak via polyphase 4x oversampling (BS.1770 Annex 2 style)."""
    up = signal.resample_poly(np.asarray(x, dtype=np.float64), oversample, 1)
    return 20.0 * np.log10(max(np.max(np.abs(up)), 1e-15))


@pytest.fixture
def fs() -> int:
    return FS


@pytest.fixture
def gen():
    """Namespace with all signal generators and measurement helpers."""
    import types
    return types.SimpleNamespace(
        sine=sine, white_noise=white_noise, pink_noise=pink_noise, bandlimited_noise=bandlimited_noise,
        speech_like=speech_like, sibilance_bursts=sibilance_bursts, process_blocks=process_blocks,
        rms_db=rms_db, band_rms_db=band_rms_db, tone_level_db=tone_level_db,
        true_peak_dbfs=true_peak_dbfs, db_to_amp=db_to_amp,
    )
