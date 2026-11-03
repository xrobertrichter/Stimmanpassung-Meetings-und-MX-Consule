"""Downward expander: static curve, noise reduction, speech transparency, hold, timing."""

import numpy as np
import pytest

from voicechain.dsp import Expander
from voicechain.dsp.envelope import HoldMax
from voicechain.dsp.expander import expander_gain_db

DEFAULT = dict(enabled=True, threshold_db=-45.0, ratio=2.5, range_db=20.0,
               attack_ms=5.0, hold_ms=100.0, release_ms=250.0, knee_db=6.0)


def test_holdmax_matches_naive_running_maximum():
    rng = np.random.default_rng(0)
    x = rng.standard_normal(5000)
    hold = 37
    naive = np.array([x[max(0, n - hold + 1):n + 1].max() for n in range(len(x))])
    stage = HoldMax(hold, fill_value=-np.inf)
    out = np.concatenate([stage.process(x[i:i + 123]) for i in range(0, len(x), 123)])
    assert np.array_equal(out, naive)


def test_static_curve_values():
    levels = np.array([-60.0, -55.0, -48.0, -45.0, -42.0, -20.0])
    gain = expander_gain_db(levels, -45.0, 2.5, 6.0, 20.0)
    expected = [-20.0, -15.0, -4.5, -1.125, 0.0, 0.0]    # clamp / linear / knee edge / knee centre / knee edge / above
    np.testing.assert_allclose(gain, expected, atol=1e-12)
    hard = expander_gain_db(levels, -45.0, 2.5, 0.0, 20.0)
    np.testing.assert_allclose(hard, [-20.0, -15.0, -4.5, 0.0, 0.0, 0.0], atol=1e-12)


def test_stationary_noise_floor_is_lowered(fs, gen):
    x = gen.white_noise(-55.0, 2.0, fs, seed=1)
    y = gen.process_blocks(Expander(fs, **DEFAULT), x, 256)
    reduction = gen.rms_db(x[fs:]) - gen.rms_db(y[fs:])
    assert 12.0 <= reduction <= 20.0          # static: (-55+45)*(2.5-1) = 15 dB, never beyond range_db


