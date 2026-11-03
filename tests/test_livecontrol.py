"""Tests for voicechain.livecontrol (console commands, offsets, reset, relearn, snapshot, kill switch)."""
import copy
import json
import math

import numpy as np
import pytest

from voicechain import adaptive, calibrate as cal, meters, synth
from voicechain.config import default_config_path, load_config
from voicechain.livecontrol import (
    DEFAULT_SNAPSHOT_PATH, INTENSITY_COMPRESSOR_DB_PER_STEP, LOUDNESS_TARGET_BOUNDS_LUFS, OFFSETS, LiveControl,
)
from test_adaptive import FakeChain, LEARN_S, INTERVAL_S, STEP_DB, meters_dict, events, _run_closed_loop

SR = 48000
STATE_KEYS = {"offsets", "target_lufs", "frozen", "muted", "bypass", "processing", "low_snr", "snr_lu",
              "loudness_lufs", "adaptive_enabled", "learning", "tilt_db", "expander_threshold_db",
              "deesser_threshold_db", "compressor_threshold_db"}


@pytest.fixture(scope="module")
def base_cfg():
    return load_config(default_config_path())


class HookLog:
    def __init__(self):
        self.calls = []

    def hooks(self):
        return {"mute": lambda on: self.calls.append(("mute", on)),
                "bypass": lambda on: self.calls.append(("bypass", on)),
                "processing": lambda on: self.calls.append(("processing", on))}


class MeterChain(FakeChain):
    """FakeChain with draining get_meters (counts drains) and non-draining peek_meters."""

    def __init__(self, cfg):
        super().__init__(cfg)
        self.drains = 0
        self.meters = {"normalizer": {"gain_db": 1.0, "loudness_lufs": -18.4, "snr_lu": 25.0, "low_snr": 0.0},
                       "compressor": {"gain_reduction_mean_db": 4.5, "gain_reduction_max_db": 6.0}}

    def get_meters(self):
        self.drains += 1
        out = copy.deepcopy(self.meters)
        for stage in self.meters.values():
            for key in list(stage):
                if key.startswith("gain_reduction"):
                    stage[key] = 0.0
        return out

    def peek_meters(self):
        return copy.deepcopy(self.meters)


def fake_setup(with_controller=True, hooks=True, log_path=None):
    cfg = load_config(default_config_path())
    cfg["adaptive"].update({"learn_s": LEARN_S, "interval_s": INTERVAL_S, "max_step_db": STEP_DB})
    chain = MeterChain(cfg)
    chain.params[("eq", "tilt_db")] = 0.0
    ctrl = adaptive.AdaptiveController(cfg, chain, clock=lambda: 0.0, log_path=log_path) if with_controller else None
    hl = HookLog()
    lc = LiveControl(cfg, chain, controller=ctrl, hooks=hl.hooks() if hooks else None, clock=lambda: 0.0)
    return cfg, chain, ctrl, lc, hl


def finish_learning(ctrl, floor=-60.0, speech=-20.0):
    for k in range(16):
        ctrl.update(meters_dict(floor=floor, speech=speech), now=k * INTERVAL_S)
    ctrl.clock = lambda: 32.0


# --------------------------------------------------------------------------- answer format

def test_answer_format_and_state_keys():
    cfg, chain, ctrl, lc, hl = fake_setup()
    r = lc.handle({"cmd": "status"})
    assert r["ok"] is True and set(r) == {"ok", "state", "message"}
    assert STATE_KEYS <= set(r["state"])
    st = r["state"]
    assert st["target_lufs"] == -18.0 and st["loudness_lufs"] == -18.4 and st["snr_lu"] == 25.0
    assert st["low_snr"] is False and st["adaptive_enabled"] is True and st["learning"] is True
    assert st["processing"] is True and st["offsets"] == {name: 0.0 for name in OFFSETS}
    assert isinstance(r["message"], str) and "LUFS" in r["message"] and "LERNPHASE" in r["message"]
    json.dumps(r, allow_nan=False)   # serialisable for the socket protocol, no NaN/Infinity


def test_status_and_answers_read_meters_without_draining_the_accumulators():
    cfg, chain, ctrl, lc, hl = fake_setup()
    for _ in range(5):
        assert lc.handle({"cmd": "status"})["ok"]
        assert lc.handle({"cmd": "offset", "name": "loudness_lu", "delta": 0.5})["ok"]
    assert chain.drains == 0
    assert chain.meters["compressor"]["gain_reduction_mean_db"] == 4.5   # still there for the regulation tick


@pytest.mark.parametrize("cmd", [
    {"cmd": "nope"}, {"cmd": "offset", "name": "room_db"}, {"cmd": "offset", "name": "x", "delta": 1},
    {"cmd": "offset", "name": "room_db", "delta": 2, "extra": 1}, {"cmd": "set_offset", "name": "tone_db"},
    {"cmd": "freeze", "on": "maybe"}, {"cmd": "marker"}, {"cmd": "marker", "text": "  "},
    {"cmd": "offset", "name": "room_db", "delta": "two"}, {"cmd": "offset", "name": "room_db", "delta": True},
    {"cmd": "offset", "name": "room_db", "delta": float("nan")}, {"cmd": "set_offset", "name": "loudness_lu", "value": float("inf")},
    {"cmd": "processing", "on": 1}, "status", None, {"name": "room_db"},
])
def test_invalid_commands_return_ok_false_without_raising_or_changing_anything(cmd):
    cfg, chain, ctrl, lc, hl = fake_setup()
    r = lc.handle(cmd)
    assert r["ok"] is False and isinstance(r["error"], str) and r["error"]
    assert chain.history == [] and lc.offsets == {name: 0.0 for name in OFFSETS}


