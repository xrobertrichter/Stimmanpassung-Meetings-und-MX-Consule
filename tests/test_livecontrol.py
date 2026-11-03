"""Tests for voicechain.livecontrol (console commands, offsets, reset, snapshot)."""
import copy
import json

import numpy as np
import pytest

from voicechain import adaptive, calibrate as cal, meters, synth
from voicechain.config import default_config_path, load_config
from voicechain.livecontrol import (
    DEFAULT_SNAPSHOT_PATH, INTENSITY_COMPRESSOR_DB_PER_STEP, INTENSITY_DEESSER_DB_PER_STEP,
    LOUDNESS_TARGET_BOUNDS_LUFS, OFFSETS, LiveControl,
)
from test_adaptive import FakeChain, LEARN_S, INTERVAL_S, STEP_DB, meters_dict, events

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


def fake_setup(with_controller=True, hooks=True, log_path=None):
    cfg = load_config(default_config_path())
    cfg["adaptive"].update({"learn_s": LEARN_S, "interval_s": INTERVAL_S, "max_step_db": STEP_DB})
    chain = FakeChain(cfg)
    chain.params[("eq", "tilt_db")] = 0.0
    chain.meters = {"normalizer": {"gain_db": 1.0, "loudness_lufs": -18.4, "snr_lu": 25.0, "low_snr": 0.0}}
    ctrl = adaptive.AdaptiveController(cfg, chain, clock=lambda: 0.0, log_path=log_path) if with_controller else None
    hl = HookLog()
    lc = LiveControl(cfg, chain, controller=ctrl, hooks=hl.hooks() if hooks else None, clock=lambda: 0.0)
    return cfg, chain, ctrl, lc, hl


# --------------------------------------------------------------------------- answer format

def test_answer_format_and_state_keys():
    cfg, chain, ctrl, lc, hl = fake_setup()
    r = lc.handle({"cmd": "status"})
    assert r["ok"] is True and set(r) == {"ok", "state", "message"}
    assert STATE_KEYS <= set(r["state"])
    st = r["state"]
    assert st["target_lufs"] == -18.0 and st["loudness_lufs"] == -18.4 and st["snr_lu"] == 25.0
    assert st["low_snr"] is False and st["adaptive_enabled"] is True and st["learning"] is True
    assert st["offsets"] == {name: 0.0 for name in OFFSETS}
    assert isinstance(r["message"], str) and "LUFS" in r["message"] and "LERNPHASE" in r["message"]
    json.dumps(r)   # serialisable for the socket protocol


@pytest.mark.parametrize("cmd", [
    {"cmd": "nope"}, {"cmd": "offset", "name": "room_db"}, {"cmd": "offset", "name": "x", "delta": 1},
    {"cmd": "offset", "name": "room_db", "delta": 2, "extra": 1}, {"cmd": "set_offset", "name": "tone_db"},
    {"cmd": "freeze", "on": "maybe"}, {"cmd": "marker"}, {"cmd": "marker", "text": "  "},
    {"cmd": "offset", "name": "room_db", "delta": "two"}, {"cmd": "offset", "name": "room_db", "delta": True},
    "status", None, {"name": "room_db"},
])
def test_invalid_commands_return_ok_false_without_raising(cmd):
    cfg, chain, ctrl, lc, hl = fake_setup()
    r = lc.handle(cmd)
    assert r["ok"] is False and isinstance(r["error"], str) and r["error"]
    assert chain.history == []


def test_handle_never_raises_even_when_the_chain_fails():
    cfg, chain, ctrl, lc, hl = fake_setup()

    def boom(stage, name, value):
        raise RuntimeError("stage exploded")
    chain.set_param = boom
    r = lc.handle({"cmd": "offset", "name": "loudness_lu", "delta": 0.5})
    assert r["ok"] is False and "RuntimeError" in r["error"] and "stage exploded" in r["error"]


# --------------------------------------------------------------------------- offsets and clamping

