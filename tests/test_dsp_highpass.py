"""High-pass stage: attenuation figures, transparency at 1 kHz, run-time changes."""

import numpy as np
import pytest

from voicechain.dsp import HighPass

DEFAULT = dict(enabled=True, freq_hz=80.0, order=4)


def _attenuation_db(stage, freq, fs, gen):
    x = gen.sine(freq, -10.0, 2.0, fs)
    y = gen.process_blocks(stage, x, 256)
    return gen.rms_db(x[fs:]) - gen.rms_db(y[fs:])    # second half = settled


def test_50hz_attenuation_order4(fs, gen):
    att = _attenuation_db(HighPass(fs, **DEFAULT), 50.0, fs, gen)
    # Butterworth, 4th order, fc = 80 Hz: 10*log10(1 + (80/50)**8) = 16.4 dB (see docs/02)
    assert 16.0 <= att <= 17.0


def test_40hz_and_20hz_attenuation(fs, gen):
    assert _attenuation_db(HighPass(fs, **DEFAULT), 40.0, fs, gen) >= 20.0
    assert _attenuation_db(HighPass(fs, **DEFAULT), 20.0, fs, gen) >= 40.0


def test_1khz_unchanged(fs, gen):
    assert abs(_attenuation_db(HighPass(fs, **DEFAULT), 1000.0, fs, gen)) < 0.5


def test_20db_at_50hz_is_reachable_by_configuration(fs, gen):
    assert _attenuation_db(HighPass(fs, enabled=True, freq_hz=80.0, order=6), 50.0, fs, gen) >= 20.0
    assert _attenuation_db(HighPass(fs, enabled=True, freq_hz=90.0, order=4), 50.0, fs, gen) >= 20.0


def test_dc_is_removed(fs):
    y = HighPass(fs, **DEFAULT).process(np.full(fs, 0.5))
    assert np.max(np.abs(y[-fs // 10:])) < 1e-3


def test_block_invariance(fs, gen):
    x = gen.pink_noise(-20.0, 1.0, fs, seed=2)
    a = gen.process_blocks(HighPass(fs, **DEFAULT), x, 64)
    b = gen.process_blocks(HighPass(fs, **DEFAULT), x, 4096)
    assert np.max(np.abs(a - b)) < 1e-10


def test_disabled_is_passthrough(fs, gen):
    x = gen.pink_noise(-20.0, 0.2, fs, seed=3)
    assert np.array_equal(HighPass(fs, enabled=False, freq_hz=80.0, order=4).process(x), x)


def test_runtime_frequency_change_is_click_free(fs, gen):
    stage = HighPass(fs, **DEFAULT)
    x = gen.sine(1000.0, -10.0, 1.0, fs)
    block = 480                                              # exactly 10 periods: block RMS is constant
    levels = []
    for i in range(0, len(x), block):
        if i == len(x) // 2:
            stage.set_param("freq_hz", 120.0)
        y = stage.process(x[i:i + block])
        levels.append(gen.rms_db(y))
        if i > fs // 10:                                     # after the filter's own start-up transient
            assert np.max(np.abs(y)) <= 10 ** (-10.0 / 20.0) * 1.02
    assert np.max(np.abs(np.diff(levels[20:]))) < 0.1
    assert stage.get_param("freq_hz") == 120.0


def test_parameter_validation(fs):
    stage = HighPass(fs, **DEFAULT)
    with pytest.raises(ValueError):
        stage.set_param("freq_hz", 1000.0)      # above the allowed 400 Hz
    with pytest.raises(ValueError):
        HighPass(fs, enabled=True, freq_hz=80.0, order=4.5)
    with pytest.raises(KeyError):
        stage.set_param("slope", 24.0)
    with pytest.raises(ValueError):
        HighPass(fs, enabled=True, freq_hz=80.0)   # missing order