def test_failed_set_param_leaves_the_offset_unchanged_and_never_raises():
    cfg, chain, ctrl, lc, hl = fake_setup()
    lc.handle({"cmd": "offset", "name": "loudness_lu", "delta": 0.5})

    def boom(stage, name, value):
        raise RuntimeError("stage exploded")
    chain.set_param = boom
    r = lc.handle({"cmd": "offset", "name": "loudness_lu", "delta": 0.5})
    assert r["ok"] is False and "RuntimeError" in r["error"] and "stage exploded" in r["error"]
    assert lc.offsets["loudness_lu"] == 0.5
    assert lc.handle({"cmd": "offset", "name": "room_db", "delta": 2})["ok"] is False
    assert lc.offsets["room_db"] == 0.0


def test_non_finite_meters_become_null_and_unbekannt():
    cfg, chain, ctrl, lc, hl = fake_setup()
    chain.meters["normalizer"]["snr_lu"] = math.inf
    chain.meters["normalizer"]["loudness_lufs"] = math.nan
    r = lc.handle({"cmd": "status"})
    assert r["state"]["snr_lu"] is None and r["state"]["loudness_lufs"] is None
    assert "unbekannt" in r["message"]
    json.dumps(r, allow_nan=False)


# --------------------------------------------------------------------------- offsets, clamping, stops

@pytest.mark.parametrize("name,lo,hi,step", [(n, *spec) for n, spec in OFFSETS.items()])
def test_offset_steps_move_one_step_and_stop_with_ok_false_at_the_range_end(name, lo, hi, step):
    cfg, chain, ctrl, lc, hl = fake_setup(with_controller=False)
    # A delta larger than one step moves exactly one step.
    r = lc.handle({"cmd": "offset", "name": name, "delta": 10 * step})
    assert r["ok"] and r["state"]["offsets"][name] == pytest.approx(step)
    answers = [lc.handle({"cmd": "offset", "name": name, "delta": step}) for _ in range(50)]
    assert lc.offsets[name] <= hi + 1e-9
    stops = [a for a in answers if not a["ok"]]
    assert stops and all("am Anschlag" in a["error"] and "wirksam" in a["error"] for a in stops)
    assert all(a["ok"] for a in answers[: len(answers) - len(stops)])   # ok first, then only stops
    for _ in range(100):
        lc.handle({"cmd": "offset", "name": name, "delta": -step})
    assert lc.offsets[name] >= lo - 1e-9
    assert lc.handle({"cmd": "set_offset", "name": name, "value": 0})["ok"]
    assert lc.offsets[name] == 0.0
    assert lc.handle({"cmd": "set_offset", "name": name, "value": 0})["ok"] is False   # no change = stop


def test_loudness_offset_moves_the_target_within_minus24_minus14():
    cfg, chain, ctrl, lc, hl = fake_setup()
    r = lc.handle({"cmd": "offset", "name": "loudness_lu", "delta": 0.5})
    assert r["state"]["target_lufs"] == pytest.approx(-17.5)
    assert chain.get_param("normalizer", "target_lufs") == pytest.approx(-17.5)
    assert "+0,5 LU" in r["message"] and "-17,5 LUFS" in r["message"]
    # Base -18: +6 would be -12, clamped to -14 -> effective offset +4, answer names the limit.
    r = lc.handle({"cmd": "set_offset", "name": "loudness_lu", "value": 6.0})
    assert r["ok"] and r["state"]["offsets"]["loudness_lu"] == pytest.approx(4.0)
    assert chain.get_param("normalizer", "target_lufs") == LOUDNESS_TARGET_BOUNDS_LUFS[1]
    assert "begrenzt" in r["message"]
    r = lc.handle({"cmd": "offset", "name": "loudness_lu", "delta": 0.5})
    assert r["ok"] is False and "am Anschlag" in r["error"] and "-14 LUFS" in r["error"]
    assert lc.offsets["loudness_lu"] == pytest.approx(4.0)
    cfg3 = copy.deepcopy(cfg)
    cfg3["chain"]["normalizer"]["target_lufs"] = -20.0
    lc3 = LiveControl(cfg3, MeterChain(cfg3), controller=None)
    r = lc3.handle({"cmd": "set_offset", "name": "loudness_lu", "value": -6.0})
    assert r["ok"] and lc3.chain.get_param("normalizer", "target_lufs") == LOUDNESS_TARGET_BOUNDS_LUFS[0]
    assert lc3.offsets["loudness_lu"] == pytest.approx(-4.0)


def test_threshold_offsets_without_controller_apply_to_the_calibrated_values():
    cfg, chain, ctrl, lc, hl = fake_setup(with_controller=False)
    base_e = cfg["chain"]["expander"]["threshold_db"]
    base_d = cfg["chain"]["deesser"]["threshold_db"]
    base_c = cfg["chain"]["compressor"]["threshold_db"]
    lc.handle({"cmd": "offset", "name": "room_db", "delta": 2})
    assert chain.get_param("expander", "threshold_db") == pytest.approx(base_e + 2.0)
    lc.handle({"cmd": "offset", "name": "room_db", "delta": -2})
    lc.handle({"cmd": "offset", "name": "room_db", "delta": -2})
    assert chain.get_param("expander", "threshold_db") == pytest.approx(base_e - 2.0)
    lc.handle({"cmd": "offset", "name": "deess_db", "delta": -2})
    assert chain.get_param("deesser", "threshold_db") == pytest.approx(base_d - 2.0)
    lc.handle({"cmd": "offset", "name": "intensity", "delta": 1})
    lc.handle({"cmd": "offset", "name": "intensity", "delta": 1})
    assert chain.get_param("compressor", "threshold_db") == pytest.approx(base_c + 2 * INTENSITY_COMPRESSOR_DB_PER_STEP)
    assert chain.get_param("deesser", "threshold_db") == pytest.approx(base_d - 2.0)   # intensity leaves the de-esser alone
    assert lc.threshold_offsets() == {"expander.threshold_db": -2.0, "deesser.threshold_db": -2.0,
                                      "compressor.threshold_db": 2 * INTENSITY_COMPRESSOR_DB_PER_STEP}