@pytest.mark.parametrize("name,lo,hi,step", [(n, *spec) for n, spec in OFFSETS.items()])
def test_offset_steps_are_clamped_to_one_step_and_to_the_range(name, lo, hi, step):
    cfg, chain, ctrl, lc, hl = fake_setup(with_controller=False)
    # A delta larger than one step moves exactly one step.
    r = lc.handle({"cmd": "offset", "name": name, "delta": 10 * step})
    assert r["ok"] and r["state"]["offsets"][name] == pytest.approx(step)
    # Repeated presses stop at the upper limit; the other way at the lower limit.
    for _ in range(50):
        lc.handle({"cmd": "offset", "name": name, "delta": step})
    assert lc.offsets[name] == pytest.approx(hi)
    for _ in range(100):
        lc.handle({"cmd": "offset", "name": name, "delta": -step})
    assert lc.offsets[name] == pytest.approx(lo)
    # set_offset clamps too.
    assert lc.handle({"cmd": "set_offset", "name": name, "value": hi + 100})["state"]["offsets"][name] == pytest.approx(hi)
    assert lc.handle({"cmd": "set_offset", "name": name, "value": lo - 100})["state"]["offsets"][name] == pytest.approx(lo)
    assert lc.handle({"cmd": "set_offset", "name": name, "value": 0})["state"]["offsets"][name] == 0.0


def test_loudness_offset_moves_the_target_within_minus24_minus12():
    cfg, chain, ctrl, lc, hl = fake_setup()
    r = lc.handle({"cmd": "offset", "name": "loudness_lu", "delta": 0.5})
    assert r["state"]["target_lufs"] == pytest.approx(-17.5)
    assert chain.get_param("normalizer", "target_lufs") == pytest.approx(-17.5)
    assert "+0,5 LU" in r["message"] and "-17,5 LUFS" in r["message"]
    lc.handle({"cmd": "set_offset", "name": "loudness_lu", "value": 6.0})
    assert chain.get_param("normalizer", "target_lufs") == pytest.approx(-12.0)
    # A base of -14 LUFS plus +6 LU would exceed -12: clamped.
    cfg2 = copy.deepcopy(cfg)
    cfg2["chain"]["normalizer"]["target_lufs"] = -14.0
    chain2 = FakeChain(cfg2)
    lc2 = LiveControl(cfg2, chain2, controller=None)
    lc2.handle({"cmd": "set_offset", "name": "loudness_lu", "value": 6.0})
    assert chain2.get_param("normalizer", "target_lufs") == LOUDNESS_TARGET_BOUNDS_LUFS[1]
    cfg3 = copy.deepcopy(cfg)
    cfg3["chain"]["normalizer"]["target_lufs"] = -20.0
    lc3 = LiveControl(cfg3, FakeChain(cfg3), controller=None)
    lc3.handle({"cmd": "set_offset", "name": "loudness_lu", "value": -6.0})
    assert lc3.chain.get_param("normalizer", "target_lufs") == LOUDNESS_TARGET_BOUNDS_LUFS[0]


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
    assert chain.get_param("deesser", "threshold_db") == pytest.approx(base_d - 2.0 + 2 * INTENSITY_DEESSER_DB_PER_STEP)
    # Bounds of the configuration are never exceeded (expander upper bound -30).
    lc.handle({"cmd": "set_offset", "name": "room_db", "value": 6.0})
    cfg_hi = cfg["adaptive"]["bounds"]["expander.threshold_db"][1]
    assert chain.get_param("expander", "threshold_db") == pytest.approx(min(base_e + 6.0, cfg_hi))
    assert lc.threshold_offsets() == {"expander.threshold_db": 6.0,
                                      "deesser.threshold_db": -2.0 + 2 * INTENSITY_DEESSER_DB_PER_STEP,
                                      "compressor.threshold_db": 2 * INTENSITY_COMPRESSOR_DB_PER_STEP}


def test_threshold_offsets_with_controller_apply_at_once_and_enter_the_setpoints():
    cfg, chain, ctrl, lc, hl = fake_setup()
    before = chain.get_param("expander", "threshold_db")
    lc.handle({"cmd": "offset", "name": "room_db", "delta": 2})
    assert chain.get_param("expander", "threshold_db") == pytest.approx(before + 2.0)   # audible at once
    assert ctrl.user_offsets == {"expander.threshold_db": 2.0, "deesser.threshold_db": 0.0,
                                 "compressor.threshold_db": 0.0}
    lc.handle({"cmd": "offset", "name": "intensity", "delta": 1})
    assert ctrl.user_offsets["compressor.threshold_db"] == INTENSITY_COMPRESSOR_DB_PER_STEP
    assert ctrl.user_offsets["deesser.threshold_db"] == INTENSITY_DEESSER_DB_PER_STEP
    # The controller's tracking target includes the offset: floor -60 -> -50 + 2 = -48.
    for k in range(40):
        ctrl.update(meters_dict(floor=-60.0, speech=-20.0), now=LEARN_S + k * INTERVAL_S)
    assert abs(chain.get_param("expander", "threshold_db") - (-48.0)) <= adaptive.EXPANDER_DEADZONE_DB
    assert abs(chain.get_param("compressor", "threshold_db") - (-22.0)) <= adaptive.COMPRESSOR_DEADZONE_DB


