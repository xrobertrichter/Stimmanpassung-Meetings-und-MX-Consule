"""Tests for voicechain.adaptive with a FakeChain, scripted meters, the real
LiveAnalyzer on synthetic signals and one closed loop with the real chain."""
import copy
import json

import numpy as np
import pytest

from voicechain import adaptive, calibrate as cal, meters, synth
from voicechain.config import default_config_path, load_config

SR = 48000
LEARN_S = 30.0
INTERVAL_S = 2.0
STEP_DB = 0.5
OVERSHOOT = cal.deesser_overshoot_db(4.0)   # 5.33 dB at ratio 4


@pytest.fixture(scope="module")
def base_cfg():
    return load_config(default_config_path())


class FakeChain:
    """Parameter store with the Chain set_param/get_param contract; process = gain."""

    def __init__(self, cfg):
        self.params = {}
        for stage, values in cfg["chain"].items():
            for name, value in values.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    self.params[(stage, name)] = float(value)
        self.meters = {}
        self.history = []

    def process(self, x):
        return x * 10 ** (self.params[("input_trim", "gain_db")] / 20)

    def set_param(self, stage, name, value):
        if (stage, name) not in self.params:
            raise KeyError((stage, name))
        self.params[(stage, name)] = float(value)
        self.history.append((stage, name, float(value)))

    def get_param(self, stage, name):
        return self.params[(stage, name)]

    def get_meters(self):
        return copy.deepcopy(self.meters)


def analysis(floor=-60.0, speech=-20.0, sib=-15.0, band_peak=-32.0, active=True, ready=True,
             detected=True, ratio=0.7, dynamic=None):
    """Neutral defaults: speech at the reference, clean voice whose resting de-esser
    target (band peak + 2 dB = -30) equals the shipped threshold."""
    return {"ready": ready, "speech_detected": detected, "noise_floor_dbfs": floor,
            "speech_level_dbfs": speech, "dynamic_range_db": speech - floor if dynamic is None else dynamic,
            "sibilance_ratio_db": sib, "band_peak_p95_dbfs": band_peak, "band_hz": (6500.0, 1.0),
            "speech_active": active, "speech_ratio": ratio,
            "recent_speech_ratio": 1.0 if active else 0.0, "frames": 400, "history_s": 20.0}


def meters_dict(limiter_gr=0.0, expander_gr=0.0, norm_gain=0.0, **analysis_kw):
    return {"limiter": {"gain_reduction_mean_db": limiter_gr, "gain_reduction_max_db": limiter_gr},
            "expander": {"gain_reduction_mean_db": expander_gr, "gain_reduction_max_db": expander_gr},
            "normalizer": {"gain_db": norm_gain},
            "output": {"peak_dbfs": -3.0, "rms_dbfs": -20.0},
            "analysis": analysis(**analysis_kw)}


def make(cfg_overrides=None, log_path=None, clock=lambda: 0.0):
    cfg = load_config(default_config_path())
    cfg["adaptive"].update({"learn_s": LEARN_S, "interval_s": INTERVAL_S, "max_step_db": STEP_DB})
    if cfg_overrides:
        for stage, values in cfg_overrides.items():
            cfg["chain"][stage].update(values)
    chain = FakeChain(cfg)
    ctrl = adaptive.AdaptiveController(cfg, chain, clock=clock, log_path=log_path)
    return cfg, chain, ctrl


def run(ctrl, meters_fn, n_intervals, t0=LEARN_S):
    """Call update once per interval starting at t0; return all changes."""
    changes = []
    for i in range(n_intervals):
        t = t0 + i * INTERVAL_S
        changes.extend(ctrl.update(meters_fn(i), now=t))
    return changes


def events(log_path):
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]


# --------------------------------------------------------------------------- learning phase

def test_no_change_before_learn_s_and_first_change_after(tmp_path):
    log = tmp_path / "a.jsonl"
    cfg, chain, ctrl = make(log_path=log)
    changes = run(ctrl, lambda i: meters_dict(floor=-70.0), n_intervals=15, t0=0.0)  # 0..28 s
    assert changes == []
    assert chain.history == []
    first = ctrl.update(meters_dict(floor=-70.0), now=LEARN_S)
    assert len(first) >= 1
    assert first[0]["stage"] == "expander" and first[0]["reason"] == "noise_floor_track"
    ctrl.close()
    names = [e["event"] for e in events(log) if "event" in e]
    assert names.count("learning") == 15
    assert "learn_done" in names


def test_learning_phase_is_measured_from_construction_with_the_given_clock():
    cfg, chain, ctrl = make(clock=lambda: 100.0)
    assert ctrl.update(meters_dict(floor=-70.0), now=100.0) == []
    assert ctrl.update(meters_dict(floor=-70.0), now=100.0 + LEARN_S - 1.0) == []
    assert len(ctrl.update(meters_dict(floor=-70.0), now=100.0 + LEARN_S)) >= 1
    # Without `now` the clock is used (still 100.0 -> learning).
    assert ctrl.update(meters_dict(floor=-70.0)) == []


# --------------------------------------------------------------------------- tracking

def test_expander_converges_within_40_intervals_and_moves_in_one_direction():
    cfg, chain, ctrl = make()
    # floor -60 + 10 = -50, speech -20 - 12 = -32 -> target -50; start -45; 5 dB away.
    changes = run(ctrl, lambda i: meters_dict(floor=-60.0, speech=-20.0), 40)
    thr = chain.get_param("expander", "threshold_db")
    assert abs(thr - (-50.0)) <= adaptive.EXPANDER_DEADZONE_DB
    steps = [c["new"] - c["old"] for c in changes if c["stage"] == "expander"]
    assert len(steps) >= 6   # 5 dB error, dead zone 2 dB -> at least 3 dB = 6 steps
    assert all(s == pytest.approx(-STEP_DB) for s in steps)
    more = run(ctrl, lambda i: meters_dict(floor=-60.0, speech=-20.0), 20, t0=LEARN_S + 40 * INTERVAL_S)
    assert [c for c in more if c["stage"] == "expander"] == []


