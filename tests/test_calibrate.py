"""Tests for voicechain.calibrate (analysis, recommendation, evaluation, report).

The base configuration is always the shipped ``config/chain.default.yaml``.
"""
import copy
import math

import numpy as np
import pytest
import soundfile as sf

from voicechain import calibrate as cal
from voicechain import meters, synth
from voicechain.config import default_config_path, load_config

SR = 48000
SAMPLE_KW = dict(level_dbfs=-26.0, noise_floor_dbfs=-50.0, pause_ratio=0.3, seed=3,
                 lead_silence_s=10.0, long_pause_s=6.0)


@pytest.fixture(scope="module")
def base_cfg():
    return load_config(default_config_path())


@pytest.fixture(scope="module")
def quiet_sample():
    """-26 dBFS speech, -50 dBFS floor, pronounced sibilance, 10 s lead, 6 s pause at 32 s."""
    return synth.make_speech_like(60.0, SR, sibilance_db=3.0, **SAMPLE_KW)


@pytest.fixture(scope="module")
def noisy_sample():
    """-26 dBFS speech, -40 dBFS floor (SNR 14 dB), mild sibilance."""
    kw = dict(SAMPLE_KW, noise_floor_dbfs=-40.0)
    return synth.make_speech_like(60.0, SR, sibilance_db=-6.0, **kw)


def bounds(cfg, stage, name):
    lo, hi = cfg["adaptive"]["bounds"][f"{stage}.{name}"]
    return lo, hi


def _write_wav(path, x, sr=SR):
    sf.write(str(path), x, sr, subtype="PCM_16")
    return path


# --------------------------------------------------------------------------- analyze

def test_analyze_reports_generated_values(quiet_sample):
    m = cal.analyze(quiet_sample, SR)
    assert m["duration_s"] == pytest.approx(60.0)
    assert m["noise_floor_dbfs"] == pytest.approx(-50.0, abs=1.5)
    assert m["speech_level_dbfs"] == pytest.approx(-26.0, abs=1.5)
    assert m["snr_db"] == pytest.approx(24.0, abs=2.0)
    assert m["dynamic_range_db"] == pytest.approx(24.0, abs=3.0)
    assert m["speech_detected"] is True
    assert 100.0 <= m["f0_hz"] <= 140.0 and m["voiced_ratio"] > 0.7
    assert m["sibilance_ratio_db"] > cal.SIBILANCE_REFERENCE_DB
    assert 5000.0 <= m["sibilance_centroid_hz"] <= 9000.0
    assert m["sibilant_band_freq_hz"] == pytest.approx(m["sibilance_centroid_hz"])
    assert m["sibilant_band_peak_dbfs"] > m["speech_level_dbfs"]
    assert m["true_peak_dbtp"] >= m["sample_peak_dbfs"]
    assert set(m["eq_bands"]) == {"low_shelf@120", "peak@250", "peak@3000", "high_shelf@10000"}
    assert m["eq_anchor"]["lo_hz"] == 350.0
    assert m["pause"]["start_s"] == pytest.approx(32.0, abs=0.3)
    assert m["pause"]["end_s"] == pytest.approx(38.0, abs=0.3)
    assert m["pause"]["rms_dbfs"] == pytest.approx(-50.0, abs=0.5)


def test_analyze_uses_the_given_deesser_band(quiet_sample):
    m_a = cal.analyze(quiet_sample, SR, deesser_freq_hz=6500.0, deesser_bandwidth_oct=1.0)
    m_b = cal.analyze(quiet_sample, SR, deesser_freq_hz=6500.0, deesser_bandwidth_oct=0.5)
    assert m_a["sibilant_band_freq_hz"] == 6500.0 and m_b["sibilant_band_width_oct"] == 0.5
    # A narrower band passes less energy: lower peak level.
    assert m_b["sibilant_band_peak_dbfs"] < m_a["sibilant_band_peak_dbfs"]


def test_analyze_rejects_too_short_and_nonfinite_samples():
    with pytest.raises(ValueError, match="10 s"):
        cal.analyze(synth.make_speech_like(5.0, SR, seed=0), SR)
    x = synth.make_speech_like(20.0, SR, seed=0).astype(np.float64)
    x[1000] = np.nan
    with pytest.raises(ValueError, match="nicht-finite"):
        cal.analyze(x, SR)


# --------------------------------------------------------------------------- recommend

def test_recommend_on_quiet_sample_sets_trim_and_expander(quiet_sample, base_cfg):
    m = cal.analyze(quiet_sample, SR)
    cfg = cal.recommend(m, base_cfg)
    trim = cfg["chain"]["input_trim"]["gain_db"]
    assert trim == pytest.approx(6.0, abs=1.5)
    floor_after = m["noise_floor_dbfs"] + trim
    speech_after = m["speech_level_dbfs"] + trim
    thr = cfg["chain"]["expander"]["threshold_db"]
    assert cal.EXPANDER_FLOOR_MARGIN_DB == 10.0
    assert thr == pytest.approx(floor_after + 10.0, abs=2.0)
    assert thr <= speech_after - 12.0 + 1e-6
    assert "Rauschteppich + 10" in cfg["_calibration"]["reasons"]["chain.expander.threshold_db"]
    assert "_calibration" in cfg and "measurements" in cfg["_calibration"]


def test_recommend_on_noisy_sample_caps_expander_below_speech(noisy_sample, base_cfg):
    m = cal.analyze(noisy_sample, SR)
    cfg = cal.recommend(m, base_cfg)
    trim = cfg["chain"]["input_trim"]["gain_db"]
    assert trim == pytest.approx(6.0, abs=1.5)
    speech_after = m["speech_level_dbfs"] + trim
    thr = cfg["chain"]["expander"]["threshold_db"]
    # floor + trim + 10 = -24 would be only 4 dB under speech -> the speech rule wins.
    assert thr == pytest.approx(speech_after - 12.0, abs=0.5)
    assert thr < m["noise_floor_dbfs"] + trim + 10.0 - 2.0
    assert "Sprachpegel" in cfg["_calibration"]["reasons"]["chain.expander.threshold_db"]


def test_recommend_compressor_threshold_and_makeup(quiet_sample, base_cfg):
    m = cal.analyze(quiet_sample, SR)
    cfg = cal.recommend(m, base_cfg)
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
    assert cfg["chain"]["normalizer"]["target_lufs"] == base_cfg["chain"]["normalizer"]["target_lufs"]
    assert cfg["chain"]["limiter"]["ceiling_dbfs"] == -1.0


