"""Adaptive control of the chain during live operation.

Two classes:

``LiveAnalyzer(sample_rate)``
    Fed from the audio callback with the raw input blocks (``push``), it keeps
    a rolling history of 50 ms frame levels and band energies and estimates
    noise floor, speech level, sibilance ratio and speech activity
    (``snapshot``). ``push`` is cheap (one RMS and one real FFT per 50 ms) and
    does not allocate per block beyond the input conversion.

``AdaptiveController(cfg, chain, clock=time.monotonic, log_path=None)``
    Called every ``adaptive.interval_s`` from a service thread with
    ``chain.get_meters()`` plus ``meters["analysis"] = analyzer.snapshot()``.
    After the learning phase it moves the expander, de-esser and compressor
    thresholds towards the same targets the calibration uses
    (``voicechain.calibrate.*_target``), in steps of at most ``max_step_db``,
    inside ``adaptive.bounds``, with dead zones, and with at least two intervals
    between changes of the same parameter. Protection rules react to limiter
    overload and to speech being cut by the expander.

Snapshot format (``meters["analysis"]``)::

    {"ready": bool,              # enough history (>= MIN_HISTORY_S) for estimates
     "speech_detected": bool,    # loud/soft frame span >= MIN_SPEECH_SNR_DB
     "noise_floor_dbfs": float,  # 10th percentile of frame RMS (dBFS, as measured)
     "speech_level_dbfs": float, # energy mean of active frames (dBFS, as measured)
     "sibilance_ratio_db": float,# 5-10 kHz minus 0.3-3 kHz energy of active frames
     "speech_active": bool,      # most recent frame classified as speech
     "speech_ratio": float,      # share of active frames in the history
     "frames": int,              # frames in the history
     "history_s": float}         # seconds of audio the history covers

The analyzer sees the signal *before* the input trim (that is what the runtime
has in hand), so the controller adds ``chain.get_param("input_trim", "gain_db")``
to the level values. Pass ``analysis_is_post_trim=True`` if the analyzer is fed
after the trim instead.

Meter keys used from ``chain.get_meters()`` (all optional; a missing key disables
the corresponding rule): ``limiter.gain_reduction_db``,
``expander.gain_reduction_db``, ``normalizer.gain_db``. Gain reductions are
read as magnitudes (``abs``), so either sign convention works.

Log format (JSON lines): every applied change is
``{"t", "stage", "name", "old", "new", "reason"}``; informational entries are
``{"t", "event", ...}`` (events: learning, learn_done, no_analysis, frozen,
limiter_protect, freeze).
"""
from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path

import numpy as np

from voicechain import meters as _m
from voicechain.calibrate import (
    DEFAULT_CONFIG,
    clamp,
    compressor_threshold_target,
    deesser_threshold_target,
    expander_threshold_target,
    parse_bounds,
)

# --------------------------------------------------------------------------- #
# Constants (German explanation in docs/03)
# --------------------------------------------------------------------------- #

#: Rolling history of the live analyzer. 20 s is long enough to contain speech
#: pauses (noise-floor percentile) and short enough to follow a changed
#: environment within a minute. Assumption.
HISTORY_S = 20.0
#: Minimum history before the snapshot is marked ready: 2 s = 40 frames, the
#: smallest sample on which percentiles are meaningful. Assumption.
MIN_HISTORY_S = 2.0

#: Dead zones (task specification): no change while |target - value| is inside.
EXPANDER_DEADZONE_DB = 2.0
DEESSER_DEADZONE_DB = 1.5
COMPRESSOR_DEADZONE_DB = 2.0
#: Minimum number of intervals between two changes of the same parameter.
MIN_CHANGE_SPACING_INTERVALS = 2

#: Protection: mean limiter gain reduction over an interval above this means the
#: chain is pushing too hard; the compressor threshold is then not raised and
#: the normalizer may not add more gain. Task specification: 3 dB.
LIMITER_PROTECT_GR_DB = 3.0
#: Hysteresis for leaving the protection state (assumption: half the trigger).
LIMITER_PROTECT_RELEASE_GR_DB = 1.5
#: Protection: mean expander gain reduction while speech is active above this
#: means speech is being cut; the expander threshold is lowered immediately.
#: Task specification: 6 dB.
SPEECH_CUT_GR_DB = 6.0
#: Default bounds used if ``adaptive.bounds`` lacks a key (schema section 4.1).
DEFAULT_BOUNDS = {
    ("expander", "threshold_db"): (-70.0, -30.0),
    ("deesser", "threshold_db"): (-45.0, -15.0),
    ("compressor", "threshold_db"): (-35.0, -10.0),
}
#: Numerical tolerance for "no change" and time comparisons.
EPS = 1e-9