def test_deesser_and_compressor_converge_to_targets():
    cfg, chain, ctrl = make()
    # speech -26 (plausible: within -20 +- 6), band peak -22, sibilance -4 (engaged):
    # de-esser target = -22 - 5.33 = -27.33 (start -30, up); compressor target = -26 (start -20, down).
    changes = run(ctrl, lambda i: meters_dict(speech=-26.0, sib=-4.0, band_peak=-22.0), 40)
    assert abs(chain.get_param("deesser", "threshold_db") - (-22.0 - OVERSHOOT)) <= adaptive.DEESSER_DEADZONE_DB
    assert chain.get_param("deesser", "threshold_db") > -30.0
    assert abs(chain.get_param("compressor", "threshold_db") - (-26.0)) <= adaptive.COMPRESSOR_DEADZONE_DB
    assert chain.get_param("compressor", "threshold_db") <= -24.0
    for stage in ("deesser", "compressor"):
        signs = {np.sign(c["new"] - c["old"]) for c in changes if c["stage"] == stage}
        assert len(signs) == 1, f"{stage} changed direction: {signs}"
    reasons = {c["reason"] for c in changes}
    assert {"sibilance_track", "speech_level_track"} <= reasons


def test_deesser_threshold_can_rise_above_the_calibrated_value_within_bounds():
    cfg, chain, ctrl = make()
    lo, hi = cfg["adaptive"]["bounds"]["deesser.threshold_db"]
    # Band peak -8 dBFS -> target -13.3 (clipped to the upper bound if it is lower); start -30.
    run(ctrl, lambda i: meters_dict(sib=-4.0, band_peak=-8.0), 80)
    target = cal.clamp(-8.0 - OVERSHOOT, lo, hi)
    thr = chain.get_param("deesser", "threshold_db")
    assert abs(thr - target) <= adaptive.DEESSER_DEADZONE_DB
    assert thr > -20.0 and lo <= thr <= hi


def test_deesser_rests_two_db_above_the_band_peak_when_sibilance_is_low():
    cfg, chain, ctrl = make({"deesser": {"threshold_db": -40.0}})
    # Clean voice, band peak -32 -> resting threshold -30 (same formula as the calibration).
    changes = run(ctrl, lambda i: meters_dict(sib=-25.0, band_peak=-32.0), 40)
    assert abs(chain.get_param("deesser", "threshold_db") - (-30.0)) <= adaptive.DEESSER_DEADZONE_DB
    assert all(c["reason"] == "sibilance_rest" for c in changes if c["stage"] == "deesser")
    assert len([c for c in changes if c["stage"] == "deesser"]) >= 10


def test_no_change_inside_deadzone():
    cfg, chain, ctrl = make({"expander": {"threshold_db": -51.0}})   # target -50, error 1 dB
    changes = run(ctrl, lambda i: meters_dict(floor=-60.0, speech=-20.0), 10)
    assert [c for c in changes if c["stage"] == "expander"] == []


def test_changes_to_one_parameter_are_two_intervals_apart():
    cfg, chain, ctrl = make()
    changes = run(ctrl, lambda i: meters_dict(floor=-60.0), 20)
    times = [c["t"] for c in changes if c["stage"] == "expander"]
    gaps = np.diff(times)
    assert gaps.size >= 4
    assert np.all(gaps >= 2 * INTERVAL_S - 1e-9)


def test_step_never_exceeds_max_step_db():
    cfg, chain, ctrl = make()
    changes = run(ctrl, lambda i: meters_dict(floor=-80.0, speech=-16.0, sib=10.0, band_peak=0.0), 40)
    assert changes
    assert max(abs(c["new"] - c["old"]) for c in changes) <= STEP_DB + 1e-9


def test_bounds_are_never_violated():
    cfg, chain, ctrl = make()
    lo_e, hi_e = cfg["adaptive"]["bounds"]["expander.threshold_db"]
    lo_c, hi_c = cfg["adaptive"]["bounds"]["compressor.threshold_db"]
    # floor -95 -> target -85 (below -70); speech -25 -> compressor target -25 (plausible).
    changes = run(ctrl, lambda i: meters_dict(floor=-95.0, speech=-25.0), 120)
    for c in changes:
        lo, hi = cfg["adaptive"]["bounds"][f"{c['stage']}.{c['name']}"]
        assert lo <= c["new"] <= hi
    assert lo_e <= chain.get_param("expander", "threshold_db") <= lo_e + adaptive.EXPANDER_DEADZONE_DB
    # Upper side: speech -15 -> compressor target -15 (inside -10 bound); floor -30 -> expander
    # target -20 is clipped to the -30 bound; band peak 0 -> de-esser clipped to -15.
    changes = run(ctrl, lambda i: meters_dict(floor=-30.0, speech=-15.0, sib=20.0, band_peak=0.0), 160,
                  t0=LEARN_S + 120 * INTERVAL_S)
    for c in changes:
        lo, hi = cfg["adaptive"]["bounds"][f"{c['stage']}.{c['name']}"]
        assert lo <= c["new"] <= hi
    assert hi_e - adaptive.EXPANDER_DEADZONE_DB <= chain.get_param("expander", "threshold_db") <= hi_e
    hi_d = cfg["adaptive"]["bounds"]["deesser.threshold_db"][1]
    assert hi_d - adaptive.DEESSER_DEADZONE_DB <= chain.get_param("deesser", "threshold_db") <= hi_d
    assert chain.get_param("compressor", "threshold_db") == pytest.approx(-15.0, abs=adaptive.COMPRESSOR_DEADZONE_DB)


def speech_cut_meters(**kw):
    """Meters of an interval in which the expander cut speech (max 9 dB, speech throughout)."""
    return meters_dict(expander_gr=9.0, floor=-90.0, active=True, **kw)