def test_deesser_threshold_from_band_peak_model(base_cfg):
    """De-esser model: threshold = band peak (95th percentile, after trim) - overshoot for 4 dB."""
    ratio = base_cfg["chain"]["deesser"]["ratio"]
    overshoot = cal.deesser_overshoot_db(ratio)
    assert overshoot == pytest.approx(4.0 / (1 - 1 / ratio))
    lo, hi = bounds(base_cfg, "deesser", "threshold_db")
    # Clean voice: the de-esser rests 2 dB above the loud sibilants (only outliers).
    soft = cal.analyze(synth.make_speech_like(40.0, SR, sibilance_db=-20.0, seed=4, long_pause_s=6.0), SR)
    assert soft["sibilance_ratio_db"] < cal.SIBILANCE_REFERENCE_DB
    cfg_q = cal.recommend(soft, base_cfg)
    trim_q = cfg_q["chain"]["input_trim"]["gain_db"]
    expected_rest = cal.clamp(soft["sibilant_band_peak_dbfs"] + trim_q + cal.DEESSER_REST_MARGIN_DB, lo, hi)
    assert cfg_q["chain"]["deesser"]["threshold_db"] == pytest.approx(expected_rest, abs=0.05)
    assert cfg_q["chain"]["deesser"]["threshold_db"] > soft["sibilant_band_peak_dbfs"] + trim_q
    assert "kein Zischproblem" in cfg_q["_calibration"]["reasons"]["chain.deesser.threshold_db"]
    assert soft["sibilant_frame_ratio"] == 0.0
    # Sibilant voices: threshold follows the measured band peak, within bounds.
    for sib_db in (-6.0, 3.0):
        m = cal.analyze(synth.make_speech_like(40.0, SR, sibilance_db=sib_db, seed=4, long_pause_s=6.0), SR)
        assert m["sibilance_ratio_db"] > cal.SIBILANCE_REFERENCE_DB
        cfg = cal.recommend(m, base_cfg)
        trim = cfg["chain"]["input_trim"]["gain_db"]
        expected = cal.clamp(m["sibilant_band_peak_dbfs"] + trim - overshoot, lo, hi)
        assert cfg["chain"]["deesser"]["threshold_db"] == pytest.approx(expected, abs=0.05)
        assert "Spitzenpegel" in cfg["_calibration"]["reasons"]["chain.deesser.threshold_db"]
        assert 5000.0 <= cfg["chain"]["deesser"]["freq_hz"] <= 9000.0
        assert 0.05 <= m["sibilant_frame_ratio"] <= 0.3
    assert cal.deesser_threshold_target(-20.0, -4.0, 4.0) == (pytest.approx(-20.0 - overshoot), True)
    assert cal.deesser_threshold_target(-20.0, -20.0, 4.0) == (pytest.approx(-18.0), False)
    with pytest.raises(ValueError):
        cal.deesser_threshold_target(-10.0, 0.0, 1.0)


def _deesser_reductions(x, cfg):
    """Per-frame max reduction of the real de-esser on the trimmed signal, plus the activity mask."""
    from voicechain.dsp.deesser import DeEsser
    trimmed = x.astype(np.float64) * 10 ** (cfg["chain"]["input_trim"]["gain_db"] / 20)
    stage = DeEsser(SR, **cfg["chain"]["deesser"])
    frame = int(SR * meters.ANALYSIS_FRAME_MS / 1000)
    n_frames = trimmed.size // frame
    reductions = np.zeros(n_frames)
    for i in range(n_frames):
        stage.process(trimmed[i * frame:(i + 1) * frame])
        reductions[i] = stage.meters()["gain_reduction_max_db"]
    mask, _ = meters.speech_activity(x, SR)
    return reductions, mask


def test_clean_voice_leaves_the_deesser_at_rest(base_cfg):
    """A voice without a sibilance problem (ratio <= -15 dB) is not de-essed: fewer
    than 20 % of the active frames see more than 1 dB reduction."""
    x = synth.make_speech_like(40.0, SR, sibilance_db=-12.0, seed=4, long_pause_s=6.0)
    m = cal.analyze(x, SR)
    assert m["sibilance_ratio_db"] <= cal.SIBILANCE_REFERENCE_DB
    cfg = cal.recommend(m, base_cfg)
    reductions, mask = _deesser_reductions(x, cfg)
    assert float(np.mean(reductions[mask] > 1.0)) < 0.2
    assert float(np.median(reductions[mask])) == pytest.approx(0.0, abs=0.05)
    # For comparison the same voice with pronounced sibilance is treated.
    strong = synth.make_speech_like(40.0, SR, sibilance_db=0.0, seed=4, long_pause_s=6.0)
    red_s, mask_s = _deesser_reductions(strong, cal.recommend(cal.analyze(strong, SR), base_cfg))
    assert float(np.mean(red_s[mask_s] > 1.0)) > 0.1


def test_deesser_reduction_on_sibilants_is_between_4_and_cap_minus_1_db(base_cfg):
    """With the recommended threshold the real de-esser reduces the loud
    sibilants by >= 4 dB but does not sit at max_reduction_db permanently."""
    x = synth.make_speech_like(40.0, SR, sibilance_db=-6.0, seed=4, long_pause_s=6.0)
    m = cal.analyze(x, SR)
    cfg = cal.recommend(m, base_cfg)
    de_cfg = cfg["chain"]["deesser"]
    reductions, mask = _deesser_reductions(x, cfg)
    trimmed = x * 10 ** (cfg["chain"]["input_trim"]["gain_db"] / 20)
    band_levels = meters.band_peak_frames_dbfs(trimmed, SR, de_cfg["freq_hz"], de_cfg["bandwidth_oct"])
    top = mask & (band_levels >= np.percentile(band_levels[mask], meters.BAND_PEAK_PERCENTILE))
    assert top.sum() >= 10
    median_red = float(np.median(reductions[top]))
    assert 4.0 <= median_red <= de_cfg["max_reduction_db"] - 1.0, median_red
    assert float(np.mean(reductions[top] >= de_cfg["max_reduction_db"] - 0.1)) < 0.5
    # Frames without sibilants are left alone.
    quiet = mask & (band_levels < np.percentile(band_levels[mask], 50.0))
    assert float(np.median(reductions[quiet])) < 0.5


