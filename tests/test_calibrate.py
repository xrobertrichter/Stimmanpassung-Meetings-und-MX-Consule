"""Tests for voicechain.calibrate (analysis, recommendation, evaluation, report)."""
import copy
import math

import numpy as np
import pytest
import soundfile as sf

from voicechain import calibrate as cal
from voicechain import meters, synth

SR = 48000


@pytest.fixture(scope="module")
def quiet_sample():
    """-26 dBFS speech, -50 dBFS floor, pronounced sibilance (+3 dB bursts)."""
    return synth.make_speech_like(60.0, SR, level_dbfs=-26.0, noise_floor_dbfs=-50.0,
                                  sibilance_db=3.0, pause_ratio=0.3, seed=3)


@pytest.fixture(scope="module")
def noisy_sample():
    """-26 dBFS speech, -40 dBFS floor (SNR 14 dB), mild sibilance."""
    return synth.make_speech_like(60.0, SR, level_dbfs=-26.0, noise_floor_dbfs=-40.0,
                                  sibilance_db=-6.0, pause_ratio=0.3, seed=3)


def bounds(cfg, stage, name):
    lo, hi = cfg["adaptive"]["bounds"][f"{stage}.{name}"]
    return lo, hi


# --------------------------------------------------------------------------- analyze

def test_analyze_reports_generated_values(quiet_sample):
    m = cal.analyze(quiet_sample, SR)
    assert m["duration_s"] == pytest.approx(60.0)
    assert m["noise_floor_dbfs"] == pytest.approx(-50.0, abs=1.5)
    assert m["speech_level_dbfs"] == pytest.approx(-26.0, abs=1.5)
    assert m["snr_db"] == pytest.approx(24.0, abs=2.0)
    assert m["speech_detected"] is True
    assert m["sibilance_ratio_db"] > cal.SIBILANCE_REFERENCE_DB
    assert 5000.0 <= m["sibilance_centroid_hz"] <= 9000.0
    assert m["true_peak_dbtp"] >= m["sample_peak_dbfs"]
    assert set(m["eq_bands"]) == {"low_shelf@120", "peak@250", "peak@3000", "high_shelf@10000"}
    assert m["eq_anchor"]["lo_hz"] == 350.0


def test_analyze_rejects_too_short_sample():
    with pytest.raises(ValueError, match="10 s"):
        cal.analyze(synth.make_speech_like(5.0, SR, seed=0), SR)


# --------------------------------------------------------------------------- recommend

def test_recommend_on_quiet_sample_sets_trim_and_expander(quiet_sample):
    m = cal.analyze(quiet_sample, SR)
    cfg = cal.recommend(m, cal.DEFAULT_CONFIG)
    trim = cfg["chain"]["input_trim"]["gain_db"]
    assert trim == pytest.approx(6.0, abs=1.5)
    floor_after = m["noise_floor_dbfs"] + trim
    speech_after = m["speech_level_dbfs"] + trim
    thr = cfg["chain"]["expander"]["threshold_db"]
    assert thr == pytest.approx(floor_after + 8.0, abs=2.0)
    assert thr <= speech_after - 12.0 + 1e-6
    assert "Rauschteppich + 8" in cfg["_calibration"]["reasons"]["chain.expander.threshold_db"]


def test_recommend_on_noisy_sample_caps_expander_below_speech(noisy_sample):
    m = cal.analyze(noisy_sample, SR)
    cfg = cal.recommend(m, cal.DEFAULT_CONFIG)
    trim = cfg["chain"]["input_trim"]["gain_db"]
    assert trim == pytest.approx(6.0, abs=1.5)
    speech_after = m["speech_level_dbfs"] + trim
    thr = cfg["chain"]["expander"]["threshold_db"]
    # floor + trim + 8 = -26 would be only 6 dB under speech -> the speech rule wins.
    assert thr == pytest.approx(speech_after - 12.0, abs=0.5)
    assert thr < m["noise_floor_dbfs"] + trim + 8.0 - 2.0
    assert "Sprachpegel" in cfg["_calibration"]["reasons"]["chain.expander.threshold_db"]