def test_apply_clamps_immediate_steps_to_the_bounds():
    """Safety net independent of the target clamp: a speech_cut step at the lower
    bound must not push the expander below the bound."""
    cfg, chain, ctrl = make({"expander": {"threshold_db": -70.0}})
    lo = cfg["adaptive"]["bounds"]["expander.threshold_db"][0]
    assert chain.get_param("expander", "threshold_db") == lo
    for k in range(3):   # the rule needs two consecutive intervals; none may move the value
        changes = ctrl.update(speech_cut_meters(), now=LEARN_S + k * INTERVAL_S)
        assert [c for c in changes if c["stage"] == "expander"] == []
    assert chain.get_param("expander", "threshold_db") == lo
    # One step above the bound: the immediate step lands exactly on the bound, not below.
    chain.set_param("expander", "threshold_db", lo + 0.3)
    changes = ctrl.update(speech_cut_meters(), now=LEARN_S + 3 * INTERVAL_S)
    assert [c["new"] for c in changes if c["stage"] == "expander"] == [pytest.approx(lo)]


def test_value_outside_bounds_is_walked_back_in_steps():
    """The validator refuses files with values outside bounds; at run time (set_param)
    the controller walks such a value back step by step, never jumps."""
    cfg, chain, ctrl = make()
    chain.set_param("deesser", "threshold_db", -5.0)       # above the upper bound
    chain.history.clear()
    lo_d, hi_d = cfg["adaptive"]["bounds"]["deesser.threshold_db"]
    # Clean voice with band peak -40: rest target -38, far below the bound.
    changes = run(ctrl, lambda i: meters_dict(sib=-25.0, band_peak=-40.0), 40)
    steps = [c for c in changes if c["stage"] == "deesser"]
    assert len(steps) >= 10
    assert all(c["new"] - c["old"] == pytest.approx(-STEP_DB) for c in steps)
    assert all(c["new"] <= -5.0 for c in steps)
    assert chain.get_param("deesser", "threshold_db") <= hi_d


def test_input_trim_is_added_to_analysis_levels():
    cfg, chain, ctrl = make({"input_trim": {"gain_db": 6.0}})
    # Measured floor -60 before trim -> -54 after trim -> target -44 (not -50); speech -26 -> -20.
    run(ctrl, lambda i: meters_dict(floor=-60.0, speech=-26.0), 40)
    assert abs(chain.get_param("expander", "threshold_db") - (-44.0)) <= adaptive.EXPANDER_DEADZONE_DB
    assert chain.get_param("expander", "threshold_db") > -46.0


# --------------------------------------------------------------------------- plausibility of "the speaker is talking"

@pytest.mark.parametrize("kw,expected_reason", [
    (dict(speech=-40.0, ratio=0.15), "level"),                 # keyboard clicks
    (dict(speech=-42.0, ratio=0.6), "level"),                  # colleague / playback
    (dict(speech=-20.0, ratio=0.2), "speech_ratio"),           # sparse speech
    (dict(speech=-20.0, ratio=0.6, dynamic=10.0), "dynamic"),  # too little dynamics
    (dict(speech=-20.0, ratio=0.6, detected=False), "no_speech"),
])
def test_implausible_speech_holds_deesser_and_compressor(tmp_path, kw, expected_reason):
    log = tmp_path / "p.jsonl"
    cfg, chain, ctrl = make(log_path=log)
    # Targets that would move both: band peak -12 (de-esser up), speech far from -20 (compressor).
    changes = run(ctrl, lambda i: meters_dict(sib=-4.0, band_peak=-12.0, floor=-70.0, **kw), 20)
    assert [c for c in changes if c["stage"] in ("deesser", "compressor")] == []
    # The expander still follows the floor (floor rule only).
    assert any(c["stage"] == "expander" for c in changes)
    ctrl.close()
    impl = [e for e in events(log) if e.get("event") == "implausible_speech"]
    assert len(impl) == 20
    assert any(expected_reason in r for r in impl[0]["reasons"])


def test_expander_speech_cap_stays_for_a_quiet_speaker_but_not_for_typing():
    # Quiet speaker (-28 dBFS after trim, level implausible, dynamics 14.3 dB < 15)
    # in a -46 dBFS room: floor + 10 = -36 would sit only 8 dB under the speech;
    # the cap keeps the target at -40, the expander ends at -38 (dead zone 2 dB).
    cfg, chain, ctrl = make()
    changes = run(ctrl, lambda i: meters_dict(floor=-46.0, speech=-28.0, ratio=0.6, dynamic=14.3), 40)
    thr = chain.get_param("expander", "threshold_db")
    assert abs(thr - (-40.0)) <= adaptive.EXPANDER_DEADZONE_DB
    assert thr <= -37.7
    assert thr <= -28.0 - cal.EXPANDER_SPEECH_MARGIN_DB + adaptive.EXPANDER_DEADZONE_DB
    assert [c for c in changes if c["stage"] in ("deesser", "compressor")] == []
    # Typing (speech ratio 0.15) is not speech: the cap does not apply, the floor rule does.
    cfg, chain, ctrl = make()
    run(ctrl, lambda i: meters_dict(floor=-46.0, speech=-28.0, ratio=0.15, dynamic=17.0), 40)
    assert abs(chain.get_param("expander", "threshold_db") - (-36.0)) <= adaptive.EXPANDER_DEADZONE_DB
    # Steady noise (dynamics 8 dB, below the 10 dB cap limit) is not speech either.
    cfg, chain, ctrl = make()
    run(ctrl, lambda i: meters_dict(floor=-46.0, speech=-28.0, ratio=0.6, dynamic=8.0), 40)
    assert abs(chain.get_param("expander", "threshold_db") - (-36.0)) <= adaptive.EXPANDER_DEADZONE_DB


