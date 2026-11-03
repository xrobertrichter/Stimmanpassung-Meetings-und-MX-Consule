"""Adaptive control of the chain during live operation.

Two classes:

``LiveAnalyzer(sample_rate, deesser_freq_hz=6500, deesser_bandwidth_oct=1.0)``
    Fed from the audio callback with the raw input blocks (``push``), it keeps
    a rolling history of 50 ms frame statistics (level, band energies, de-esser
    band peak) and estimates noise floor, speech level, dynamic range,
    sibilance ratio, sibilant band peak and speech activity (``snapshot``).
    ``push`` is cheap (one RMS, one real FFT and one biquad pass per 50 ms).
    Pass the chain's de-esser band (``set_deesser_band``) so the band peak is
    measured where the de-esser listens; the defaults equal the shipped config.

``AdaptiveController(cfg, chain, clock=time.monotonic, log_path=None)``
    Called every ``adaptive.interval_s`` from a service thread with
    ``chain.get_meters()`` plus ``meters["analysis"] = analyzer.snapshot()``.
    After the learning phase it moves the expander, de-esser and compressor
    thresholds towards the same targets the calibration uses
    (``voicechain.calibrate.*_target``), in steps of at most ``max_step_db``,
    inside ``adaptive.bounds``, with dead zones, and with at least two intervals
    between changes of the same parameter. A plausibility check keeps keyboard
    clicks, distant voices or playback from being treated as the speaker (a
    sound engineer does not re-trim the de-esser for typing). Protection rules
    react to limiter overload and to speech being cut by the expander.

Snapshot format (``meters["analysis"]``)::

    {"ready": bool,                 # enough history (>= MIN_HISTORY_S) for estimates
     "speech_detected": bool,       # loud/soft frame span >= MIN_SPEECH_SNR_DB
     "noise_floor_dbfs": float,     # 10th percentile of frame RMS (dBFS, as measured)
     "speech_level_dbfs": float,    # energy mean of active frames (dBFS, as measured)
     "dynamic_range_db": float,     # 95th minus 10th percentile of frame RMS
     "sibilance_ratio_db": float,   # 5-10 kHz minus 0.3-3 kHz energy of active frames
     "band_peak_p95_dbfs": float,   # 95th percentile of the de-esser band frame peaks (active frames)
     "band_hz": (freq, bandwidth_oct),  # band the peak was measured in
     "speech_active": bool,         # most recent frame classified as speech
     "speech_ratio": float,         # share of active frames in the history
     "recent_speech_ratio": float,  # share of active frames in the last RECENT_S seconds
     "frames": int,                 # frames in the history
     "history_s": float}            # seconds of audio the history covers

The analyzer sees the signal *before* the input trim (that is what the runtime
has in hand), so the controller adds ``chain.get_param("input_trim", "gain_db")``
to the level values. Pass ``analysis_is_post_trim=True`` if the analyzer is fed
after the trim instead.

Meter keys used from ``chain.get_meters()`` (accumulated by the stages since
the previous ``get_meters`` call): ``limiter.gain_reduction_mean_db``,
``expander.gain_reduction_max_db``, ``normalizer.gain_db``. A missing key
disables the corresponding rule. The stage accumulators cover the whole
interval, pauses included, so the expander maximum is attributed to speech only
in intervals whose frames were (almost) all speech
(``recent_speech_ratio >= SPEECH_CUT_MIN_ACTIVE_RATIO``).

Log format (JSON lines): every applied change is
``{"t", "stage", "name", "old", "new", "reason"}``; informational entries are
``{"t", "event", ...}`` (events: learning, learn_done, no_analysis,
implausible_speech, low_snr, frozen, limiter_protect, freeze).
"""
from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path

import numpy as np
from scipy import signal