def test_tone_offset_sets_the_eq_tilt_or_reports_it_unavailable():
    cfg, chain, ctrl, lc, hl = fake_setup(with_controller=False)
    r = lc.handle({"cmd": "offset", "name": "tone_db", "delta": 1})
    assert r["ok"] and chain.get_param("eq", "tilt_db") == 1.0 and r["state"]["tilt_db"] == 1.0
    assert "heller" in r["message"]
    lc.handle({"cmd": "set_offset", "name": "tone_db", "value": -3})
    assert chain.get_param("eq", "tilt_db") == -3.0
    # A chain without the tilt parameter: clear error, offset unchanged.
    del chain.params[("eq", "tilt_db")]
    r = lc.handle({"cmd": "offset", "name": "tone_db", "delta": 1})
    assert r["ok"] is False and "Tilt" in r["error"]
    assert lc.offsets["tone_db"] == -3.0
    assert lc.state()["tilt_db"] is None


# --------------------------------------------------------------------------- reset / freeze / mute / bypass / relearn

def test_reset_restores_calibration_offsets_hooks_and_rest_values(tmp_path):
    log = tmp_path / "lc.jsonl"
    cfg, chain, ctrl, lc, hl = fake_setup(log_path=log)
    for c in [{"cmd": "offset", "name": "loudness_lu", "delta": 0.5}, {"cmd": "offset", "name": "room_db", "delta": 2},
              {"cmd": "offset", "name": "deess_db", "delta": -2}, {"cmd": "offset", "name": "intensity", "delta": 1},
              {"cmd": "offset", "name": "tone_db", "delta": -1}, {"cmd": "mute", "on": True}, {"cmd": "bypass", "on": True}]:
        assert lc.handle(c)["ok"]
    # Simulated regulation drift on top.
    chain.set_param("expander", "threshold_db", -40.0)
    chain.set_param("compressor", "threshold_db", -25.0)
    r = lc.handle({"cmd": "reset"})
    assert r["ok"] and "Reset" in r["message"]
    st = r["state"]
    assert st["offsets"] == {n: 0.0 for n in OFFSETS}
    assert st["target_lufs"] == cfg["chain"]["normalizer"]["target_lufs"]
    assert st["tilt_db"] == 0.0 and st["muted"] is False and st["bypass"] is False
    for stage in ("expander", "deesser", "compressor"):
        assert chain.get_param(stage, "threshold_db") == cfg["chain"][stage]["threshold_db"]
    assert hl.calls[-2:] == [("mute", False), ("bypass", False)]
    assert ctrl.user_offsets == {}
    assert ctrl.learning is True                      # reset does not restart the learning phase
    ctrl.close()
    reasons = [e["reason"] for e in events(log) if "stage" in e]
    assert "reset" in reasons


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
    # Without a controller freeze/relearn are reported as unavailable; without hooks mute/bypass too.
    cfg, chain, ctrl, lc, hl = fake_setup(with_controller=False, hooks=False)
    assert lc.handle({"cmd": "freeze", "on": True})["ok"] is False
    assert lc.handle({"cmd": "relearn"})["ok"] is False
    assert lc.handle({"cmd": "mute", "on": True})["ok"] is False
    assert lc.handle({"cmd": "bypass", "on": True})["ok"] is False
    assert lc.handle({"cmd": "reset"})["ok"] is True   # reset without hooks is fine


def test_relearn_restarts_the_learning_phase_and_keeps_offsets():
    cfg, chain, ctrl, lc, hl = fake_setup()
    la = adaptive.LiveAnalyzer(SR)
    la.push(np.zeros(5 * SR, dtype=np.float32))
    lc.analyzer = la
    lc.handle({"cmd": "offset", "name": "room_db", "delta": 2})
    for k in range(20):
        ctrl.update(meters_dict(floor=-60.0), now=k * INTERVAL_S)           # 0..38 s: learning over at 30
    ctrl.clock = lambda: 40.0
    assert ctrl.learning is False
    r = lc.handle({"cmd": "relearn"})
    assert r["ok"] and "Lernphase" in r["message"] and r["state"]["learning"] is True
    assert lc.offsets["room_db"] == 2.0 and ctrl.user_offsets["expander.threshold_db"] == 2.0
    assert la.snapshot()["frames"] == 0
    assert ctrl.update(meters_dict(floor=-70.0), now=41.0) == []            # learning again
    assert len(ctrl.update(meters_dict(floor=-70.0), now=40.0 + LEARN_S)) >= 1