def test_level_plausibility_has_one_db_hysteresis(tmp_path):
    """Enter the plausible state inside +-6 dB, leave it only outside +-7 dB: a speaker
    hovering between 5.9 and 6.5 dB below the reference neither flips track/rest
    nor floods the log."""
    log = tmp_path / "hyst.jsonl"
    cfg, chain, ctrl = make(log_path=log)
    # 6.5 dB off at the start: not plausible yet (entry threshold 6 dB).
    ctrl.update(meters_dict(speech=-26.5, band_peak=-22.0, sib=-4.0), now=LEARN_S)
    assert not ctrl._level_plausible
    # 5.9 dB: enters; 6.5 dB: stays (exit threshold 7 dB); 7.2 dB: leaves; 6.5 dB: stays out.
    levels = [-25.9, -26.5, -26.5, -26.5, -27.2, -26.5, -26.5]
    expected = [True, True, True, True, False, False, False]
    for k, (lvl, exp) in enumerate(zip(levels, expected), start=1):
        ctrl.update(meters_dict(speech=lvl, band_peak=-22.0, sib=-4.0), now=LEARN_S + k * INTERVAL_S)
        assert ctrl._level_plausible is exp, (lvl, k)
    ctrl.close()
    impl = [e for e in events(log) if e.get("event") == "implausible_speech"]
    assert len(impl) == 1 + 3           # the first interval and the last three
    # Oscillating around the entry edge after having entered: no track/rest alternation.
    cfg, chain, ctrl = make()
    changes = []
    for k in range(40):
        lvl = -25.9 if k % 2 == 0 else -26.5
        changes += ctrl.update(meters_dict(speech=lvl, band_peak=-22.0, sib=-4.0), now=LEARN_S + k * INTERVAL_S)
    reasons = {c["reason"] for c in changes if c["stage"] in ("deesser", "compressor")}
    assert "rest" not in reasons
    comp = [c for c in changes if c["stage"] == "compressor"]
    assert comp and all(c["new"] - c["old"] == pytest.approx(-STEP_DB) for c in comp)   # one direction only


def test_low_snr_event_is_logged_with_hint_and_throttled(tmp_path):
    log = tmp_path / "snr.jsonl"
    cfg, chain, ctrl = make(log_path=log)
    # Own analysis: floor -40, speech -26 -> input SNR 14 dB < 16 dB.
    changes = run(ctrl, lambda i: meters_dict(floor=-40.0, speech=-26.0, dynamic=14.0), 31)   # 30..90 s
    assert all("snr" not in c["reason"] for c in changes)          # advice only, no own parameter change
    ctrl.close()
    low = [e for e in events(log) if e.get("event") == "low_snr"]
    assert [e["t"] for e in low] == [30.0, 60.0, 90.0]              # at most every 30 s
    assert low[0]["input_snr_db"] == pytest.approx(14.0) and low[0]["snr_lu"] == pytest.approx(14.0)
    assert low[0]["source"] == "analysis" and "Störabstand zu gering" in low[0]["hint"]
    # Normalizer meter flag (with its own SNR figure) triggers it as well; a healthy room does not.
    log2 = tmp_path / "snr2.jsonl"
    cfg, chain, ctrl = make(log_path=log2)
    m = meters_dict(floor=-60.0, speech=-20.0)
    m["normalizer"] = {"gain_db": 0.0, "low_snr": True, "snr_lu": 12.5}
    ctrl.update(m, now=LEARN_S)
    ctrl.update(meters_dict(floor=-60.0, speech=-20.0), now=LEARN_S + 40.0)
    ctrl.close()
    low2 = [e for e in events(log2) if e.get("event") == "low_snr"]
    assert len(low2) == 1 and low2[0]["source"] == "normalizer" and low2[0]["snr_lu"] == pytest.approx(12.5)


def test_live_floor_estimate_holds_for_dense_talk():
    """Dense talk (10 % pauses): the floor comes from the pause frames, not from
    syllable valleys (the global 10th percentile read 2.5 dB too high)."""
    dense = synth.make_speech_like(60.0, SR, noise_floor_dbfs=-50.0, pause_ratio=0.1, seed=12)
    normal = synth.make_speech_like(60.0, SR, noise_floor_dbfs=-50.0, pause_ratio=0.3, seed=12)
    for x in (dense, normal):
        la = adaptive.LiveAnalyzer(SR)
        la.push(x)
        assert la.snapshot()["noise_floor_dbfs"] == pytest.approx(-50.0, abs=1.5)
    # Pure noise (no speech): the estimate is the noise level as well.
    la = adaptive.LiveAnalyzer(SR)
    la.push(np.random.default_rng(2).standard_normal(20 * SR) * 10 ** (-50 / 20))
    assert la.snapshot()["noise_floor_dbfs"] == pytest.approx(-50.0, abs=1.5)


def test_rest_after_60_s_without_plausible_speech(tmp_path):
    log = tmp_path / "r.jsonl"
    cfg, chain, ctrl = make(log_path=log)
    # Earlier drift (simulated): de-esser -36, compressor -25; calibrated values -30 / -20.
    chain.set_param("deesser", "threshold_db", -36.0)
    chain.set_param("compressor", "threshold_db", -25.0)
    chain.history.clear()
    impl = lambda i: meters_dict(speech=-40.0, ratio=0.15, floor=-60.0)
    # 30 s learning + up to 60 s implausible: nothing but expander tracking.
    changes = run(ctrl, impl, 15, t0=LEARN_S)          # 30..58 s
    assert [c for c in changes if c["stage"] in ("deesser", "compressor")] == []
    changes = run(ctrl, impl, 40, t0=LEARN_S + 30.0)   # 60..138 s: rest kicks in at 60 s
    rest = [c for c in changes if c["reason"] == "rest"]
    assert rest and all(c["stage"] in ("deesser", "compressor") for c in rest)
    assert rest[0]["t"] == pytest.approx(60.0)
    assert all(abs(c["new"] - c["old"]) == pytest.approx(STEP_DB) for c in rest)
    assert chain.get_param("deesser", "threshold_db") == pytest.approx(-30.0)
    assert chain.get_param("compressor", "threshold_db") == pytest.approx(-20.0)
    # Plausible speech again: tracking resumes, no further rest steps.
    changes = run(ctrl, lambda i: meters_dict(speech=-24.0, band_peak=-22.0, sib=-4.0), 10, t0=LEARN_S + 110.0)
    assert all(c["reason"] != "rest" for c in changes)
    ctrl.close()
    since = [e["since_s"] for e in events(log) if e.get("event") == "implausible_speech"]
    assert since[0] == pytest.approx(30.0) and max(since) >= 100.0