from voicechain import meters as _m
from voicechain.calibrate import (
    REFERENCE_SPEECH_LEVEL_DBFS,
    SIBILANCE_REFERENCE_DB,
    clamp,
    compressor_threshold_target,
    deesser_threshold_target,
    expander_threshold_target,
    parse_bounds,
    tracked_bounds,
)
from voicechain.dsp.biquad import bandpass_sos, bandwidth_to_q

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
#: Window of ``recent_speech_ratio`` = one controller interval (2 s default).
RECENT_S = 2.0
#: Live noise-floor estimate: low percentile of the *inactive* frames (below the
#: activity threshold) when at least FLOOR_MIN_INACTIVE_RATIO of the history is
#: inactive. The inactive set holds the pauses and the syllable valleys; in a
#: dense talk (10 % pauses) the pauses are only the lowest tenth of it, so the
#: 5th percentile is needed to land inside them. Single-frame outliers are no
#: concern: the RMS of a 50 ms frame of stationary noise scatters by only
#: ~0.1 dB. Without enough inactive frames the 5th percentile of all frames is
#: used. Assumptions.
FLOOR_INACTIVE_PERCENTILE = 5.0
FLOOR_MIN_INACTIVE_RATIO = 0.05
FLOOR_FALLBACK_PERCENTILE = 5.0
#: De-esser engagement follows the sibilance ratio with hysteresis around the
#: reference: engage above reference + 1 dB, release below reference - 1 dB, keep
#: the state in between. Without it a voice near the reference would flip
#: between the tracking target (peak - overshoot) and the resting target
#: (peak + 2 dB) with every 20 s window. Assumption: 1 dB.
SIBILANCE_HYSTERESIS_DB = 1.0
#: Default de-esser band of the analyzer = shipped configuration (6.5 kHz, 1 oct).
DEFAULT_DEESSER_FREQ_HZ = 6500.0
DEFAULT_DEESSER_BANDWIDTH_OCT = 1.0

#: Dead zones (task specification): no change while |target - value| is inside.
EXPANDER_DEADZONE_DB = 2.0
DEESSER_DEADZONE_DB = 1.5
COMPRESSOR_DEADZONE_DB = 2.0
#: Minimum number of intervals between two changes of the same parameter.
MIN_CHANGE_SPACING_INTERVALS = 2

#: Plausibility of "the speaker is talking": the measured speech level
#: after trim must lie within this tolerance of the reference level (the
#: calibration put the speaker there), speech must fill a reasonable share of
#: the history and the frame-level dynamics must look like speech. Keyboard
#: clicks (3 per second, -30 dBFS) read as -40 dBFS "speech" with 15 % activity,
#: a colleague in the room or playback at -42 dBFS fails the level test.
PLAUSIBLE_LEVEL_TOLERANCE_DB = 6.0
#: Hysteresis of the level test: enter at +-6 dB, leave at +-7 dB. Assumption: 1 dB.
PLAUSIBLE_LEVEL_HYSTERESIS_DB = 1.0
PLAUSIBLE_MIN_SPEECH_RATIO = 0.3
PLAUSIBLE_MIN_DYNAMIC_RANGE_DB = 15.0
#: The expander's "12 dB below speech" cap protects any speech, also a speaker
#: at the wrong level: it needs speech activity, a speech-like share of frames
#: and only a modest dynamic range (a quiet speaker in a slightly louder room
#: measures 12-15 dB). Below 10 dB the signal is steady noise, not speech.
CAP_MIN_DYNAMIC_RANGE_DB = 10.0
#: Low signal-to-noise state (docs/00 section 6.4 b): below this input SNR
#: (speech RMS minus noise floor, after trim) the expander cannot lower pauses
#: further without cutting speech (floor + 10 dB would exceed speech - 12 dB
#: already at 22 dB; at 16 dB the pauses reach speech - 15 LU after the chain).
LOW_SNR_DB = 16.0
#: The low-SNR event is repeated at most this often (it carries advice, not a change).
LOW_SNR_LOG_INTERVAL_S = 30.0
LOW_SNR_HINT = "Störabstand zu gering: Raum verbessern oder neu kalibrieren"
#: After this long without plausible speech the de-esser and compressor
#: thresholds walk back to their calibrated values (reason ``rest``).
IMPLAUSIBLE_REST_S = 60.0

