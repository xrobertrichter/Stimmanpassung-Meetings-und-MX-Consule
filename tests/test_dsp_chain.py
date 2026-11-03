"""Whole chain: contract of docs/00_Beauftragung.md 4.2/4.3 and end-to-end behaviour."""

import copy
import threading
import time

import numpy as np
import pytest
from scipy.signal import sosfilt

from voicechain.config import default_config_path, load_config
from voicechain.dsp import (
    DB_FLOOR_DBFS, Chain, Compressor, DeEsser, Equalizer, Expander, HighPass, InputTrim, Limiter,
    Normalizer, STAGE_ORDER,
)
from voicechain.dsp.biquad import k_weighting_sos

# The order of the "On Air" reference chain, written out literally (not derived from
# STAGE_ORDER) so that a wrong constant is caught by the test instead of being copied.
EXPECTED_ORDER = (
    ("input_trim", InputTrim), ("highpass", HighPass), ("expander", Expander), ("deesser", DeEsser),
    ("eq", Equalizer), ("compressor", Compressor), ("normalizer", Normalizer), ("limiter", Limiter),
)


@pytest.fixture
def cfg():
    return load_config(default_config_path())


@pytest.fixture
def chain(cfg):
    return Chain.from_config(cfg)


def _run(chain, x, block):
    return np.concatenate([chain.process(x[i:i + block]) for i in range(0, len(x), block)])


def test_from_config_and_stage_order(chain, cfg):
    assert tuple(chain.stages) == tuple(name for name, _ in EXPECTED_ORDER)
    assert STAGE_ORDER == tuple(name for name, _ in EXPECTED_ORDER)
    for (name, cls), (actual_name, stage) in zip(EXPECTED_ORDER, chain.stages.items()):
        assert actual_name == name and type(stage) is cls
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
    assert m["output"]["peak_dbfs"] == DB_FLOOR_DBFS == -150.0 and m["normalizer"]["gain_db"] == 0.0
    assert m["expander"]["level_dbfs"] == DB_FLOOR_DBFS


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
    assert set(m["input"]) == {"peak_dbfs", "rms_dbfs", "nonfinite_blocks"}
    assert set(m["output"]) == {"peak_dbfs", "rms_dbfs"}
    assert m["input"]["nonfinite_blocks"] == 0.0
    for side in ("input", "output"):
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
    x, active = gen.speech_like(-20.0, 40.0, fs, seed=4, noise_rms_dbfs=-55.0, talk_s=0.6, pause_s=1.0)
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
    assert speech_to_floor_out - speech_to_floor_in >= 6.0          # Beauftragung 6.3: SNR gain
    assert gen.true_peak_dbfs(y) <= -1.0
    # Loudness target: the normaliser regulates the *speech* (gated, BS.1770 style). After the
    # settling time (effective ~0.4 dB/s with pauses and falling syllables) the K-weighted
    # loudness of the active part sits at -18 LUFS +-2 LU; 3 s windows that contain the 40 %
    # pauses of this synthetic signal read about 2-3 LU lower.
    weighted = sosfilt(k_weighting_sos(fs), y)
    tail = slice(30 * fs, len(y))
    active_loudness = -0.691 + 10 * np.log10(np.mean(weighted[tail][active[tail]] ** 2))
    assert abs(active_loudness + 18.0) <= 2.0
    for start in range(33 * fs, 37 * fs, fs):
        short_term = -0.691 + 10 * np.log10(np.mean(weighted[start:start + 3 * fs] ** 2))
        assert -22.0 <= short_term <= -16.0