def test_eq_shelves_are_cut_only_and_low_shelf_skipped_for_high_voices(base_cfg):
    """A high voice (f0 190-250 Hz) gets no low-frequency boost; shelves never > 0."""
    x = synth.make_speech_like(40.0, SR, seed=5, pitch_range_hz=(190.0, 250.0), long_pause_s=6.0)
    m = cal.analyze(x, SR)
    assert m["f0_hz"] > cal.EQ_LOW_SHELF_MAX_F0_HZ
    # The 80-170 Hz band is far below the reference (no speech there) ...
    anchor = m["eq_anchor"]["density_db"]
    assert m["eq_bands"]["low_shelf@120"]["density_db"] - anchor < -10.0
    cfg = cal.recommend(m, base_cfg)
    bands = cfg["chain"]["eq"]["bands"]
    # ... yet no low shelf is created or boosted.
    assert all(b["type"] != "low_shelf" for b in bands)
    reason = cfg["_calibration"]["reasons"]["chain.eq.low_shelf@120.gain_db"]
    assert "kein Eingriff" in reason and "Grundfrequenz" in reason
    for b in bands:
        if b["type"] in cal.EQ_CUT_ONLY_TYPES:
            assert b["gain_db"] <= 0.0
        assert -cal.EQ_MAX_GAIN_DB <= b["gain_db"] <= cal.EQ_MAX_GAIN_DB
    # The shipped +1.5 dB high shelf can only go down (cut-only, reference steeper above 4 kHz).
    high = next(b for b in bands if b["type"] == "high_shelf")
    assert high["gain_db"] <= 0.0


def test_high_shelf_is_measured_without_sibilants_and_not_pinned_at_its_cut_limit(base_cfg):
    """The 7-14 kHz band of a sibilant voice is measured on non-sibilant frames only
    and the shelf cut is limited to -3 dB; a normal voice does not sit at that limit."""
    x = synth.make_speech_like(40.0, SR, sibilance_db=-3.0, seed=6, long_pause_s=6.0)
    m = cal.analyze(x, SR)
    assert m["sibilance_ratio_db"] > cal.SIBILANCE_REFERENCE_DB
    band = m["eq_bands"]["high_shelf@10000"]
    assert 0.05 <= band["excluded_sibilant_ratio"] <= 0.4
    assert band["excluded_sibilant_ratio"] == m["sibilant_frame_ratio"]
    # Excluding the bursts lowers the measured top-octave energy.
    mask, _ = meters.speech_activity(x, SR)
    all_frames = meters.band_energies_db(x, SR, [7000.0, 14000.0])[0]
    assert band["energy_db"] < all_frames - 1.0
    cfg = cal.recommend(m, base_cfg)
    high = next(b for b in cfg["chain"]["eq"]["bands"] if b["type"] == "high_shelf")
    assert -cal.EQ_HIGH_SHELF_MAX_CUT_DB < high["gain_db"] <= 0.0
    assert "ohne Zischrahmen" in cfg["_calibration"]["reasons"]["chain.eq.high_shelf@10000.gain_db"]
    # Even an extremely bright measurement is cut by at most 3 dB.
    bright = copy.deepcopy(m)
    bright["eq_bands"]["high_shelf@10000"]["density_db"] += 30.0
    cfg_b = cal.recommend(bright, base_cfg)
    assert next(b for b in cfg_b["chain"]["eq"]["bands"] if b["type"] == "high_shelf")["gain_db"] == -cal.EQ_HIGH_SHELF_MAX_CUT_DB


def test_eq_low_shelf_cuts_bass_heavy_low_voice_and_never_boosts(base_cfg):
    x = synth.make_speech_like(40.0, SR, seed=5, long_pause_s=6.0).astype(np.float64)
    t = np.arange(x.size) / SR
    # Strong 130 Hz component (proximity effect of a low voice) at -18 dBFS RMS,
    # present only while the voice is active so the pauses stay pauses.
    mask, _ = meters.speech_activity(x, SR)
    frame = int(SR * meters.ANALYSIS_FRAME_MS / 1000)
    gate = np.repeat(mask.astype(np.float64), frame)
    gate = np.concatenate([gate, np.zeros(x.size - gate.size)])
    x += gate * 10 ** (-18 / 20) * math.sqrt(2) * np.sin(2 * math.pi * 130.0 * t)
    m = cal.analyze(x, SR)
    assert m["f0_hz"] <= cal.EQ_LOW_SHELF_MAX_F0_HZ
    cfg = cal.recommend(m, base_cfg)
    by_key = {f"{b['type']}@{b['freq_hz']:.0f}": b["gain_db"] for b in cfg["chain"]["eq"]["bands"]}
    assert "low_shelf@120" in by_key
    assert -cal.EQ_MAX_GAIN_DB <= by_key["low_shelf@120"] <= -2.0
    # Without the rumble the band is below the reference: cut-only -> no band is created.
    plain = cal.recommend(cal.analyze(synth.make_speech_like(40.0, SR, seed=5, long_pause_s=6.0), SR), base_cfg)
    assert all(b["type"] != "low_shelf" for b in plain["chain"]["eq"]["bands"])
    assert "kein Band angelegt" in plain["_calibration"]["reasons"]["chain.eq.low_shelf@120.gain_db"]


def test_eq_reference_curve_is_piecewise():
    assert cal.eq_reference_db(300.0) == 0.0
    assert cal.eq_reference_db(1000.0) == pytest.approx(-6.0)
    assert cal.eq_reference_db(4000.0) == pytest.approx(-18.0)
    assert cal.eq_reference_db(8000.0) == pytest.approx(-28.0)
    assert cal.eq_reference_db(16000.0) == pytest.approx(-38.0)


def test_eq_correction_is_added_to_base_voicing_and_labelled_by_actual_frequency(base_cfg):
    """Base bands at 220/3200 Hz keep their voicing; the correction is added within +-4 dB."""
    flat = copy.deepcopy(base_cfg)
    for band in flat["chain"]["eq"]["bands"]:
        band["gain_db"] = 0.0
    m = cal.analyze(synth.make_speech_like(30.0, SR, seed=5), SR)
    voiced = {f"{b['type']}@{b['freq_hz']:.0f}": b["gain_db"]
              for b in cal.recommend(m, base_cfg)["chain"]["eq"]["bands"]}
    plain = {f"{b['type']}@{b['freq_hz']:.0f}": b["gain_db"]
             for b in cal.recommend(m, flat)["chain"]["eq"]["bands"]}
    base = {f"{b['type']}@{b['freq_hz']:.0f}": b["gain_db"] for b in base_cfg["chain"]["eq"]["bands"]}
    reasons = cal.recommend(m, base_cfg)["_calibration"]["reasons"]
    assert "chain.eq.peak@220.gain_db" in reasons and "chain.eq.peak@3200.gain_db" in reasons
    for key in ("peak@220", "peak@3200"):
        assert voiced[key] == pytest.approx(cal.clamp(plain[key] + base[key], -4.0, 4.0), abs=0.011)
    assert voiced["high_shelf@10000"] == pytest.approx(cal.clamp(plain["high_shelf@10000"] + base["high_shelf@10000"], -4.0, 0.0), abs=0.011)
    for g in list(voiced.values()) + list(plain.values()):
        assert -cal.EQ_MAX_GAIN_DB <= g <= cal.EQ_MAX_GAIN_DB