def test_recommend_compressor_threshold_and_makeup(quiet_sample):
    m = cal.analyze(quiet_sample, SR)
    cfg = cal.recommend(m, cal.DEFAULT_CONFIG)
    comp = cfg["chain"]["compressor"]
    trim = cfg["chain"]["input_trim"]["gain_db"]
    speech_after = m["speech_level_dbfs"] + trim
    assert speech_after == pytest.approx(-20.0, abs=0.01)
    assert comp["threshold_db"] == pytest.approx(-20.0, abs=0.1)
    # Syllable peaks of the synthetic speech lie ~10 dB above the RMS; at 3:1 with
    # a 6 dB knee the peak-detector compressor removes ~6.4 dB there -> makeup.
    assert 8.0 <= m["speech_crest_db"] <= 12.0
    expected = cal.static_compressor_reduction_db(m["speech_peak_level_dbfs"] + trim, -20.0, 3.0, 6.0)
    assert comp["makeup_db"] == pytest.approx(expected, abs=0.05)
    assert 5.0 <= comp["makeup_db"] <= 8.0
    assert cal.static_compressor_reduction_db(-10.0, -20.0, 3.0, 0.0) == pytest.approx(10.0 * 2 / 3)
    assert cal.static_compressor_reduction_db(-30.0, -20.0, 3.0, 6.0) == 0.0
    assert cfg["chain"]["normalizer"]["target_lufs"] == -18.0
    assert cfg["chain"]["limiter"]["ceiling_dbfs"] == -1.0


def test_deesser_engages_only_above_reference():
    default_thr = cal.DEFAULT_CONFIG["chain"]["deesser"]["threshold_db"]
    lo, hi = bounds(cal.DEFAULT_CONFIG, "deesser", "threshold_db")

    def computed(m):
        return (-20.0 + m["sibilance_ratio_db"] + cal.SIBILANCE_BURST_CREST_DB
                - cal.DEESSER_MIN_REDUCTION_DB / (1 - 1 / 4.0))

    # Soft voice (sibilance ratio well below -15 dB): default threshold stays.
    soft = cal.analyze(synth.make_speech_like(40.0, SR, sibilance_db=-20.0, seed=4), SR)
    assert soft["sibilance_ratio_db"] < cal.SIBILANCE_REFERENCE_DB
    cfg_q = cal.recommend(soft, cal.DEFAULT_CONFIG)
    assert cfg_q["chain"]["deesser"]["threshold_db"] == default_thr
    assert "nicht über der Referenz" in cfg_q["_calibration"]["reasons"]["chain.deesser.threshold_db"]

    # Mildly sibilant voice (ratio between -15 and about -11 dB): the burst level
    # would not reach the default threshold, so the threshold is lowered.
    mild = cal.analyze(synth.make_speech_like(40.0, SR, sibilance_db=-4.0, seed=4), SR)
    assert cal.SIBILANCE_REFERENCE_DB < mild["sibilance_ratio_db"] < -11.0
    cfg_m = cal.recommend(mild, cal.DEFAULT_CONFIG)
    thr_m = cfg_m["chain"]["deesser"]["threshold_db"]
    assert thr_m == pytest.approx(cal.clamp(computed(mild), lo, hi), abs=0.05)
    assert thr_m < default_thr
    assert "rechnerisch" in cfg_m["_calibration"]["reasons"]["chain.deesser.threshold_db"]

    # Strongly sibilant voice: the default threshold already yields > 4 dB
    # reduction; raising it would de-ess less, so it is kept.
    strong = cal.analyze(synth.make_speech_like(40.0, SR, sibilance_db=3.0, seed=4), SR)
    assert strong["sibilance_ratio_db"] > -8.0
    assert computed(strong) > default_thr
    cfg_s = cal.recommend(strong, cal.DEFAULT_CONFIG)
    assert cfg_s["chain"]["deesser"]["threshold_db"] == default_thr
    assert "bereits tiefer" in cfg_s["_calibration"]["reasons"]["chain.deesser.threshold_db"]
    assert 5000.0 <= cfg_s["chain"]["deesser"]["freq_hz"] <= 9000.0