def test_keyboard_clicks_do_not_move_deesser_or_compressor(tmp_path):
    """Listening phase: 3 min of typing (3 strokes/s, -30 dBFS) on -55 dBFS noise."""
    log = tmp_path / "k.jsonl"
    cfg, chain, ctrl = make(log_path=log)
    x = synth.make_keyboard_clicks(180.0, SR, rate_per_s=3.0, level_dbfs=-30.0, noise_floor_dbfs=-55.0, seed=1)
    la = adaptive.LiveAnalyzer(SR)
    changes = []
    block = int(INTERVAL_S * SR)
    for i in range(0, x.size, block):
        la.push(x[i:i + block])
        m = meters_dict()
        m["analysis"] = la.snapshot()
        changes.extend(ctrl.update(m, now=(i + block) / SR))
    assert [c for c in changes if c["stage"] in ("deesser", "compressor")] == []
    ctrl.close()
    impl = [e for e in events(log) if e.get("event") == "implausible_speech"]
    assert len(impl) >= 60
    assert chain.get_param("deesser", "threshold_db") == cfg["chain"]["deesser"]["threshold_db"]
    assert chain.get_param("compressor", "threshold_db") == cfg["chain"]["compressor"]["threshold_db"]


def test_distant_quiet_voice_does_not_move_deesser_or_compressor(tmp_path):
    """Listening phase: 3 min of a quiet voice at -42 dBFS (colleague, playback)."""
    log = tmp_path / "f.jsonl"
    cfg, chain, ctrl = make(log_path=log)
    x = synth.make_speech_like(180.0, SR, level_dbfs=-42.0, noise_floor_dbfs=-55.0, sibilance_db=3.0, seed=2)
    la = adaptive.LiveAnalyzer(SR)
    changes = []
    block = int(INTERVAL_S * SR)
    for i in range(0, x.size, block):
        la.push(x[i:i + block])
        m = meters_dict()
        m["analysis"] = la.snapshot()
        changes.extend(ctrl.update(m, now=(i + block) / SR))
    assert [c for c in changes if c["stage"] in ("deesser", "compressor")] == []
    assert la.snapshot()["speech_level_dbfs"] == pytest.approx(-42.0, abs=2.0)
    ctrl.close()
    assert any(e.get("event") == "implausible_speech" for e in events(log))


# --------------------------------------------------------------------------- protection rules

def test_speech_cut_needs_two_consecutive_intervals_then_steps_without_deadzone():
    cfg, chain, ctrl = make({"expander": {"threshold_db": -50.0}})  # exactly on target (floor -60 + 10)
    cut = lambda: meters_dict(expander_gr=8.0, floor=-60.0, active=True)
    # First interval: suspicion only, no change.
    assert [c for c in ctrl.update(cut(), now=LEARN_S) if c["stage"] == "expander"] == []
    # Second consecutive interval: immediate step although the value sits inside the dead zone.
    changes = ctrl.update(cut(), now=LEARN_S + INTERVAL_S)
    exp = [c for c in changes if c["stage"] == "expander"]
    assert len(exp) == 1 and exp[0]["reason"] == "speech_cut"
    assert exp[0]["new"] == pytest.approx(-50.0 - STEP_DB)
    # Still cutting -> another immediate step (no 2-interval spacing).
    changes = ctrl.update(cut(), now=LEARN_S + 2 * INTERVAL_S)
    assert [c["reason"] for c in changes if c["stage"] == "expander"] == ["speech_cut"]
    assert chain.get_param("expander", "threshold_db") == pytest.approx(-51.0)
    # One clean interval resets the streak: the next suspicious one does not act alone.
    ctrl.update(meters_dict(expander_gr=0.0, floor=-60.0, active=True), now=LEARN_S + 3 * INTERVAL_S)
    assert [c for c in ctrl.update(cut(), now=LEARN_S + 4 * INTERVAL_S) if c["stage"] == "expander"] == []


def test_speech_cut_uses_the_max_meter_not_the_mean():
    cfg, chain, ctrl = make({"expander": {"threshold_db": -50.0}})
    for k in range(2):
        m = meters_dict(floor=-60.0, active=True)
        m["expander"] = {"gain_reduction_mean_db": 1.0, "gain_reduction_max_db": 7.0}
        changes = ctrl.update(m, now=LEARN_S + k * INTERVAL_S)
    assert [c["reason"] for c in changes if c["stage"] == "expander"] == ["speech_cut"]


def test_expander_reduction_in_pauses_is_not_speech_cut():
    cfg, chain, ctrl = make({"expander": {"threshold_db": -50.0}})
    for k in range(3):
        changes = ctrl.update(meters_dict(expander_gr=15.0, floor=-60.0, active=False), now=LEARN_S + k * INTERVAL_S)
        assert [c for c in changes if c["stage"] == "expander"] == []
    # A pause that ends 70 ms into the interval leaves 39 of 40 frames active (97.5 %):
    # the maximum stems from the closed gate, not from speech.
    for k in range(3, 6):
        m = meters_dict(expander_gr=14.0, floor=-60.0, active=True)
        m["analysis"]["recent_speech_ratio"] = 39 / 40
        changes = ctrl.update(m, now=LEARN_S + k * INTERVAL_S)
        assert [c for c in changes if c["stage"] == "expander"] == []