#: Protection: mean limiter gain reduction over an interval above this means the
#: chain is pushing too hard; the compressor threshold is then not raised and
#: the normalizer may not add more gain. Task specification: 3 dB.
LIMITER_PROTECT_GR_DB = 3.0
#: Hysteresis for leaving the protection state (assumption: half the trigger).
LIMITER_PROTECT_RELEASE_GR_DB = 1.5
#: Protection: maximum expander gain reduction while speech is active above this
#: means speech is being cut; the expander threshold is lowered immediately.
#: Task specification: 6 dB.
SPEECH_CUT_GR_DB = 6.0
#: The expander's interval maximum counts as "during speech" only when at least
#: this share of the interval's 50 ms frames were speech: a pause that ends
#: within the first 100 ms of an interval still leaves the gate closed (12-14 dB)
#: while 39 of 40 frames read active, so one inactive frame (97.5 %) must already
#: disqualify the interval. Assumption.
SPEECH_CUT_MIN_ACTIVE_RATIO = 0.98
#: speech_cut acts only when this many consecutive intervals show the condition:
#: a single interval can still be an onset artefact, two in a row are not.
SPEECH_CUT_CONSECUTIVE_INTERVALS = 2
#: Numerical tolerance for "no change" and time comparisons.
EPS = 1e-9
#: Parameters the controller tracks (stage, name) and their dead zones.
TRACKED = {
    ("expander", "threshold_db"): EXPANDER_DEADZONE_DB,
    ("deesser", "threshold_db"): DEESSER_DEADZONE_DB,
    ("compressor", "threshold_db"): COMPRESSOR_DEADZONE_DB,
}


# --------------------------------------------------------------------------- #
# LiveAnalyzer
# --------------------------------------------------------------------------- #