def test_eq_cuts_a_bass_heavy_recording_and_keeps_gain_limits():
    x = synth.make_speech_like(40.0, SR, seed=5).astype(np.float64)
    t = np.arange(x.size) / SR
    # Strong 130 Hz component (proximity effect / rumble) at -18 dBFS RMS.
    x += 10 ** (-18 / 20) * math.sqrt(2) * np.sin(2 * math.pi * 130.0 * t)
    cfg = cal.recommend(cal.analyze(x, SR), cal.DEFAULT_CONFIG)
    gains = {f"{b['type']}@{b['freq_hz']:.0f}": b["gain_db"] for b in cfg["chain"]["eq"]["bands"]}
    assert gains["low_shelf@120"] <= -2.0
    assert gains["low_shelf@120"] >= -cal.EQ_MAX_GAIN_DB
    for g in gains.values():
        assert -cal.EQ_MAX_GAIN_DB <= g <= cal.EQ_MAX_GAIN_DB


def test_recommend_appends_missing_eq_bands_and_keeps_others():
    base = copy.deepcopy(cal.DEFAULT_CONFIG)
    base["chain"]["eq"]["bands"] = [{"type": "peak", "freq_hz": 800.0, "gain_db": 2.0, "q": 1.0}]
    cfg = cal.recommend(cal.analyze(synth.make_speech_like(30.0, SR, seed=1), SR), base)
    bands = cfg["chain"]["eq"]["bands"]
    assert bands[0] == {"type": "peak", "freq_hz": 800.0, "gain_db": 2.0, "q": 1.0}
    assert len(bands) == 5
    assert base["chain"]["eq"]["bands"] == [{"type": "peak", "freq_hz": 800.0, "gain_db": 2.0, "q": 1.0}]


def test_eq_correction_is_added_to_base_voicing_and_labelled_by_actual_frequency():
    """Base bands at 220/3200/10000 Hz (as in config/chain.default.yaml) keep their
    voicing; the measured correction is added and the result stays within +-4 dB."""
    base = copy.deepcopy(cal.DEFAULT_CONFIG)
    base["chain"]["eq"]["bands"] = [
        {"type": "peak", "freq_hz": 220.0, "gain_db": -2.0, "q": 1.0},
        {"type": "peak", "freq_hz": 3200.0, "gain_db": 2.0, "q": 1.2},
        {"type": "high_shelf", "freq_hz": 10000.0, "gain_db": 3.5, "q": 0.707},
    ]
    flat = copy.deepcopy(base)
    for band in flat["chain"]["eq"]["bands"]:
        band["gain_db"] = 0.0
    x = synth.make_speech_like(30.0, SR, seed=5).astype(np.float64)
    t = np.arange(x.size) / SR
    x += 10 ** (-18 / 20) * math.sqrt(2) * np.sin(2 * math.pi * 130.0 * t)   # bass-heavy
    m = cal.analyze(x, SR)
    voiced = {f"{b['type']}@{b['freq_hz']:.0f}": b["gain_db"]
              for b in cal.recommend(m, base)["chain"]["eq"]["bands"]}
    plain = {f"{b['type']}@{b['freq_hz']:.0f}": b["gain_db"]
             for b in cal.recommend(m, flat)["chain"]["eq"]["bands"]}
    assert set(voiced) == {"peak@220", "peak@3200", "high_shelf@10000", "low_shelf@120"}
    # No flat band is created at 250/3000 Hz because 220/3200 Hz lie within 20 %.
    reasons = cal.recommend(m, base)["_calibration"]["reasons"]
    assert "chain.eq.peak@220.gain_db" in reasons and "chain.eq.peak@3200.gain_db" in reasons
    assert "chain.eq.low_shelf@120.gain_db" in reasons and "neues Band" in reasons["chain.eq.low_shelf@120.gain_db"]
    assert "Basiswert +2.0 dB" in reasons["chain.eq.peak@3200.gain_db"]
    # Same measured correction in both runs; the base voicing is added on top ...
    assert voiced["peak@220"] == pytest.approx(plain["peak@220"] - 2.0, abs=0.011)
    assert voiced["peak@3200"] == pytest.approx(plain["peak@3200"] + 2.0, abs=0.011)
    assert voiced["low_shelf@120"] == pytest.approx(plain["low_shelf@120"], abs=1e-9)
    # ... and the +3.5 dB shelf is clamped at the +4 dB limit instead of exceeding it.
    assert voiced["high_shelf@10000"] == pytest.approx(min(cal.EQ_MAX_GAIN_DB, plain["high_shelf@10000"] + 3.5), abs=0.011)
    # The 130 Hz tone lifts the 80-170 Hz band: the new low shelf cuts.
    assert plain["low_shelf@120"] <= -2.0
    for g in list(voiced.values()) + list(plain.values()):
        assert -cal.EQ_MAX_GAIN_DB <= g <= cal.EQ_MAX_GAIN_DB