def test_bounds_stop_gives_ok_false_and_offset_equals_the_effective_value():
    cfg, chain, ctrl, lc, hl = fake_setup(with_controller=False)
    cfg_hi = cfg["adaptive"]["bounds"]["expander.threshold_db"][1]
    base_e = cfg["chain"]["expander"]["threshold_db"]          # -45: +6 fits inside the -30 bound
    lc.handle({"cmd": "set_offset", "name": "room_db", "value": 6.0})
    assert chain.get_param("expander", "threshold_db") == pytest.approx(base_e + 6.0)
    # Base -34 (calibrated room): +4 reaches -30, +6 is a stop.
    cfg2 = copy.deepcopy(cfg)
    cfg2["chain"]["expander"]["threshold_db"] = -34.0
    chain2 = MeterChain(cfg2)
    lc2 = LiveControl(cfg2, chain2, controller=None)
    assert lc2.handle({"cmd": "offset", "name": "room_db", "delta": 2})["ok"]
    assert lc2.handle({"cmd": "offset", "name": "room_db", "delta": 2})["ok"]
    assert chain2.get_param("expander", "threshold_db") == pytest.approx(cfg_hi)
    r = lc2.handle({"cmd": "offset", "name": "room_db", "delta": 2})
    assert r["ok"] is False and "am Anschlag" in r["error"] and "-30 dBFS" in r["error"]
    assert lc2.offsets["room_db"] == pytest.approx(4.0)
    # A partial step (base -33: +2 -> -31, next +2 -> only -30 = effective +3) names the limit.
    cfg3 = copy.deepcopy(cfg)
    cfg3["chain"]["expander"]["threshold_db"] = -33.0
    chain3 = MeterChain(cfg3)
    lc3 = LiveControl(cfg3, chain3, controller=None)
    lc3.handle({"cmd": "offset", "name": "room_db", "delta": 2})
    r = lc3.handle({"cmd": "offset", "name": "room_db", "delta": 2})
    assert r["ok"] and "begrenzt" in r["message"] and r["state"]["offsets"]["room_db"] == pytest.approx(3.0)
    assert chain3.get_param("expander", "threshold_db") == pytest.approx(cfg_hi)
    # Turning back works from the effective value: the first press moves the threshold.
    r = lc3.handle({"cmd": "offset", "name": "room_db", "delta": -2})
    assert r["ok"] and chain3.get_param("expander", "threshold_db") == pytest.approx(cfg_hi - 2.0)


def test_threshold_offsets_with_controller_apply_at_once_and_enter_the_setpoints():
    cfg, chain, ctrl, lc, hl = fake_setup()
    finish_learning(ctrl)                                # last snapshot: speech -20 -> cap -32
    before = chain.get_param("expander", "threshold_db")
    lc.handle({"cmd": "offset", "name": "room_db", "delta": 2})
    lc.handle({"cmd": "offset", "name": "room_db", "delta": 2})
    assert chain.get_param("expander", "threshold_db") == pytest.approx(before + 4.0)   # audible at once
    assert ctrl.user_offsets == {"expander.threshold_db": 4.0, "deesser.threshold_db": 0.0,
                                 "compressor.threshold_db": 0.0}
    lc.handle({"cmd": "offset", "name": "intensity", "delta": 1})
    assert ctrl.user_offsets["compressor.threshold_db"] == INTENSITY_COMPRESSOR_DB_PER_STEP
    assert ctrl.user_offsets["deesser.threshold_db"] == 0.0
    # Tracking target includes the offset: floor -60 -> -50 + 4 = -46; a controller that
    # ignored the offset would walk down to -48 (-50 + dead zone) instead.
    for k in range(40):
        ctrl.update(meters_dict(floor=-60.0, speech=-20.0), now=LEARN_S + 2.0 + k * INTERVAL_S)
    thr = chain.get_param("expander", "threshold_db")
    assert -46.0 - 0.5 <= thr <= -46.0 + adaptive.EXPANDER_DEADZONE_DB
    assert abs(chain.get_param("compressor", "threshold_db") - (-22.0)) <= adaptive.COMPRESSOR_DEADZONE_DB


