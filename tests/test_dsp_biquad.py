"""Coefficient designs (RBJ, Butterworth, BS.1770 K-weighting) and the stateful SOS filter."""

import ast
import pathlib

import numpy as np
import pytest

from voicechain.dsp.biquad import (
    SosFilter, bandpass_sos, bandwidth_to_q, butter_highpass_sos, high_shelf_sos,
    k_weighting_sos, low_shelf_sos, peak_sos, sos_response_db,
)

# ITU-R BS.1770-4, Table 1 and Table 2 (48 kHz)
BS1770_PRE_48K = [1.53512485958697, -2.69169618940638, 1.19839281085285, 1.0, -1.69065929318241, 0.73248077421585]
BS1770_RLB_48K = [1.0, -2.0, 1.0, 1.0, -1.99004745483398, 0.99007225036621]


def test_k_weighting_matches_bs1770_table_at_48k(fs):
    sos = k_weighting_sos(fs)
    assert sos.shape == (2, 6)
    np.testing.assert_allclose(sos[0], BS1770_PRE_48K, atol=1e-9)
    np.testing.assert_allclose(sos[1], BS1770_RLB_48K, atol=1e-9)


def test_k_weighting_gain_at_1khz_matches_loudness_offset(fs):
    # BS.1770: a full-scale 1 kHz sine reads -3.01 LUFS = -0.691 + 10*log10(0.5 * |H(1k)|^2)
    gain = float(sos_response_db(k_weighting_sos(fs), 1000.0, fs)[0])
    assert abs(gain - 0.691) < 0.01


@pytest.mark.parametrize("rate", [44100, 96000])
def test_k_weighting_shape_at_other_rates(rate):
    sos = k_weighting_sos(rate)
    # +4 dB shelf at high frequencies, ~0 dB around 1 kHz is slightly positive, strong roll-off below 40 Hz
    assert abs(float(sos_response_db(sos, 10000.0, rate)[0]) - 3.9) < 0.3
    assert float(sos_response_db(sos, 20.0, rate)[0]) < -9.0


def test_peak_filter_response(fs):
    sos = peak_sos(1000.0, 6.0, 1.0, fs)
    resp = sos_response_db(sos, [1000.0 / 16, 1000.0, 16000.0], fs)
    assert abs(resp[1] - 6.0) < 0.01
    assert abs(resp[0]) < 0.05 and abs(resp[2]) < 0.05


def test_low_shelf_response(fs):
    sos = low_shelf_sos(200.0, 4.0, 0.707, fs)
    resp = sos_response_db(sos, [20.0, 200.0, 10000.0], fs)
    assert abs(resp[0] - 4.0) < 0.01          # full gain well below f0
    assert abs(resp[1] - 2.0) < 0.01          # half the dB gain at f0 (RBJ definition)
    assert abs(resp[2]) < 0.01                 # untouched above


def test_high_shelf_response(fs):
    sos = high_shelf_sos(10000.0, 3.0, 0.707, fs)
    resp = sos_response_db(sos, [100.0, 10000.0, 22000.0], fs)
    assert abs(resp[0]) < 0.01
    assert abs(resp[1] - 1.5) < 0.01
    assert abs(resp[2] - 3.0) < 0.01


def test_bandpass_response_and_bandwidth(fs):
    f0 = 6500.0
    sos = bandpass_sos(f0, bandwidth_to_q(1.0, f0, fs), fs)
    resp = sos_response_db(sos, [f0 / 2 ** 0.5, f0, f0 * 2 ** 0.5, 1000.0], fs)
    assert abs(resp[1]) < 1e-6                           # exactly 0 dB at the centre
    assert -3.5 < resp[0] < -2.5 and -3.5 < resp[2] < -2.5   # ~-3 dB at +-1/2 octave
    assert resp[3] < -15.0                                 # 2.7 octaves below: strongly rejected


def test_bandwidth_to_q(fs):
    assert abs(bandwidth_to_q(1.0, 100.0, fs) - 2 ** 0.5 / 1.0) < 0.01  # 1 octave -> Q = 1.414 at low f
    assert bandwidth_to_q(1.0, 6500.0, fs) < 2 ** 0.5                   # frequency warping lowers Q
    with pytest.raises(ValueError):
        bandwidth_to_q(0.0, 1000.0, fs)


def test_butter_highpass_response(fs):
    sos = butter_highpass_sos(80.0, 4, fs)
    assert sos.shape == (2, 6)
    resp = sos_response_db(sos, [20.0, 50.0, 80.0, 1000.0], fs)
    assert abs(resp[2] + 3.01) < 0.02
    assert abs(resp[1] + 16.43) < 0.2       # 10*log10(1 + (80/50)**8)
    assert resp[0] < -48.0
    assert abs(resp[3]) < 0.01


@pytest.mark.parametrize("design", [
    lambda fs: peak_sos(1000.0, 0.0, 1.0, fs),
    lambda fs: low_shelf_sos(200.0, 0.0, 0.7, fs),
    lambda fs: high_shelf_sos(8000.0, 0.0, 0.7, fs),
])
def test_zero_gain_designs_are_identity(design, fs):
    sos = design(fs)
    np.testing.assert_allclose(sos[0, :3], sos[0, 3:], atol=1e-15)


def test_invalid_designs_raise(fs):
    with pytest.raises(ValueError):
        peak_sos(fs / 2, 3.0, 1.0, fs)
    with pytest.raises(ValueError):
        peak_sos(1000.0, 3.0, 0.0, fs)
    with pytest.raises(ValueError):
        butter_highpass_sos(80.0, 0, fs)


def test_sosfilter_block_invariance(fs, gen):
    x = gen.white_noise(-20.0, 1.0, fs, seed=5)
    reference = SosFilter(peak_sos(1000.0, 6.0, 1.0, fs)).process(x)
    filt = SosFilter(peak_sos(1000.0, 6.0, 1.0, fs))
    rng = np.random.default_rng(1)
    pos, parts = 0, []
    while pos < len(x):
        n = int(rng.integers(1, 700))
        parts.append(filt.process(x[pos:pos + n]))
        pos += n
    assert np.max(np.abs(np.concatenate(parts) - reference)) < 1e-12


def test_sosfilter_set_sos_keeps_state(fs, gen):
    x = gen.sine(1000.0, -10.0, 0.5, fs)
    sos = butter_highpass_sos(80.0, 4, fs)
    continuous = SosFilter(sos).process(x)
    filt = SosFilter(sos)
    first = filt.process(x[:1000])
    filt.set_sos(butter_highpass_sos(80.0, 4, fs))     # same design re-set at run time
    second = filt.process(x[1000:])
    assert np.array_equal(np.concatenate([first, second]), continuous)
    filt.set_sos(butter_highpass_sos(80.0, 2, fs))     # different section count -> fresh state
    assert filt.process(np.zeros(10)).max() == 0.0


def test_biquad_is_a_leaf_module_with_public_k_weighting():
    """SW-10: meters/calibration import the K-weighting from here, so biquad must not import voicechain."""
    import voicechain.dsp.biquad as biquad
    source = pathlib.Path(biquad.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("voicechain")
        if isinstance(node, ast.Import):
            assert not any(alias.name.startswith("voicechain") for alias in node.names)
    assert callable(biquad.k_weighting_sos) and biquad.k_weighting_sos(48000).shape == (2, 6)