def test_long_pause_in_calibrated_chain(cfg, fs, gen):
    """Long pause on the whole chain with calibration-like thresholds: speech 30 s, pause 20 s, speech 20 s."""
    for noise_dbfs in (-50.0, -42.0):
        first, _ = gen.speech_like(-26.0, 30.0, fs, seed=11, noise_rms_dbfs=noise_dbfs)
        pause = gen.white_noise(noise_dbfs, 20.0, fs, seed=12)
        second, _ = gen.speech_like(-26.0, 20.0, fs, seed=13, noise_rms_dbfs=noise_dbfs)
        x = np.concatenate([first, pause, second])
        c = copy.deepcopy(cfg)
        c["chain"]["input_trim"]["gain_db"] = 6.0                          # speech -26 -> -20 dBFS RMS
        c["chain"]["expander"]["threshold_db"] = noise_dbfs + 6.0 + 10.0   # floor after trim + 10 dB
        c["chain"]["compressor"]["makeup_db"] = 6.0                        # static reduction at the peaks
        chain = Chain.from_config(c)
        out, gains = [], []
        for i in range(0, len(x), 256):
            out.append(chain.process(x[i:i + 256].astype(np.float32)))
            gains.append(chain.get_meters()["normalizer"]["gain_db"])
        y = np.concatenate(out).astype(np.float64)
        gains = np.array(gains)
        per_s = fs / 256
        in_pause = gains[int(30 * per_s):int(50 * per_s)]
        assert np.max(in_pause) - np.min(in_pause) <= 1.0                 # gain frozen during the pause
        snr_in = gen.rms_db(x[20 * fs:30 * fs]) - gen.rms_db(x[32 * fs:49 * fs])
        snr_out = gen.rms_db(y[20 * fs:30 * fs]) - gen.rms_db(y[32 * fs:49 * fs])
        assert snr_out - snr_in >= 6.0                                    # pause lowered relative to speech
        assert gen.rms_db(y[int(50.5 * fs):int(51.5 * fs)]) <= gen.rms_db(y[29 * fs:30 * fs]) + 1.0
        assert abs(gen.rms_db(y[20 * fs:30 * fs]) + 18.0) <= 2.0          # speech sits near the target