class LiveAnalyzer:
    """Rolling estimate of noise floor, speech level, sibilance, band peaks and activity."""

    def __init__(self, sample_rate: int, frame_ms: float = _m.ANALYSIS_FRAME_MS,
                 history_s: float = HISTORY_S,
                 deesser_freq_hz: float = DEFAULT_DEESSER_FREQ_HZ,
                 deesser_bandwidth_oct: float = DEFAULT_DEESSER_BANDWIDTH_OCT):
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
        self.recent_frames = int(math.ceil(RECENT_S / self.frame_s))
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
        self._levels = np.full(self.history_frames, _m.DB_FLOOR_DBFS)
        self._core = np.zeros(self.history_frames)
        self._sib = np.zeros(self.history_frames)
        self._band_peak = np.full(self.history_frames, _m.DB_FLOOR_DBFS)
        self._pos = 0
        self._count = 0
        self._lock = threading.Lock()
        self._band_sos = None
        self._band_zi = None
        self.band_hz = (0.0, 0.0)
        self.set_deesser_band(deesser_freq_hz, deesser_bandwidth_oct)

    def set_deesser_band(self, freq_hz: float, bandwidth_oct: float) -> None:
        """Measure the band peak where the de-esser listens (resets the band filter)."""
        sos = bandpass_sos(freq_hz, bandwidth_to_q(bandwidth_oct, freq_hz, self.sample_rate), self.sample_rate)
        with self._lock:
            self._band_sos = sos
            self._band_zi = np.zeros((sos.shape[0], 2))
            self.band_hz = (float(freq_hz), float(bandwidth_oct))

    def reset(self) -> None:
        with self._lock:
            self._fill = 0
            self._pos = 0
            self._count = 0
            self._band_zi[:] = 0.0

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
            band, self._band_zi = signal.sosfilt(self._band_sos, buf, zi=self._band_zi)
            band_peak = float(_m._amp_to_db(float(np.max(np.abs(band)))))
            self._levels[self._pos] = level
            self._core[self._pos] = core
            self._sib[self._pos] = sib
            self._band_peak[self._pos] = band_peak
            self._pos = (self._pos + 1) % self.history_frames
            self._count = min(self._count + 1, self.history_frames)

    def _history(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Copies of the valid history in chronological order."""
        with self._lock:
            count = self._count
            pos = self._pos
            arrays = (self._levels.copy(), self._core.copy(), self._sib.copy(), self._band_peak.copy())
        if count < self.history_frames:
            return tuple(a[:count] for a in arrays)
        order = np.concatenate((np.arange(pos, self.history_frames), np.arange(0, pos)))
        return tuple(a[order] for a in arrays)

    def snapshot(self) -> dict:
        """Current estimates; see the module docstring for the key meanings."""
        levels, core, sib, band_peak = self._history()
        count = levels.size
        out = {
            "ready": bool(count >= self.min_frames),
            "speech_detected": False,
            "noise_floor_dbfs": _m.DB_FLOOR_DBFS,
            "speech_level_dbfs": _m.DB_FLOOR_DBFS,
            "dynamic_range_db": 0.0,
            "sibilance_ratio_db": 0.0,
            "band_peak_p95_dbfs": _m.DB_FLOOR_DBFS,
            "band_hz": self.band_hz,
            "speech_active": False,
            "speech_ratio": 0.0,
            "recent_speech_ratio": 0.0,
            "frames": int(count),
            "history_s": float(count * self.frame_s),
        }
        if count == 0:
            return out
        thr, detected = _m.activity_threshold_db(levels)
        out["dynamic_range_db"] = _m.dynamic_range_db(levels)
        out["speech_detected"] = bool(detected)
        mask = levels > thr if detected else np.zeros(count, dtype=bool)
        # Noise floor from the pause frames: a global 10th percentile reads syllable
        # valleys instead of the floor when less than ~10 % of the history is pause
        # (dense talk). With enough inactive frames the floor is a low percentile of
        # those; otherwise the 5th percentile of everything is the best estimate.
        inactive = levels[~mask]
        if inactive.size >= FLOOR_MIN_INACTIVE_RATIO * count:
            out["noise_floor_dbfs"] = float(np.percentile(inactive, FLOOR_INACTIVE_PERCENTILE))
        else:
            out["noise_floor_dbfs"] = float(np.percentile(levels, FLOOR_FALLBACK_PERCENTILE))
        if np.any(mask):
            out["speech_level_dbfs"] = float(_m._power_to_db(np.mean(10.0 ** (levels[mask] / 10.0))))
            sel = mask
        else:
            out["speech_level_dbfs"] = float(np.percentile(levels, _m.SPEECH_TOP_PERCENTILE))
            sel = np.ones(count, dtype=bool)
        out["sibilance_ratio_db"] = float(_m._power_to_db(np.sum(sib[sel])) - _m._power_to_db(np.sum(core[sel])))
        out["band_peak_p95_dbfs"] = float(np.percentile(band_peak[sel], _m.BAND_PEAK_PERCENTILE))
        out["speech_active"] = bool(mask[-1])
        out["speech_ratio"] = float(np.mean(mask))
        out["recent_speech_ratio"] = float(np.mean(mask[-self.recent_frames:]))
        return out


# --------------------------------------------------------------------------- #
# AdaptiveController
# --------------------------------------------------------------------------- #

class AdaptiveController:
    """Slow, bounded, logged tracking of three thresholds plus protection rules."""

    def __init__(self, cfg: dict, chain, clock=time.monotonic, log_path=None,
                 analysis_is_post_trim: bool = False):
        adaptive = cfg["adaptive"]
        self.learn_s = float(adaptive["learn_s"])
        self.interval_s = float(adaptive["interval_s"])
        self.max_step_db = float(adaptive["max_step_db"])
        if self.learn_s < 0 or self.interval_s <= 0 or self.max_step_db <= 0:
            raise ValueError(
                f"adaptive settings invalid: learn_s={self.learn_s}, interval_s={self.interval_s}, "
                f"max_step_db={self.max_step_db}")
        self.bounds = parse_bounds(cfg)
        for stage, name in TRACKED:
            tracked_bounds(self.bounds, stage, name)
        self.chain = chain
        self.clock = clock
        self.analysis_is_post_trim = bool(analysis_is_post_trim)
        chain_cfg = cfg["chain"]
        self.deesser_ratio = float(chain_cfg["deesser"]["ratio"])
        self.normalizer_max_gain_db = float(chain_cfg["normalizer"]["max_gain_db"])
        #: The limiter protection moves normalizer.max_gain_db inside its bounds
        #: entry when the configuration has one, else inside [0, configured value].
        self.normalizer_gain_bounds = self.bounds.get(("normalizer", "max_gain_db"),
                                                      (0.0, self.normalizer_max_gain_db))
        if not self.normalizer_gain_bounds[0] <= self.normalizer_max_gain_db <= self.normalizer_gain_bounds[1]:
            raise ValueError(
                f"normalizer.max_gain_db={self.normalizer_max_gain_db} lies outside "
                f"adaptive.bounds {self.normalizer_gain_bounds}")
        #: Calibrated values the de-esser and compressor return to when no
        #: plausible speech has been heard for IMPLAUSIBLE_REST_S.
        self.rest_values = {key: float(chain.get_param(*key)) for key in TRACKED}
        self._frozen = False
        # The learning phase starts when the controller is created (the runtime
        # creates it right before opening the stream).
        self._t_start: float = float(clock())
        self._last_decision_t: float = self._t_start - self.interval_s
        self._last_plausible_t: float = self._t_start
        self._last_learning_log_t: float | None = None
        self._learn_done_logged = False
        self._last_change_t: dict[tuple[str, str], float] = {}
        self._limiter_protect = False
        self._speech_cut_streak = 0
        self._last_low_snr_log_t: float | None = None
        self._current_meters: dict = {}
        #: De-esser engagement state (None until the first decision: it is then
        #: read off the calibrated threshold, which lies below the band peak when
        #: the calibration engaged the de-esser and above it when it rests).
        self._deesser_engaged: bool | None = None
        self._level_plausible = False
        self._user_offsets: dict[tuple[str, str], float] = {}
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

    @property
    def learning(self) -> bool:
        """True while the learning phase (measured from start or the last relearn) runs."""
        return float(self.clock()) - self._t_start < self.learn_s - EPS

    @property
    def user_offsets(self) -> dict[str, float]:
        """Live offsets per tracked parameter as ``{"stage.name": dB}`` (see set_user_offsets)."""
        return {f"{stage}.{name}": value for (stage, name), value in self._user_offsets.items()}

    def set_user_offsets(self, offsets: dict[str, float]) -> None:
        """Set the live offsets the controller adds to its targets (and to the rest
        values): ``{"expander.threshold_db": +4.0, ...}``. Keys must name tracked
        parameters; a missing key means 0 dB. Bounds still apply after the offset,
        so an offset can never push a parameter outside ``adaptive.bounds``.
        """
        parsed: dict[tuple[str, str], float] = {}
        for key, value in offsets.items():
            if "." not in key:
                raise ValueError(f"offset key '{key}' must look like 'stage.param'")
            stage, name = key.split(".", 1)
            if (stage, name) not in TRACKED:
                raise ValueError(f"'{key}' is not a tracked parameter; tracked: "
                                 + ", ".join(f"{s}.{n}" for s, n in TRACKED))
            parsed[(stage, name)] = float(value)
        self._user_offsets = parsed
        self._log({"t": self._now(None), "event": "user_offsets", "offsets": self.user_offsets})

    def apply_rest_values(self, reason: str = "reset") -> list[dict]:
        """Put every tracked parameter back on its calibrated value plus the current
        live offset, immediately (not in steps), inside the bounds; returns the changes."""
        now = self._now(None)
        changes = []
        for key in TRACKED:
            lo, hi = self._bounds(*key)
            target = clamp(self.rest_values[key] + self._user_offsets.get(key, 0.0), lo, hi)
            old = float(self.chain.get_param(*key))
            if abs(target - old) <= EPS:
                continue
            self.chain.set_param(key[0], key[1], target)
            self._last_change_t[key] = now
            entry = {"t": now, "stage": key[0], "name": key[1], "old": old, "new": target, "reason": reason}
            self._log(entry)
            changes.append(entry)
        return changes

    def relearn(self, now: float | None = None) -> None:
        """Restart the learning phase (after a change of seat or room); offsets stay."""
        now = self._now(now)
        self._t_start = now
        self._last_decision_t = now - self.interval_s
        self._last_plausible_t = now
        self._last_learning_log_t = None
        self._learn_done_logged = False
        self._speech_cut_streak = 0
        self._deesser_engaged = None
        self._level_plausible = False
        self._drain()
        self._log({"t": now, "event": "relearn", "learn_s": self.learn_s})

    def log_event(self, entry: dict) -> None:
        """Write an informational JSONL entry (``event`` key) with the current time."""
        if "event" not in entry:
            raise ValueError("log_event needs an 'event' key")
        self._log({"t": self._now(None), **entry})

    def close(self) -> None:
        if self._log_fh is not None:
            self._log_fh.close()
            self._log_fh = None

    def update(self, meters: dict, now: float | None = None) -> list[dict]:
        """Evaluate meters (+ ``meters["analysis"]``) and apply at most one step per parameter.

        Returns the list of applied changes ``{"t", "stage", "name", "old", "new", "reason"}``.
        """
        now = self._now(now)
        elapsed = now - self._t_start
        if elapsed < self.learn_s - EPS:
            # Listening only: meters of the learning phase are not collected, so
            # nothing from it can trigger a rule after the phase ends.
            if (self._last_learning_log_t is None
                    or now - self._last_learning_log_t >= self.interval_s - EPS):
                self._last_learning_log_t = now
                self._log({"t": now, "event": "learning", "elapsed_s": round(elapsed, 3),
                           "remaining_s": round(self.learn_s - elapsed, 3)})
            return []
        if not self._learn_done_logged:
            self._learn_done_logged = True
            self._drain()
            self._log({"t": now, "event": "learn_done", "learn_s": self.learn_s})
        self._accumulate(meters)
        if now - self._last_decision_t < self.interval_s - EPS:
            return []
        self._last_decision_t = now
        stats = self._drain()
        self._current_meters = meters
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
        """Collect the stage accumulators handed over with this call.

        The stages accumulate mean/max gain reduction since the previous
        ``get_meters``; if ``update`` is called more than once per interval the
        values are combined (mean of means, max of maxima)."""
        lim = self._meter(meters, "limiter", "gain_reduction_mean_db")
        if lim is not None:
            self._acc_limiter.append(abs(lim))
        analysis = meters.get("analysis")
        recent = float(analysis.get("recent_speech_ratio", 0.0)) if isinstance(analysis, dict) else 0.0
        exp = self._meter(meters, "expander", "gain_reduction_max_db")
        if exp is not None and recent >= SPEECH_CUT_MIN_ACTIVE_RATIO:
            self._acc_expander_speech.append(abs(exp))
        norm = self._meter(meters, "normalizer", "gain_db")
        if norm is not None:
            self._acc_normalizer.append(norm)

    def _drain(self) -> dict:
        stats = {
            "limiter_gr_mean": float(np.mean(self._acc_limiter)) if self._acc_limiter else None,
            "expander_gr_speech_max": float(np.max(self._acc_expander_speech)) if self._acc_expander_speech else None,
            "normalizer_gain_last": self._acc_normalizer[-1] if self._acc_normalizer else None,
        }
        self._acc_limiter = []
        self._acc_expander_speech = []
        self._acc_normalizer = []
        return stats

    def _bounds(self, stage: str, name: str) -> tuple[float, float]:
        return tracked_bounds(self.bounds, stage, name)

    def _track(self, stage: str, name: str, target: float, deadzone: float,
               reason: str, block_increase: bool = False) -> dict | None:
        lo, hi = self._bounds(stage, name)
        # Live offsets (console) shift the setpoint; the controller never regulates
        # them away because they are part of the target itself.
        target = clamp(target + self._user_offsets.get((stage, name), 0.0), lo, hi)
        current = float(self.chain.get_param(stage, name))
        error = target - current
        if abs(error) <= deadzone:
            return None
        if block_increase and error > 0:
            return None
        step = math.copysign(min(self.max_step_db, abs(error)), error)
        return {"stage": stage, "name": name, "step": step, "reason": reason,
                "immediate": False, "target": target}

    def _plausibility(self, analysis: dict, speech_after_trim: float) -> list[str]:
        """Reasons why the analysed signal is not the speaker; empty = plausible.

        Each reason starts with a code (``no_speech``, ``level``, ``speech_ratio``,
        ``dynamic_range``) followed by the measured value.
        """
        reasons = []
        if not analysis.get("speech_detected", False):
            reasons.append("no_speech")
        # Level plausibility with hysteresis: enter the plausible state inside
        # +-PLAUSIBLE_LEVEL_TOLERANCE_DB, leave it only outside the tolerance plus
        # PLAUSIBLE_LEVEL_HYSTERESIS_DB, so a speaker sitting right at the edge
        # does not flip between tracking and resting every interval.
        deviation = abs(speech_after_trim - REFERENCE_SPEECH_LEVEL_DBFS)
        limit = (PLAUSIBLE_LEVEL_TOLERANCE_DB + PLAUSIBLE_LEVEL_HYSTERESIS_DB
                 if self._level_plausible else PLAUSIBLE_LEVEL_TOLERANCE_DB)
        self._level_plausible = deviation <= limit
        if not self._level_plausible:
            reasons.append(f"level {speech_after_trim:.1f} dBFS outside "
                           f"{REFERENCE_SPEECH_LEVEL_DBFS:.0f}±{PLAUSIBLE_LEVEL_TOLERANCE_DB:.0f}")
        if float(analysis.get("speech_ratio", 0.0)) < PLAUSIBLE_MIN_SPEECH_RATIO:
            reasons.append(f"speech_ratio {float(analysis.get('speech_ratio', 0.0)):.2f} < {PLAUSIBLE_MIN_SPEECH_RATIO}")
        if float(analysis.get("dynamic_range_db", 0.0)) < PLAUSIBLE_MIN_DYNAMIC_RANGE_DB:
            reasons.append(f"dynamic_range {float(analysis.get('dynamic_range_db', 0.0)):.1f} dB < "
                           f"{PLAUSIBLE_MIN_DYNAMIC_RANGE_DB:.0f}")
        return reasons

    @staticmethod
    def _speech_present(analysis: dict, reasons: list[str]) -> bool:
        """True when what is heard is speech of some kind (possibly at the wrong
        level): the expander's "12 dB below speech" cap then still protects it.
        Typing (low speech ratio) or steady noise (dynamics below
        CAP_MIN_DYNAMIC_RANGE_DB) is not speech; a quiet speaker whose dynamics
        fall below the 15 dB plausibility limit still is."""
        if any(r.startswith(("no_speech", "speech_ratio")) for r in reasons):
            return False
        return float(analysis.get("dynamic_range_db", 0.0)) >= CAP_MIN_DYNAMIC_RANGE_DB

    def _deesser_engagement(self, sibilance_ratio_db: float, band_peak_dbfs: float) -> bool:
        """Engagement state with hysteresis around SIBILANCE_REFERENCE_DB."""
        if self._deesser_engaged is None:
            current = float(self.chain.get_param("deesser", "threshold_db"))
            self._deesser_engaged = current < band_peak_dbfs
        if sibilance_ratio_db > SIBILANCE_REFERENCE_DB + SIBILANCE_HYSTERESIS_DB:
            self._deesser_engaged = True
        elif sibilance_ratio_db < SIBILANCE_REFERENCE_DB - SIBILANCE_HYSTERESIS_DB:
            self._deesser_engaged = False
        return self._deesser_engaged

    def _check_low_snr(self, now: float, meters: dict, floor: float, speech: float,
                       speech_present: bool) -> None:
        """Log ``low_snr`` (advice only, no parameter change) when the normalizer
        reports it or the own input SNR after trim is below LOW_SNR_DB."""
        meter_flag = self._meter(meters, "normalizer", "low_snr")
        meter_snr = self._meter(meters, "normalizer", "snr_lu")
        own_snr = speech - floor
        low = bool(meter_flag) or (speech_present and own_snr < LOW_SNR_DB)
        if not low:
            return
        if self._last_low_snr_log_t is not None and now - self._last_low_snr_log_t < LOW_SNR_LOG_INTERVAL_S - EPS:
            return
        self._last_low_snr_log_t = now
        self._log({"t": now, "event": "low_snr",
                   "snr_lu": round(meter_snr if meter_snr is not None else own_snr, 1),
                   "input_snr_db": round(own_snr, 1),
                   "source": "normalizer" if meter_flag else "analysis",
                   "hint": LOW_SNR_HINT})

    def _propose(self, now: float, analysis: dict, stats: dict) -> list[dict]:
        trim = 0.0 if self.analysis_is_post_trim else float(self.chain.get_param("input_trim", "gain_db"))
        floor = float(analysis["noise_floor_dbfs"]) + trim
        speech = float(analysis["speech_level_dbfs"]) + trim
        sib = float(analysis["sibilance_ratio_db"])
        band_peak = float(analysis["band_peak_p95_dbfs"]) + trim
        implausible = self._plausibility(analysis, speech)
        plausible = not implausible
        if plausible:
            self._last_plausible_t = now
        else:
            self._log({"t": now, "event": "implausible_speech", "reasons": implausible,
                       "since_s": round(now - self._last_plausible_t, 1)})
        speech_present = self._speech_present(analysis, implausible)
        self._check_low_snr(now, self._current_meters, floor, speech, speech_present)
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

        # Expander: speech-cut protection has priority over tracking. The
        # "12 dB below speech" cap applies only to plausible speech.
        exp_speech = stats["expander_gr_speech_max"]
        if exp_speech is not None and exp_speech > SPEECH_CUT_GR_DB:
            self._speech_cut_streak += 1
        else:
            self._speech_cut_streak = 0
        if self._speech_cut_streak >= SPEECH_CUT_CONSECUTIVE_INTERVALS:
            proposals.append({"stage": "expander", "name": "threshold_db", "step": -self.max_step_db,
                              "reason": "speech_cut", "immediate": True, "target": None})
        else:
            p = self._track("expander", "threshold_db",
                            expander_threshold_target(floor, speech if speech_present else None),
                            EXPANDER_DEADZONE_DB, "noise_floor_track")
            if p:
                proposals.append(p)

        if plausible:
            engaged = self._deesser_engagement(sib, band_peak)
            # Same control law as the calibration, evaluated with the hysteresis state.
            de_target, _ = deesser_threshold_target(
                band_peak, SIBILANCE_REFERENCE_DB + (1.0 if engaged else -1.0), self.deesser_ratio)
            p = self._track("deesser", "threshold_db", de_target, DEESSER_DEADZONE_DB,
                            "sibilance_track" if engaged else "sibilance_rest")
            if p:
                proposals.append(p)
            p = self._track("compressor", "threshold_db", compressor_threshold_target(speech),
                            COMPRESSOR_DEADZONE_DB, "speech_level_track",
                            block_increase=self._limiter_protect)
            if p:
                proposals.append(p)
        elif now - self._last_plausible_t >= IMPLAUSIBLE_REST_S - EPS:
            for key in (("deesser", "threshold_db"), ("compressor", "threshold_db")):
                p = self._track(key[0], key[1], self.rest_values[key], 0.0, "rest")
                if p:
                    proposals.append(p)
        return [p for p in proposals if p is not None]

    def _normalizer_hold(self, stats: dict, reason: str) -> dict | None:
        """Cap normalizer.max_gain_db at the current gain so it cannot add more level."""
        current_gain = stats["normalizer_gain_last"]
        if current_gain is None:
            return None
        hold = clamp(current_gain, self.normalizer_gain_bounds[0],
                     min(self.normalizer_gain_bounds[1], self.normalizer_max_gain_db))
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
                # Safety net independent of the target clamp in _track: a step
                # never leaves the bounds (speech_cut at the lower bound), and a
                # value that starts outside the bounds is walked back in steps
                # of max_step_db, never jumped, and may not move further out.
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