def _measurements(speech, floor, sib, centroid=6500.0, band_peak=None, f0=120.0):
    """Hand-made measurement dict on top of a real analysis (band layout, pause)."""
    m = copy.deepcopy(cal.analyze(synth.make_speech_like(20.0, SR, seed=0), SR))
    m.update({"speech_level_dbfs": speech, "noise_floor_dbfs": floor, "snr_db": speech - floor,
              "speech_peak_level_dbfs": speech + 10.0, "speech_crest_db": 10.0,
              "sibilance_ratio_db": sib, "sibilance_centroid_hz": centroid,
              "sibilant_band_peak_dbfs": speech + 8.0 if band_peak is None else band_peak,
              "speech_detected": True, "f0_hz": f0})
    return m


@pytest.mark.parametrize("speech,floor,sib,centroid,band_peak", [
    (-60.0, -95.0, -30.0, 2000.0, -70.0),   # far too quiet, dead silent room
    (-5.0, -8.0, 10.0, 15000.0, 0.0),       # hot, noisy, extremely sibilant
    (-26.0, -60.0, -15.0, 6500.0, -20.0),   # nominal
    (-45.0, -47.0, 0.0, 9500.0, -60.0),     # almost no SNR
])
def test_recommend_respects_all_bounds(speech, floor, sib, centroid, band_peak, base_cfg):
    cfg = cal.recommend(_measurements(speech, floor, sib, centroid, band_peak), base_cfg)
    chain = cfg["chain"]
    assert cal.TRIM_BOUNDS_DB[0] <= chain["input_trim"]["gain_db"] <= cal.TRIM_BOUNDS_DB[1]
    for stage in ("expander", "deesser", "compressor"):
        lo, hi = bounds(cfg, stage, "threshold_db")
        assert lo <= chain[stage]["threshold_db"] <= hi, stage
    assert cal.DEESSER_FREQ_BOUNDS_HZ[0] <= chain["deesser"]["freq_hz"] <= cal.DEESSER_FREQ_BOUNDS_HZ[1]
    assert cal.MAKEUP_BOUNDS_DB[0] <= chain["compressor"]["makeup_db"] <= cal.MAKEUP_BOUNDS_DB[1]
    for band in chain["eq"]["bands"]:
        assert -cal.EQ_MAX_GAIN_DB <= band["gain_db"] <= cal.EQ_MAX_GAIN_DB
        if band["type"] in cal.EQ_CUT_ONLY_TYPES:
            assert band["gain_db"] <= 0.0
    assert set(cfg["_calibration"]["reasons"]) >= {
        "chain.input_trim.gain_db", "chain.expander.threshold_db", "chain.deesser.freq_hz",
        "chain.deesser.threshold_db", "chain.compressor.threshold_db", "chain.compressor.makeup_db",
        "chain.normalizer.target_lufs", "chain.limiter.ceiling_dbfs"}


def test_recommend_does_not_mutate_base_config(base_cfg):
    snapshot = copy.deepcopy(base_cfg)
    cal.recommend(_measurements(-26.0, -50.0, -5.0), base_cfg)
    assert base_cfg == snapshot


def test_recommend_requires_bounds_for_tracked_parameters(base_cfg):
    broken = copy.deepcopy(base_cfg)
    del broken["adaptive"]["bounds"]["deesser.threshold_db"]
    with pytest.raises(ValueError, match="deesser.threshold_db"):
        cal.recommend(_measurements(-26.0, -50.0, -5.0), broken)


# --------------------------------------------------------------------------- normalizer refinement

def test_refine_normalizer_derives_gate_and_max_gain_from_pause_loudness(base_cfg, quiet_sample):
    cfg = cal.recommend(cal.analyze(quiet_sample, SR), base_cfg)
    after = cal.analyze(quiet_sample, SR)
    target = cfg["chain"]["normalizer"]["target_lufs"]
    # Quiet pause (-47 LUFS): gate = -47 + 8 = -39, gain limit 12 dB (capped).
    after["pause"]["median_lufs"] = -47.0
    out = cal.refine_normalizer(cfg, after)
    assert out["chain"]["normalizer"]["gate_lufs"] == pytest.approx(-39.0)
    assert out["chain"]["normalizer"]["max_gain_db"] == pytest.approx(12.0)
    # Very quiet pause (-60 LUFS): the gate does not go below -40.
    after["pause"]["median_lufs"] = -60.0
    assert cal.refine_normalizer(cfg, after)["chain"]["normalizer"]["gate_lufs"] == pytest.approx(-40.0)
    # Loud pause (-30 LUFS): gate = -22 capped at target - 15 = -33; max gain = -18 - (-24) = 6.
    after["pause"]["median_lufs"] = -30.0
    out = cal.refine_normalizer(cfg, after)
    assert out["chain"]["normalizer"]["gate_lufs"] == pytest.approx(target - 15.0)
    assert out["chain"]["normalizer"]["max_gain_db"] == pytest.approx(6.0)
    assert "Pausenlautheit" in out["_calibration"]["reasons"]["chain.normalizer.gate_lufs"]
    assert "Sprachlautheit" in out["_calibration"]["reasons"]["chain.normalizer.max_gain_db"]
    # Pause as loud as speech: no gain at all, with a warning in the reason.
    after["pause"]["median_lufs"] = -14.0
    out = cal.refine_normalizer(cfg, after)
    assert out["chain"]["normalizer"]["max_gain_db"] == 0.0
    assert "ACHTUNG" in out["_calibration"]["reasons"]["chain.normalizer.max_gain_db"]
    assert cfg["chain"]["normalizer"]["gate_lufs"] == base_cfg["chain"]["normalizer"]["gate_lufs"]  # input untouched
    after["pause"] = None
    with pytest.raises(ValueError, match="Pause"):
        cal.refine_normalizer(cfg, after)


