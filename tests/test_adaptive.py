"""Tests for voicechain.adaptive with a FakeChain and scripted meters."""
import copy
import json

import numpy as np
import pytest

from voicechain import adaptive, meters, synth
from voicechain.calibrate import DEFAULT_CONFIG

SR = 48000
LEARN_S = 30.0
INTERVAL_S = 2.0
STEP_DB = 0.5


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


def analysis(floor=-60.0, speech=-20.0, sib=-15.0, active=True, ready=True, detected=True):
    return {"ready": ready, "speech_detected": detected, "noise_floor_dbfs": floor,
            "speech_level_dbfs": speech, "sibilance_ratio_db": sib, "speech_active": active,
            "speech_ratio": 0.7, "frames": 400, "history_s": 20.0}


def meters_dict(limiter_gr=0.0, expander_gr=0.0, norm_gain=0.0, **analysis_kw):
    return {"limiter": {"gain_reduction_db": limiter_gr},
            "expander": {"gain_reduction_db": expander_gr},
            "normalizer": {"gain_db": norm_gain},
            "output": {"peak_dbfs": -3.0, "rms_dbfs": -20.0},
            "analysis": analysis(**analysis_kw)}


def make(cfg_overrides=None, log_path=None):
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["adaptive"].update({"learn_s": LEARN_S, "interval_s": INTERVAL_S, "max_step_db": STEP_DB})
    if cfg_overrides:
        for stage, values in cfg_overrides.items():
            cfg["chain"][stage].update(values)
    chain = FakeChain(cfg)
    ctrl = adaptive.AdaptiveController(cfg, chain, clock=lambda: 0.0, log_path=log_path)
    return cfg, chain, ctrl


def run(ctrl, meters_fn, n_intervals, t0=LEARN_S):
    """Call update once per interval starting at t0; return all changes."""
    changes = []
    for i in range(n_intervals):
        t = t0 + i * INTERVAL_S
        changes.extend(ctrl.update(meters_fn(i), now=t))
    return changes


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
    events = [json.loads(l)["event"] for l in log.read_text().splitlines() if "event" in json.loads(l)]
    assert events.count("learning") == 15
    assert "learn_done" in events


def test_learning_phase_is_measured_from_construction_with_the_given_clock():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["adaptive"].update({"learn_s": LEARN_S, "interval_s": INTERVAL_S, "max_step_db": STEP_DB})
    chain = FakeChain(cfg)
    ctrl = adaptive.AdaptiveController(cfg, chain, clock=lambda: 100.0)
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
    # Converged: no further changes in the next 20 intervals.
    more = run(ctrl, lambda i: meters_dict(floor=-60.0, speech=-20.0), 20, t0=LEARN_S + 40 * INTERVAL_S)
    assert [c for c in more if c["stage"] == "expander"] == []


def test_deesser_and_compressor_converge_to_targets():
    cfg, chain, ctrl = make()
    # speech -26, sibilance -13: de-esser target = min(-30, -26 - 13 + 6 - 4/(1-1/4)) = -38.33
    # (8.3 dB away, 14 steps until inside the 1.5 dB dead zone -> 28 intervals);
    # compressor target = speech = -26; start -20 (6 dB away, 8 steps -> 16 intervals).
    changes = run(ctrl, lambda i: meters_dict(speech=-26.0, sib=-13.0), 40)
    assert abs(chain.get_param("deesser", "threshold_db") - (-38.33)) <= adaptive.DEESSER_DEADZONE_DB
    assert chain.get_param("deesser", "threshold_db") < -36.0
    assert abs(chain.get_param("compressor", "threshold_db") - (-26.0)) <= adaptive.COMPRESSOR_DEADZONE_DB
    assert chain.get_param("compressor", "threshold_db") <= -24.0
    for stage in ("deesser", "compressor"):
        signs = {np.sign(c["new"] - c["old"]) for c in changes if c["stage"] == stage}
        assert len(signs) == 1, f"{stage} changed direction: {signs}"
    reasons = {c["reason"] for c in changes}
    assert {"sibilance_track", "speech_level_track"} <= reasons