def test_room_offset_respects_the_speech_cap_in_controller_and_immediate_step():
    """Speaker 5 dB quieter than calibrated (speech -25 after trim -> cap -37), room +6:
    neither the immediate step nor the regulation may pass speech - 12 dB."""
    cfg, chain, ctrl, lc, hl = fake_setup()
    chain.set_param("expander", "threshold_db", -40.0)
    finish_learning(ctrl, floor=-50.0, speech=-25.0)
    assert ctrl.last_expander_cap == pytest.approx(-37.0)
    # Immediate steps: -40 -> -38 -> -37 (capped, partial) -> stop.
    answers = [lc.handle({"cmd": "offset", "name": "room_db", "delta": 2}) for _ in range(3)]
    assert [a["ok"] for a in answers] == [True, True, False]
    assert "Sprach-Kappe" in answers[1]["message"] and "Sprach-Kappe" in answers[2]["error"]
    assert chain.get_param("expander", "threshold_db") == pytest.approx(-37.0)
    assert lc.offsets["room_db"] == pytest.approx(3.0)              # effective, not +6
    for k in range(40):
        ctrl.update(meters_dict(floor=-50.0, speech=-25.0), now=LEARN_S + 2.0 + k * INTERVAL_S)
    assert chain.get_param("expander", "threshold_db") <= -37.0 + 0.5
    # A threshold already above the cap (calibrated -34, speaker got quieter): the key cannot
    # raise it and the regulation pulls it down to the cap every interval (speech_cap).
    cfg, chain, ctrl, lc, hl = fake_setup()
    chain.set_param("expander", "threshold_db", -34.0)
    finish_learning(ctrl, floor=-50.0, speech=-25.0)
    r = lc.handle({"cmd": "offset", "name": "room_db", "delta": 2})
    assert r["ok"] is False and "Sprach-Kappe" in r["error"] and lc.offsets["room_db"] == 0.0
    changes = [c for k in range(8) for c in ctrl.update(meters_dict(floor=-50.0, speech=-25.0),
                                                        now=LEARN_S + 2.0 + k * INTERVAL_S)]
    reasons = [c["reason"] for c in changes if c["stage"] == "expander"]
    # Pulled to the cap in 0.5 dB steps every interval (no spacing), then the floor rule
    # (-50 + 10 = -40, below the cap) takes over at the usual pace.
    assert reasons[:5] == ["speech_cap"] * 5 and set(reasons[5:]) <= {"speech_cap", "noise_floor_track"}
    assert chain.get_param("expander", "threshold_db") <= -37.0 + 1e-9
    # The controller target is min(floor + 10 + offset, speech - 12): offset +6 on -40 = -34,
    # capped at -37 -> the threshold sits at or below the cap, never 4-7 dB above it.
    cfg2, chain2, ctrl2, lc2, _ = fake_setup()
    finish_learning(ctrl2, floor=-50.0, speech=-25.0)
    lc2.handle({"cmd": "set_offset", "name": "room_db", "value": 6.0})
    for k in range(60):
        ctrl2.update(meters_dict(floor=-50.0, speech=-25.0), now=LEARN_S + 2.0 + k * INTERVAL_S)
    assert chain2.get_param("expander", "threshold_db") <= -37.0 + 0.5


def test_tone_offset_sets_the_eq_tilt_or_reports_it_unavailable():
    cfg, chain, ctrl, lc, hl = fake_setup(with_controller=False)
    r = lc.handle({"cmd": "offset", "name": "tone_db", "delta": 1})
    assert r["ok"] and chain.get_param("eq", "tilt_db") == 1.0 and r["state"]["tilt_db"] == 1.0
    assert "heller" in r["message"]
    lc.handle({"cmd": "set_offset", "name": "tone_db", "value": -3})
    assert chain.get_param("eq", "tilt_db") == -3.0
    assert lc.handle({"cmd": "offset", "name": "tone_db", "delta": -1})["ok"] is False
    del chain.params[("eq", "tilt_db")]
    r = lc.handle({"cmd": "offset", "name": "tone_db", "delta": 1})
    assert r["ok"] is False and "Tilt" in r["error"]
    assert lc.offsets["tone_db"] == -3.0
    assert lc.state()["tilt_db"] is None


# --------------------------------------------------------------------------- reset / relearn / freeze / mute / bypass

def test_reset_restores_operating_state_but_keeps_tracked_thresholds(tmp_path):
    log = tmp_path / "lc.jsonl"
    cfg, chain, ctrl, lc, hl = fake_setup(log_path=log)
    finish_learning(ctrl)
    # Regulation tracked the expander from -45 to -50 (floor -60) ...
    for k in range(40):
        ctrl.update(meters_dict(floor=-60.0, speech=-20.0), now=LEARN_S + 2.0 + k * INTERVAL_S)
    tracked = chain.get_param("expander", "threshold_db")
    assert tracked == pytest.approx(-48.0, abs=adaptive.EXPANDER_DEADZONE_DB)
    # ... then the user sets offsets, freeze, mute, bypass, kill switch.
    for c in [{"cmd": "offset", "name": "loudness_lu", "delta": 0.5}, {"cmd": "offset", "name": "room_db", "delta": 2},
              {"cmd": "offset", "name": "deess_db", "delta": -2}, {"cmd": "offset", "name": "intensity", "delta": 1},
              {"cmd": "offset", "name": "tone_db", "delta": -1}, {"cmd": "freeze", "on": True},
              {"cmd": "mute", "on": True}, {"cmd": "bypass", "on": True}, {"cmd": "processing", "on": False}]:
        assert lc.handle(c)["ok"], c
    hl.calls.clear()
    r = lc.handle({"cmd": "reset"})
    assert r["ok"] and "Reset" in r["message"] and "nachgeführte Schwellen bleiben" in r["message"]
    st = r["state"]
    assert st["offsets"] == {n: 0.0 for n in OFFSETS}
    assert st["target_lufs"] == cfg["chain"]["normalizer"]["target_lufs"]
    assert st["tilt_db"] == 0.0 and st["muted"] is False and st["bypass"] is False and st["processing"] is True
    assert st["frozen"] is False and ctrl.frozen is False
    assert hl.calls == [("processing", True), ("mute", False), ("bypass", False)]
    # Tracked expander value stays (offset taken off), de-esser/compressor back where they were.
    assert chain.get_param("expander", "threshold_db") == pytest.approx(tracked)
    assert chain.get_param("deesser", "threshold_db") == cfg["chain"]["deesser"]["threshold_db"]
    assert chain.get_param("compressor", "threshold_db") == cfg["chain"]["compressor"]["threshold_db"]
    assert ctrl.user_offsets == {}
    assert ctrl.learning is False                       # no new learning phase
    ctrl.close()
    assert "relearn" not in [e.get("event") for e in events(log)]


