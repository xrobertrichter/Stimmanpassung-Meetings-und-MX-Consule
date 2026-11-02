"""Loudness normaliser: target, rate limit, gate, gain limits, block invariance."""

import numpy as np
import pytest
from scipy.signal import sosfilt

from voicechain.dsp import Normalizer
from voicechain.dsp.biquad import k_weighting_sos

DEFAULT = dict(enabled=True, target_lufs=-18.0, window_s=3.0, max_gain_db=12.0,
               min_gain_db=-12.0, rate_db_per_s=1.0, gate_lufs=-40.0)


def loudness_lufs(x, fs):
    """Reference: BS.1770 mono loudness over the whole segment (rectangular window)."""
    weighted = sosfilt(k_weighting_sos(fs), np.asarray(x, dtype=np.float64))
    return -0.691 + 10.0 * np.log10(np.mean(weighted ** 2))


def _run(stage, x, block):
    out, gains = [], []
    for i in range(0, len(x), block):
        out.append(stage.process(x[i:i + block]))
        gains.append(stage.meters()["gain_db"])
    return np.concatenate(out), np.array(gains)


def test_reference_loudness_of_sine(fs, gen):
    x = gen.sine(1000.0, -27.0, 5.0, fs)          # -30 dBFS RMS -> -30 LUFS at 1 kHz
    assert abs(loudness_lufs(x, fs) + 30.0) < 0.05
    stage = Normalizer(fs, **DEFAULT)
    estimate = np.concatenate([stage.loudness_curve(x[i:i + 1024]) for i in range(0, len(x), 1024)])
    assert abs(estimate[-1] + 30.0) < 0.1
    assert abs(estimate[fs // 2] + 30.0) < 0.1     # bias correction: valid long before window_s


def test_quiet_signal_is_pulled_to_target_within_rate_limit(fs, gen):
    x = gen.sine(1000.0, -27.0, 25.0, fs)
    y, gains = _run(Normalizer(fs, **DEFAULT), x, 1024)
    assert abs(loudness_lufs(y[-3 * fs:], fs) - DEFAULT["target_lufs"]) < 1.0
    block_s = 1024 / fs
    assert np.max(np.diff(gains)) <= DEFAULT["rate_db_per_s"] * block_s * (1 + 1e-9)
    assert np.all(np.diff(gains) >= -1e-12)         # monotonic ramp towards the target
    assert 4.0 <= gains[int(5.0 / block_s)] <= 5.2  # 1 dB/s -> ~5 dB after 5 s
    assert abs(gains[-1] - 12.0) < 0.05             # exactly the 12 dB that are needed


def test_gate_prevents_pumping_up_silence(fs, gen):
    x = gen.white_noise(-60.0, 10.0, fs, seed=1)
    y, gains = _run(Normalizer(fs, **DEFAULT), x, 1024)
    assert np.all(gains == 0.0)
    assert np.array_equal(y, x)
    z, gains = _run(Normalizer(fs, **DEFAULT), np.zeros(5 * fs), 256)
    assert np.max(np.abs(z)) == 0.0 and np.all(gains == 0.0)


def test_max_and_min_gain_are_respected(fs, gen):
    x = gen.sine(1000.0, -32.0, 25.0, fs)           # -35 LUFS needs +17 dB -> capped at +12
    y, gains = _run(Normalizer(fs, **DEFAULT), x, 1024)
    assert np.max(gains) <= DEFAULT["max_gain_db"] + 1e-12
    assert abs(loudness_lufs(y[-3 * fs:], fs) + 23.0) < 0.5
    loud = gen.sine(1000.0, 3.0, 25.0, fs)          # 0 LUFS needs -18 dB -> capped at -12
    y, gains = _run(Normalizer(fs, **DEFAULT), loud, 1024)
    assert np.min(gains) >= DEFAULT["min_gain_db"] - 1e-12
    assert abs(loudness_lufs(y[-3 * fs:], fs) + 12.0) < 0.5


def test_gain_is_held_in_pauses_and_resumes(fs, gen):
    x, _ = gen.speech_like(-30.0, 20.0, fs, seed=2, noise_rms_dbfs=-70.0, talk_s=1.5, pause_s=1.5)
    y, gains = _run(Normalizer(fs, **DEFAULT), x, 1024)
    assert np.all(np.diff(gains) >= -1e-12)         # never pulled down by a pause
    assert gains[-1] > 6.0


def test_block_invariance(fs, gen):
    x, _ = gen.speech_like(-28.0, 6.0, fs, seed=3)
    a, _ = _run(Normalizer(fs, **DEFAULT), x, 64)
    b, _ = _run(Normalizer(fs, **DEFAULT), x, 4096)
    assert np.max(np.abs(a - b)) < 1e-9


def test_disabled_keeps_measuring(fs, gen):
    x = gen.sine(1000.0, -27.0, 4.0, fs)
    stage = Normalizer(fs, **{**DEFAULT, "enabled": False})
    y, gains = _run(stage, x, 1024)
    assert np.array_equal(y, x) and np.all(gains == 0.0)
    assert abs(stage.meters()["loudness_lufs"] + 30.0) < 0.1


def test_max_gain_can_be_changed_at_runtime(fs, gen):
    """The adaptive controller lowers/raises max_gain_db live; the cap must apply at once."""
    x = gen.sine(1000.0, -32.0, 20.0, fs)           # -35 LUFS wants +17 dB
    stage = Normalizer(fs, **DEFAULT)
    _run(stage, x[:10 * fs], 1024)
    assert abs(stage.meters()["gain_db"] - 10.0) < 0.05        # 1 dB/s for 10 s
    stage.set_param("max_gain_db", 6.0)
    assert stage.get_param("max_gain_db") == 6.0
    _, gains = _run(stage, x[10 * fs:], 1024)
    assert np.all(gains <= 10.0 + 1e-12) and abs(gains[-1] - 6.0) < 0.05   # ramps down to the new cap
    assert np.min(np.diff(gains)) >= -DEFAULT["rate_db_per_s"] * 1024 / fs * (1 + 1e-9)


def test_validation(fs):
    with pytest.raises(ValueError):
        Normalizer(fs, **{**DEFAULT, "min_gain_db": 5.0, "max_gain_db": 2.0})
    stage = Normalizer(fs, **DEFAULT)
    with pytest.raises(ValueError):
        stage.set_param("target_lufs", 0.0)
    stage.set_param("rate_db_per_s", 2.0)
    assert stage.get_param("rate_db_per_s") == 2.0