def test_deesser_returns_to_rest_threshold_when_sibilance_is_low():
    cfg, chain, ctrl = make({"deesser": {"threshold_db": -40.0}})
    ctrl.deesser_rest_threshold_db = -30.0   # calibrated resting value differs from current
    changes = run(ctrl, lambda i: meters_dict(sib=-25.0), 40)
    assert abs(chain.get_param("deesser", "threshold_db") - (-30.0)) <= adaptive.DEESSER_DEADZONE_DB
    assert all(c["reason"] == "sibilance_rest" for c in changes if c["stage"] == "deesser")


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
    changes = run(ctrl, lambda i: meters_dict(floor=-80.0, speech=-5.0, sib=10.0), 40)
    assert changes
    assert max(abs(c["new"] - c["old"]) for c in changes) <= STEP_DB + 1e-9


def test_bounds_are_never_violated():
    cfg, chain, ctrl = make()
    lo_e, hi_e = cfg["adaptive"]["bounds"]["expander.threshold_db"]
    lo_c, hi_c = cfg["adaptive"]["bounds"]["compressor.threshold_db"]
    # floor -95 -> target -87 (below -70); speech -45 -> compressor target -51 (below -35).
    changes = run(ctrl, lambda i: meters_dict(floor=-95.0, speech=-45.0), 120)
    for c in changes:
        lo, hi = cfg["adaptive"]["bounds"][f"{c['stage']}.{c['name']}"]
        assert lo <= c["new"] <= hi
    # The clipped target sits on the bound; tracking stops inside the dead zone.
    assert lo_e <= chain.get_param("expander", "threshold_db") <= lo_e + adaptive.EXPANDER_DEADZONE_DB
    assert lo_c <= chain.get_param("compressor", "threshold_db") <= lo_c + adaptive.COMPRESSOR_DEADZONE_DB
    # Upper side: speech -5 -> compressor target -5 is clipped to the -10 bound; floor -30
    # -> expander target -22 is clipped to the -30 bound; heavy sibilance keeps the -30 rest threshold.
    # From about -68 up to -30 is 38 dB = 76 steps of 0.5 dB, one per two intervals.
    changes = run(ctrl, lambda i: meters_dict(floor=-30.0, speech=-5.0, sib=20.0), 160,
                  t0=LEARN_S + 120 * INTERVAL_S)
    for c in changes:
        lo, hi = cfg["adaptive"]["bounds"][f"{c['stage']}.{c['name']}"]
        assert lo <= c["new"] <= hi
    assert hi_e - adaptive.EXPANDER_DEADZONE_DB <= chain.get_param("expander", "threshold_db") <= hi_e
    assert chain.get_param("deesser", "threshold_db") == pytest.approx(-30.0)
    assert hi_c - adaptive.COMPRESSOR_DEADZONE_DB <= chain.get_param("compressor", "threshold_db") <= hi_c


def test_deesser_rest_threshold_outside_bounds_is_walked_to_the_bound_in_steps():
    cfg, chain, ctrl = make({"deesser": {"threshold_db": -10.0}})   # above the -15 upper bound
    lo_d, hi_d = cfg["adaptive"]["bounds"]["deesser.threshold_db"]
    changes = run(ctrl, lambda i: meters_dict(sib=-25.0), 40)
    steps = [c for c in changes if c["stage"] == "deesser"]
    # -10 -> -15 is 5 dB; the dead zone (1.5 dB) stops tracking at -13.5: 7 steps of 0.5 dB.
    assert len(steps) == 7
    assert all(c["new"] - c["old"] == pytest.approx(-STEP_DB) for c in steps)
    assert all(c["new"] > hi_d - adaptive.DEESSER_DEADZONE_DB - 1e-9 for c in steps)
    assert chain.get_param("deesser", "threshold_db") == pytest.approx(-13.5)