# --------------------------------------------------------------------------- evaluate

def test_evaluate_identity_fails_snr_and_loudness_but_passes_peak_and_pause(quiet_sample, base_cfg):
    cfg = cal.recommend(cal.analyze(quiet_sample, SR), base_cfg)
    ev = cal.evaluate(quiet_sample, quiet_sample, SR, cfg)
    c = ev["criteria"]
    assert set(c) == {"noise_floor_reduction", "pause_loudness", "sibilance_reduced",
                      "loudness_in_target", "true_peak_below_ceiling"}
    assert c["noise_floor_reduction"]["passed"] is False      # 0 dB SNR gain < 6 dB
    assert "+0.0 dB" in c["noise_floor_reduction"]["actual"]
    assert c["pause_loudness"]["passed"] is True              # -47 LUFS <= -38
    assert c["loudness_in_target"]["passed"] is False         # ~-26 LUFS vs -18
    assert c["true_peak_below_ceiling"]["passed"] is True     # ~-11 dBTP
    assert c["sibilance_reduced"]["passed"] is False          # de-esser needed, no change
    assert ev["passed"] is False
    assert ev["info"]["snr_gain_db"] == pytest.approx(0.0, abs=1e-9)
    assert ev["info"]["pause_rms_change_db"] == pytest.approx(0.0, abs=1e-9)


def _ideal_output(x: np.ndarray, gate_db: float = -18.0) -> np.ndarray:
    """Gate pauses, lower the 5-10 kHz band by 6 dB, scale to -18 LUFS."""
    from scipy import signal
    x = x.astype(np.float64)
    mask, _ = meters.speech_activity(x, SR)
    frame = int(SR * meters.ANALYSIS_FRAME_MS / 1000)
    gain = np.repeat(np.where(mask, 1.0, 10 ** (gate_db / 20)), frame)
    gain = np.concatenate([gain, np.full(x.size - gain.size, gain[-1])])
    y = x * gain
    sos = signal.butter(4, [5000.0, 10000.0], btype="bandpass", fs=SR, output="sos")
    y = y - signal.sosfiltfilt(sos, y) * (1 - 10 ** (-6 / 20))
    return y * 10 ** ((-18.0 - meters.integrated_lufs(y, SR)) / 20)


def test_evaluate_passes_on_an_ideal_output(quiet_sample, base_cfg):
    cfg = cal.recommend(cal.analyze(quiet_sample, SR), base_cfg)
    y = _ideal_output(quiet_sample)
    ev = cal.evaluate(quiet_sample, y, SR, cfg)
    for key, crit in ev["criteria"].items():
        assert crit["passed"] is True, (key, crit)
    assert ev["passed"] is True
    assert ev["info"]["snr_gain_db"] == pytest.approx(18.0, abs=1.5)
    assert ev["after"]["integrated_lufs"] == pytest.approx(-18.0, abs=0.1)
    # The pause is 18 dB quieter and then lifted by ~8 dB: about -10 dB absolute.
    assert ev["info"]["pause_rms_change_db"] == pytest.approx(-10.0, abs=2.0)
    assert ev["after"]["pause"]["max_lufs"] < -18.0 - cal.PAUSE_LOUDNESS_MARGIN_LU


def test_evaluate_pause_criterion_fails_when_the_pause_is_lifted(quiet_sample, base_cfg):
    cfg = cal.recommend(cal.analyze(quiet_sample, SR), base_cfg)
    y = _ideal_output(quiet_sample)
    a, b = int(33.0 * SR), int(37.5 * SR)
    y[a:b] *= 10 ** (25.0 / 20)           # normalizer run-up in the pause: +25 dB
    ev = cal.evaluate(quiet_sample, y, SR, cfg)
    crit = ev["criteria"]["pause_loudness"]
    assert crit["passed"] is False
    assert ev["after"]["pause"]["max_lufs"] > -18.0 - cal.PAUSE_LOUDNESS_MARGIN_LU
    assert "max_gain_db senken" in crit["note"]
    assert ev["passed"] is False


def test_evaluate_requires_a_pause_of_5_s(base_cfg):
    x = synth.make_speech_like(30.0, SR, seed=1)      # pauses < 1.5 s only
    cfg = cal.recommend(cal.analyze(x, SR), base_cfg)
    with pytest.raises(ValueError) as err:
        cal.evaluate(x, x, SR, cfg)
    assert "5 s" in str(err.value) and "neu aufnehmen" in str(err.value)


# --------------------------------------------------------------------------- read_mono_wav

def test_read_mono_wav_mixes_picks_channels_and_checks_rate(tmp_path):
    left = synth.make_speech_like(12.0, SR, seed=1)
    right = np.zeros_like(left)
    sf.write(str(tmp_path / "st.wav"), np.stack([left, right], axis=1), SR, subtype="PCM_16")
    mixed, sr = cal.read_mono_wav(tmp_path / "st.wav")
    assert sr == SR and mixed.dtype == np.float32
    assert meters.rms_dbfs(mixed) == pytest.approx(meters.rms_dbfs(left) - 6.02, abs=0.05)
    picked, _ = cal.read_mono_wav(tmp_path / "st.wav", channel=0)
    assert meters.rms_dbfs(picked) == pytest.approx(meters.rms_dbfs(left), abs=0.05)
    with pytest.raises(ValueError, match="Kanal 2"):
        cal.read_mono_wav(tmp_path / "st.wav", channel=2)
    with pytest.raises(ValueError, match="44100"):
        cal.read_mono_wav(tmp_path / "st.wav", expected_sr=44100)
    with pytest.raises(FileNotFoundError):
        cal.read_mono_wav(tmp_path / "missing.wav")


def test_read_mono_wav_rejects_garbage_empty_and_nonfinite(tmp_path):
    (tmp_path / "text.wav").write_text("not audio", encoding="utf-8")
    with pytest.raises(ValueError, match="lesbare WAV"):
        cal.read_mono_wav(tmp_path / "text.wav")
    sf.write(str(tmp_path / "empty.wav"), np.zeros((0, 1), dtype=np.float32), SR)
    with pytest.raises(ValueError, match="keine Samples"):
        cal.read_mono_wav(tmp_path / "empty.wav")
    x = synth.make_speech_like(12.0, SR, seed=1)
    x[100:110] = np.nan
    x[200] = np.inf
    sf.write(str(tmp_path / "nan.wav"), x, SR, subtype="FLOAT")
    with pytest.raises(ValueError, match="nicht-finite") as err:
        cal.read_mono_wav(tmp_path / "nan.wav")
    assert "11 von" in str(err.value)


