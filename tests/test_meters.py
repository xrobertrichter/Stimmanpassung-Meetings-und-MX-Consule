"""Tests for voicechain.meters: every assertion carries a number."""
import math

import numpy as np
import pytest

from voicechain import meters, synth

SR = 48000


def sine(freq_hz: float, rms_dbfs: float, duration_s: float = 5.0, sr: int = SR, phase: float = 0.0):
    t = np.arange(int(duration_s * sr)) / sr
    amp = 10 ** (rms_dbfs / 20) * math.sqrt(2.0)
    return amp * np.sin(2 * math.pi * freq_hz * t + phase)


def pink_noise(n: int, seed: int = 0) -> np.ndarray:
    """White noise shaped to a 1/f power spectrum via FFT scaling (-3 dB/oct)."""
    rng = np.random.default_rng(seed)
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n)
    f[0] = f[1]
    spec /= np.sqrt(f)
    x = np.fft.irfft(spec, n)
    return x / np.sqrt(np.mean(x * x)) * 0.1


# --------------------------------------------------------------------------- K-weighting

def test_k_weighting_design_reproduces_bs1770_48k_table():
    designed = meters.design_k_weighting_sos(48000)
    table = meters.k_weighting_sos(48000)
    assert np.allclose(designed, table, atol=1e-9), designed - table
    # Table values from BS.1770 (shelf b0 and high-pass a1) spot-checked.
    assert table[0, 0] == pytest.approx(1.53512485958697, abs=1e-12)
    assert table[1, 4] == pytest.approx(-1.99004745483398, abs=1e-12)


@pytest.mark.parametrize("sr", [44100, 48000, 96000])
def test_k_weighting_gain_is_about_zero_at_1khz_and_plus4_at_10khz(sr):
    from scipy import signal
    sos = meters.k_weighting_sos(sr)
    w, h = signal.sosfreqz(sos, worN=[1000.0, 10000.0], fs=sr)
    gain_db = 20 * np.log10(np.abs(h))
    # BS.1770: at 1 kHz the shelf onset gives +0.69 dB, which is exactly what the
    # -0.691 loudness offset cancels; at 10 kHz the shelf has reached ~+4 dB.
    assert gain_db[0] == pytest.approx(-meters.LUFS_OFFSET, abs=0.1)
    assert 3.5 < gain_db[1] < 4.3


# --------------------------------------------------------------------------- loudness

@pytest.mark.parametrize("level", [-20.0, -23.0])
def test_sine_1khz_loudness_equals_rms_level(level):
    """BS.1770: a 997 Hz/1 kHz sine at -23 dBFS RMS reads -23 LUFS (K ~ 0 dB there)."""
    m = meters.LoudnessMeter(SR)
    m.push(sine(1000.0, level))
    assert m.momentary_lufs() == pytest.approx(level, abs=0.2)
    assert m.short_term_lufs() == pytest.approx(level, abs=0.2)
    assert m.integrated_lufs() == pytest.approx(level, abs=0.2)
    assert meters.integrated_lufs(sine(1000.0, level), SR) == pytest.approx(level, abs=0.2)


def test_loudness_meter_is_block_size_independent():
    x = synth.make_speech_like(12.0, SR, seed=5)
    whole = meters.LoudnessMeter(SR)
    whole.push(x)
    blocks = meters.LoudnessMeter(SR)
    for i in range(0, x.size, 64):
        blocks.push(x[i:i + 64])
    assert blocks.momentary_lufs() == pytest.approx(whole.momentary_lufs(), abs=1e-6)
    assert blocks.short_term_lufs() == pytest.approx(whole.short_term_lufs(), abs=1e-6)
    assert blocks.integrated_lufs() == pytest.approx(whole.integrated_lufs(), abs=1e-6)


def test_momentary_follows_level_change_short_term_lags():
    m = meters.LoudnessMeter(SR)
    m.push(sine(1000.0, -30.0, duration_s=3.0))
    m.push(sine(1000.0, -10.0, duration_s=0.5))
    # Momentary covers only the last 400 ms (all at -10 dBFS).
    assert m.momentary_lufs() == pytest.approx(-10.0, abs=0.3)
    # Short-term covers 3 s: 0.5 s at -10 dBFS and 2.5 s at -30 dBFS
    # -> 10*log10((0.5*0.1 + 2.5*0.001)/3) = -17.6 LUFS.
    assert m.short_term_lufs() == pytest.approx(-17.6, abs=0.3)


def test_integrated_gate_ignores_long_silence_and_very_quiet_noise():
    speech = synth.make_speech_like(20.0, SR, seed=2)
    reference = meters.integrated_lufs(speech, SR)
    with_zeros = np.concatenate([speech, np.zeros(60 * SR, dtype=np.float32)])
    quiet = (np.random.default_rng(1).standard_normal(60 * SR) * 10 ** (-80 / 20)).astype(np.float32)
    with_quiet = np.concatenate([speech, quiet])
    assert abs(meters.integrated_lufs(with_zeros, SR) - reference) < 0.1
    assert abs(meters.integrated_lufs(with_quiet, SR) - reference) < 0.1
    # Ungated mean would drop by 10*log10(20/80) = -6 dB; make sure the gate matters.
    assert reference > -40.0