def test_input_trim_is_added_to_analysis_levels():
    cfg, chain, ctrl = make({"input_trim": {"gain_db": 6.0}})
    # Measured floor -60 before trim -> -54 after trim -> target -44 (not -50).
    run(ctrl, lambda i: meters_dict(floor=-60.0, speech=-26.0), 40)
    assert abs(chain.get_param("expander", "threshold_db") - (-44.0)) <= adaptive.EXPANDER_DEADZONE_DB
    assert chain.get_param("expander", "threshold_db") > -46.0


# --------------------------------------------------------------------------- protection rules

def test_speech_cut_lowers_expander_immediately_even_inside_deadzone():
    cfg, chain, ctrl = make({"expander": {"threshold_db": -50.0}})  # exactly on target (floor -60 + 10)
    changes = ctrl.update(meters_dict(expander_gr=8.0, floor=-60.0, active=True), now=LEARN_S)
    exp = [c for c in changes if c["stage"] == "expander"]
    assert len(exp) == 1
    assert exp[0]["reason"] == "speech_cut"
    assert exp[0]["new"] == pytest.approx(-50.0 - STEP_DB)
    # Next interval still cutting -> another immediate step (no 2-interval spacing).
    changes = ctrl.update(meters_dict(expander_gr=8.0, floor=-60.0, active=True), now=LEARN_S + INTERVAL_S)
    assert [c["reason"] for c in changes if c["stage"] == "expander"] == ["speech_cut"]
    assert chain.get_param("expander", "threshold_db") == pytest.approx(-51.0)


def test_expander_reduction_in_pauses_is_not_speech_cut():
    cfg, chain, ctrl = make({"expander": {"threshold_db": -50.0}})
    changes = ctrl.update(meters_dict(expander_gr=15.0, floor=-60.0, active=False), now=LEARN_S)
    assert [c for c in changes if c["stage"] == "expander"] == []


def test_limiter_overload_blocks_compressor_raise_and_holds_normalizer(tmp_path):
    log = tmp_path / "lim.jsonl"
    cfg, chain, ctrl = make(log_path=log)
    # speech -10 -> compressor target -10 (above the current -20): a raise is wanted.
    changes = ctrl.update(meters_dict(limiter_gr=5.0, norm_gain=4.0, speech=-10.0), now=LEARN_S)
    comp = [c for c in changes if c["stage"] == "compressor"]
    assert comp == []
    hold = [c for c in changes if c["stage"] == "normalizer"]
    assert len(hold) == 1 and hold[0]["reason"] == "limiter_overload"
    assert hold[0]["old"] == 12.0 and hold[0]["new"] == pytest.approx(4.0)
    assert chain.get_param("normalizer", "max_gain_db") == pytest.approx(4.0)
    assert ctrl.limiter_protect_active
    # Lowering the compressor threshold is still allowed under protection.
    changes = ctrl.update(meters_dict(limiter_gr=5.0, norm_gain=4.0, speech=-30.0), now=LEARN_S + INTERVAL_S)
    assert [c["new"] - c["old"] for c in changes if c["stage"] == "compressor"] == [pytest.approx(-STEP_DB)]
    # Limiter relaxes: normalizer restored and the raise proceeds.
    changes = ctrl.update(meters_dict(limiter_gr=0.0, norm_gain=1.0, speech=-10.0), now=LEARN_S + 2 * INTERVAL_S)
    rel = [c for c in changes if c["stage"] == "normalizer"]
    assert len(rel) == 1 and rel[0]["reason"] == "limiter_recovered" and rel[0]["new"] == 12.0
    assert not ctrl.limiter_protect_active
    changes = ctrl.update(meters_dict(limiter_gr=0.0, speech=-10.0), now=LEARN_S + 4 * INTERVAL_S)
    assert [c["new"] - c["old"] for c in changes if c["stage"] == "compressor"] == [pytest.approx(STEP_DB)]
    ctrl.close()
    flags = [json.loads(l) for l in log.read_text().splitlines()]
    protect = [e for e in flags if e.get("event") == "limiter_protect"]
    assert protect[0]["active"] is True and protect[0]["hold_normalizer_gain"] is True
    assert protect[1]["active"] is False