def test_reset_restores_normalizer_gain_limit_after_limiter_protection():
    cfg, chain, ctrl, lc, hl = fake_setup()
    finish_learning(ctrl)
    ctrl.update(meters_dict(limiter_gr=5.0, norm_gain=4.0, speech=-15.0), now=LEARN_S + 2.0)
    assert ctrl.limiter_protect_active and chain.get_param("normalizer", "max_gain_db") == pytest.approx(4.0)
    assert lc.handle({"cmd": "reset"})["ok"]
    assert chain.get_param("normalizer", "max_gain_db") == pytest.approx(12.0)
    assert not ctrl.limiter_protect_active
    # Without a controller the configured value is restored as well.
    cfg, chain, ctrl, lc, hl = fake_setup(with_controller=False)
    chain.set_param("normalizer", "max_gain_db", 3.0)
    lc.handle({"cmd": "reset"})
    assert chain.get_param("normalizer", "max_gain_db") == pytest.approx(cfg["chain"]["normalizer"]["max_gain_db"])


def test_reset_without_controller_sets_the_calibrated_values():
    cfg, chain, ctrl, lc, hl = fake_setup(with_controller=False)
    lc.handle({"cmd": "set_offset", "name": "room_db", "value": 4.0})
    lc.handle({"cmd": "set_offset", "name": "intensity", "value": 2.0})
    chain.set_param("deesser", "threshold_db", -40.0)
    lc.handle({"cmd": "reset"})
    for stage in ("expander", "deesser", "compressor"):
        assert chain.get_param(stage, "threshold_db") == cfg["chain"][stage]["threshold_db"]


def test_relearn_puts_regulation_back_to_rest_restarts_learning_and_keeps_offsets():
    cfg, chain, ctrl, lc, hl = fake_setup()
    la = adaptive.LiveAnalyzer(SR)
    la.push(np.zeros(5 * SR, dtype=np.float32))
    lc.analyzer = la
    finish_learning(ctrl)
    for k in range(40):
        ctrl.update(meters_dict(floor=-60.0, speech=-20.0), now=LEARN_S + 2.0 + k * INTERVAL_S)
    lc.handle({"cmd": "offset", "name": "room_db", "delta": 2})
    assert chain.get_param("expander", "threshold_db") < -45.0
    ctrl.clock = lambda: 120.0
    r = lc.handle({"cmd": "relearn"})
    assert r["ok"] and "Kalibrierwerte" in r["message"] and r["state"]["learning"] is True
    assert chain.get_param("expander", "threshold_db") == pytest.approx(-45.0 + 2.0)   # rest + offset, at once
    assert lc.offsets["room_db"] == 2.0 and ctrl.user_offsets["expander.threshold_db"] == 2.0
    assert la.snapshot()["frames"] == 0
    assert ctrl.update(meters_dict(floor=-70.0), now=121.0) == []
    assert len(ctrl.update(meters_dict(floor=-70.0), now=120.0 + LEARN_S)) >= 1


def test_freeze_mute_bypass_toggle_and_explicit():
    cfg, chain, ctrl, lc, hl = fake_setup()
    assert lc.handle({"cmd": "freeze", "on": "toggle"})["state"]["frozen"] is True
    assert ctrl.frozen is True
    assert lc.handle({"cmd": "freeze", "on": False})["state"]["frozen"] is False
    assert lc.handle({"cmd": "freeze"})["state"]["frozen"] is True
    assert lc.handle({"cmd": "mute", "on": "toggle"})["state"]["muted"] is True
    assert lc.handle({"cmd": "mute", "on": "toggle"})["state"]["muted"] is False
    assert lc.handle({"cmd": "bypass", "on": True})["state"]["bypass"] is True
    assert lc.handle({"cmd": "bypass", "on": False})["state"]["bypass"] is False
    assert hl.calls == [("mute", True), ("mute", False), ("bypass", True), ("bypass", False)]
    cfg, chain, ctrl, lc, hl = fake_setup(with_controller=False, hooks=False)
    assert lc.handle({"cmd": "freeze", "on": True})["ok"] is False
    assert lc.handle({"cmd": "relearn"})["ok"] is False
    assert lc.handle({"cmd": "mute", "on": True})["ok"] is False
    assert lc.handle({"cmd": "bypass", "on": True})["ok"] is False
    assert lc.handle({"cmd": "reset"})["ok"] is True


# --------------------------------------------------------------------------- kill switch

def test_processing_kill_switch_toggle_on_off_hook_and_state():
    cfg, chain, ctrl, lc, hl = fake_setup()
    r = lc.handle({"cmd": "processing"})
    assert r["ok"] and r["state"]["processing"] is False and "Verarbeitung AUS" in r["message"]
    assert hl.calls[-1] == ("processing", False)
    assert "OFF" in lc.handle({"cmd": "status"})["message"]
    r = lc.handle({"cmd": "processing", "on": "toggle"})
    assert r["state"]["processing"] is True and "Verarbeitung AN" in r["message"]
    assert lc.handle({"cmd": "processing", "on": False})["state"]["processing"] is False
    assert lc.handle({"cmd": "processing", "on": True})["state"]["processing"] is True
    cfg, chain, ctrl, lc, hl = fake_setup(hooks=False)
    r = lc.handle({"cmd": "processing", "on": False})
    assert r["ok"] is False and "processing" in r["error"] and lc.state()["processing"] is True