def test_meters_collected_during_learning_do_not_trigger_after_it():
    cfg, chain, ctrl = make({"expander": {"threshold_db": -50.0}})
    # Heavy "speech cut" readings during the learning phase ...
    for k in range(15):
        ctrl.update(meters_dict(expander_gr=12.0, floor=-60.0, active=True), now=k * INTERVAL_S)
    # ... are dropped at learn_done: two clean intervals after it change nothing.
    for k in range(2):
        changes = ctrl.update(meters_dict(expander_gr=0.0, floor=-60.0, active=True), now=LEARN_S + k * INTERVAL_S)
        assert changes == []


def test_limiter_overload_blocks_compressor_raise_and_holds_normalizer(tmp_path):
    log = tmp_path / "lim.jsonl"
    cfg, chain, ctrl = make(log_path=log)
    # speech -15 -> compressor target -15 (above the current -20): a raise is wanted.
    changes = ctrl.update(meters_dict(limiter_gr=5.0, norm_gain=4.0, speech=-15.0), now=LEARN_S)
    assert [c for c in changes if c["stage"] == "compressor"] == []
    hold = [c for c in changes if c["stage"] == "normalizer"]
    assert len(hold) == 1 and hold[0]["reason"] == "limiter_overload"
    assert hold[0]["old"] == 12.0 and hold[0]["new"] == pytest.approx(4.0)
    assert chain.get_param("normalizer", "max_gain_db") == pytest.approx(4.0)
    assert ctrl.limiter_protect_active
    # Lowering the compressor threshold is still allowed under protection.
    changes = ctrl.update(meters_dict(limiter_gr=5.0, norm_gain=4.0, speech=-25.0), now=LEARN_S + INTERVAL_S)
    assert [c["new"] - c["old"] for c in changes if c["stage"] == "compressor"] == [pytest.approx(-STEP_DB)]
    # Limiter relaxes: normalizer restored and the raise proceeds.
    changes = ctrl.update(meters_dict(limiter_gr=0.0, norm_gain=1.0, speech=-15.0), now=LEARN_S + 2 * INTERVAL_S)
    rel = [c for c in changes if c["stage"] == "normalizer"]
    assert len(rel) == 1 and rel[0]["reason"] == "limiter_recovered" and rel[0]["new"] == 12.0
    assert not ctrl.limiter_protect_active
    changes = ctrl.update(meters_dict(limiter_gr=0.0, speech=-15.0), now=LEARN_S + 4 * INTERVAL_S)
    assert [c["new"] - c["old"] for c in changes if c["stage"] == "compressor"] == [pytest.approx(STEP_DB)]
    ctrl.close()
    protect = [e for e in events(log) if e.get("event") == "limiter_protect"]
    assert protect[0]["active"] is True and protect[0]["hold_normalizer_gain"] is True
    assert protect[1]["active"] is False


def test_limiter_protection_reads_the_interval_mean_meter():
    cfg, chain, ctrl = make()
    m = meters_dict(norm_gain=4.0)
    m["limiter"] = {"gain_reduction_mean_db": 2.9, "gain_reduction_max_db": 12.0}
    ctrl.update(m, now=LEARN_S)
    assert not ctrl.limiter_protect_active          # mean below 3 dB although the max is 12 dB
    m["limiter"] = {"gain_reduction_mean_db": 3.1, "gain_reduction_max_db": 3.1}
    ctrl.update(m, now=LEARN_S + INTERVAL_S)
    assert ctrl.limiter_protect_active


# --------------------------------------------------------------------------- freeze / logging

def test_freeze_stops_changes_but_keeps_logging(tmp_path):
    log = tmp_path / "f.jsonl"
    cfg, chain, ctrl = make(log_path=log)
    ctrl.freeze(True)
    changes = run(ctrl, lambda i: meters_dict(floor=-70.0), 5)
    assert changes == [] and chain.history == []
    ctrl.freeze(False)
    changes = run(ctrl, lambda i: meters_dict(floor=-70.0), 2, t0=LEARN_S + 5 * INTERVAL_S)
    # Two intervals after unfreezing: exactly one expander step (speech -20 already
    # matches the compressor threshold -20, sibilance -15 keeps the de-esser at rest).
    assert [c["stage"] for c in changes] == ["expander"]
    assert changes[0]["new"] == pytest.approx(-45.0 - STEP_DB)
    ctrl.close()
    entries = events(log)
    frozen = [e for e in entries if e.get("event") == "frozen"]
    assert len(frozen) == 5
    assert frozen[0]["proposed"]["stage"] == "expander"
    assert [e["on"] for e in entries if e.get("event") == "freeze"] == [True, False]


def test_jsonl_lines_are_valid_and_complete(tmp_path):
    log = tmp_path / "log" / "adaptive.jsonl"
    cfg, chain, ctrl = make(log_path=log)
    changes = run(ctrl, lambda i: meters_dict(floor=-60.0, speech=-24.0, sib=-4.0, band_peak=-22.0), 10, t0=0.0)
    changes += run(ctrl, lambda i: meters_dict(floor=-60.0, speech=-24.0, sib=-4.0, band_peak=-22.0), 10)
    ctrl.close()
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= len(changes) + 10
    n_changes = 0
    for line in lines:
        entry = json.loads(line)
        assert "t" in entry
        if "event" in entry:
            continue
        assert set(entry) == {"t", "stage", "name", "old", "new", "reason"}
        assert isinstance(entry["old"], float) and isinstance(entry["new"], float)
        n_changes += 1
    assert n_changes == len(changes)
    assert changes[0] == json.loads([l for l in lines if "event" not in json.loads(l)][0])