def test_speech_level_is_untouched(fs, gen):
    x = gen.sine(1000.0, -17.0, 1.0, fs)      # -20 dBFS RMS
    y = gen.process_blocks(Expander(fs, **DEFAULT), x, 256)
    assert abs(gen.rms_db(y[fs // 2:]) - gen.rms_db(x[fs // 2:])) < 1.0


def test_speech_with_pauses(fs, gen):
    x, active = gen.speech_like(-20.0, 10.0, fs, seed=4, noise_rms_dbfs=-55.0, talk_s=0.6, pause_s=1.5)
    y = gen.process_blocks(Expander(fs, **DEFAULT), x, 256)
    # last 0.5 s of every 1.5 s pause: hold (100 ms) + detector (10 ms) + 2.5 release constants are over
    period = int(2.1 * fs)
    deep = np.zeros(len(x), dtype=bool)
    for start in range(int(0.6 * fs), len(x), period):
        deep[start + int(1.0 * fs):start + int(1.5 * fs)] = True
    assert gen.rms_db(x[deep]) - gen.rms_db(y[deep]) >= 12.0
    assert gen.rms_db(x[~active]) - gen.rms_db(y[~active]) >= 6.0      # whole pauses on average
    assert abs(gen.rms_db(y[active]) - gen.rms_db(x[active])) < 1.0


def test_hold_keeps_gate_open_and_release_closes_it(fs, gen):
    stage = Expander(fs, **DEFAULT)
    pre = gen.white_noise(-55.0, 1.0, fs, seed=3)
    tone = gen.sine(1000.0, -17.0, 0.5, fs) + gen.white_noise(-55.0, 0.5, fs, seed=4)
    post = gen.white_noise(-55.0, 2.0, fs, seed=5)
    sig = np.concatenate([pre, tone, post])
    gain = np.concatenate([stage.gain_curve(sig[i:i + 256]) for i in range(0, len(sig), 256)])
    t_on, t_off = len(pre), len(pre) + len(tone)
    hold = int(DEFAULT["hold_ms"] * 1e-3 * fs)
    assert gain[t_on - 1] <= -13.0                           # closed before the tone (-15 dB static)
    assert np.all(gain[t_off:t_off + hold] >= -0.5)          # hold: stays open for hold_ms
    assert gain[t_off + hold + int(0.005 * fs)] >= -0.5        # still open right after the hold (detector window)
    # closing: detector window (10 ms) + exponential release, 63 % after release_ms
    t_close = t_off + hold + int(0.010 * fs)
    at_tau = gain[t_close + int(DEFAULT["release_ms"] * 1e-3 * fs)]
    assert -12.0 <= at_tau <= -6.0                           # ~63 % of 15 dB = 9.5 dB (measured -9.3)
    assert gain[t_close + int(2.5 * DEFAULT["release_ms"] * 1e-3 * fs)] <= -12.0
    assert gain[-1] <= -13.0     # steady state ~ -14.3 dB (static -15 dB, +-1 dB level ripple of the 10 ms window)
    # opening: 63 % of the way to 0 dB within attack_ms (+ detector window)
    g0 = gain[t_on - 1]
    t63 = np.argmax(gain[t_on:] >= g0 * (1.0 - 0.632)) / fs * 1e3
    assert 0.9 * DEFAULT["attack_ms"] <= t63 <= DEFAULT["attack_ms"] + 10.0


def test_short_gap_does_not_chatter(fs, gen):
    stage = Expander(fs, **DEFAULT)
    stage.process(gen.sine(1000.0, -17.0, 0.3, fs))                       # open the gate
    gap = np.zeros(int(0.02 * fs))
    sig = np.concatenate([gen.sine(1000.0, -17.0, 0.3, fs), gap, gen.sine(1000.0, -17.0, 0.3, fs)])
    sig += gen.white_noise(-55.0, len(sig) / fs, fs, seed=7)
    gain = np.concatenate([stage.gain_curve(sig[i:i + 256]) for i in range(0, len(sig), 256)])
    assert np.min(gain) >= -0.5                                            # 20 ms gap < hold_ms


def test_block_invariance(fs, gen):
    x, _ = gen.speech_like(-20.0, 3.0, fs, seed=8)
    a = gen.process_blocks(Expander(fs, **DEFAULT), x, 64)
    b = gen.process_blocks(Expander(fs, **DEFAULT), x, 4096)
    assert np.max(np.abs(a - b)) < 1e-9


def test_disabled_passthrough_and_meters(fs, gen):
    x = gen.white_noise(-55.0, 0.5, fs, seed=9)
    off = Expander(fs, **{**DEFAULT, "enabled": False})
    assert np.array_equal(off.process(x), x)
    assert off.meters()["gain_reduction_db"] == 0.0
    on = Expander(fs, **DEFAULT)
    gen.process_blocks(on, gen.white_noise(-55.0, 2.0, fs, seed=10), 256)
    m = on.meters()
    assert set(m) >= {"gain_reduction_db", "gain_reduction_max_db", "level_dbfs", "enabled"}
    assert 12.0 <= m["gain_reduction_db"] <= 20.0 and m["gain_reduction_max_db"] >= m["gain_reduction_db"]
    assert m["gain_reduction_mean_db"] <= m["gain_reduction_max_db"] <= 20.0
    assert abs(m["level_dbfs"] + 55.0) < 1.5


def test_runtime_parameter_changes(fs, gen):
    stage = Expander(fs, **DEFAULT)
    stage.process(gen.sine(1000.0, -17.0, 0.2, fs))
    stage.set_param("hold_ms", 200.0)
    stage.set_param("threshold_db", -42.0)
    assert stage.get_param("threshold_db") == -42.0
    y = stage.process(gen.sine(1000.0, -17.0, 0.2, fs))
    assert np.all(np.isfinite(y))
    with pytest.raises(ValueError):
        stage.set_param("ratio", 0.5)
    with pytest.raises(KeyError):
        stage.get_param("nonexistent")


def test_reduction_accumulates_between_meter_reads(fs, gen):
    """PO-5: mean/max since the last meters() call, drained by reading; last block stays separate."""
    stage = Expander(fs, **DEFAULT)
    stage.process(gen.sine(1000.0, -17.0, 0.5, fs))          # open the gate
    stage.meters()                                            # drain
    gen.process_blocks(stage, gen.sine(1000.0, -17.0, 2.0, fs), 256)           # 2 s open: reduction 0
    gen.process_blocks(stage, gen.white_noise(-55.0, 1.0, fs, seed=2), 256)   # closes within ~0.4 s
    m = stage.meters()
    assert 12.0 <= m["gain_reduction_max_db"] <= 20.0
    assert 2.0 <= m["gain_reduction_mean_db"] <= 6.0           # ~0.6 s of ~14 dB over 3 s
    assert 12.0 <= m["gain_reduction_db"] <= 20.0              # last block alone: closed
    again = stage.meters()                                    # nothing processed since: accumulators empty
    assert again["gain_reduction_mean_db"] == 0.0 and again["gain_reduction_max_db"] == 0.0
    assert again["gain_reduction_db"] == m["gain_reduction_db"]