def test_processing_kill_switch_toggle_on_off_hook_and_state():
    cfg, chain, ctrl, lc, hl = fake_setup()
    assert lc.state()["processing"] is True
    r = lc.handle({"cmd": "processing"})                       # missing on = toggle -> off
    assert r["ok"] and r["state"]["processing"] is False
    assert "Verarbeitung AUS" in r["message"] and "Prozessorlast" in r["message"]
    assert hl.calls[-1] == ("processing", False)
    assert "OFF" in lc.handle({"cmd": "status"})["message"]
    r = lc.handle({"cmd": "processing", "on": "toggle"})
    assert r["state"]["processing"] is True and "Verarbeitung AN" in r["message"]
    assert hl.calls[-1] == ("processing", True)
    assert lc.handle({"cmd": "processing", "on": False})["state"]["processing"] is False
    assert lc.handle({"cmd": "processing", "on": True})["state"]["processing"] is True
    assert lc.handle({"cmd": "processing", "on": "maybe"})["ok"] is False
    # Without the hook the command is refused and the state stays.
    cfg, chain, ctrl, lc, hl = fake_setup(hooks=False)
    r = lc.handle({"cmd": "processing", "on": False})
    assert r["ok"] is False and "processing" in r["error"]
    assert lc.state()["processing"] is True


def test_processing_off_parks_the_freeze_state_and_restores_it_without_relearn():
    for frozen_before in (False, True):
        cfg, chain, ctrl, lc, hl = fake_setup()
        for k in range(16):
            ctrl.update(meters_dict(floor=-60.0), now=k * INTERVAL_S)        # learning phase over at 30 s
        ctrl.clock = lambda: 32.0
        t_start = ctrl._t_start
        if frozen_before:
            lc.handle({"cmd": "freeze", "on": True})
        lc.handle({"cmd": "processing", "on": False})
        assert ctrl.frozen is True                                            # regulation parked while off
        assert ctrl.update(meters_dict(floor=-70.0), now=34.0) == []
        r = lc.handle({"cmd": "processing", "on": True})
        assert ctrl.frozen is frozen_before, frozen_before                    # previous state restored
        assert ("FROZEN" in r["message"]) is frozen_before
        assert ctrl._t_start == t_start and ctrl.learning is False           # no new learning phase
        assert ctrl.user_offsets == {"expander.threshold_db": 0.0, "deesser.threshold_db": 0.0,
                                     "compressor.threshold_db": 0.0}
    # A freeze wish given while off becomes the state restored on power-up.
    cfg, chain, ctrl, lc, hl = fake_setup()
    lc.handle({"cmd": "processing", "on": False})
    r = lc.handle({"cmd": "freeze", "on": True})
    assert r["ok"] and "wirkt beim Einschalten" in r["message"]
    lc.handle({"cmd": "processing", "on": True})
    assert ctrl.frozen is True


