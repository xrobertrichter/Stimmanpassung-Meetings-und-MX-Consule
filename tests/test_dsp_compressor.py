"""Compressor: static characteristic, knee, make-up, attack and release timing."""

import numpy as np
import pytest

from voicechain.dsp import Compressor
from voicechain.dsp.compressor import compressor_reduction_db

DEFAULT = dict(enabled=True, threshold_db=-20.0, ratio=3.0, knee_db=6.0,
               attack_ms=10.0, release_ms=100.0, makeup_db=0.0)


def _settled_peak_db(y, fs):
    return 20.0 * np.log10(np.max(np.abs(y[fs:])))


def test_static_curve_values():
    levels = np.array([-40.0, -23.0, -20.0, -17.0, -10.0, 0.0])
    gr = compressor_reduction_db(levels, -20.0, 3.0, 6.0)
    # below knee / knee edge / knee centre (slope*(3)^2/12 = 0.5) / knee edge (2) / linear 6.67 / 13.33
    np.testing.assert_allclose(gr, [0.0, 0.0, 0.5, 2.0, 20.0 / 3.0, 40.0 / 3.0], atol=1e-12)
    np.testing.assert_allclose(compressor_reduction_db(levels, -20.0, 3.0, 0.0),
                               [0.0, 0.0, 0.0, 2.0, 20.0 / 3.0, 40.0 / 3.0], atol=1e-12)


def test_static_characteristic_ratio3_knee6(fs, gen):
    x = gen.sine(1000.0, -10.0, 2.0, fs)
    y = gen.process_blocks(Compressor(fs, **DEFAULT), x, 256)
    assert abs(_settled_peak_db(y, fs) - (-20.0 + 10.0 / 3.0)) < 0.3    # -16.67 dBFS


def test_input_at_threshold_sees_half_knee_reduction(fs, gen):
    x = gen.sine(1000.0, -20.0, 2.0, fs)
    y = gen.process_blocks(Compressor(fs, **DEFAULT), x, 256)
    assert abs(_settled_peak_db(y, fs) - (-20.5)) < 0.3


def test_below_threshold_is_transparent(fs, gen):
    x = gen.sine(1000.0, -30.0, 1.0, fs)
    y = gen.process_blocks(Compressor(fs, **DEFAULT), x, 256)
    assert np.max(np.abs(y - x)) < 1e-12


def test_makeup_gain(fs, gen):
    x = gen.sine(1000.0, -30.0, 1.0, fs)
    y = gen.process_blocks(Compressor(fs, **{**DEFAULT, "makeup_db": 6.0}), x, 256)
    assert abs(gen.rms_db(y) - gen.rms_db(x) - 6.0) < 0.01


def test_attack_and_release_63_percent_points(fs, gen):
    stage = Compressor(fs, **DEFAULT)
    x = np.concatenate([gen.sine(1000.0, -30.0, 1.0, fs), gen.sine(1000.0, -10.0, 1.0, fs), gen.sine(1000.0, -30.0, 1.0, fs)])
    gr = np.concatenate([stage.reduction_curve(x[i:i + 256]) for i in range(0, len(x), 256)])
    full = gr[int(1.9 * fs)]
    assert abs(full - 20.0 / 3.0) < 0.3
    attack_ms = np.argmax(gr[fs:] >= 0.632 * full) / fs * 1e3
    release_ms = np.argmax(gr[2 * fs:] <= 0.368 * full) / fs * 1e3
    assert 0.7 * DEFAULT["attack_ms"] <= attack_ms <= 1.3 * DEFAULT["attack_ms"]
    assert 0.7 * DEFAULT["release_ms"] <= release_ms <= 1.3 * DEFAULT["release_ms"]


def test_block_invariance(fs, gen):
    x, _ = gen.speech_like(-14.0, 3.0, fs, seed=5)
    a = gen.process_blocks(Compressor(fs, **DEFAULT), x, 64)
    b = gen.process_blocks(Compressor(fs, **DEFAULT), x, 4096)
    assert np.max(np.abs(a - b)) < 1e-9


def test_disabled_meters_and_validation(fs, gen):
    x = gen.sine(1000.0, -10.0, 0.5, fs)
    assert np.array_equal(Compressor(fs, **{**DEFAULT, "enabled": False}).process(x), x)
    stage = Compressor(fs, **DEFAULT)
    stage.process(x)
    m = stage.meters()
    assert 5.5 <= m["gain_reduction_max_db"] <= 7.0 and m["gain_reduction_db"] <= m["gain_reduction_max_db"]
    assert m["gain_reduction_mean_db"] <= m["gain_reduction_max_db"]
    assert abs(m["level_dbfs"] + 10.0) < 0.1
    with pytest.raises(ValueError):
        stage.set_param("ratio", 50.0)
    with pytest.raises(ValueError):
        stage.set_param("ratio", 1.0)                      # schema lower bound 1.1 (review SW-7)
    assert stage.get_param("ratio") == 3.0
    stage.set_param("threshold_db", -25.0)
    assert stage.get_param("threshold_db") == -25.0


def test_reduction_accumulator_mean_max_and_drain(fs, gen):
    """PO-5: 2 s without reduction, then 10 ms with 10 dB -> small mean, max 10; drained by reading."""
    stage = Compressor(fs, **{**DEFAULT, "attack_ms": 0.1})
    x = np.concatenate([gen.sine(1000.0, -30.0, 2.0, fs), gen.sine(1000.0, -5.0, 0.010, fs)])  # -5 dBFS: 15 dB over -> 10 dB
    gen.process_blocks(stage, x, 240)                       # 240 = 5 ms blocks, the burst spans two blocks
    m = stage.meters()
    assert 9.5 <= m["gain_reduction_max_db"] <= 10.5
    assert m["gain_reduction_mean_db"] < 0.2                # 10 dB * 10 ms / 2010 ms = 0.05 dB
    assert 9.0 <= m["gain_reduction_db"] <= 10.5           # last 5 ms block lies inside the burst
    drained = stage.meters()
    assert drained["gain_reduction_mean_db"] == 0.0 and drained["gain_reduction_max_db"] == 0.0
    gen.process_blocks(stage, gen.sine(1000.0, -30.0, 1.0, fs), 240)   # release tail (100 ms) dies out
    stage.meters()
    stage.process(gen.sine(1000.0, -30.0, 0.1, fs))
    assert stage.meters()["gain_reduction_max_db"] < 0.01   # the burst is forgotten