# --------------------------------------------------------------------------- run_calibration

def test_run_calibration_writes_config_and_report(tmp_path, quiet_sample, base_cfg):
    wav = _write_wav(tmp_path / "sample.wav", quiet_sample)
    out_cfg = tmp_path / "out" / "chain.calibrated.yaml"
    report = tmp_path / "reports" / "calibration.md"
    calls = []

    def fake_processor(x):
        calls.append(x.size)
        return _ideal_output(x).astype(np.float32)

    result = cal.run_calibration(str(wav), None, str(out_cfg), str(report), processor=fake_processor)
    assert len(calls) == 2                      # first pass + refined pass
    assert out_cfg.exists() and report.exists()
    assert result["passed"] is True
    assert result["out_cfg_path"] == str(out_cfg)
    loaded = load_config(out_cfg)               # validator accepts the written file
    assert loaded["chain"]["input_trim"]["gain_db"] == pytest.approx(6.0, abs=1.5)
    assert loaded["sample_rate"] == 48000
    assert "_calibration" in loaded and "reasons" in loaded["_calibration"]
    assert "chain.normalizer.gate_lufs" in loaded["_calibration"]["reasons"]
    assert loaded["chain"]["normalizer"]["gate_lufs"] == pytest.approx(-40.0)
    assert loaded["chain"]["normalizer"]["max_gain_db"] == pytest.approx(12.0)
    assert loaded["chain"]["eq"]["bands"] != base_cfg["chain"]["eq"]["bands"]
    text = report.read_text(encoding="utf-8")
    for section in cal.REPORT_SECTIONS:
        assert section in text
    assert "**bestanden**" in text
    assert "Pausenlautheit nach Kette" in text and "Pause: RMS" in text
    assert "Störabstand" in text and "Zischanteil" in text and "True Peak" in text
    assert "chain.input_trim.gain_db" in text and "Grundfrequenz" in text


def test_run_calibration_uses_base_config_file_and_accepts_calibrated_input(tmp_path, quiet_sample, base_cfg):
    import yaml
    base = copy.deepcopy(base_cfg)
    base["chain"]["normalizer"]["target_lufs"] = -16.0
    base["_calibration"] = {"reasons": {"old": "x"}}
    base_path = tmp_path / "base.yaml"
    base_path.write_text(yaml.safe_dump(base), encoding="utf-8")
    wav = _write_wav(tmp_path / "s.wav", quiet_sample)
    result = cal.run_calibration(str(wav), str(base_path), str(tmp_path / "o.yaml"),
                                 str(tmp_path / "r.md"), processor=lambda x: _ideal_output(x))
    assert result["cfg"]["chain"]["normalizer"]["target_lufs"] == -16.0
    assert "old" not in result["cfg"]["_calibration"]["reasons"]
    assert "-16 LUFS" in (tmp_path / "r.md").read_text(encoding="utf-8")


def test_run_calibration_rejects_wrong_sample_rate_and_missing_pause(tmp_path):
    x = synth.make_speech_like(20.0, 44100, seed=0, long_pause_s=6.0)
    wav = _write_wav(tmp_path / "s44.wav", x, sr=44100)
    with pytest.raises(ValueError) as err:
        cal.run_calibration(str(wav), None, str(tmp_path / "o.yaml"), str(tmp_path / "r.md"),
                            processor=lambda x: x)
    msg = str(err.value)
    assert "44100" in msg and "48000" in msg and "afconvert" in msg
    assert not (tmp_path / "o.yaml").exists()
    wav = _write_wav(tmp_path / "nopause.wav", synth.make_speech_like(30.0, SR, seed=1))
    with pytest.raises(ValueError, match="neu aufnehmen"):
        cal.run_calibration(str(wav), None, str(tmp_path / "o.yaml"), str(tmp_path / "r.md"),
                            processor=lambda x: x)
    # Leading silence alone (10 s lead, 5 s speech without a pause) does not count.
    lead_only = synth.make_speech_like(15.0, SR, seed=1, lead_silence_s=10.0, pause_ratio=0.0)
    assert cal.analyze(lead_only, SR)["pause"] is None
    wav = _write_wav(tmp_path / "leadonly.wav", lead_only)
    with pytest.raises(ValueError, match="nach dem ersten"):
        cal.run_calibration(str(wav), None, str(tmp_path / "o.yaml"), str(tmp_path / "r.md"),
                            processor=lambda x: x)
    assert not (tmp_path / "o.yaml").exists()
    with pytest.raises(FileNotFoundError):
        cal.run_calibration(str(tmp_path / "missing.wav"), None, str(tmp_path / "o.yaml"),
                            str(tmp_path / "r.md"), processor=lambda x: x)


def test_first_pass_without_normalizer_keeps_gain_headroom_in_a_noisy_room(tmp_path, base_cfg):
    """Floor -38 dBFS (SNR 12 dB): measured without the AGC, the pause is quiet
    enough to leave some normalizer gain; the first pass used the normalizer-off chain."""
    kw = dict(SAMPLE_KW, noise_floor_dbfs=-38.0)
    x = synth.make_speech_like(60.0, SR, sibilance_db=-6.0, **kw)
    wav = _write_wav(tmp_path / "noisy.wav", x)
    result = cal.run_calibration(str(wav), None, str(tmp_path / "n.yaml"), str(tmp_path / "n.md"))
    norm = result["cfg"]["chain"]["normalizer"]
    first = result["cfg"]["_calibration"]["measurements_first_pass"]
    assert norm["max_gain_db"] > 0.0
    assert norm["max_gain_db"] == pytest.approx(
        cal.clamp(norm["target_lufs"] - (first["pause"]["median_lufs"] + cal.NORMALIZER_PAUSE_HEADROOM_LU), 0.0, 12.0),
        abs=0.01)   # written with two decimals
    # Pause loudness of the normalizer-off pass lies below the final output's pause loudness
    # by at most the allowed gain (the AGC can only add what max_gain_db allows).
    assert result["evaluation"]["after"]["pause"]["median_lufs"] <= first["pause"]["median_lufs"] + norm["max_gain_db"] + 0.5
    assert "abgeschaltetem Normalizer" in result["cfg"]["_calibration"]["reasons"]["chain.normalizer.gate_lufs"]
    assert norm["enabled"] is True