def test_offsets_taken_while_off_are_in_effect_when_switched_on():
    cfg, chain, ctrl, lc, hl = fake_setup()
    lc.handle({"cmd": "processing", "on": False})
    r = lc.handle({"cmd": "offset", "name": "loudness_lu", "delta": 0.5})
    assert r["ok"] and "wirkt beim Einschalten" in r["message"]
    assert lc.handle({"cmd": "offset", "name": "room_db", "delta": 2})["ok"]
    assert lc.handle({"cmd": "offset", "name": "tone_db", "delta": -1})["ok"]
    assert lc.handle({"cmd": "offset", "name": "intensity", "delta": 1})["ok"]
    assert lc.offsets == {"loudness_lu": 0.5, "room_db": 2.0, "deess_db": 0.0, "tone_db": -1.0, "intensity": 1.0}
    # Simulate a stage reset on power-up that lost the absolute values.
    chain.params[("normalizer", "target_lufs")] = -18.0
    chain.params[("eq", "tilt_db")] = 0.0
    r = lc.handle({"cmd": "processing", "on": True})
    assert r["ok"] and "wirkt beim Einschalten" not in r["message"]
    assert chain.get_param("normalizer", "target_lufs") == pytest.approx(-17.5)
    assert chain.get_param("eq", "tilt_db") == pytest.approx(-1.0)
    assert chain.get_param("expander", "threshold_db") == pytest.approx(cfg["chain"]["expander"]["threshold_db"] + 2.0)
    assert chain.get_param("compressor", "threshold_db") == pytest.approx(cfg["chain"]["compressor"]["threshold_db"] - 2.0)
    assert ctrl.user_offsets["expander.threshold_db"] == 2.0
    # Without a controller the same holds.
    cfg, chain, ctrl, lc, hl = fake_setup(with_controller=False)
    lc.handle({"cmd": "processing", "on": False})
    lc.handle({"cmd": "set_offset", "name": "deess_db", "value": -4.0})
    lc.handle({"cmd": "processing", "on": True})
    assert chain.get_param("deesser", "threshold_db") == pytest.approx(cfg["chain"]["deesser"]["threshold_db"] - 4.0)


def test_reset_switches_processing_on_again():
    cfg, chain, ctrl, lc, hl = fake_setup()
    lc.handle({"cmd": "freeze", "on": True})
    lc.handle({"cmd": "processing", "on": False})
    lc.handle({"cmd": "offset", "name": "room_db", "delta": 2})
    hl.calls.clear()
    r = lc.handle({"cmd": "reset"})
    assert r["ok"] and r["state"]["processing"] is True
    assert hl.calls == [("processing", True), ("mute", False), ("bypass", False)]
    assert r["state"]["offsets"]["room_db"] == 0.0
    assert chain.get_param("expander", "threshold_db") == cfg["chain"]["expander"]["threshold_db"]
    assert ctrl.frozen is True                     # reset restores the parked freeze state, it does not unfreeze
    assert ctrl.learning is True


# --------------------------------------------------------------------------- snapshot / marker

def test_snapshot_writes_base_plus_offsets_and_live_block(tmp_path):
    cfg, chain, ctrl, lc, hl = fake_setup()
    lc.handle({"cmd": "offset", "name": "loudness_lu", "delta": 0.5})
    lc.handle({"cmd": "offset", "name": "room_db", "delta": 2})
    lc.handle({"cmd": "offset", "name": "intensity", "delta": 1})
    path = tmp_path / "live" / "chain.live.yaml"
    r = lc.handle({"cmd": "snapshot", "path": str(path)})
    assert r["ok"] and str(path) in r["message"]
    saved = load_config(path)
    assert saved["chain"]["normalizer"]["target_lufs"] == pytest.approx(-17.5)
    assert saved["chain"]["expander"]["threshold_db"] == pytest.approx(cfg["chain"]["expander"]["threshold_db"] + 2.0)
    assert saved["chain"]["compressor"]["threshold_db"] == pytest.approx(cfg["chain"]["compressor"]["threshold_db"] - 2.0)
    assert saved["chain"]["deesser"]["threshold_db"] == pytest.approx(cfg["chain"]["deesser"]["threshold_db"] - 1.0)
    assert saved["_live"]["offsets"] == {"loudness_lu": 0.5, "room_db": 2.0, "deess_db": 0.0, "tone_db": 0.0, "intensity": 1.0}
    assert "created" in saved["_live"]
    # No tone offset: the tilt stays at the configured base (key only present when the base has it).
    assert saved["chain"]["eq"].get("tilt_db", 0.0) == cfg["chain"]["eq"].get("tilt_db", 0.0)
    lc.handle({"cmd": "offset", "name": "tone_db", "delta": -1})
    lc.handle({"cmd": "snapshot", "path": str(path)})
    assert load_config(path)["chain"]["eq"]["tilt_db"] == pytest.approx(cfg["chain"]["eq"].get("tilt_db", 0.0) - 1.0)
    assert DEFAULT_SNAPSHOT_PATH.as_posix() == "config/chain.live.yaml"