def test_processing_off_pauses_the_controller_clock_and_restores_freeze():
    for frozen_before in (False, True):
        cfg, chain, ctrl, lc, hl = fake_setup()
        for k in range(10):
            ctrl.update(meters_dict(floor=-60.0), now=k * INTERVAL_S)      # 20 s of the 30 s learned
        ctrl.clock = lambda: 20.0
        if frozen_before:
            lc.handle({"cmd": "freeze", "on": True})
        lc.handle({"cmd": "processing", "on": False})
        assert ctrl.paused and ctrl.frozen is True
        assert ctrl.update(meters_dict(floor=-70.0), now=25.0) == []
        ctrl.clock = lambda: 100.0                                           # 80 s switched off
        assert ctrl.learning is True                                         # the clock stood still
        r = lc.handle({"cmd": "processing", "on": True})
        assert not ctrl.paused and ctrl.frozen is frozen_before
        assert ("FROZEN" in r["message"]) is frozen_before
        # 10 s of learning are left: no change at 105 s, first change at 110 s (unless frozen).
        assert ctrl.update(meters_dict(floor=-70.0), now=105.0) == []
        assert ctrl.learning is True
        ctrl.clock = lambda: 111.0
        assert ctrl.learning is False
        changes = ctrl.update(meters_dict(floor=-70.0), now=111.0)
        assert (len(changes) >= 1) is (not frozen_before)
    assert lc.handle({"cmd": "relearn"})["ok"]
    lc.handle({"cmd": "processing", "on": False})
    assert lc.handle({"cmd": "relearn"})["ok"] is False                     # not while off


def test_offsets_taken_while_off_are_in_effect_when_switched_on():
    cfg, chain, ctrl, lc, hl = fake_setup()
    lc.handle({"cmd": "processing", "on": False})
    r = lc.handle({"cmd": "offset", "name": "loudness_lu", "delta": 0.5})
    assert r["ok"] and "wirkt beim Einschalten" in r["message"]
    assert lc.handle({"cmd": "offset", "name": "room_db", "delta": 2})["ok"]
    assert lc.handle({"cmd": "offset", "name": "tone_db", "delta": -1})["ok"]
    assert lc.handle({"cmd": "offset", "name": "intensity", "delta": 1})["ok"]
    chain.params[("normalizer", "target_lufs")] = -18.0        # simulated stage reset on power-up
    chain.params[("eq", "tilt_db")] = 0.0
    r = lc.handle({"cmd": "processing", "on": True})
    assert r["ok"] and "wirkt beim Einschalten" not in r["message"]
    assert chain.get_param("normalizer", "target_lufs") == pytest.approx(-17.5)
    assert chain.get_param("eq", "tilt_db") == pytest.approx(-1.0)
    assert chain.get_param("expander", "threshold_db") == pytest.approx(cfg["chain"]["expander"]["threshold_db"] + 2.0)
    assert chain.get_param("compressor", "threshold_db") == pytest.approx(cfg["chain"]["compressor"]["threshold_db"] - 2.0)
    assert ctrl.user_offsets["expander.threshold_db"] == 2.0


# --------------------------------------------------------------------------- snapshot / marker

def test_snapshot_writes_the_effective_values_and_live_block(tmp_path):
    cfg, chain, ctrl, lc, hl = fake_setup()
    finish_learning(ctrl)
    for k in range(20):
        ctrl.update(meters_dict(floor=-60.0, speech=-20.0), now=LEARN_S + 2.0 + k * INTERVAL_S)
    tracked = chain.get_param("expander", "threshold_db")
    assert tracked < cfg["chain"]["expander"]["threshold_db"]
    lc.handle({"cmd": "offset", "name": "loudness_lu", "delta": 0.5})
    lc.handle({"cmd": "offset", "name": "room_db", "delta": 2})
    lc.handle({"cmd": "offset", "name": "intensity", "delta": 1})
    path = tmp_path / "live" / "chain.live.yaml"
    r = lc.handle({"cmd": "snapshot", "path": str(path)})
    assert r["ok"] and str(path) in r["message"]
    saved = load_config(path)
    assert saved["chain"]["expander"]["threshold_db"] == pytest.approx(tracked + 2.0)     # tracked + offset
    assert saved["chain"]["normalizer"]["target_lufs"] == pytest.approx(-17.5)
    assert saved["chain"]["compressor"]["threshold_db"] == pytest.approx(cfg["chain"]["compressor"]["threshold_db"] - 2.0)
    assert saved["chain"]["deesser"]["threshold_db"] == pytest.approx(cfg["chain"]["deesser"]["threshold_db"])
    assert saved["_live"]["offsets"] == {"loudness_lu": 0.5, "room_db": 2.0, "deess_db": 0.0, "tone_db": 0.0, "intensity": 1.0}
    assert saved["_live"]["tracked"]["expander.threshold_db"] == pytest.approx(tracked + 2.0)
    assert saved["_live"]["base"]["expander.threshold_db"] == cfg["chain"]["expander"]["threshold_db"]
    assert "created" in saved["_live"]
    assert DEFAULT_SNAPSHOT_PATH.as_posix() == "config/chain.live.yaml"


def test_marker_goes_to_the_controller_log_or_the_configured_log_file(tmp_path):
    log = tmp_path / "m.jsonl"
    cfg, chain, ctrl, lc, hl = fake_setup(log_path=log)
    assert lc.handle({"cmd": "marker", "text": "Folie 3"})["ok"]
    ctrl.close()
    assert [e for e in events(log) if e.get("event") == "marker"] == [{"t": 0.0, "event": "marker", "text": "Folie 3"}]
    cfg, chain, ctrl, lc, hl = fake_setup(with_controller=False)
    cfg["adaptive"]["log_path"] = str(tmp_path / "plain.jsonl")
    assert lc.handle({"cmd": "marker", "text": "Pause"})["ok"]
    assert json.loads((tmp_path / "plain.jsonl").read_text().splitlines()[0]) == {"t": 0.0, "event": "marker", "text": "Pause"}


# --------------------------------------------------------------------------- real chain

@pytest.fixture(scope="module")
def calibrated(base_cfg):
    x = synth.make_speech_like(60.0, SR, level_dbfs=-26.0, noise_floor_dbfs=-50.0, sibilance_db=0.0,
                               seed=3, lead_silence_s=10.0, long_pause_s=6.0)
    return cal.recommend(cal.analyze(x, SR), base_cfg)