def test_mildly_sibilant_voice_passes_the_sibilance_criterion_with_the_real_chain(tmp_path, base_cfg):
    """A voice just above the reference (de-esser engaged, no clear problem) must not
    fail only because compressor/normalizer lift the band by the amount the de-esser takes back."""
    x = synth.make_speech_like(60.0, SR, sibilance_db=-6.0, **SAMPLE_KW)
    wav = _write_wav(tmp_path / "border.wav", x)
    result = cal.run_calibration(str(wav), None, str(tmp_path / "b.yaml"), str(tmp_path / "b.md"))
    before = result["measurements"]["sibilance_ratio_db"]
    assert cal.SIBILANCE_REFERENCE_DB < before <= cal.SIBILANCE_CLEAR_PROBLEM_DB
    crit = result["evaluation"]["criteria"]["sibilance_reduced"]
    assert crit["passed"] is True, crit
    assert result["evaluation"]["after"]["sibilance_ratio_db"] <= before + cal.SIBILANCE_MAX_INCREASE_DB
    assert "geringem Zischproblem" in crit["note"]


@pytest.mark.parametrize("before", [-18.0, -15.5, -13.6, -12.0, -11.0, -8.0])
@pytest.mark.parametrize("delta", [-2.0, 0.5, 1.5])
def test_sibilance_criterion_closed_rule_table(before, delta):
    """Closed rule: pass if after <= -15, or delta <= -1, or (before <= -10 and delta <= +1)."""
    after = before + delta
    expected = ((after <= -15.0) or (delta <= -1.0) or (before <= -10.0 and delta <= 1.0)
                or (before <= -15.0 and delta <= 3.0 and after <= -12.0))
    assert cal.sibilance_criterion_passed(before, after) is expected
    # Spelled out: every voice passes with -2 dB; +0.5 dB passes unless the voice is clearly
    # sibilant (-8 dB); +1.5 dB passes for the resting voices (-18, -15.5: still <= -12 dB),
    # not for the engaged ones (-13.6 ... -8).
    if delta == -2.0:
        assert expected is True
    if delta == 0.5:
        assert expected is (before <= -10.0)
    if delta == 1.5:
        assert expected is (before <= -15.0)
    # Resting branch limits: +3 dB from -15 passes (-12), +3.1 does not, +2 from -14.5 does not (engaged).
    assert cal.sibilance_criterion_passed(-15.0, -12.0) is True
    assert cal.sibilance_criterion_passed(-15.0, -11.9) is False
    assert cal.sibilance_criterion_passed(-14.5, -12.5) is False
    assert cal.sibilance_criterion_passed(-8.0, -7.5) is False


def _voice_with_sibilance_ratio(target_db: float, seed: int = 4) -> np.ndarray:
    """30 s speech-like signal (5 s lead, 6 s pause) whose 5-10 kHz band is scaled so
    that the measured sibilance ratio lands near ``target_db``."""
    from scipy import signal
    base = synth.make_speech_like(30.0, SR, sibilance_db=-6.0, seed=seed, lead_silence_s=5.0,
                                  long_pause_s=6.0, noise_floor_dbfs=-55.0).astype(np.float64)
    sos = signal.butter(4, [5000.0, 10000.0], btype="bandpass", fs=SR, output="sos")
    x = base
    for _ in range(3):   # the ratio is not exactly linear in the band gain: iterate
        ratio = cal.analyze(x, SR)["sibilance_ratio_db"]
        band = signal.sosfiltfilt(sos, x)
        x = x + band * (10 ** ((target_db - ratio) / 20) - 1.0)
    return x.astype(np.float32)


@pytest.mark.slow
@pytest.mark.parametrize("target", [-18.0, -17.0, -16.0, -15.0, -14.0, -13.0, -12.0, -11.0, -10.0])
def test_sibilance_criterion_passes_across_the_reference_with_the_real_chain(tmp_path, target):
    """Calibrated chain on voices from -18 to -10 dB: none fails the sibilance criterion
    (resting voices rise by the 2-2.5 dB the chain adds, engaged ones are held or improved)."""
    x = _voice_with_sibilance_ratio(target)
    measured = cal.analyze(x, SR)["sibilance_ratio_db"]
    assert measured == pytest.approx(target, abs=0.6)
    wav = _write_wav(tmp_path / f"v{int(-target)}.wav", x)
    result = cal.run_calibration(str(wav), None, str(tmp_path / "v.yaml"), str(tmp_path / "v.md"))
    crit = result["evaluation"]["criteria"]["sibilance_reduced"]
    assert crit["passed"] is True, (target, crit)
    after = result["evaluation"]["after"]["sibilance_ratio_db"]
    assert after - measured <= cal.SIBILANCE_REST_MAX_INCREASE_DB
    if measured <= cal.SIBILANCE_REFERENCE_DB:
        assert after <= cal.SIBILANCE_REST_MAX_AFTER_DB


def test_sibilance_criterion_on_signals_follows_the_closed_rule():
    """The evaluate() criterion applies the closed rule to measured before/after ratios."""
    cfg_stub = {"chain": {"deesser": {"freq_hz": 6500.0, "bandwidth_oct": 1.0},
                          "normalizer": {"target_lufs": -18.0}, "limiter": {"ceiling_dbfs": -1.0}}}
    base = synth.make_speech_like(40.0, SR, sibilance_db=0.0, seed=4, long_pause_s=6.0)
    from scipy import signal
    sos = signal.butter(4, [5000.0, 10000.0], btype="bandpass", fs=SR, output="sos")
    band = signal.sosfiltfilt(sos, base.astype(np.float64))

    def shifted(db):   # scale the 5-10 kHz band by db
        return (base + band * (10 ** (db / 20) - 1.0)).astype(np.float32)

    ratio_before = cal.analyze(base, SR)["sibilance_ratio_db"]
    assert ratio_before > cal.SIBILANCE_CLEAR_PROBLEM_DB   # clearly sibilant (~-7 dB)
    # clearly sibilant: -2 dB passes, +0.5 dB (still above the reference) fails
    assert cal.evaluate(base, shifted(-2.0), SR, cfg_stub)["criteria"]["sibilance_reduced"]["passed"] is True
    assert cal.evaluate(base, shifted(+0.5), SR, cfg_stub)["criteria"]["sibilance_reduced"]["passed"] is False
    # mild voice (about -12 dB): +0.5 dB passes, +1.5 dB fails
    mild = shifted(-12.0 - ratio_before)
    mild_ratio = cal.analyze(mild, SR)["sibilance_ratio_db"]   # band scaling is not exactly 1:1 in the ratio
    assert cal.SIBILANCE_REFERENCE_DB < mild_ratio <= cal.SIBILANCE_CLEAR_PROBLEM_DB
    mb = signal.sosfiltfilt(sos, mild.astype(np.float64))
    up_half = (mild + mb * (10 ** (0.5 / 20) - 1.0)).astype(np.float32)
    up_two = (mild + mb * (10 ** (1.5 / 20) - 1.0)).astype(np.float32)
    assert cal.evaluate(mild, up_half, SR, cfg_stub)["criteria"]["sibilance_reduced"]["passed"] is True
    assert cal.evaluate(mild, up_two, SR, cfg_stub)["criteria"]["sibilance_reduced"]["passed"] is False


