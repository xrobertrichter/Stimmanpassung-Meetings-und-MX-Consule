"""Tests for voicechain.synth (deterministic speech-like generator)."""
import numpy as np
import pytest
import soundfile as sf

from voicechain import meters, synth

SR = 48000


def test_is_deterministic_per_seed():
    a = synth.make_speech_like(5.0, SR, seed=1)
    b = synth.make_speech_like(5.0, SR, seed=1)
    c = synth.make_speech_like(5.0, SR, seed=2)
    assert np.array_equal(a, b)
    assert np.max(np.abs(a - c)) > 0.01


def test_shape_dtype_and_headroom():
    x = synth.make_speech_like(7.5, SR, seed=0)
    assert x.shape == (int(7.5 * SR),)
    assert x.dtype == np.float32
    assert np.max(np.abs(x)) < 1.0
    assert np.all(np.isfinite(x))


def test_levels_match_requested_values():
    x = synth.make_speech_like(40.0, SR, level_dbfs=-24.0, noise_floor_dbfs=-52.0, seed=3)
    assert meters.noise_floor_dbfs(x, SR) == pytest.approx(-52.0, abs=1.0)
    assert meters.speech_level_dbfs(x, SR) == pytest.approx(-24.0, abs=1.5)


def test_pause_ratio_is_respected():
    x = synth.make_speech_like(60.0, SR, pause_ratio=0.4, noise_floor_dbfs=-60.0, seed=6)
    levels = meters.frame_rms_db(x, SR, 100.0)
    floor = np.percentile(levels, 10)
    pause_share = float(np.mean(levels < floor + 6.0))
    assert pause_share == pytest.approx(0.4, abs=0.1)


def test_lead_silence_contains_only_noise():
    x = synth.make_speech_like(30.0, SR, noise_floor_dbfs=-50.0, lead_silence_s=10.0, seed=8)
    lead = x[: 10 * SR]
    assert meters.rms_dbfs(lead) == pytest.approx(-50.0, abs=0.5)
    assert meters.rms_dbfs(x[10 * SR:]) > -35.0


def test_sibilance_setting_changes_high_band_energy():
    low = synth.make_speech_like(20.0, SR, sibilance_db=-20.0, seed=2)
    high = synth.make_speech_like(20.0, SR, sibilance_db=0.0, seed=2)
    band = [5000.0, 10000.0]
    e_low = meters.band_energies_db(low, SR, band)[0]
    e_high = meters.band_energies_db(high, SR, band)[0]
    assert e_high - e_low > 10.0


def test_invalid_arguments_raise():
    with pytest.raises(ValueError):
        synth.make_speech_like(5.0, SR, pause_ratio=0.95)
    with pytest.raises(ValueError):
        synth.make_speech_like(5.0, SR, lead_silence_s=5.0)
    with pytest.raises(ValueError):
        synth.make_speech_like(5.0, SR, level_dbfs=0.0)  # would clip


def test_write_sample_wav_creates_48k_16bit_mono_file(tmp_path):
    path = synth.write_sample_wav(tmp_path / "sub" / "sample.wav", duration_s=20.0, seed=0,
                                  lead_silence_s=5.0)
    assert path.exists()
    info = sf.info(str(path))
    assert info.samplerate == 48000
    assert info.channels == 1
    assert info.subtype == "PCM_16"
    assert info.frames == 20 * 48000
    x, sr = sf.read(str(path), dtype="float32")
    assert meters.noise_floor_dbfs(x, sr) == pytest.approx(-48.0, abs=1.0)