def test_limiter_mean_is_averaged_over_calls_within_an_interval():
    cfg, chain, ctrl = make()
    # Four samples at 2 dB and one at 5 dB -> mean 2.6 dB < 3 dB: no protection.
    for k, gr in enumerate((2.0, 2.0, 2.0, 2.0)):
        ctrl.update(meters_dict(limiter_gr=gr), now=LEARN_S - 1.0 + 0.1 * k)
    changes = ctrl.update(meters_dict(limiter_gr=5.0), now=LEARN_S)
    assert not ctrl.limiter_protect_active
    assert [c for c in changes if c["stage"] == "normalizer"] == []


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
    entries = [json.loads(l) for l in log.read_text().splitlines()]
    frozen = [e for e in entries if e.get("event") == "frozen"]
    assert len(frozen) == 5
    assert frozen[0]["proposed"]["stage"] == "expander"
    assert [e["on"] for e in entries if e.get("event") == "freeze"] == [True, False]


def test_jsonl_lines_are_valid_and_complete(tmp_path):
    log = tmp_path / "log" / "adaptive.jsonl"
    cfg, chain, ctrl = make(log_path=log)
    changes = run(ctrl, lambda i: meters_dict(floor=-60.0, speech=-20.0, sib=-4.0), 10, t0=0.0)
    changes += run(ctrl, lambda i: meters_dict(floor=-60.0, speech=-20.0, sib=-4.0), 10)
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
    events = [json.loads(l).get("event") for l in log.read_text().splitlines()]
    assert events.count("no_analysis") == 2


def test_invalid_adaptive_settings_raise():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["adaptive"]["interval_s"] = 0.0
    with pytest.raises(ValueError):
        adaptive.AdaptiveController(cfg, FakeChain(cfg))
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["adaptive"]["bounds"]["expander.threshold_db"] = [-30.0, -70.0]
    with pytest.raises(ValueError):
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


def test_live_analyzer_matches_offline_meters_on_synth():
    x = synth.make_speech_like(30.0, SR, level_dbfs=-24.0, noise_floor_dbfs=-52.0, sibilance_db=0.0, seed=12)
    la = adaptive.LiveAnalyzer(SR)
    for i in range(0, x.size, 256):
        la.push(x[i:i + 256])
    snap = la.snapshot()
    tail = x[-int(adaptive.HISTORY_S * SR):]
    assert snap["history_s"] == pytest.approx(adaptive.HISTORY_S)
    assert snap["speech_detected"] is True
    assert snap["noise_floor_dbfs"] == pytest.approx(meters.noise_floor_dbfs(tail, SR, meters.ANALYSIS_FRAME_MS), abs=1.0)
    assert snap["speech_level_dbfs"] == pytest.approx(meters.speech_level_dbfs(tail, SR), abs=1.0)
    assert snap["sibilance_ratio_db"] == pytest.approx(meters.sibilance_ratio_db(tail, SR), abs=1.5)
    assert 0.5 <= snap["speech_ratio"] <= 0.85


def test_live_analyzer_block_size_independent_and_resettable():
    x = synth.make_speech_like(10.0, SR, seed=1)
    a = adaptive.LiveAnalyzer(SR)
    b = adaptive.LiveAnalyzer(SR)
    a.push(x)
    for i in range(0, x.size, 64):
        b.push(x[i:i + 64])
    sa, sb = a.snapshot(), b.snapshot()
    for key in ("noise_floor_dbfs", "speech_level_dbfs", "sibilance_ratio_db"):
        assert sa[key] == pytest.approx(sb[key], abs=1e-9)
    a.reset()
    assert a.snapshot()["frames"] == 0


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