def _measurements(speech, floor, sib, centroid=6500.0):
    """Hand-made measurement dict with an arbitrary (flat) band layout."""
    m = cal.analyze(synth.make_speech_like(20.0, SR, seed=0), SR)
    m = copy.deepcopy(m)
    m.update({"speech_level_dbfs": speech, "noise_floor_dbfs": floor, "snr_db": speech - floor,
              "speech_peak_level_dbfs": speech + 10.0, "speech_crest_db": 10.0,
              "sibilance_ratio_db": sib, "sibilance_centroid_hz": centroid, "speech_detected": True})
    return m


@pytest.mark.parametrize("speech,floor,sib,centroid", [
    (-60.0, -95.0, -30.0, 2000.0),   # far too quiet, dead silent room
    (-5.0, -8.0, 10.0, 15000.0),     # hot, noisy, extremely sibilant
    (-26.0, -60.0, -15.0, 6500.0),   # nominal
    (-45.0, -47.0, 0.0, 9500.0),     # almost no SNR
])
def test_recommend_respects_all_bounds(speech, floor, sib, centroid):
    cfg = cal.recommend(_measurements(speech, floor, sib, centroid), cal.DEFAULT_CONFIG)
    chain = cfg["chain"]
    assert cal.TRIM_BOUNDS_DB[0] <= chain["input_trim"]["gain_db"] <= cal.TRIM_BOUNDS_DB[1]
    for stage in ("expander", "deesser", "compressor"):
        lo, hi = bounds(cfg, stage, "threshold_db")
        assert lo <= chain[stage]["threshold_db"] <= hi, stage
    assert cal.DEESSER_FREQ_BOUNDS_HZ[0] <= chain["deesser"]["freq_hz"] <= cal.DEESSER_FREQ_BOUNDS_HZ[1]
    assert cal.MAKEUP_BOUNDS_DB[0] <= chain["compressor"]["makeup_db"] <= cal.MAKEUP_BOUNDS_DB[1]
    for band in chain["eq"]["bands"]:
        assert -cal.EQ_MAX_GAIN_DB <= band["gain_db"] <= cal.EQ_MAX_GAIN_DB
    assert set(cfg["_calibration"]["reasons"]) >= {
        "chain.input_trim.gain_db", "chain.expander.threshold_db", "chain.deesser.freq_hz",
        "chain.deesser.threshold_db", "chain.compressor.threshold_db", "chain.compressor.makeup_db",
        "chain.normalizer.target_lufs", "chain.limiter.ceiling_dbfs"}