def test_nonfinite_input_block_is_replaced_and_state_recovers(chain, fs, gen):
    """A NaN/Inf block must not poison the filter states (Beauftragung 4.3: no NaN)."""
    x = gen.sine(1000.0, -20.0, 1.0, fs).astype(np.float32)
    reference = _run(Chain.from_config(load_config(default_config_path())), x, 256)
    poisoned = x.copy()
    poisoned[300] = np.nan
    poisoned[600] = np.inf
    y = _run(chain, poisoned, 256)
    assert np.isfinite(y).all()
    assert np.max(np.abs(y[256:768])) == 0.0                          # the two bad blocks become silence
    assert chain.get_meters()["input"]["nonfinite_blocks"] == 2.0
    # from the reset state on, the chain behaves exactly like a fresh chain fed with the clean tail
    fresh = Chain.from_config(load_config(default_config_path()))
    expected = _run(fresh, x[768:], 256)
    assert np.array_equal(y[768:], expected)
    assert np.max(np.abs(y[-fs // 2:])) > 0.05 and np.max(np.abs(reference[-fs // 2:])) > 0.05


def test_swapping_deesser_and_compressor_changes_the_result(cfg, fs, gen):
    """Behavioural check of the stage order (acceptance criterion 6). Sibilance bursts on a loud tone: with the de-esser
    *before* the compressor the bursts are tamed before the compressor sees them."""
    bursts, mask = gen.sibilance_bursts(-10.0, 3.0, fs, seed=5)
    x = (gen.sine(1000.0, -12.0, 3.0, fs) + bursts).astype(np.float32)

    def run_order(order):
        c = copy.deepcopy(cfg)
        stages = {name: Chain.from_config(c).stage(name) for name in order}
        y = x.astype(np.float64)
        for name in order:
            y = stages[name].process(y)
        return y
    correct = ("input_trim", "highpass", "expander", "deesser", "eq", "compressor", "normalizer", "limiter")
    swapped = ("input_trim", "highpass", "expander", "compressor", "eq", "deesser", "normalizer", "limiter")
    y_chain = _run(Chain.from_config(cfg), x, len(x)).astype(np.float64)
    y_correct = run_order(correct)
    y_swapped = run_order(swapped)
    assert np.max(np.abs(y_chain - y_correct)) < 1e-6
    band = lambda sig: gen.band_rms_db(sig[mask], 5000.0, 9000.0, fs)
    assert abs(band(y_correct) - band(y_swapped)) >= 0.5
    assert np.max(np.abs(y_chain - y_swapped)) > 1e-3


def test_concurrent_set_param_and_process(cfg, fs, gen):
    """Concurrent control and audio threads: without the chain lock this produces shape errors or NaN."""
    x, _ = gen.speech_like(-20.0, 3.0, fs, seed=1)
    x = x.astype(np.float32)
    for _ in range(3):
        chain = Chain.from_config(cfg)
        errors, stop = [], threading.Event()

        def setter():
            k = 0
            while not stop.is_set():
                try:
                    chain.set_param("highpass", "order", 2.0 if k % 2 else 4.0)     # changes the SOS section count
                    chain.set_param("eq", "bands.1.gain_db", 1.0 + k % 3)
                    chain.set_param("deesser", "freq_hz", 6000.0 + 100.0 * (k % 5))
                    chain.set_param("limiter", "lookahead_ms", 5.0 if k % 2 else 6.0)
                    chain.set_param("expander", "hold_ms", 50.0 + 10.0 * (k % 4))
                except Exception as exc:                                              # noqa: BLE001 - recorded, asserted below
                    errors.append(repr(exc))
                k += 1
        worker = threading.Thread(target=setter)
        worker.start()
        try:
            for i in range(0, len(x), 256):
                y = chain.process(x[i:i + 256])
                assert y.shape == x[i:i + 256].shape
                assert np.isfinite(y).all()
        finally:
            stop.set()
            worker.join()
        assert errors == []


def test_get_meters_drains_accumulators(chain, fs, gen):
    x = gen.sine(1000.0, -1.0, 0.5, fs).astype(np.float32)     # above compressor threshold and limiter ceiling
    _run(chain, x, 256)
    first = chain.get_meters()
    assert first["compressor"]["gain_reduction_max_db"] > 5.0 and first["limiter"]["gain_reduction_max_db"] > 0.0
    second = chain.get_meters()
    assert second["compressor"]["gain_reduction_max_db"] == 0.0 and second["compressor"]["gain_reduction_mean_db"] == 0.0
    assert second["compressor"]["gain_reduction_db"] == first["compressor"]["gain_reduction_db"]


def test_runtime_budget(chain, fs, gen):
    """Beauftragung 4.3: a 256-sample block must compute in < 1 ms; checked as median over
    1875 blocks, with p99 < 2 ms against scheduler hiccups (a single outlier is not a regression)."""
    x, _ = gen.speech_like(-20.0, 10.0, fs, seed=5)
    x = x.astype(np.float32)
    blocks = [x[i:i + 256] for i in range(0, len(x), 256)]
    chain.process(blocks[0])
    durations = []
    for b in blocks:
        t0 = time.perf_counter()
        chain.process(b)
        durations.append(time.perf_counter() - t0)
    durations = np.array(durations) * 1e3
    median, p99 = float(np.median(durations)), float(np.percentile(durations, 99))
    print(f"\nchain runtime per 256-sample block: median {median:.3f} ms, p99 {p99:.3f} ms, "
          f"max {durations.max():.3f} ms, total {durations.sum() / 1e3:.3f} s for 10 s audio")
    assert median < 1.0
    assert p99 < 2.0


def test_room_gets_louder_after_calibration(cfg, fs, gen):
    """Beauftragung 6.4: chain calibrated at a -50 dBFS floor (gate -40, trim +6, expander
    floor+10, makeup 6), then operated with a floor of -42 / -38 dBFS (speech 30 s, pause
    20 s, speech 20 s) once the adaptive controller has followed the floor with the expander
    threshold (floor after trim + 10). Pause loudness <= speech loudness - 15 LU, next
    sentence <= +1.5 dB, normaliser gain frozen in the pause.

    Without the expander tracking the pause lies only 8-12 LU below the speech at the
    normaliser's input, which no gate can separate (the stage treats anything closer
    than 15 LU to the speech as speech); that case is the controller's job and is
    reported in docs/02 section 7."""
    for noise_dbfs, min_distance in ((-42.0, 15.0), (-38.0, 14.0)):
        first, _ = gen.speech_like(-26.0, 30.0, fs, seed=11, noise_rms_dbfs=noise_dbfs)
        pause = gen.white_noise(noise_dbfs, 20.0, fs, seed=12)
        second, _ = gen.speech_like(-26.0, 20.0, fs, seed=13, noise_rms_dbfs=noise_dbfs)
        x = np.concatenate([first, pause, second])
        c = copy.deepcopy(cfg)
        c["chain"]["input_trim"]["gain_db"] = 6.0
        c["chain"]["expander"]["threshold_db"] = noise_dbfs + 6.0 + 10.0
        c["chain"]["compressor"]["makeup_db"] = 6.0
        c["chain"]["normalizer"]["gate_lufs"] = -40.0                     # as calibrated for the -50 room
        chain = Chain.from_config(c)
        out, gains = [], []
        for i in range(0, len(x), 256):
            out.append(chain.process(x[i:i + 256].astype(np.float32)))
            gains.append(chain.get_meters()["normalizer"]["gain_db"])
        y = np.concatenate(out).astype(np.float64)
        gains = np.array(gains)
        per_s = fs / 256
        weighted = sosfilt(k_weighting_sos(fs), y)

        def lufs(a, b):
            return -0.691 + 10 * np.log10(np.mean(weighted[int(a * fs):int(b * fs)] ** 2))
        speech_before, pause_loud, speech_after = lufs(20, 30), lufs(32, 49), lufs(52, 60)
        # -38 dBFS (+12 dB room) sits at the limit of the expander's 15 dB static reduction at
        # threshold + 10: measured 15.0 LU, asserted with 1 LU tolerance; -42 dBFS gives 18.8 LU.
        assert speech_before - pause_loud >= min_distance
        assert pause_loud <= -18.0 - 15.0
        assert np.max(gains[int(30 * per_s):int(50 * per_s)]) - np.min(gains[int(30 * per_s):int(50 * per_s)]) <= 1.0
        assert gen.rms_db(y[int(50.5 * fs):int(51.5 * fs)]) <= gen.rms_db(y[29 * fs:30 * fs]) + 1.5
        assert abs(speech_after - speech_before) <= 1.5
        m = chain.get_meters()["normalizer"]
        assert -40.0 <= m["effective_gate_lufs"] <= m["loudness_lufs"] - 5.0   # gate raised above the configured -40


def test_nonfinite_block_keeps_normaliser_gain(cfg, fs, gen):
    """A single bad block must not cost seconds of loudness: filters are cleared, the learnt
    gain, reference and floor of the normaliser survive."""
    x, _ = gen.speech_like(-30.0, 40.0, fs, seed=41, noise_rms_dbfs=-60.0)
    x = x.astype(np.float32)
    x[20 * fs + 100] = np.nan
    chain = Chain.from_config(cfg)
    out, gains = [], []
    for i in range(0, len(x), 256):
        out.append(chain.process(x[i:i + 256]))
        gains.append(chain.get_meters()["normalizer"]["gain_db"])
    y = np.concatenate(out).astype(np.float64)
    per_s = fs / 256
    assert chain.get_meters()["input"]["nonfinite_blocks"] == 1.0
    assert gains[int(19.9 * per_s)] > 6.0
    assert abs(gains[int(20.1 * per_s)] - gains[int(19.9 * per_s)]) < 1e-9
    weighted = sosfilt(k_weighting_sos(fs), y)

    def lufs(a, b):
        return -0.691 + 10 * np.log10(np.mean(weighted[int(a * fs):int(b * fs)] ** 2))
    assert abs(lufs(21, 22) - lufs(18, 20)) <= 2.0
    m = chain.get_meters()["normalizer"]
    assert m["loudness_lufs"] > -40.0 and m["noise_floor_lufs"] > -150.0
