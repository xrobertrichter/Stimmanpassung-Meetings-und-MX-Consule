"""Equalizer: measured frequency response per band type, transparency of zero-gain bands."""

import numpy as np
import pytest

from voicechain.dsp import Equalizer
from voicechain.dsp.biquad import sos_response_db

DEFAULT_BANDS = [
    {"type": "peak", "freq_hz": 220.0, "gain_db": -2.0, "q": 1.0},
    {"type": "peak", "freq_hz": 3200.0, "gain_db": 2.0, "q": 1.2},
    {"type": "high_shelf", "freq_hz": 10000.0, "gain_db": 0.0, "q": 0.707},   # 0 dB: the NT-USB is bright enough
]


def _measured_gain_db(stage, freq, fs, gen):
    x = gen.sine(freq, -20.0, 1.0, fs)
    y = gen.process_blocks(stage, x, 256)
    return gen.rms_db(y[fs // 2:]) - gen.rms_db(x[fs // 2:])


@pytest.mark.parametrize("band, probe_hz, expected_db", [
    ({"type": "peak", "freq_hz": 1000.0, "gain_db": 3.0, "q": 1.0}, 1000.0, 3.0),
    ({"type": "peak", "freq_hz": 250.0, "gain_db": -3.0, "q": 1.0}, 250.0, -3.0),
    ({"type": "low_shelf", "freq_hz": 200.0, "gain_db": 2.5, "q": 0.707}, 30.0, 2.5),
    ({"type": "high_shelf", "freq_hz": 8000.0, "gain_db": -2.0, "q": 0.707}, 20000.0, -2.0),
    ({"type": "peak", "freq_hz": 1000.0, "gain_db": 3.0, "q": 1.0}, 60.0, 0.0),          # 4 octaves away
])
def test_measured_response_matches_target(band, probe_hz, expected_db, fs, gen):
    stage = Equalizer(fs, enabled=True, bands=[band])
    assert abs(_measured_gain_db(stage, probe_hz, fs, gen) - expected_db) < 0.5
    assert abs(float(sos_response_db(stage.sos, probe_hz, fs)[0]) - expected_db) < 0.1


def test_default_bands_each_hit_their_target(fs, gen):
    stage = Equalizer(fs, enabled=True, bands=DEFAULT_BANDS)
    assert abs(_measured_gain_db(stage, 220.0, fs, gen) + 2.0) < 0.5
    assert abs(_measured_gain_db(stage, 3200.0, fs, gen) - 2.0) < 0.5
    assert abs(_measured_gain_db(stage, 20000.0, fs, gen)) < 0.5                 # shelf at 0 dB: transparent
    assert abs(_measured_gain_db(stage, 1000.0, fs, gen)) < 0.5


def test_zero_gain_bands_are_transparent(fs, gen):
    bands = [{**b, "gain_db": 0.0} for b in DEFAULT_BANDS]
    x = gen.pink_noise(-20.0, 0.5, fs, seed=1)
    y = Equalizer(fs, enabled=True, bands=bands).process(x)
    assert np.max(np.abs(y - x)) < 1e-12


def test_empty_band_list_and_disabled_are_passthrough(fs, gen):
    x = gen.pink_noise(-20.0, 0.2, fs, seed=2)
    assert np.array_equal(Equalizer(fs, enabled=True, bands=[]).process(x), x)
    assert np.array_equal(Equalizer(fs, enabled=False, bands=DEFAULT_BANDS).process(x), x)


def test_runtime_band_parameter_change(fs, gen):
    stage = Equalizer(fs, enabled=True, bands=DEFAULT_BANDS)
    assert stage.get_param("bands.1.gain_db") == 2.0
    stage.set_param("bands.1.gain_db", -1.0)
    assert abs(_measured_gain_db(stage, 3200.0, fs, gen) + 1.0) < 0.5
    assert stage.get_params()["bands.1.gain_db"] == -1.0
    stage.set_param("bands.2.gain_db", 1.5)
    assert abs(_measured_gain_db(stage, 20000.0, fs, gen) - 1.5) < 0.5
    with pytest.raises(KeyError):
        stage.set_param("bands.7.gain_db", 1.0)
    with pytest.raises(KeyError):
        stage.set_param("gain_db", 1.0)
    with pytest.raises(ValueError):
        stage.set_param("bands.0.gain_db", 40.0)


def test_band_validation(fs):
    with pytest.raises(ValueError, match="type"):
        Equalizer(fs, enabled=True, bands=[{"type": "notch", "freq_hz": 100.0, "gain_db": 0.0, "q": 1.0}])
    with pytest.raises(ValueError, match="missing key 'q'"):
        Equalizer(fs, enabled=True, bands=[{"type": "peak", "freq_hz": 100.0, "gain_db": 0.0}])
    with pytest.raises(ValueError, match="freq_hz"):
        Equalizer(fs, enabled=True, bands=[{"type": "peak", "freq_hz": 30000.0, "gain_db": 0.0, "q": 1.0}])
    with pytest.raises(ValueError, match="bands"):
        Equalizer(fs, enabled=True)
    with pytest.raises(ValueError, match="unknown"):
        Equalizer(fs, enabled=True, bands=[], slope=1)


def test_block_invariance(fs, gen):
    x = gen.pink_noise(-20.0, 1.0, fs, seed=3)
    a = gen.process_blocks(Equalizer(fs, enabled=True, bands=DEFAULT_BANDS), x, 64)
    b = gen.process_blocks(Equalizer(fs, enabled=True, bands=DEFAULT_BANDS), x, 4096)
    assert np.max(np.abs(a - b)) < 1e-10


def test_tilt_response_and_transparency(fs, gen):
    """Tilt +2 dB: about -1 dB at 100 Hz and +1 dB at 8 kHz, pivot around 1 kHz; 0 dB tilt is bit-exact."""
    stage = Equalizer(fs, enabled=True, bands=[], tilt_db=2.0)
    resp = sos_response_db(stage.sos, [100.0, 250.0, 1000.0, 4000.0, 8000.0], fs)
    assert abs(resp[0] + 1.0) < 0.3 and abs(resp[4] - 1.0) < 0.3          # analytic: -0.98 / +0.96 dB
    assert abs(resp[1] + 0.5) < 0.05 and abs(resp[3] - 0.5) < 0.05        # half gain at the shelf corners
    assert abs(resp[2]) < 0.05                                             # 1 kHz untouched
    assert abs(_measured_gain_db(stage, 100.0, fs, gen) + 1.0) < 0.3       # time-domain check
    assert abs(_measured_gain_db(stage, 8000.0, fs, gen) - 1.0) < 0.3
    warm = Equalizer(fs, enabled=True, bands=[], tilt_db=-2.0)
    assert abs(float(sos_response_db(warm.sos, 100.0, fs)[0]) - 1.0) < 0.3  # negative tilt = warmer
    x = gen.pink_noise(-20.0, 0.5, fs, seed=5)
    flat = Equalizer(fs, enabled=True, bands=DEFAULT_BANDS)                # tilt defaults to 0
    with_tilt_zero = Equalizer(fs, enabled=True, bands=DEFAULT_BANDS, tilt_db=0.0)
    assert flat.get_param("tilt_db") == 0.0
    assert np.array_equal(flat.process(x), with_tilt_zero.process(x))
    assert np.array_equal(Equalizer(fs, enabled=True, bands=[], tilt_db=0.0).process(x), x)   # exact identity
    assert flat.sos.shape == (len(DEFAULT_BANDS) + 2, 6)                   # the two shelves are always present


def test_tilt_change_at_runtime_is_click_free(fs, gen):
    """Switching the tilt 0 -> +3 -> -3 on a 1 kHz tone: no block level step > 0.5 dB, no transient."""
    stage = Equalizer(fs, enabled=True, bands=DEFAULT_BANDS)
    x = gen.sine(1000.0, -20.0, 3.0, fs)
    block = 480                                                            # exactly 10 periods per block
    levels, roughness = [], []
    for k, i in enumerate(range(0, len(x), block)):
        if k == 100:
            stage.set_param("tilt_db", 3.0)
        if k == 200:
            stage.set_param("tilt_db", -3.0)
        y = stage.process(x[i:i + block])
        levels.append(gen.rms_db(y))
        roughness.append(np.max(np.abs(np.diff(y, n=2))) / np.sqrt(np.mean(y ** 2)))
    assert np.max(np.abs(np.diff(levels[20:]))) < 0.5
    assert np.max(roughness[90:]) < 2.0 * np.max(roughness[20:90])
    assert stage.get_param("tilt_db") == -3.0 and stage.get_params()["tilt_db"] == -3.0
    with pytest.raises(ValueError):
        stage.set_param("tilt_db", 4.0)
    assert stage.get_param("tilt_db") == -3.0
    with pytest.raises(ValueError):
        Equalizer(fs, enabled=True, bands=[], tilt_db=-3.5)