def test_marker_goes_to_the_controller_log_or_the_configured_log_file(tmp_path):
    log = tmp_path / "m.jsonl"
    cfg, chain, ctrl, lc, hl = fake_setup(log_path=log)
    assert lc.handle({"cmd": "marker", "text": "Folie 3"})["ok"]
    ctrl.close()
    marks = [e for e in events(log) if e.get("event") == "marker"]
    assert marks == [{"t": 0.0, "event": "marker", "text": "Folie 3"}]
    # Without a controller the marker is appended to cfg.adaptive.log_path.
    cfg, chain, ctrl, lc, hl = fake_setup(with_controller=False)
    cfg["adaptive"]["log_path"] = str(tmp_path / "plain.jsonl")
    assert lc.handle({"cmd": "marker", "text": "Pause"})["ok"]
    assert json.loads((tmp_path / "plain.jsonl").read_text().splitlines()[0]) == {"t": 0.0, "event": "marker", "text": "Pause"}


# --------------------------------------------------------------------------- real chain

def _calibrated(base_cfg):
    x = synth.make_speech_like(60.0, SR, level_dbfs=-26.0, noise_floor_dbfs=-50.0, sibilance_db=0.0,
                               seed=3, lead_silence_s=10.0, long_pause_s=6.0)
    return cal.recommend(cal.analyze(x, SR), base_cfg)


def _run(cfg, x, offsets=None, block=2048):
    """Process x through a fresh chain with the offsets applied via LiveControl (no controller);
    returns output and the average stage meters (gain reductions) over the whole run."""
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


@pytest.fixture(scope="module")
def speech():
    return synth.make_speech_like(40.0, SR, level_dbfs=-26.0, noise_floor_dbfs=-50.0, sibilance_db=0.0,
                                  seed=5, long_pause_s=6.0)


def test_loudness_offset_changes_output_loudness_by_the_same_amount(base_cfg, speech):
    cfg = _calibrated(base_cfg)
    y0, _ = _run(cfg, speech)
    y1, _ = _run(cfg, speech, {"loudness_lu": 1.0})
    y2, _ = _run(cfg, speech, {"loudness_lu": -1.0})
    half = speech.size // 2
    l0, l1, l2 = (meters.integrated_lufs(y[half:], SR) for y in (y0, y1, y2))
    assert l1 - l0 == pytest.approx(1.0, abs=0.5)
    assert l2 - l0 == pytest.approx(-1.0, abs=0.5)


def test_room_offset_changes_the_pause_attenuation(base_cfg, speech):
    cfg = _calibrated(base_cfg)
    span = meters.longest_pause(speech, SR, 5.0)
    y0, _ = _run(cfg, speech)
    y_less, _ = _run(cfg, speech, {"room_db": 4.0})
    y_more, _ = _run(cfg, speech, {"room_db": -4.0})
    p = lambda y: meters.pause_loudness_lufs(y, SR, *span)["rms_dbfs"]
    assert p(y_less) < p(y0) - 2.0          # less room: pause quieter
    assert p(y_more) > p(y0) + 1.0          # more room: pause louder


def test_deess_and_intensity_offsets_change_the_gain_reductions(base_cfg, speech):
    cfg = _calibrated(base_cfg)
    _, m0 = _run(cfg, speech)
    _, m_de = _run(cfg, speech, {"deess_db": -4.0})
    _, m_int = _run(cfg, speech, {"intensity": 2.0})
    _, m_nat = _run(cfg, speech, {"intensity": -2.0})
    assert m_de["deesser_gr"] > m0["deesser_gr"] * 1.3 + 0.1
    assert m_int["compressor_gr"] > m0["compressor_gr"] + 1.0
    assert m_nat["compressor_gr"] < m0["compressor_gr"] - 1.0
    assert m_int["deesser_gr"] > m0["deesser_gr"]      # intensity also de-esses a little more


def test_tone_offset_tilts_the_frequency_response(base_cfg):
    """+2 dB tilt: the 7-14 kHz band gains relative to 80-170 Hz by about 2 dB (tilt +-1 dB
    per shelf); -2 dB the opposite."""
    cfg = _calibrated(base_cfg)
    rng = np.random.default_rng(7)
    noise = (rng.standard_normal(20 * SR) * 10 ** (-20 / 20)).astype(np.float32)
    cfg_flat = copy.deepcopy(cfg)
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