def _calibrate_standard_sample(tmp_dir, sibilance_db: float) -> dict:
    """Full calibration (real chain) of the standard sample: 10 s lead silence, 6 s pause."""
    x = synth.make_speech_like(60.0, SR, sibilance_db=sibilance_db, **SAMPLE_KW)
    wav = _write_wav(tmp_dir / f"lead{int(-sibilance_db)}.wav", x)
    result = cal.run_calibration(str(wav), None, str(tmp_dir / "l.yaml"), str(tmp_dir / "l.md"))
    result["report_text"] = (tmp_dir / "l.md").read_text(encoding="utf-8")
    return result


@pytest.fixture(scope="module")
def clean_calibration(tmp_path_factory):
    """One real-chain calibration of a clean voice (sibilance -12), shared by the tests below."""
    return _calibrate_standard_sample(tmp_path_factory.mktemp("clean"), -12.0)


@pytest.fixture(scope="module")
def soft_calibration(tmp_path_factory):
    return _calibrate_standard_sample(tmp_path_factory.mktemp("soft"), -18.0)


@pytest.mark.parametrize("which", ["clean", "soft"])
def test_calibration_with_lead_silence_lets_the_normalizer_regulate(request, which):
    """Standard sample (10 s lead silence, 6 s pause): the evaluation pass must not leave
    the normalizer frozen on the lead noise; loudness lands on target with gain > 0.5 dB."""
    result = request.getfixturevalue(f"{which}_calibration")
    ev = result["evaluation"]
    assert ev["criteria"]["loudness_in_target"]["passed"] is True, ev["criteria"]["loudness_in_target"]
    assert abs(ev["after"]["integrated_lufs"] - (-18.0)) <= 2.0
    assert ev["info"]["normalizer_gain_end_db"] is not None
    assert ev["info"]["normalizer_gain_end_db"] > 0.5
    assert "Normalizer-Gain am Ende" in result["report_text"]
    assert result["passed"] is True, ev["criteria"]


def test_bright_microphone_gets_a_moderate_high_shelf_cut(base_cfg):
    """A voice with +4 dB above 7 kHz (bright capsule) is cut by about half of
    that, within the -3 dB limit; the plain voice (reference-like above 7 kHz) is left alone."""
    x = synth.make_speech_like(40.0, SR, sibilance_db=-12.0, seed=5, long_pause_s=6.0).astype(np.float64)
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(x.size, 1 / SR)
    gain = np.ones_like(f)
    ramp = (f >= 5000.0) & (f < 7000.0)
    gain[ramp] = 10 ** (4.0 / 20 * (f[ramp] - 5000.0) / 2000.0)
    gain[f >= 7000.0] = 10 ** (4.0 / 20)
    bright = np.fft.irfft(spec * gain, x.size)
    plain_m = cal.analyze(x, SR)
    bright_m = cal.analyze(bright, SR)
    anchor = lambda m: m["eq_bands"]["high_shelf@10000"]["density_db"] - m["eq_anchor"]["density_db"]
    assert anchor(bright_m) - anchor(plain_m) == pytest.approx(4.0, abs=0.7)
    shelf = lambda cfg: next(b for b in cfg["chain"]["eq"]["bands"] if b["type"] == "high_shelf")["gain_db"]
    plain_gain = shelf(cal.recommend(plain_m, base_cfg))
    bright_gain = shelf(cal.recommend(bright_m, base_cfg))
    assert plain_gain == 0.0
    assert -cal.EQ_HIGH_SHELF_MAX_CUT_DB <= bright_gain <= -1.0


def test_clean_voice_passes_the_sibilance_criterion_with_the_real_chain(clean_calibration):
    result = clean_calibration
    assert result["measurements"]["sibilance_ratio_db"] <= cal.SIBILANCE_REFERENCE_DB
    crit = result["evaluation"]["criteria"]["sibilance_reduced"]
    assert crit["passed"] is True, crit
    assert result["cfg"]["chain"]["deesser"]["threshold_db"] > result["measurements"]["sibilant_band_peak_dbfs"] + result["cfg"]["chain"]["input_trim"]["gain_db"]


def test_end_to_end_with_real_chain(tmp_path, base_cfg):
    """Acceptance criterion docs/00 section 6.3 against the real DSP chain and the shipped YAML."""
    x = synth.make_speech_like(60.0, SR, sibilance_db=0.0, **SAMPLE_KW)
    wav = _write_wav(tmp_path / "real.wav", x)
    result = cal.run_calibration(str(wav), None, str(tmp_path / "cal.yaml"), str(tmp_path / "cal.md"))
    c = result["evaluation"]["criteria"]
    before = result["evaluation"]["before"]
    after = result["evaluation"]["after"]
    target = base_cfg["chain"]["normalizer"]["target_lufs"]
    assert after["true_peak_dbtp"] <= -1.0 + cal.TRUE_PEAK_TOLERANCE_DB
    assert abs(after["integrated_lufs"] - target) <= 2.0
    assert after["snr_db"] - before["snr_db"] >= 6.0
    assert after["pause"]["max_lufs"] <= target - cal.PAUSE_LOUDNESS_MARGIN_LU
    assert after["sibilance_ratio_db"] < before["sibilance_ratio_db"]
    # Speech level after the chain sits near the normalizer target (-18 LUFS ~ -18 dBFS RMS).
    assert after["speech_level_dbfs"] == pytest.approx(target, abs=2.5)
    assert all(item["passed"] for item in c.values()), c
    assert result["passed"] is True
    assert load_config(tmp_path / "cal.yaml")["chain"]["deesser"]["freq_hz"] == result["cfg"]["chain"]["deesser"]["freq_hz"]
    text = (tmp_path / "cal.md").read_text(encoding="utf-8")
    assert "**bestanden**" in text and "nicht bestanden" not in text