def test_silence_reads_floor_not_nan():
    m = meters.LoudnessMeter(SR)
    m.push(np.zeros(SR))
    assert math.isfinite(m.momentary_lufs())
    assert m.integrated_lufs() <= -100.0
    assert meters.rms_dbfs(np.zeros(100)) == pytest.approx(-120.0)


# --------------------------------------------------------------------------- peaks / rms

def test_true_peak_of_1khz_sine_matches_sample_peak_within_0_2_db():
    x = sine(1000.0, -0.5 - 3.0103)  # peak -0.5 dBFS
    tp = meters.true_peak_dbfs(x, SR)
    sp = meters.sample_peak_dbfs(x)
    assert sp == pytest.approx(-0.5, abs=0.01)
    assert sp - 0.05 <= tp <= sp + 0.2


def test_true_peak_detects_inter_sample_peak():
    # At fs/4 with 45 degree phase every sample sits at +-0.707 of the true peak:
    # sample peak -3.01 dBFS, true peak 0 dBFS.
    x = sine(SR / 4, -3.0103, phase=math.pi / 4)
    sp = meters.sample_peak_dbfs(x)
    tp = meters.true_peak_dbfs(x, SR)
    assert sp == pytest.approx(-3.01, abs=0.05)
    assert tp == pytest.approx(0.0, abs=0.15)


def test_rms_dbfs_of_sine():
    assert meters.rms_dbfs(sine(440.0, -12.0)) == pytest.approx(-12.0, abs=0.01)


# --------------------------------------------------------------------------- speech statistics

@pytest.mark.parametrize("level,floor", [(-26.0, -40.0), (-26.0, -50.0), (-20.0, -60.0), (-35.0, -55.0)])
def test_noise_floor_and_speech_level_within_2_db_of_generated(level, floor):
    x = synth.make_speech_like(40.0, SR, level_dbfs=level, noise_floor_dbfs=floor, seed=7)
    assert meters.noise_floor_dbfs(x, SR) == pytest.approx(floor, abs=2.0)
    assert meters.speech_level_dbfs(x, SR) == pytest.approx(level, abs=2.0)
    assert 0.55 <= meters.speech_activity_ratio(x, SR) <= 0.8  # pause_ratio 0.3 by default


def test_sibilance_ratio_increases_monotonically_with_generator_setting():
    values = []
    for sib in (-20.0, -12.0, -6.0, 0.0, 3.0):
        x = synth.make_speech_like(30.0, SR, sibilance_db=sib, seed=11)
        values.append(meters.sibilance_ratio_db(x, SR))
    assert all(b > a + 1.0 for a, b in zip(values[:-1], values[1:])), values
    # Pronounced sibilance (0 dB bursts) must clearly exceed the calibration reference.
    assert values[3] > -12.0
    assert values[0] < -20.0


def test_sibilance_centroid_lies_inside_the_burst_band():
    x = synth.make_speech_like(30.0, SR, sibilance_db=3.0, seed=4)
    c = meters.sibilance_centroid_hz(x, SR)
    assert 5000.0 <= c <= 9000.0


def test_white_noise_is_not_speech_and_has_zero_tilt():
    x = np.random.default_rng(3).standard_normal(20 * SR) * 0.05
    mask, levels = meters.speech_activity(x, SR)
    assert not mask.any()
    assert np.ptp(levels) < 3.0
    assert meters.spectral_tilt_db_per_oct(x, SR) == pytest.approx(0.0, abs=0.5)


def test_pink_noise_tilt_is_minus_3_db_per_octave():
    x = pink_noise(20 * SR)
    assert meters.spectral_tilt_db_per_oct(x, SR) == pytest.approx(-3.0, abs=0.5)


def test_speech_like_signal_has_negative_tilt():
    x = synth.make_speech_like(30.0, SR, seed=9)
    assert meters.spectral_tilt_db_per_oct(x, SR) < -2.0


def test_band_energies_sum_to_total_power_and_reject_bad_edges():
    x = np.random.default_rng(5).standard_normal(10 * SR) * 0.05
    total = meters.band_energies_db(x, SR, [0.0, SR / 2 + 1])[0]
    assert total == pytest.approx(meters.rms_dbfs(x), abs=0.3)
    parts = meters.band_energies_db(x, SR, [0.0, 6000.0, 12000.0, 18000.0, SR / 2 + 1])
    # White noise: four equal-width bands each carry a quarter of the power (-6 dB).
    for p in parts:
        assert p == pytest.approx(total - 6.02, abs=0.5)
    with pytest.raises(ValueError):
        meters.band_energies_db(x, SR, [1000.0, 500.0])


def test_frame_rms_db_rejects_too_short_signal():
    with pytest.raises(ValueError):
        meters.frame_rms_db(np.zeros(10), SR, 100.0)
