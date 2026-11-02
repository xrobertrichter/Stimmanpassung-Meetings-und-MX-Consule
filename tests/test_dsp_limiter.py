"""Look-ahead limiter: hard ceiling, latency, transparency, true-peak margin."""

import numpy as np
import pytest

from voicechain.dsp import Limiter

DEFAULT = dict(enabled=True, ceiling_dbfs=-1.0, lookahead_ms=5.0, release_ms=50.0)
CEILING = 10 ** (-1.0 / 20.0)


def test_square_wave_never_exceeds_ceiling(fs, gen):
    x = np.sign(gen.sine(100.0, 0.0, 1.0, fs))                  # 0 dBFS square
    y = gen.process_blocks(Limiter(fs, **DEFAULT), x, 256)
    assert np.max(np.abs(y)) <= CEILING
    assert np.max(np.abs(y)) > 0.5                              # it is limiting, not muting


def test_impulses_never_exceed_ceiling(fs, gen):
    x = gen.sine(1000.0, -20.0, 1.0, fs)
    x[::int(0.1 * fs)] = 10 ** (6.0 / 20.0)                     # +6 dBFS single-sample spikes
    x[fs // 2 + 7] = -(10 ** (6.0 / 20.0))
    for block in (64, 256, 4096):
        y = gen.process_blocks(Limiter(fs, **DEFAULT), x, block)
        assert np.max(np.abs(y)) <= CEILING


def test_latency_equals_lookahead(fs, gen):
    stage = Limiter(fs, **DEFAULT)
    lookahead = int(round(5.0e-3 * fs))
    assert stage.latency_samples == lookahead
    x = np.zeros(fs)
    x[1000] = 0.1
    y = gen.process_blocks(stage, x, 256)
    assert np.argmax(np.abs(y)) == 1000 + lookahead
    assert abs(y[1000 + lookahead] - 0.1) < 1e-12


def test_quiet_signal_passes_unchanged(fs, gen):
    x = gen.sine(1000.0, -20.0, 1.0, fs)
    stage = Limiter(fs, **DEFAULT)
    y = gen.process_blocks(stage, x, 256)
    d = stage.latency_samples
    assert np.max(np.abs(y[d:] - x[:-d])) <= 0.1 * (10 ** (0.01 / 20.0) - 1.0)   # within 0.01 dB
    assert stage.meters()["gain_reduction_max_db"] == 0.0


def test_release_recovers_after_burst(fs, gen):
    x = gen.sine(1000.0, -20.0, 2.0, fs)
    x[fs // 2:fs // 2 + fs // 10] *= 10 ** (26.0 / 20.0)        # 100 ms at +6 dBFS
    stage = Limiter(fs, **DEFAULT)
    y = gen.process_blocks(stage, x, 256)
    d = stage.latency_samples
    assert np.max(np.abs(y)) <= CEILING
    t_end = fs // 2 + fs // 10 + d
    after = np.abs(y[t_end + int(0.3 * fs):t_end + int(0.4 * fs)])    # 6 release constants later
    assert np.max(after) >= 0.1 * 10 ** (-0.1 / 20.0)
    # recovery is gradual: 25 ms after the burst the gain is still clearly reduced
    assert np.max(np.abs(y[t_end + int(0.02 * fs):t_end + int(0.03 * fs)])) < 0.1 * 10 ** (-2.0 / 20.0)


def test_true_peak_margin_on_realistic_material(fs, gen):
    speech, _ = gen.speech_like(0.0, 4.0, fs, seed=1, noise_rms_dbfs=-60.0)
    pink = gen.pink_noise(-6.0, 4.0, fs, seed=2)
    for x in (speech, pink):
        y = gen.process_blocks(Limiter(fs, **DEFAULT), x, 256)
        assert gen.true_peak_dbfs(y) <= DEFAULT["ceiling_dbfs"]
        assert 20 * np.log10(np.max(np.abs(y))) <= DEFAULT["ceiling_dbfs"] - 1.5 + 1e-9
    y = gen.process_blocks(Limiter(fs, **{**DEFAULT, "true_peak_margin_db": 0.0}), speech, 256)
    assert gen.true_peak_dbfs(y) <= DEFAULT["ceiling_dbfs"] + 0.1       # speech itself hardly overshoots


def test_block_invariance(fs, gen):
    x, _ = gen.speech_like(-4.0, 2.0, fs, seed=3)
    a = gen.process_blocks(Limiter(fs, **DEFAULT), x, 64)
    b = gen.process_blocks(Limiter(fs, **DEFAULT), x, 4096)
    assert np.max(np.abs(a - b)) < 1e-9


def test_runtime_ceiling_change_and_disabled(fs, gen):
    x = np.sign(gen.sine(100.0, 0.0, 1.0, fs))
    stage = Limiter(fs, **DEFAULT)
    stage.process(x[:fs // 2])
    stage.set_param("ceiling_dbfs", -6.0)
    y = stage.process(x[fs // 2:])
    assert np.max(np.abs(y[fs // 10:])) <= 10 ** (-6.0 / 20.0)
    off = Limiter(fs, **{**DEFAULT, "enabled": False})
    assert np.array_equal(off.process(x), x)
    with pytest.raises(ValueError):
        stage.set_param("lookahead_ms", 100.0)