def test_recommend_does_not_mutate_base_config():
    base = copy.deepcopy(cal.DEFAULT_CONFIG)
    snapshot = copy.deepcopy(base)
    cal.recommend(_measurements(-26.0, -50.0, -5.0), base)
    assert base == snapshot


# --------------------------------------------------------------------------- evaluate

def test_evaluate_identity_fails_loudness_and_noise_but_passes_peak(quiet_sample):
    cfg = cal.recommend(cal.analyze(quiet_sample, SR), cal.DEFAULT_CONFIG)
    ev = cal.evaluate(quiet_sample, quiet_sample, SR, cfg)
    c = ev["criteria"]
    assert c["noise_floor_reduction"]["passed"] is False      # 0 dB SNR gain < 6 dB
    assert "+0.0 dB" in c["noise_floor_reduction"]["actual"]
    assert c["loudness_in_target"]["passed"] is False         # ~-25 LUFS vs -18
    assert c["true_peak_below_ceiling"]["passed"] is True     # ~-8.5 dBTP
    assert c["sibilance_reduced"]["passed"] is False          # de-esser needed, no change
    assert ev["passed"] is False
    assert ev["info"]["snr_gain_db"] == pytest.approx(0.0, abs=1e-9)


def test_evaluate_passes_on_an_ideal_output(quiet_sample):
    """Build the output the chain is supposed to deliver and check the criteria pass."""
    cfg = cal.recommend(cal.analyze(quiet_sample, SR), cal.DEFAULT_CONFIG)
    x = quiet_sample.astype(np.float64)
    # Gate the pauses by 18 dB (expander range is 20 dB), lower the 5-10 kHz band
    # by 6 dB, then scale to -18 LUFS (about +7 dB, so the floor ends ~11 dB lower).
    mask, _ = meters.speech_activity(x, SR)
    frame = int(SR * meters.ANALYSIS_FRAME_MS / 1000)
    gain = np.repeat(np.where(mask, 1.0, 10 ** (-18 / 20)), frame)
    gain = np.concatenate([gain, np.full(x.size - gain.size, gain[-1])])
    y = x * gain
    from scipy import signal
    sos = signal.butter(4, [5000.0, 10000.0], btype="bandpass", fs=SR, output="sos")
    band = signal.sosfiltfilt(sos, y)
    y = y - band * (1 - 10 ** (-6 / 20))
    y *= 10 ** ((-18.0 - meters.integrated_lufs(y, SR)) / 20)
    ev = cal.evaluate(x, y, SR, cfg)
    assert ev["criteria"]["noise_floor_reduction"]["passed"] is True
    assert ev["info"]["snr_gain_db"] == pytest.approx(18.0, abs=1.5)
    assert ev["criteria"]["sibilance_reduced"]["passed"] is True
    assert ev["criteria"]["loudness_in_target"]["passed"] is True
    assert ev["criteria"]["true_peak_below_ceiling"]["passed"] is True
    assert ev["passed"] is True
    assert ev["after"]["integrated_lufs"] == pytest.approx(-18.0, abs=0.1)


# --------------------------------------------------------------------------- run_calibration

def _write_wav(path, x, sr=SR):
    sf.write(str(path), x, sr, subtype="PCM_16")
    return path


