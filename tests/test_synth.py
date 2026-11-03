"""Tests for voicechain.synth (deterministic speech-like and keyboard generators)."""
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


def test_long_pause_is_placed_and_silent():
    x = synth.make_speech_like(60.0, SR, noise_floor_dbfs=-50.0, seed=8, long_pause_s=6.0, long_pause_at_s=20.0)
    pause = x[20 * SR: 26 * SR]
    assert meters.rms_dbfs(pause) == pytest.approx(-50.0, abs=0.5)
    # Speech on both sides of the pause.
    assert meters.rms_dbfs(x[14 * SR: 20 * SR]) > -35.0
    assert meters.rms_dbfs(x[26 * SR: 32 * SR]) > -35.0
    # Default placement is the middle of the part after the lead silence.
    y = synth.make_speech_like(60.0, SR, seed=8, lead_silence_s=10.0, long_pause_s=6.0)
    assert meters.longest_pause(y, SR, 5.0) == pytest.approx((32.0, 38.0), abs=0.3)
    with pytest.raises(ValueError):
        synth.make_speech_like(20.0, SR, long_pause_s=6.0, long_pause_at_s=16.0)


def test_pitch_range_changes_the_fundamental():
    low = synth.make_speech_like(20.0, SR, seed=4, pitch_range_hz=(100.0, 140.0))
    high = synth.make_speech_like(20.0, SR, seed=4, pitch_range_hz=(190.0, 250.0))
    f_low, _ = meters.fundamental_frequency_hz(low, SR)
    f_high, _ = meters.fundamental_frequency_hz(high, SR)
    assert 100.0 <= f_low <= 140.0
    assert 190.0 <= f_high <= 250.0


def test_sibilance_setting_changes_high_band_energy():
    low = synth.make_speech_like(20.0, SR, sibilance_db=-20.0, seed=2)
    high = synth.make_speech_like(20.0, SR, sibilance_db=0.0, seed=2)
    band = [5000.0, 10000.0]
    e_low = meters.band_energies_db(low, SR, band)[0]
    e_high = meters.band_energies_db(high, SR, band)[0]
    assert e_high - e_low > 10.0


def test_keyboard_clicks_statistics():
    x = synth.make_keyboard_clicks(60.0, SR, rate_per_s=3.0, level_dbfs=-30.0, noise_floor_dbfs=-55.0, seed=1)
    assert x.dtype == np.float32 and x.shape == (60 * SR,)
    assert np.array_equal(x, synth.make_keyboard_clicks(60.0, SR, seed=1))
    assert meters.noise_floor_dbfs(x, SR) == pytest.approx(-55.0, abs=1.0)
    # 3 clicks of 4-10 ms per second occupy 3 of 20 frames per second (~15 %),
    # each reading ~10 dB below its own RMS in a 50 ms frame.
    assert 0.10 <= meters.speech_activity_ratio(x, SR) <= 0.22
    assert -43.0 <= meters.speech_level_dbfs(x, SR) <= -35.0
    with pytest.raises(ValueError):
        synth.make_keyboard_clicks(10.0, SR, rate_per_s=0.0)


def test_invalid_arguments_raise():
    with pytest.raises(ValueError):
        synth.make_speech_like(5.0, SR, pause_ratio=0.95)
    with pytest.raises(ValueError):
        synth.make_speech_like(5.0, SR, lead_silence_s=5.0)
    with pytest.raises(ValueError):
        synth.make_speech_like(5.0, SR, level_dbfs=0.0)  # would clip
    with pytest.raises(ValueError):
        synth.make_speech_like(5.0, SR, pitch_range_hz=(200.0, 100.0))


def test_write_sample_wav_creates_48k_16bit_mono_file_with_lead_and_pause(tmp_path):
    path = synth.write_sample_wav(tmp_path / "sub" / "sample.wav", duration_s=40.0, seed=0,
                                  lead_silence_s=5.0, long_pause_s=6.0)
    assert path.exists()
    info = sf.info(str(path))
    assert info.samplerate == 48000
    assert info.channels == 1
    assert info.subtype == "PCM_16"
    assert info.frames == 40 * 48000
    x, sr = sf.read(str(path), dtype="float32")
    assert meters.noise_floor_dbfs(x, sr) == pytest.approx(-48.0, abs=1.0)
    # lead 5 s + (40 - 5 - 6) / 2 = 19.5 s
    assert meters.longest_pause(x, sr, 5.0) == pytest.approx((19.5, 25.5), abs=0.3)


def test_write_sample_wav_defaults_contain_a_6s_pause_in_the_middle(tmp_path):
    path = synth.write_sample_wav(tmp_path / "default.wav")
    x, sr = sf.read(str(path), dtype="float32")
    assert x.shape[0] == 60 * 48000
    assert meters.longest_pause(x, sr, 5.0) == pytest.approx((32.0, 38.0), abs=0.3)