@pytest.fixture(scope="module")
def speech():
    return synth.make_speech_like(25.0, SR, level_dbfs=-26.0, noise_floor_dbfs=-50.0, sibilance_db=0.0,
                                  seed=5, long_pause_s=6.0)


def _run(cfg, x, offsets=None, block=2048):
    """Fresh chain with offsets applied via LiveControl (no controller); returns the output
    and the average compressor / de-esser gain reductions."""
    from voicechain.dsp.chain import Chain
    chain = Chain.from_config(cfg)
    lc = LiveControl(cfg, chain, controller=None)
    for name, value in (offsets or {}).items():
        r = lc.handle({"cmd": "set_offset", "name": name, "value": value})
        assert r["ok"], r
    out = []
    comp_gr, de_gr, n = 0.0, 0.0, 0
    for i in range(0, x.size, block):
        out.append(chain.process(x[i:i + block]))
        m = chain.get_meters()
        comp_gr += m["compressor"]["gain_reduction_mean_db"]
        de_gr += m["deesser"]["gain_reduction_mean_db"]
        n += 1
    return np.concatenate(out), {"compressor_gr": comp_gr / n, "deesser_gr": de_gr / n}


def test_loudness_offset_changes_output_loudness_by_the_same_amount(calibrated, speech):
    y0, _ = _run(calibrated, speech)
    y1, _ = _run(calibrated, speech, {"loudness_lu": 1.0})
    y2, _ = _run(calibrated, speech, {"loudness_lu": -1.0})
    half = speech.size // 2
    l0, l1, l2 = (meters.integrated_lufs(y[half:], SR) for y in (y0, y1, y2))
    assert l1 - l0 == pytest.approx(1.0, abs=0.5)
    assert l2 - l0 == pytest.approx(-1.0, abs=0.5)


def test_room_offset_changes_the_pause_attenuation(calibrated, speech):
    span = meters.longest_pause(speech, SR, 5.0)
    y0, _ = _run(calibrated, speech)
    y_less, _ = _run(calibrated, speech, {"room_db": 4.0})
    y_more, _ = _run(calibrated, speech, {"room_db": -4.0})
    p = lambda y: meters.pause_loudness_lufs(y, SR, *span)["rms_dbfs"]
    assert p(y_less) < p(y0) - 2.0
    assert p(y_more) > p(y0) + 1.0


def test_deess_and_intensity_offsets_change_the_gain_reductions(calibrated, speech):
    _, m0 = _run(calibrated, speech)
    _, m_de = _run(calibrated, speech, {"deess_db": -4.0})
    _, m_int = _run(calibrated, speech, {"intensity": 2.0})
    _, m_nat = _run(calibrated, speech, {"intensity": -2.0})
    assert m_de["deesser_gr"] > m0["deesser_gr"] * 1.3 + 0.1
    assert m_int["compressor_gr"] > m0["compressor_gr"] + 1.0
    assert m_nat["compressor_gr"] < m0["compressor_gr"] - 1.0
    assert m_int["deesser_gr"] == pytest.approx(m0["deesser_gr"], abs=0.05)   # de-esser not coupled


def test_tone_offset_tilts_the_frequency_response(calibrated):
    rng = np.random.default_rng(7)
    noise = (rng.standard_normal(12 * SR) * 10 ** (-20 / 20)).astype(np.float32)
    cfg_flat = copy.deepcopy(calibrated)
    for stage in ("expander", "deesser", "compressor", "normalizer", "limiter"):
        cfg_flat["chain"][stage]["enabled"] = False
    y0, _ = _run(cfg_flat, noise)
    y_b, _ = _run(cfg_flat, noise, {"tone_db": 2.0})
    y_w, _ = _run(cfg_flat, noise, {"tone_db": -2.0})

    def balance(y):
        lo, hi = meters.band_energies_db(y[SR:], SR, [80.0, 170.0, 7000.0, 14000.0])[::2]
        return hi - lo
    assert balance(y_b) - balance(y0) == pytest.approx(2.0, abs=0.7)
    assert balance(y_w) - balance(y0) == pytest.approx(-2.0, abs=0.7)


def test_reset_without_controller_restores_every_chain_parameter(calibrated):
    """Without regulation reset means calibration state: every get_params() value of all
    stages equals a chain freshly built from the loaded configuration."""
    from voicechain.dsp.chain import Chain
    cfg = calibrated
    chain = Chain.from_config(cfg)
    lc = LiveControl(cfg, chain, controller=None, hooks=HookLog().hooks())
    for c in [{"cmd": "set_offset", "name": "loudness_lu", "value": 2.0}, {"cmd": "set_offset", "name": "room_db", "value": 4.0},
              {"cmd": "set_offset", "name": "deess_db", "value": -4.0}, {"cmd": "set_offset", "name": "intensity", "value": 2.0},
              {"cmd": "set_offset", "name": "tone_db", "value": 1.0}, {"cmd": "mute", "on": True}]:
        assert lc.handle(c)["ok"], c
    chain.set_param("normalizer", "max_gain_db", 3.0)
    fresh = Chain.from_config(cfg)
    assert any(chain.stages[s].get_params() != fresh.stages[s].get_params() for s in chain.stages)
    assert lc.handle({"cmd": "reset"})["ok"]
    for stage_name, stage in chain.stages.items():
        expected = fresh.stages[stage_name].get_params()
        actual = stage.get_params()
        assert set(actual) == set(expected), stage_name
        for pname, value in actual.items():
            assert value == pytest.approx(expected[pname]), (stage_name, pname)