def test_run_calibration_writes_config_and_report(tmp_path, quiet_sample):
    wav = _write_wav(tmp_path / "sample.wav", quiet_sample)
    out_cfg = tmp_path / "out" / "chain.calibrated.yaml"
    report = tmp_path / "reports" / "calibration.md"

    def fake_processor(x):
        return (x * 10 ** (6.0 / 20)).astype(np.float32)

    result = cal.run_calibration(str(wav), None, str(out_cfg), str(report), processor=fake_processor)
    assert out_cfg.exists() and report.exists()
    assert isinstance(result["passed"], bool)
    assert result["out_cfg_path"] == str(out_cfg)
    import yaml
    loaded = yaml.safe_load(out_cfg.read_text(encoding="utf-8"))
    assert loaded["chain"]["input_trim"]["gain_db"] == pytest.approx(6.0, abs=1.5)
    assert loaded["sample_rate"] == 48000
    assert "_calibration" in loaded and "reasons" in loaded["_calibration"]
    text = report.read_text(encoding="utf-8")
    for section in cal.REPORT_SECTIONS:
        assert section in text
    assert "bestanden" in text
    assert "Rauschteppich" in text and "Zischanteil" in text and "True Peak" in text
    assert "chain.input_trim.gain_db" in text


def test_run_calibration_uses_base_config_file(tmp_path, quiet_sample):
    import yaml
    base = copy.deepcopy(cal.DEFAULT_CONFIG)
    base["chain"]["normalizer"]["target_lufs"] = -16.0
    base_path = tmp_path / "base.yaml"
    base_path.write_text(yaml.safe_dump(base), encoding="utf-8")
    wav = _write_wav(tmp_path / "s.wav", quiet_sample)
    result = cal.run_calibration(str(wav), str(base_path), str(tmp_path / "o.yaml"),
                                 str(tmp_path / "r.md"), processor=lambda x: x)
    assert result["cfg"]["chain"]["normalizer"]["target_lufs"] == -16.0
    assert "-16 LUFS" in (tmp_path / "r.md").read_text(encoding="utf-8")


def test_run_calibration_rejects_wrong_sample_rate(tmp_path):
    x = synth.make_speech_like(20.0, 44100, seed=0)
    wav = _write_wav(tmp_path / "s44.wav", x, sr=44100)
    with pytest.raises(ValueError) as err:
        cal.run_calibration(str(wav), None, str(tmp_path / "o.yaml"), str(tmp_path / "r.md"),
                            processor=lambda x: x)
    msg = str(err.value)
    assert "44100" in msg and "48000" in msg and "ffmpeg" in msg
    assert not (tmp_path / "o.yaml").exists()


def test_run_calibration_reports_missing_wav(tmp_path):
    with pytest.raises(FileNotFoundError):
        cal.run_calibration(str(tmp_path / "missing.wav"), None, str(tmp_path / "o.yaml"),
                            str(tmp_path / "r.md"), processor=lambda x: x)


def test_end_to_end_with_real_chain(tmp_path):
    """Acceptance criterion docs/00 section 6.3 against the real DSP chain."""
    pytest.importorskip("voicechain.dsp.chain")
    x = synth.make_speech_like(60.0, SR, level_dbfs=-26.0, noise_floor_dbfs=-50.0,
                               sibilance_db=0.0, pause_ratio=0.3, seed=3, lead_silence_s=5.0)
    wav = _write_wav(tmp_path / "real.wav", x)
    result = cal.run_calibration(str(wav), None, str(tmp_path / "cal.yaml"), str(tmp_path / "cal.md"))
    c = result["evaluation"]["criteria"]
    before = result["evaluation"]["before"]
    after = result["evaluation"]["after"]
    assert after["true_peak_dbtp"] <= -1.0 + cal.TRUE_PEAK_TOLERANCE_DB
    assert abs(after["integrated_lufs"] - (-18.0)) <= 2.0
    assert after["snr_db"] - before["snr_db"] >= 6.0
    assert after["sibilance_ratio_db"] < before["sibilance_ratio_db"]
    # Speech level after the chain sits near the normalizer target (-18 LUFS ~ -18 dBFS RMS).
    assert after["speech_level_dbfs"] == pytest.approx(-18.0, abs=2.5)
    assert all(item["passed"] for item in c.values()), c
    assert result["passed"] is True
    text = (tmp_path / "cal.md").read_text(encoding="utf-8")
    assert "**bestanden**" in text and "nicht bestanden" not in text