def test_missing_analysis_is_logged_and_skipped(tmp_path):
    log = tmp_path / "n.jsonl"
    cfg, chain, ctrl = make(log_path=log)
    m = meters_dict()
    del m["analysis"]
    assert ctrl.update(m, now=LEARN_S) == []
    assert ctrl.update(meters_dict(ready=False), now=LEARN_S + INTERVAL_S) == []
    ctrl.close()
    assert [e.get("event") for e in events(log)].count("no_analysis") == 2


def test_invalid_adaptive_settings_raise(base_cfg):
    cfg = copy.deepcopy(base_cfg)
    cfg["adaptive"]["interval_s"] = 0.0
    with pytest.raises(ValueError):
        adaptive.AdaptiveController(cfg, FakeChain(cfg))
    cfg = copy.deepcopy(base_cfg)
    cfg["adaptive"]["bounds"]["expander.threshold_db"] = [-30.0, -70.0]
    with pytest.raises(ValueError):
        adaptive.AdaptiveController(cfg, FakeChain(cfg))
    cfg = copy.deepcopy(base_cfg)
    del cfg["adaptive"]["bounds"]["compressor.threshold_db"]
    with pytest.raises(ValueError, match="compressor.threshold_db"):
        adaptive.AdaptiveController(cfg, FakeChain(cfg))


# --------------------------------------------------------------------------- LiveAnalyzer

def test_live_analyzer_not_ready_before_two_seconds():
    la = adaptive.LiveAnalyzer(SR)
    la.push(np.zeros(int(1.5 * SR), dtype=np.float32))
    assert la.snapshot()["ready"] is False
    la.push(np.zeros(int(0.6 * SR), dtype=np.float32))
    snap = la.snapshot()
    assert snap["ready"] is True
    assert snap["speech_detected"] is False
    assert snap["frames"] == 42
    assert snap["band_hz"] == (6500.0, 1.0)


def test_live_analyzer_matches_offline_meters_on_synth():
    x = synth.make_speech_like(30.0, SR, level_dbfs=-24.0, noise_floor_dbfs=-52.0, sibilance_db=0.0, seed=12)
    la = adaptive.LiveAnalyzer(SR, deesser_freq_hz=6800.0, deesser_bandwidth_oct=1.0)
    for i in range(0, x.size, 256):
        la.push(x[i:i + 256])
    snap = la.snapshot()
    tail = x[-int(adaptive.HISTORY_S * SR):]
    assert snap["history_s"] == pytest.approx(adaptive.HISTORY_S)
    assert snap["speech_detected"] is True
    assert snap["noise_floor_dbfs"] == pytest.approx(meters.noise_floor_dbfs(tail, SR, meters.ANALYSIS_FRAME_MS), abs=1.0)
    assert snap["speech_level_dbfs"] == pytest.approx(meters.speech_level_dbfs(tail, SR), abs=1.0)
    assert snap["dynamic_range_db"] == pytest.approx(meters.dynamic_range_db(meters.frame_rms_db(tail, SR)), abs=1.0)
    assert snap["sibilance_ratio_db"] == pytest.approx(meters.sibilance_ratio_db(tail, SR), abs=1.5)
    assert snap["band_peak_p95_dbfs"] == pytest.approx(meters.band_peak_dbfs(tail, SR, 6800.0, 1.0), abs=1.5)
    assert 0.5 <= snap["speech_ratio"] <= 0.85


def test_live_analyzer_block_size_independent_and_resettable():
    x = synth.make_speech_like(10.0, SR, seed=1)
    a = adaptive.LiveAnalyzer(SR)
    b = adaptive.LiveAnalyzer(SR)
    a.push(x)
    for i in range(0, x.size, 64):
        b.push(x[i:i + 64])
    sa, sb = a.snapshot(), b.snapshot()
    for key in ("noise_floor_dbfs", "speech_level_dbfs", "sibilance_ratio_db", "band_peak_p95_dbfs"):
        assert sa[key] == pytest.approx(sb[key], abs=1e-9)
    a.reset()
    assert a.snapshot()["frames"] == 0
    a.set_deesser_band(7000.0, 0.5)
    assert a.band_hz == (7000.0, 0.5)


def test_live_analyzer_speech_active_flag_follows_signal():
    la = adaptive.LiveAnalyzer(SR)
    rng = np.random.default_rng(0)
    noise = rng.standard_normal(5 * SR) * 10 ** (-50 / 20)
    loud = rng.standard_normal(2 * SR) * 10 ** (-20 / 20)
    la.push(noise)
    la.push(loud)
    assert la.snapshot()["speech_active"] is True
    la.push(noise[: SR])
    assert la.snapshot()["speech_active"] is False


# --------------------------------------------------------------------------- closed loop with the real chain

def _run_closed_loop(x, cfg, log, learn_s=LEARN_S, block=2048):
    """Chain + LiveAnalyzer + AdaptiveController on a simulated clock; update every interval."""
    from voicechain.dsp.chain import Chain
    cfg = copy.deepcopy(cfg)
    cfg["adaptive"].update({"learn_s": learn_s, "interval_s": INTERVAL_S, "max_step_db": STEP_DB})
    chain = Chain.from_config(cfg)
    la = adaptive.LiveAnalyzer(SR, deesser_freq_hz=cfg["chain"]["deesser"]["freq_hz"],
                               deesser_bandwidth_oct=cfg["chain"]["deesser"]["bandwidth_oct"])
    ctrl = adaptive.AdaptiveController(cfg, chain, clock=lambda: 0.0, log_path=log)
    tick = int(INTERVAL_S * SR)
    changes = []
    next_tick = tick
    for i in range(0, x.size, block):
        blk = x[i:i + block]
        la.push(blk)
        chain.process(blk)
        if i + block >= next_tick:
            m = chain.get_meters()
            m["analysis"] = la.snapshot()
            changes.extend(ctrl.update(m, now=next_tick / SR))
            next_tick += tick
    ctrl.close()
    return chain, changes