# --------------------------------------------------------------------------- #
# LiveAnalyzer
# --------------------------------------------------------------------------- #

class LiveAnalyzer:
    """Rolling estimate of noise floor, speech level, sibilance and activity."""

    def __init__(self, sample_rate: int, frame_ms: float = _m.ANALYSIS_FRAME_MS,
                 history_s: float = HISTORY_S):
        if sample_rate <= 0:
            raise ValueError(f"sample_rate must be positive, got {sample_rate}")
        if history_s < MIN_HISTORY_S:
            raise ValueError(f"history_s must be >= {MIN_HISTORY_S}, got {history_s}")
        self.sample_rate = int(sample_rate)
        self.frame_len = int(round(self.sample_rate * frame_ms / 1000.0))
        if self.frame_len < 2:
            raise ValueError(f"frame of {frame_ms} ms is too short at {sample_rate} Hz")
        self.frame_s = self.frame_len / self.sample_rate
        self.history_frames = int(round(history_s / self.frame_s))
        self.min_frames = int(math.ceil(MIN_HISTORY_S / self.frame_s))
        self._buf = np.zeros(self.frame_len)
        self._fill = 0
        self._window = np.hanning(self.frame_len)
        freqs = np.fft.rfftfreq(self.frame_len, d=1.0 / self.sample_rate)
        f_max = _m.MAX_USABLE_FREQ_FRACTION * self.sample_rate
        lo, hi = _m.SPEECH_CORE_BAND_HZ
        self._core_sel = (freqs >= lo) & (freqs < min(hi, f_max))
        lo, hi = _m.SIBILANCE_BAND_HZ
        self._sib_sel = (freqs >= lo) & (freqs < min(hi, f_max))
        if not np.any(self._sib_sel):
            raise ValueError(f"sample rate {sample_rate} Hz is too low for the 5-10 kHz sibilance band")
        # Ring buffers of per-frame statistics; written by push(), read by snapshot().
        self._levels = np.full(self.history_frames, _m.DB_FLOOR)
        self._core = np.zeros(self.history_frames)
        self._sib = np.zeros(self.history_frames)
        self._pos = 0
        self._count = 0
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._fill = 0
            self._pos = 0
            self._count = 0

    def push(self, x: np.ndarray) -> None:
        """Feed one block of raw input samples (float, mono)."""
        x = np.asarray(x, dtype=np.float64).ravel()
        pos = 0
        n = x.size
        while pos < n:
            take = min(self.frame_len - self._fill, n - pos)
            self._buf[self._fill:self._fill + take] = x[pos:pos + take]
            self._fill += take
            pos += take
            if self._fill == self.frame_len:
                self._finish_frame()
                self._fill = 0

    def _finish_frame(self) -> None:
        buf = self._buf
        level = float(_m._power_to_db(float(np.mean(buf * buf))))
        spec = np.fft.rfft(buf * self._window)
        power = spec.real * spec.real + spec.imag * spec.imag
        core = float(np.sum(power[self._core_sel]))
        sib = float(np.sum(power[self._sib_sel]))
        with self._lock:
            self._levels[self._pos] = level
            self._core[self._pos] = core
            self._sib[self._pos] = sib
            self._pos = (self._pos + 1) % self.history_frames
            self._count = min(self._count + 1, self.history_frames)

    def _history(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Copies of the valid history in chronological order."""
        with self._lock:
            count = self._count
            pos = self._pos
            levels = self._levels.copy()
            core = self._core.copy()
            sib = self._sib.copy()
        if count < self.history_frames:
            return levels[:count], core[:count], sib[:count]
        order = np.concatenate((np.arange(pos, self.history_frames), np.arange(0, pos)))
        return levels[order], core[order], sib[order]

    def snapshot(self) -> dict:
        """Current estimates; see the module docstring for the key meanings."""
        levels, core, sib = self._history()
        count = levels.size
        out = {
            "ready": bool(count >= self.min_frames),
            "speech_detected": False,
            "noise_floor_dbfs": _m.DB_FLOOR,
            "speech_level_dbfs": _m.DB_FLOOR,
            "sibilance_ratio_db": 0.0,
            "speech_active": False,
            "speech_ratio": 0.0,
            "frames": int(count),
            "history_s": float(count * self.frame_s),
        }
        if count == 0:
            return out
        thr, detected = _m.activity_threshold_db(levels)
        out["noise_floor_dbfs"] = float(np.percentile(levels, _m.NOISE_FLOOR_PERCENTILE))
        out["speech_detected"] = bool(detected)
        if detected:
            mask = levels > thr
        else:
            mask = np.zeros(count, dtype=bool)
        if np.any(mask):
            out["speech_level_dbfs"] = float(_m._power_to_db(np.mean(10.0 ** (levels[mask] / 10.0))))
            sel = mask
        else:
            out["speech_level_dbfs"] = float(np.percentile(levels, _m.SPEECH_TOP_PERCENTILE))
            sel = np.ones(count, dtype=bool)
        out["sibilance_ratio_db"] = float(_m._power_to_db(np.sum(sib[sel])) - _m._power_to_db(np.sum(core[sel])))
        out["speech_active"] = bool(mask[-1])
        out["speech_ratio"] = float(np.mean(mask))
        return out


# --------------------------------------------------------------------------- #
# AdaptiveController
# --------------------------------------------------------------------------- #

class AdaptiveController:
    """Slow, bounded, logged tracking of three thresholds plus protection rules."""

    def __init__(self, cfg: dict, chain, clock=time.monotonic, log_path=None,
                 analysis_is_post_trim: bool = False):
        adaptive = cfg.get("adaptive", {})
        defaults = DEFAULT_CONFIG["adaptive"]
        self.learn_s = float(adaptive.get("learn_s", defaults["learn_s"]))
        self.interval_s = float(adaptive.get("interval_s", defaults["interval_s"]))
        self.max_step_db = float(adaptive.get("max_step_db", defaults["max_step_db"]))
        if self.learn_s < 0 or self.interval_s <= 0 or self.max_step_db <= 0:
            raise ValueError(
                f"adaptive settings invalid: learn_s={self.learn_s}, interval_s={self.interval_s}, "
                f"max_step_db={self.max_step_db}")
        self.bounds = dict(DEFAULT_BOUNDS)
        self.bounds.update(parse_bounds(cfg))
        self.chain = chain
        self.clock = clock
        self.analysis_is_post_trim = bool(analysis_is_post_trim)
        chain_cfg = cfg.get("chain", {})
        deesser_cfg = chain_cfg.get("deesser", {})
        self.deesser_ratio = float(deesser_cfg.get("ratio", DEFAULT_CONFIG["chain"]["deesser"]["ratio"]))
        #: Threshold the de-esser returns to when sibilance is below the reference
        #: (the calibrated/configured value).
        self.deesser_rest_threshold_db = float(
            deesser_cfg.get("threshold_db", DEFAULT_CONFIG["chain"]["deesser"]["threshold_db"]))
        self.normalizer_max_gain_db = float(chain_cfg.get("normalizer", {}).get(
            "max_gain_db", DEFAULT_CONFIG["chain"]["normalizer"]["max_gain_db"]))
        self._frozen = False
        # The learning phase starts when the controller is created (the runtime
        # creates it right before opening the stream).
        self._t_start: float = float(clock())
        self._last_decision_t: float = self._t_start - self.interval_s
        self._last_learning_log_t: float | None = None
        self._learn_done_logged = False
        self._last_change_t: dict[tuple[str, str], float] = {}
        self._limiter_protect = False
        self._acc_limiter: list[float] = []
        self._acc_expander_speech: list[float] = []
        self._acc_normalizer: list[float] = []
        self._log_fh = None
        if log_path:
            path = Path(log_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._log_fh = open(path, "a", encoding="utf-8")

    # ---------------------------------------------------------------- public
    @property
    def frozen(self) -> bool:
        return self._frozen

    @property
    def limiter_protect_active(self) -> bool:
        return self._limiter_protect

    def freeze(self, on: bool) -> None:
        """Stop (True) or resume (False) applying changes; logging continues."""
        self._frozen = bool(on)
        self._log({"t": self._now(None), "event": "freeze", "on": self._frozen})

    def close(self) -> None:
        if self._log_fh is not None:
            self._log_fh.close()
            self._log_fh = None

    def update(self, meters: dict, now: float | None = None) -> list[dict]:
        """Evaluate meters (+ ``meters["analysis"]``) and apply at most one step per parameter.

        Returns the list of applied changes ``{"t", "stage", "name", "old", "new", "reason"}``.
        """
        now = self._now(now)
        self._accumulate(meters)
        elapsed = now - self._t_start
        if elapsed < self.learn_s - EPS:
            if (self._last_learning_log_t is None
                    or now - self._last_learning_log_t >= self.interval_s - EPS):
                self._last_learning_log_t = now
                self._log({"t": now, "event": "learning", "elapsed_s": round(elapsed, 3),
                           "remaining_s": round(self.learn_s - elapsed, 3)})
            return []
        if not self._learn_done_logged:
            self._learn_done_logged = True
            self._log({"t": now, "event": "learn_done", "learn_s": self.learn_s})
        if now - self._last_decision_t < self.interval_s - EPS:
            return []
        self._last_decision_t = now
        stats = self._drain()
        analysis = meters.get("analysis")
        if not isinstance(analysis, dict) or not analysis.get("ready", False):
            self._log({"t": now, "event": "no_analysis",
                       "detail": "meters['analysis'] missing or not ready"})
            return []
        proposals = self._propose(now, analysis, stats)
        return self._apply(now, proposals)

    # ------------------------------------------------------------- internals
    def _now(self, now: float | None) -> float:
        return float(self.clock()) if now is None else float(now)

    def _log(self, entry: dict) -> None:
        if self._log_fh is None:
            return
        self._log_fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._log_fh.flush()

    @staticmethod
    def _meter(meters: dict, stage: str, name: str) -> float | None:
        stage_meters = meters.get(stage)
        if not isinstance(stage_meters, dict):
            return None
        value = stage_meters.get(name)
        if value is None:
            return None
        value = float(value)
        if math.isnan(value):
            return None
        return value

    def _accumulate(self, meters: dict) -> None:
        lim = self._meter(meters, "limiter", "gain_reduction_db")
        if lim is not None:
            self._acc_limiter.append(abs(lim))
        analysis = meters.get("analysis")
        speech_active = bool(analysis.get("speech_active", False)) if isinstance(analysis, dict) else False
        exp = self._meter(meters, "expander", "gain_reduction_db")
        if exp is not None and speech_active:
            self._acc_expander_speech.append(abs(exp))
        norm = self._meter(meters, "normalizer", "gain_db")
        if norm is not None:
            self._acc_normalizer.append(norm)

    def _drain(self) -> dict:
        def mean_or_none(values: list[float]) -> float | None:
            return float(np.mean(values)) if values else None
        stats = {
            "limiter_gr_mean": mean_or_none(self._acc_limiter),
            "expander_gr_speech_mean": mean_or_none(self._acc_expander_speech),
            "normalizer_gain_last": self._acc_normalizer[-1] if self._acc_normalizer else None,
        }
        self._acc_limiter = []
        self._acc_expander_speech = []
        self._acc_normalizer = []
        return stats

    def _bounds(self, stage: str, name: str) -> tuple[float, float]:
        return self.bounds[(stage, name)]

    def _track(self, stage: str, name: str, target: float, deadzone: float,
               reason: str, block_increase: bool = False) -> dict | None:
        lo, hi = self._bounds(stage, name)
        target = clamp(target, lo, hi)
        current = float(self.chain.get_param(stage, name))
        error = target - current
        if abs(error) <= deadzone:
            return None
        if block_increase and error > 0:
            return None
        step = math.copysign(min(self.max_step_db, abs(error)), error)
        return {"stage": stage, "name": name, "step": step, "reason": reason,
                "immediate": False, "target": target}

    def _propose(self, now: float, analysis: dict, stats: dict) -> list[dict]:
        trim = 0.0 if self.analysis_is_post_trim else float(self.chain.get_param("input_trim", "gain_db"))
        floor = float(analysis["noise_floor_dbfs"]) + trim
        speech = float(analysis["speech_level_dbfs"]) + trim
        sib = float(analysis["sibilance_ratio_db"])
        speech_detected = bool(analysis.get("speech_detected", False))
        proposals: list[dict] = []

        # Limiter protection state (hysteresis), evaluated first because it
        # gates the compressor rule.
        lim_mean = stats["limiter_gr_mean"]
        if lim_mean is not None:
            if not self._limiter_protect and lim_mean > LIMITER_PROTECT_GR_DB:
                self._limiter_protect = True
                self._log({"t": now, "event": "limiter_protect", "active": True,
                           "limiter_gr_mean_db": round(lim_mean, 2),
                           "hold_normalizer_gain": True, "block_compressor_raise": True})
                proposals.append(self._normalizer_hold(stats, reason="limiter_overload"))
            elif self._limiter_protect and lim_mean < LIMITER_PROTECT_RELEASE_GR_DB:
                self._limiter_protect = False
                self._log({"t": now, "event": "limiter_protect", "active": False,
                           "limiter_gr_mean_db": round(lim_mean, 2),
                           "hold_normalizer_gain": False, "block_compressor_raise": False})
                proposals.append(self._normalizer_release(reason="limiter_recovered"))

        # Expander: speech-cut protection has priority over tracking.
        exp_speech = stats["expander_gr_speech_mean"]
        if exp_speech is not None and exp_speech > SPEECH_CUT_GR_DB:
            proposals.append({"stage": "expander", "name": "threshold_db", "step": -self.max_step_db,
                              "reason": "speech_cut", "immediate": True, "target": None})
        else:
            p = self._track("expander", "threshold_db",
                            expander_threshold_target(floor, speech if speech_detected else None),
                            EXPANDER_DEADZONE_DB, "noise_floor_track")
            if p:
                proposals.append(p)

        if speech_detected:
            de_target, engaged = deesser_threshold_target(speech, sib, self.deesser_ratio,
                                                          self.deesser_rest_threshold_db)
            p = self._track("deesser", "threshold_db", de_target, DEESSER_DEADZONE_DB,
                            "sibilance_track" if engaged else "sibilance_rest")
            if p:
                proposals.append(p)
            p = self._track("compressor", "threshold_db", compressor_threshold_target(speech),
                            COMPRESSOR_DEADZONE_DB, "speech_level_track",
                            block_increase=self._limiter_protect)
            if p:
                proposals.append(p)
        return [p for p in proposals if p is not None]

    def _normalizer_hold(self, stats: dict, reason: str) -> dict | None:
        """Cap normalizer.max_gain_db at the current gain so it cannot add more level."""
        current_gain = stats["normalizer_gain_last"]
        if current_gain is None:
            return None
        hold = clamp(current_gain, 0.0, self.normalizer_max_gain_db)
        return {"stage": "normalizer", "name": "max_gain_db", "absolute": hold,
                "reason": reason, "immediate": True, "target": hold}

    def _normalizer_release(self, reason: str) -> dict:
        return {"stage": "normalizer", "name": "max_gain_db", "absolute": self.normalizer_max_gain_db,
                "reason": reason, "immediate": True, "target": self.normalizer_max_gain_db}

    def _apply(self, now: float, proposals: list[dict]) -> list[dict]:
        changes: list[dict] = []
        spacing = MIN_CHANGE_SPACING_INTERVALS * self.interval_s
        for p in proposals:
            key = (p["stage"], p["name"])
            if not p["immediate"]:
                last = self._last_change_t.get(key)
                if last is not None and now - last < spacing - EPS:
                    continue
            old = float(self.chain.get_param(*key))
            if "absolute" in p:
                new = float(p["absolute"])
            else:
                lo, hi = self._bounds(*key)
                # A value that starts outside the bounds is walked back in steps
                # of max_step_db, never jumped; it may not move further out.
                new = clamp(old + p["step"], min(lo, old), max(hi, old))
            if abs(new - old) <= EPS:
                continue
            entry = {"t": now, "stage": key[0], "name": key[1], "old": old, "new": new,
                     "reason": p["reason"]}
            if self._frozen:
                self._log({"t": now, "event": "frozen", "proposed": entry})
                continue
            self.chain.set_param(key[0], key[1], new)
            self._last_change_t[key] = now
            self._log(entry)
            changes.append(entry)
        return changes