def test_reset_restores_every_chain_parameter_of_the_loaded_configuration(base_cfg):
    """Section 6.5: after offsets, drift and reset, every get_param value equals the loaded config."""
    from voicechain.dsp.chain import Chain
    cfg = _calibrated(base_cfg)
    chain = Chain.from_config(cfg)
    ctrl = adaptive.AdaptiveController(cfg, chain, clock=lambda: 0.0)
    lc = LiveControl(cfg, chain, controller=ctrl, hooks=HookLog().hooks())
    for c in [{"cmd": "set_offset", "name": "loudness_lu", "value": 2.0}, {"cmd": "set_offset", "name": "room_db", "value": 4.0},
              {"cmd": "set_offset", "name": "deess_db", "value": -4.0}, {"cmd": "set_offset", "name": "intensity", "value": 2.0},
              {"cmd": "mute", "on": True}]:
        assert lc.handle(c)["ok"], c
    chain.set_param("expander", "threshold_db", chain.get_param("expander", "threshold_db") - 1.5)   # regulation drift
    changed = {(s, p) for s, st in chain.stages.items() for p, v in st.get_params().items()
               if v != Chain.from_config(cfg).stages[s].get_params()[p]}
    assert changed >= {("normalizer", "target_lufs"), ("expander", "threshold_db"),
                       ("deesser", "threshold_db"), ("compressor", "threshold_db")}
    assert lc.handle({"cmd": "reset"})["ok"]
    fresh = Chain.from_config(cfg)
    for stage_name, stage in chain.stages.items():
        expected = fresh.stages[stage_name].get_params()
        actual = stage.get_params()
        assert set(actual) == set(expected), stage_name
        for pname, value in actual.items():
            assert value == pytest.approx(expected[pname]), (stage_name, pname)
        # Every numeric value of the loaded configuration is among them.
        for pname, cfg_value in cfg["chain"][stage_name].items():
            if isinstance(cfg_value, (int, float)) and not isinstance(cfg_value, bool):
                assert actual[pname] == pytest.approx(float(cfg_value)), (stage_name, pname)


def test_controller_keeps_a_room_offset_over_300_s(tmp_path, base_cfg):
    """Section 6.4: with room +4 the expander settles at target + 4 within the dead zone and the
    regulation never steps it back down."""
    from test_adaptive import _run_closed_loop
    from voicechain.dsp.chain import Chain
    cfg = _calibrated(base_cfg)
    cfg["adaptive"].update({"learn_s": LEARN_S, "interval_s": INTERVAL_S, "max_step_db": STEP_DB})
    x = synth.make_speech_like(300.0, SR, level_dbfs=-26.0, noise_floor_dbfs=-50.0, sibilance_db=0.0,
                               seed=21, long_pause_s=6.0)
    chain = Chain.from_config(cfg)
    la = adaptive.LiveAnalyzer(SR, deesser_freq_hz=cfg["chain"]["deesser"]["freq_hz"],
                               deesser_bandwidth_oct=cfg["chain"]["deesser"]["bandwidth_oct"])
    log = tmp_path / "room.jsonl"
    ctrl = adaptive.AdaptiveController(cfg, chain, clock=lambda: 0.0, log_path=log)
    lc = LiveControl(cfg, chain, controller=ctrl)
    calibrated = cfg["chain"]["expander"]["threshold_db"]
    tick = int(INTERVAL_S * SR)
    block = 2048
    next_tick = tick
    changes = []
    pressed = False
    for i in range(0, x.size, block):
        blk = x[i:i + block]
        la.push(blk)
        chain.process(blk)
        if i + block >= next_tick:
            m = chain.get_meters()
            m["analysis"] = la.snapshot()
            changes.extend(ctrl.update(m, now=next_tick / SR))
            if not pressed and next_tick / SR >= 60.0:          # two presses of "less room" at 60 s
                assert lc.handle({"cmd": "offset", "name": "room_db", "delta": 2})["ok"]
                assert lc.handle({"cmd": "offset", "name": "room_db", "delta": 2})["ok"]
                pressed = True
                after_press = chain.get_param("expander", "threshold_db")
            next_tick += tick
    ctrl.close()
    assert after_press == pytest.approx(calibrated + 4.0, abs=STEP_DB + 1e-9)
    exp_after = [c for c in changes if c["stage"] == "expander" and c["t"] > 60.0]
    assert all(c["new"] >= c["old"] for c in exp_after), exp_after            # never stepped back down
    final = chain.get_param("expander", "threshold_db")
    assert abs(final - (calibrated + 4.0)) <= adaptive.EXPANDER_DEADZONE_DB + STEP_DB
    assert final >= calibrated + 4.0 - adaptive.EXPANDER_DEADZONE_DB
