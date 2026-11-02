"""Whole chain: contract of docs/00_Beauftragung.md 4.2/4.3 and end-to-end behaviour."""

import time

import numpy as np
import pytest
from scipy.signal import sosfilt

from voicechain.config import default_config_path, load_config
from voicechain.dsp import Chain, STAGE_ORDER
from voicechain.dsp.biquad import k_weighting_sos


@pytest.fixture
def cfg():
    return load_config(default_config_path())


@pytest.fixture
def chain(cfg):
    return Chain.from_config(cfg)


def _run(chain, x, block):
    return np.concatenate([chain.process(x[i:i + block]) for i in range(0, len(x), block)])


def test_from_config_and_stage_order(chain, cfg):
    assert tuple(chain.stages) == STAGE_ORDER
    assert chain.sample_rate == cfg["sample_rate"]
    assert chain.latency_samples == int(round(cfg["chain"]["limiter"]["lookahead_ms"] * 1e-3 * cfg["sample_rate"]))
    with pytest.raises(ValueError, match="chain"):
        Chain.from_config({"sample_rate": 48000})
    with pytest.raises(ValueError, match="chain.expander"):
        bad = {**cfg, "chain": {**cfg["chain"], "expander": {**cfg["chain"]["expander"], "ratio": 99.0}}}
        Chain.from_config(bad)


def test_output_dtype_and_shape(chain, fs, gen):
    x = gen.sine(1000.0, -20.0, 0.1, fs).astype(np.float32)
    y = chain.process(x)
    assert y.dtype == np.float32 and y.shape == x.shape
    with pytest.raises(ValueError):
        chain.process(np.zeros((2, 10), dtype=np.float32))


def test_block_size_invariance(cfg, fs, gen):
    x, _ = gen.speech_like(-20.0, 4.0, fs, seed=1)
    x = x.astype(np.float32)
    a = _run(Chain.from_config(cfg), x, 64)
    b = _run(Chain.from_config(cfg), x, 4096)
    assert np.max(np.abs(a - b)) <= 1e-5


def test_silence_in_silence_out(chain, fs):
    y = _run(chain, np.zeros(2 * fs, dtype=np.float32), 256)
    assert not np.any(np.isnan(y))
    assert np.max(np.abs(y)) < 1e-6
    m = chain.get_meters()
    assert m["output"]["peak_dbfs"] <= -150.0 and m["normalizer"]["gain_db"] == 0.0


def test_reset_restores_initial_state(cfg, fs, gen):
    x, _ = gen.speech_like(-20.0, 2.0, fs, seed=2)
    x = x.astype(np.float32)
    chain = Chain.from_config(cfg)
    first = _run(chain, x, 256)
    chain.reset()
    second = _run(chain, x, 256)
    assert np.array_equal(first, second)


def test_runtime_parameter_changes_do_not_jump(chain, fs, gen):
    x = gen.sine(1000.0, -20.0, 6.0, fs).astype(np.float32)
    block = 256
    changes = {
        400: ("compressor", "threshold_db", -21.0),
        500: ("eq", "bands.1.gain_db", 1.0),
        600: ("highpass", "freq_hz", 100.0),
        700: ("expander", "threshold_db", -42.0),
        800: ("deesser", "threshold_db", -35.0),
        900: ("limiter", "ceiling_dbfs", -3.0),
        1000: ("input_trim", "gain_db", 0.3),
    }
    levels, roughness = [], []
    for k, i in enumerate(range(0, len(x), block)):
        if k in changes:
            chain.set_param(*changes[k])
        y = chain.process(x[i:i + block]).astype(np.float64)
        levels.append(gen.rms_db(y))
        # click detector: largest second difference relative to the block RMS; for a clean
        # 1 kHz sine this ratio is a constant (omega^2 * sqrt(2)), a click raises it sharply
        roughness.append(np.max(np.abs(np.diff(y, n=2))) / np.sqrt(np.mean(y ** 2)))
    levels = np.array(levels[300:])          # skip the settling of the chain start
    assert np.max(np.abs(np.diff(levels))) < 0.5
    # A coefficient swap with retained filter states (EQ, HPF) leaves a transient of at most
    # about twice the sine's own curvature (~ -50 dB relative to the signal): no click.
    reference = np.max(roughness[300:400])   # before the first change
    assert np.max(roughness[300:]) < 3.0 * reference
    for _, (stage, name, value) in changes.items():
        assert chain.get_param(stage, name) == value