def test_closed_loop_with_real_chain_follows_a_noise_floor_step(tmp_path, base_cfg):
    """Chain + LiveAnalyzer + AdaptiveController over 300 s of normal speech: floor
    -55 dBFS for 100 s, then -45 dBFS. Learning phase untouched, expander follows
    monotonically, bounds hold, every change is logged with a reason, de-esser and
    compressor stay quiet, no speech_cut saw-tooth (at most one false alarm)."""
    quiet = synth.make_speech_like(100.0, SR, level_dbfs=-26.0, noise_floor_dbfs=-55.0, sibilance_db=-6.0,
                                   seed=21, long_pause_s=6.0)
    loud = synth.make_speech_like(200.0, SR, level_dbfs=-26.0, noise_floor_dbfs=-45.0, sibilance_db=-6.0,
                                  seed=22, long_pause_s=6.0)
    x = np.concatenate([quiet, loud])
    cfg = cal.recommend(cal.analyze(quiet, SR), base_cfg)
    start_thr = cfg["chain"]["expander"]["threshold_db"]
    trim = cfg["chain"]["input_trim"]["gain_db"]
    log = tmp_path / "loop.jsonl"
    chain, changes = _run_closed_loop(x, cfg, log)
    exp = [c for c in changes if c["stage"] == "expander"]
    assert changes and all(c["t"] >= LEARN_S for c in changes)
    assert [c for c in changes if c["reason"] == "speech_cut"].__len__() <= 1
    # First 100 s: the calibrated threshold already matches the floor -> no expander step.
    assert all(c["t"] > 100.0 for c in exp)
    tracked = [c for c in exp if c["reason"] == "noise_floor_track"]
    steps = [c["new"] - c["old"] for c in tracked]
    assert len(steps) >= 10 and all(s == pytest.approx(STEP_DB) for s in steps)   # monotone, +0.5 each
    assert all(np.diff([c["t"] for c in tracked]) >= 2 * INTERVAL_S - 1e-9)
    lo, hi = cfg["adaptive"]["bounds"]["expander.threshold_db"]
    assert all(lo <= c["new"] <= hi for c in exp)
    target = min(-45.0 + trim + cal.EXPANDER_FLOOR_MARGIN_DB, -20.0 - cal.EXPANDER_SPEECH_MARGIN_DB)
    final = chain.get_param("expander", "threshold_db")
    assert start_thr == pytest.approx(-55.0 + trim + cal.EXPANDER_FLOOR_MARGIN_DB, abs=1.0)
    assert abs(final - target) <= adaptive.EXPANDER_DEADZONE_DB + 0.5
    # Converged within ~90 s after the step (16 steps of 4 s plus detection): no step after 200 s.
    assert max(c["t"] for c in tracked) <= 200.0
    other = [c for c in changes if c["stage"] != "expander"]
    assert len(other) <= 2, other                                   # speaker unchanged
    entries = events(log)
    logged = [e for e in entries if "stage" in e]
    assert len(logged) == len(changes)
    assert all(set(e) == {"t", "stage", "name", "old", "new", "reason"} for e in logged)
    # Ticks at 2, 4, ..., 28 s fall into the 30 s learning phase: 14 entries.
    assert [e["event"] for e in entries if "event" in e].count("learning") == 14


def test_dense_talk_in_an_unchanged_room_does_not_move_the_expander(tmp_path, base_cfg):
    """300 s of dense talk (10 % pauses) in the room the chain was calibrated for:
    the floor estimate stays put, so the expander threshold does not wander."""
    sample = synth.make_speech_like(60.0, SR, noise_floor_dbfs=-50.0, seed=31, lead_silence_s=10.0, long_pause_s=6.0)
    cfg = cal.recommend(cal.analyze(sample, SR), base_cfg)
    dense = synth.make_speech_like(300.0, SR, noise_floor_dbfs=-50.0, pause_ratio=0.1, seed=32)
    log = tmp_path / "dense.jsonl"
    chain, changes = _run_closed_loop(dense, cfg, log)
    exp = [c for c in changes if c["stage"] == "expander"]
    assert exp == [], exp
    assert chain.get_param("expander", "threshold_db") == cfg["chain"]["expander"]["threshold_db"]


def test_pause_ending_just_after_an_interval_start_is_no_speech_cut(tmp_path, base_cfg):
    """A pause that ends 70 ms after the meter interval begins leaves the gate closed
    at the interval start (expander maximum 12-14 dB) while the analyzer counts 39
    of 40 frames active. That must not count as speech being cut."""
    speech = synth.make_speech_like(40.0, SR, level_dbfs=-26.0, noise_floor_dbfs=-55.0, sibilance_db=-6.0,
                                    seed=23, pause_ratio=0.0)          # continuous speech, no pauses
    cfg = cal.recommend(cal.analyze(synth.make_speech_like(60.0, SR, level_dbfs=-26.0, noise_floor_dbfs=-55.0,
                                                           sibilance_db=-6.0, seed=23, long_pause_s=6.0), SR), base_cfg)
    rng = np.random.default_rng(5)
    noise = (rng.standard_normal(SR) * 10 ** (-55 / 20)).astype(np.float32)
    # Blocks of 2 s: speech, then a pause that ends 70 ms into the next interval, repeated.
    pieces = []
    t = 0.0
    k = 0
    while t < 60.0:
        pieces.append(speech[int(k % 30 * SR): int(k % 30 * SR) + int(1.93 * SR)])   # 1.93 s speech
        pieces.append(noise[: int(0.14 * SR)])                                      # 0.14 s pause ...
        t += 2.07                                                                   # ... ending at +0.07 s
        k += 1
    x = np.concatenate(pieces)
    log = tmp_path / "onset.jsonl"
    chain, changes = _run_closed_loop(x, cfg, log, learn_s=4.0)
    cuts = [c for c in changes if c["reason"] == "speech_cut"]
    assert cuts == [], cuts
    assert chain.get_param("expander", "threshold_db") >= cfg["chain"]["expander"]["threshold_db"] - 1e-9
