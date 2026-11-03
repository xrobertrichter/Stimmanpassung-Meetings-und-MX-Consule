"""Split-band de-esser: sibilance reduction, transparency outside the band, reduction cap."""

import numpy as np
import pytest

from voicechain.dsp import DeEsser

DEFAULT = dict(enabled=True, freq_hz=6500.0, bandwidth_oct=1.0, threshold_db=-30.0,
               ratio=4.0, max_reduction_db=8.0, attack_ms=1.0, release_ms=60.0)


def test_sibilance_bursts_are_reduced_but_not_beyond_cap(fs, gen):
    x, mask = gen.sibilance_bursts(-15.0, 2.0, fs, seed=1)
    stage = DeEsser(fs, **DEFAULT)
    y = gen.process_blocks(stage, x, 256)
    reduction = gen.band_rms_db(x[mask], 5000.0, 9000.0, fs) - gen.band_rms_db(y[mask], 5000.0, 9000.0, fs)
    assert reduction >= 4.0
    assert reduction <= DEFAULT["max_reduction_db"] + 0.1
    m = stage.meters()
    assert m["gain_reduction_max_db"] <= DEFAULT["max_reduction_db"] + 1e-9
    assert 7.0 <= m["gain_reduction_max_db"]                                   # since the start: the bursts hit the cap
    assert 1.0 <= m["gain_reduction_mean_db"] <= 4.0                           # 25 % burst duty cycle of ~8 dB
    assert stage.meters()["gain_reduction_max_db"] == 0.0                     # drained by the read


def test_1khz_tone_untouched(fs, gen):
    x = gen.sine(1000.0, -15.0, 1.0, fs)
    y = gen.process_blocks(DeEsser(fs, **DEFAULT), x, 256)
    assert abs(gen.rms_db(y[fs // 2:]) - gen.rms_db(x[fs // 2:])) < 0.5


def test_tone_in_band_is_reduced_by_exactly_the_cap(fs, gen):
    x = gen.sine(6500.0, -15.0, 1.0, fs)                # 15 dB over threshold -> 11.25 dB wanted, capped at 8
    y = gen.process_blocks(DeEsser(fs, **DEFAULT), x, 256)
    assert abs((gen.rms_db(x[fs // 2:]) - gen.rms_db(y[fs // 2:])) - 8.0) < 0.1
    stage = DeEsser(fs, **{**DEFAULT, "max_reduction_db": 3.0})
    y = gen.process_blocks(stage, x, 256)
    assert abs((gen.rms_db(x[fs // 2:]) - gen.rms_db(y[fs // 2:])) - 3.0) < 0.1


def test_tone_below_threshold_in_band_is_untouched(fs, gen):
    x = gen.sine(6500.0, -35.0, 1.0, fs)
    y = gen.process_blocks(DeEsser(fs, **DEFAULT), x, 256)
    assert abs(gen.rms_db(y[fs // 2:]) - gen.rms_db(x[fs // 2:])) < 0.01


def test_mixed_signal_only_band_is_affected(fs, gen):
    bursts, mask = gen.sibilance_bursts(-15.0, 2.0, fs, seed=2)
    tone = gen.sine(1000.0, -15.0, 2.0, fs)
    x = tone + bursts
    y = gen.process_blocks(DeEsser(fs, **DEFAULT), x, 256)
    tone_change = gen.tone_level_db(y[fs:], 1000.0, fs) - gen.tone_level_db(x[fs:], 1000.0, fs)
    assert abs(tone_change) < 0.5
    assert gen.band_rms_db(x[mask], 5000.0, 9000.0, fs) - gen.band_rms_db(y[mask], 5000.0, 9000.0, fs) >= 4.0


def test_attack_and_release_timing(fs, gen):
    stage = DeEsser(fs, **DEFAULT)
    x = np.concatenate([gen.sine(6500.0, -40.0, 0.2, fs), gen.sine(6500.0, -15.0, 0.3, fs), gen.sine(6500.0, -40.0, 0.5, fs)])
    y = gen.process_blocks(stage, x, 256)
    envelope = np.abs(y)
    # the burst starts at 0.2 s: within 3 ms the output is already held near -23 dBFS (8 dB reduction)
    t_on = int(0.2 * fs)
    settled = 10 ** ((-15.0 - 8.0) / 20.0)
    assert np.max(envelope[t_on + int(0.003 * fs):t_on + int(0.05 * fs)]) < settled * 10 ** (1.0 / 20.0)
    # after the burst the reduction recovers: 300 ms (5 tau) later the quiet tone is back within 0.3 dB
    t_off = int(0.5 * fs)
    quiet = 10 ** (-40.0 / 20.0)
    assert np.max(envelope[t_off + int(0.3 * fs):]) > quiet * 10 ** (-0.3 / 20.0)


def test_block_invariance(fs, gen):
    x, _ = gen.sibilance_bursts(-15.0, 1.5, fs, seed=3)
    a = gen.process_blocks(DeEsser(fs, **DEFAULT), x, 64)
    b = gen.process_blocks(DeEsser(fs, **DEFAULT), x, 4096)
    assert np.max(np.abs(a - b)) < 1e-9


def test_disabled_and_validation(fs, gen):
    x, _ = gen.sibilance_bursts(-15.0, 0.5, fs, seed=4)
    assert np.array_equal(DeEsser(fs, **{**DEFAULT, "enabled": False}).process(x), x)
    with pytest.raises(ValueError):
        DeEsser(fs, **{**DEFAULT, "freq_hz": 30000.0})
    with pytest.raises(ValueError):
        DeEsser(fs, **{**DEFAULT, "bandwidth_oct": 0.0})
    with pytest.raises(ValueError):
        DeEsser(fs, **{**DEFAULT, "ratio": 1.0})           # schema lower bound 1.1 (review SW-7)
    stage = DeEsser(fs, **DEFAULT)
    stage.set_param("freq_hz", 7000.0)
    assert stage.get_param("freq_hz") == 7000.0
    assert np.all(np.isfinite(stage.process(x)))