def test_meters_contain_all_contract_keys(chain, fs, gen):
    x, _ = gen.speech_like(-20.0, 1.0, fs, seed=3)
    _run(chain, x.astype(np.float32), 256)
    m = chain.get_meters()
    for stage in STAGE_ORDER:
        assert stage in m
    for stage in ("expander", "deesser", "compressor", "limiter"):
        assert "gain_reduction_db" in m[stage]
    assert "gain_db" in m["normalizer"]
    for side in ("input", "output"):
        assert set(m[side]) == {"peak_dbfs", "rms_dbfs"}
        assert -150.0 < m[side]["rms_dbfs"] <= m[side]["peak_dbfs"] <= 0.0


def test_get_set_param_errors(chain):
    with pytest.raises(KeyError):
        chain.get_param("reverb", "mix")
    with pytest.raises(KeyError):
        chain.set_param("expander", "colour", 1.0)
    with pytest.raises(ValueError):
        chain.set_param("expander", "threshold_db", 10.0)
    chain.set_param("expander", "threshold_db", -50.0)
    assert chain.get_param("expander", "threshold_db") == -50.0


def test_impulse_latency(chain, fs):
    x = np.zeros(fs, dtype=np.float32)
    x[1000] = 0.1
    y = _run(chain, x, 256)
    assert 1000 + chain.latency_samples <= int(np.argmax(np.abs(y))) <= 1000 + chain.latency_samples + 2


def test_end_to_end_speech(chain, fs, gen):
    x, active = gen.speech_like(-20.0, 20.0, fs, seed=4, noise_rms_dbfs=-55.0, talk_s=0.6, pause_s=1.0)
    y = _run(chain, x.astype(np.float32), 256).astype(np.float64)
    d = chain.latency_samples
    y, x, active = y[d:], x[:-d], active[:-d]
    # noise floor = 10th percentile of 100 ms frame RMS inside pauses (as the calibration measures it)
    frame = fs // 10

    def floor_db(sig):
        frames = sig[~active][: (len(sig[~active]) // frame) * frame].reshape(-1, frame)
        return np.percentile(10 * np.log10(np.mean(frames ** 2, axis=1)), 10)
    speech_to_floor_in = gen.rms_db(x[active]) - floor_db(x)
    speech_to_floor_out = gen.rms_db(y[active]) - floor_db(y)
    assert speech_to_floor_out - speech_to_floor_in >= 6.0
    assert gen.true_peak_dbfs(y) <= -1.0
    weighted = sosfilt(k_weighting_sos(fs), y)
    for start in range(14 * fs, 17 * fs, fs):
        short_term = -0.691 + 10 * np.log10(np.mean(weighted[start:start + 3 * fs] ** 2))
        assert abs(short_term + 18.0) <= 2.0


def test_runtime_budget(chain, fs, gen):
    x, _ = gen.speech_like(-20.0, 10.0, fs, seed=5)
    x = x.astype(np.float32)
    blocks = [x[i:i + 256] for i in range(0, len(x), 256)]
    chain.process(blocks[0])
    t0 = time.perf_counter()
    for b in blocks:
        chain.process(b)
    elapsed = time.perf_counter() - t0
    print(f"\nchain runtime: 10 s audio in {len(blocks)} blocks of 256 -> {elapsed:.3f} s "
          f"({elapsed / len(blocks) * 1e6:.0f} us/block, real-time factor {elapsed / 10:.3f})")
    assert elapsed < 2.0