@pytest.mark.slow
def test_controller_keeps_a_room_offset_over_300_s(tmp_path, base_cfg):
    """Section 6.4: with room +4 the expander settles at target + 4 within the dead zone and the
    regulation never steps it back down. Quiet room (-55 dBFS): the calibrated threshold -39
    plus 4 dB stays below the speech cap (-32), so the whole offset is effective."""
    from voicechain.dsp.chain import Chain
    sample = synth.make_speech_like(60.0, SR, level_dbfs=-26.0, noise_floor_dbfs=-55.0, sibilance_db=0.0,
                                    seed=3, lead_silence_s=10.0, long_pause_s=6.0)
    cfg = cal.recommend(cal.analyze(sample, SR), base_cfg)
    cfg["adaptive"].update({"learn_s": LEARN_S, "interval_s": INTERVAL_S, "max_step_db": STEP_DB})
    x = synth.make_speech_like(300.0, SR, level_dbfs=-26.0, noise_floor_dbfs=-55.0, sibilance_db=0.0,
                               seed=21, long_pause_s=6.0)
    chain = Chain.from_config(cfg)
    la = adaptive.LiveAnalyzer(SR, deesser_freq_hz=cfg["chain"]["deesser"]["freq_hz"],
                               deesser_bandwidth_oct=cfg["chain"]["deesser"]["bandwidth_oct"])
    ctrl = adaptive.AdaptiveController(cfg, chain, clock=lambda: 0.0, log_path=tmp_path / "room.jsonl")
    lc = LiveControl(cfg, chain, controller=ctrl)
    calibrated_thr = cfg["chain"]["expander"]["threshold_db"]
    tick, block, next_tick = int(INTERVAL_S * SR), 2048, int(INTERVAL_S * SR)
    changes, pressed = [], False
    for i in range(0, x.size, block):
        blk = x[i:i + block]
        la.push(blk)
        chain.process(blk)
        if i + block >= next_tick:
            m = chain.get_meters()
            m["analysis"] = la.snapshot()
            changes.extend(ctrl.update(m, now=next_tick / SR))
            if not pressed and next_tick / SR >= 60.0:
                assert lc.handle({"cmd": "offset", "name": "room_db", "delta": 2})["ok"]
                assert lc.handle({"cmd": "offset", "name": "room_db", "delta": 2})["ok"]
                pressed = True
                after_press = chain.get_param("expander", "threshold_db")
            next_tick += tick
    ctrl.close()
    assert calibrated_thr + 4.0 < -32.0                        # below the speech cap of a -20 dBFS speaker
    assert after_press == pytest.approx(calibrated_thr + 4.0, abs=STEP_DB + 1e-9)
    assert lc.offsets["room_db"] == pytest.approx(4.0)
    exp_after = [c for c in changes if c["stage"] == "expander" and c["t"] > 60.0]
    assert all(c["new"] >= c["old"] for c in exp_after), exp_after
    final = chain.get_param("expander", "threshold_db")
    assert final >= calibrated_thr + 4.0 - 0.5                 # held: not regulated away (tolerance < offset)
    assert final <= calibrated_thr + 4.0 + adaptive.EXPANDER_DEADZONE_DB


@pytest.mark.slow
def test_quiet_speaker_with_room_offset_stays_under_the_speech_cap(tmp_path, calibrated):
    """Section 7 / PO: speaker 5 dB quieter than calibrated, room +6 -> expander threshold
    <= speech - 12 dB (+0.5), and no speech_cut interventions."""
    from voicechain.dsp.chain import Chain
    cfg = copy.deepcopy(calibrated)
    cfg["adaptive"].update({"learn_s": LEARN_S, "interval_s": INTERVAL_S, "max_step_db": STEP_DB})
    x = synth.make_speech_like(150.0, SR, level_dbfs=-31.0, noise_floor_dbfs=-50.0, sibilance_db=0.0,
                               seed=24, long_pause_s=6.0)
    trim = cfg["chain"]["input_trim"]["gain_db"]
    cap = -31.0 + trim - cal.EXPANDER_SPEECH_MARGIN_DB
    chain = Chain.from_config(cfg)
    la = adaptive.LiveAnalyzer(SR, deesser_freq_hz=cfg["chain"]["deesser"]["freq_hz"],
                               deesser_bandwidth_oct=cfg["chain"]["deesser"]["bandwidth_oct"])
    ctrl = adaptive.AdaptiveController(cfg, chain, clock=lambda: 0.0, log_path=tmp_path / "cap.jsonl")
    lc = LiveControl(cfg, chain, controller=ctrl)
    tick, block, next_tick = int(INTERVAL_S * SR), 2048, int(INTERVAL_S * SR)
    changes, answers = [], []
    for i in range(0, x.size, block):
        blk = x[i:i + block]
        la.push(blk)
        chain.process(blk)
        if i + block >= next_tick:
            m = chain.get_meters()
            m["analysis"] = la.snapshot()
            changes.extend(ctrl.update(m, now=next_tick / SR))
            if not answers and next_tick / SR >= 40.0:
                answers = [lc.handle({"cmd": "offset", "name": "room_db", "delta": 2}) for _ in range(3)]
                assert chain.get_param("expander", "threshold_db") <= cap + 0.5
            next_tick += tick
    ctrl.close()
    assert any("Sprach-Kappe" in a.get("message", "") or "am Anschlag" in a.get("error", "") for a in answers)
    assert lc.offsets["room_db"] < 6.0                              # effective offset, not the phantom +6
    assert chain.get_param("expander", "threshold_db") <= cap + 0.5
    assert [c for c in changes if c["reason"] == "speech_cut"] == []
    exp = [c for c in changes if c["stage"] == "expander"]
    # The calibrated threshold (-34) starts above the cap: speech_cap pulls it down step by
    # step; every other expander step keeps the threshold at or below the cap.
    assert all(c["new"] < c["old"] for c in exp if c["reason"] == "speech_cap")
    assert all(c["new"] <= cap + 0.5 for c in exp if c["reason"] != "speech_cap")
    assert any(c["reason"] == "speech_cap" for c in exp)
