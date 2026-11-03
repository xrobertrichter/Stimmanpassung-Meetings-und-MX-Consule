"""Stage 5b: slow loudness normaliser (AGC) on K-weighted loudness, gated like BS.1770.

Measurement (per sample, vectorised): the input is K-weighted (BS.1770 pre-filter
+ RLB, designed locally in ``biquad.k_weighting_sos``), squared and averaged by a
one-pole whose time constant is half the *momentary* window (0.4 s): an
exponential window of tau is variance-equivalent to a rectangular window of
2*tau. The average is bias-corrected with the number of samples seen since
``reset`` so it is unbiased from the first sample on. Loudness is
``-0.691 + 10*log10(mean square)`` LUFS (BS.1770, one channel).

Control (every ``UPDATE_INTERVAL_MS`` on an absolute sample grid, hence
independent of the block size), using the momentary loudness ``L_m`` at the end
of the previous interval:

* noise-floor estimate ``L_floor``: minimum of ``L_m`` over the last
  ``FLOOR_WINDOW_S``, allowed to rise by at most ``FLOOR_RISE_LU_PER_S`` (unless
  the estimate has not been confirmed by any value for a whole window, then the
  window minimum is accepted) and never closer than ``FLOOR_BELOW_SPEECH_LU`` to
  the speech loudness. It follows a room that gets quieter at once and a room
  that gets louder within seconds, and it is kept across a non-finite-input
  recovery (``recover``);
* effective gate ``G = max(gate_lufs, L_floor + gate_margin_lu)`` once speech has
  been learnt (before that only ``gate_lufs``, so a signal without any dips can
  still start the regulation);
* absolute gate: ``L_m < G`` -> pause, the gain is frozen;
* relative gate (BS.1770 style): ``L_m < L_speech - 10 LU`` -> frozen as well,
  where ``L_speech`` is the exponentially smoothed loudness (time constant
  ``window_s / 2``, i.e. the 3 s short-term window) of the intervals that passed
  both gates and are not falling. While ``L_m`` lies more than 10 LU under the
  reference but above the *measured* floor plus margin, ``L_speech`` decays towards
  ``L_m`` by ``REFERENCE_DECAY_LU_PER_S`` (never below measured gate + 10 LU), so a
  speaker who becomes much quieter is re-referenced instead of being frozen out
  (the gate itself uses the floor bounded to ``L_speech - 15 LU``; the measured
  floor is what the meter ``noise_floor_lufs`` reports);
* otherwise the gain target is ``clip(target_lufs - L_speech, min_gain_db,
  min(max_gain_db, target_lufs - PAUSE_HEADROOM_LU - L_floor))``: the gain may
  never lift the noise floor closer than 15 LU to the target; while ``L_m`` is
  falling faster than ``FALLING_LU_PER_S`` below the speech loudness (a decaying
  word or sentence end) the target may not lie above the current gain;
* the applied gain ramps towards the target by at most ``rate_db_per_s``
  upwards and ``release_rate_db_per_s`` downwards, evaluated per sample in
  closed form.

The normaliser is feed-forward (it measures its own input), so it cannot
oscillate; the rate limits are the only dynamic elements. Gating on the fast
momentary detector instead of on the smoothed loudness keeps the gain from
creeping up during pauses longer than the smoothing window; the internal floor
estimate keeps the protection independent of the calibrated ``gate_lufs`` when
the room gets louder during operation.
"""

from __future__ import annotations

import math

import numpy as np

from voicechain.dsp import DB_FLOOR_DBFS, StageBase
from voicechain.dsp.biquad import SosFilter, k_weighting_sos
from voicechain.dsp.envelope import OnePole, db_to_amp, power_to_db

# BS.1770-4: loudness = -0.691 + 10*log10(sum of weighted mean squares)
LOUDNESS_OFFSET_DB = -0.691
# BS.1770-4 momentary loudness window (400 ms): fast enough to see the end of a word.
MOMENTARY_WINDOW_S = 0.4
# BS.1770-4 relative gate: 10 LU below the loudness of the programme (here: the speech).
RELATIVE_GATE_LU = 10.0
# A speech envelope decays with > 50 LU/s at the end of a word; stationary ripple and
# slow drifts (a speaker leaning back, < 10 LU/s) must still be regulated upwards.
FALLING_LU_PER_S = 10.0
# Re-evaluating the gain target every 10 ms is plenty for a controller limited to a
# few dB/s (0.3 dB per interval even at the maximum release rate of 30 dB/s); the
# applied gain ramps per sample anyway.
UPDATE_INTERVAL_MS = 10.0
# Noise-floor estimate: sliding minimum over 10 s. Spoken sentences are 2-5 s long, so
# the window always contains a pause or at least an inter-word gap in which the
# momentary loudness drops to the room; a shorter window would read syllable dips of
# a long sentence as "floor", a longer one would follow a room that gets quieter only
# after that time.
FLOOR_WINDOW_S = 10.0
# The estimate follows a louder room at 1 LU/s (a fan that switches on is tracked in
# ~10 s) but cannot run up to the speech within a sentence.
FLOOR_RISE_LU_PER_S = 1.0
# A "floor" less than 15 LU below the speech is not a floor the chain can gate; the
# same 15 LU are the pause headroom the gain cap guarantees below the target
# (Beauftragung 6.4: pause loudness <= speech loudness - 15 LU).
FLOOR_BELOW_SPEECH_LU = 15.0
PAUSE_HEADROOM_LU = 15.0
# While only the relative gate blocks, the speech reference decays at 2 LU/s: a speaker
# who became 20 LU quieter is re-referenced within 10 s; five times slower than the
# fastest decay that still counts as a syllable end (FALLING_LU_PER_S), and in pauses
# the absolute (floor-based) gate stops the decay altogether.
REFERENCE_DECAY_LU_PER_S = 2.0


class Normalizer(StageBase):
    PARAM_RANGES = {
        "target_lufs": (-40.0, -6.0),
        "window_s": (0.4, 10.0),        # 0.4 s = momentary, 3 s = short-term, 10 s = very slow
        "max_gain_db": (0.0, 30.0),
        "min_gain_db": (-30.0, 0.0),
        "rate_db_per_s": (0.1, 20.0),           # upward rate
        "release_rate_db_per_s": (0.1, 30.0),   # downward rate (a too-loud start is corrected faster)
        "gate_lufs": (-80.0, -20.0),
        "gate_margin_lu": (0.0, 30.0),          # effective gate = max(gate_lufs, floor + margin)
    }
    BOOL_PARAMS = ("enabled",)
    # 3 dB/s downwards: a sentence that starts 6 dB too loud is tamed within two seconds,
    # still far from audible pumping; upwards stays at the configured 1 dB/s.
    # 8 LU above the estimated floor: the fluctuation of a stationary room floor measured
    # with a 400 ms window stays within +-3 LU, so 8 LU gate the pause reliably while
    # soft speech 15 LU above the floor still passes.
    PARAM_DEFAULTS = {"release_rate_db_per_s": 3.0, "gate_margin_lu": 8.0}

    def __init__(self, sample_rate: int, **params):
        super().__init__(sample_rate)
        self._apply_params(params)
        self._kfilter = SosFilter(k_weighting_sos(self.sample_rate))
        self._momentary = OnePole(self.sample_rate, 0.5 * MOMENTARY_WINDOW_S * 1e3)
        self._momentary_tau = 0.5 * MOMENTARY_WINDOW_S * self.sample_rate
        self._update_samples = max(1, int(round(UPDATE_INTERVAL_MS * 1e-3 * self.sample_rate)))
        self._interval_s = self._update_samples / self.sample_rate
        self._up_step = 0.0
        self._down_step = 0.0
        self._speech_coef = 0.0
        self._floor_ring = np.full(max(1, int(round(FLOOR_WINDOW_S / self._interval_s))), np.inf)
        self._floor_index = 0
        self._floor_raw_db = DB_FLOOR_DBFS       # measured: sliding minimum with slow rise
        self._floor_db = DB_FLOOR_DBFS           # used for gate and cap: never closer than 15 LU to the speech
        self._floor_known = False
        self._floor_stale = 0
        self._effective_gate = self.gate_lufs
        self._pos = 0                    # absolute sample index since reset (bias correction + grid)
        self._gain_db = 0.0
        self._target_db = 0.0
        self._last_momentary = DB_FLOOR_DBFS
        self._prev_momentary = DB_FLOOR_DBFS
        self._speech_power = 0.0
        self._speech_count = 0
        self._gated = True
        self._install_current()

    def _design_from(self, params: dict) -> dict:
        if params["min_gain_db"] > params["max_gain_db"]:
            raise ValueError(
                f"Normalizer: min_gain_db={params['min_gain_db']} must not exceed "
                f"max_gain_db={params['max_gain_db']}"
            )
        return {
            "up_step": params["rate_db_per_s"] / self.sample_rate,
            "down_step": params["release_rate_db_per_s"] / self.sample_rate,
            "speech_coef": math.exp(-self._interval_s / (0.5 * params["window_s"])),
        }

    def _install(self, design: dict) -> None:
        self._up_step = design["up_step"]
        self._down_step = design["down_step"]
        self._speech_coef = design["speech_coef"]

    # -- measurement -------------------------------------------------------------------
    def momentary_curve(self, x: np.ndarray) -> np.ndarray:
        """Per-sample momentary loudness in LUFS; advances the meter state and position."""
        weighted = self._kfilter.process(x)
        averaged = self._momentary.process(np.square(weighted))
        idx = np.arange(self._pos + 1, self._pos + x.size + 1, dtype=np.float64)
        bias = -np.expm1(-idx / self._momentary_tau)       # 1 - a**(n+1), never 0 for n >= 0
        self._pos += x.size
        return LOUDNESS_OFFSET_DB + power_to_db(averaged / bias)

    def speech_loudness(self) -> float:
        """Smoothed loudness of the un-gated intervals (DB_FLOOR_DBFS before any speech)."""
        if self._speech_count == 0:
            return DB_FLOOR_DBFS
        bias = -math.expm1(self._speech_count * math.log(self._speech_coef))
        return LOUDNESS_OFFSET_DB + float(power_to_db(self._speech_power / bias))

    def _set_speech_loudness(self, loudness_db: float) -> None:
        """Overwrite the reference (used for the slow decay) without touching the count."""
        bias = -math.expm1(self._speech_count * math.log(self._speech_coef))
        self._speech_power = 10.0 ** ((loudness_db - LOUDNESS_OFFSET_DB) / 10.0) * bias

    def noise_floor(self) -> float:
        """Measured floor estimate (sliding minimum of the momentary loudness, slow rise)."""
        return self._floor_raw_db if self._floor_known else DB_FLOOR_DBFS

    def _update_floor(self, momentary_db: float) -> None:
        if momentary_db <= DB_FLOOR_DBFS:
            return                                   # no signal seen yet (start-up sentinel)
        self._floor_ring[self._floor_index] = momentary_db
        self._floor_index = (self._floor_index + 1) % self._floor_ring.size
        window_min = float(np.min(self._floor_ring))
        rise = FLOOR_RISE_LU_PER_S * self._interval_s
        if not self._floor_known:
            floor = window_min
            self._floor_known = True
            self._floor_stale = 0
        elif window_min <= self._floor_raw_db + rise:
            floor = window_min                       # follows a quieter room at once
            self._floor_stale = 0
        else:
            # everything in the window is louder than the estimate: rise slowly, and once
            # the estimate has been unconfirmed for a whole window it is stale (the pause
            # it came from left the window) and the window minimum is accepted outright
            self._floor_stale += 1
            floor = window_min if self._floor_stale >= self._floor_ring.size else self._floor_raw_db + rise
        self._floor_raw_db = floor
        if self._speech_count > 0:
            floor = min(floor, self.speech_loudness() - FLOOR_BELOW_SPEECH_LU)
        self._floor_db = floor

    def _grid_update(self, momentary_db: float, gain_db: float) -> None:
        """Gate decision and new gain target at one grid point (``gain_db`` = gain right now)."""
        self._update_floor(momentary_db)
        gate = self.gate_lufs
        measured_gate = self.gate_lufs
        if self._speech_count > 0:
            gate = max(gate, self._floor_db + self.gate_margin_lu)
            measured_gate = max(measured_gate, self._floor_raw_db + self.gate_margin_lu)
        self._effective_gate = gate
        absolute = momentary_db < gate
        below_reference = self._speech_count > 0 and momentary_db < self.speech_loudness() - RELATIVE_GATE_LU
        falling = momentary_db < self._prev_momentary - FALLING_LU_PER_S * self._interval_s
        self._prev_momentary = momentary_db
        if falling and self._speech_count > 0 and momentary_db >= self.speech_loudness():
            falling = False   # still above the speech loudness: a syllable, not a decay into a pause
        self._gated = absolute or below_reference
        if below_reference and momentary_db >= measured_gate:
            # Clearly above the measured room floor, yet more than 10 LU under the reference:
            # a speaker who became quieter. Let the reference sink towards the signal (never
            # below measured gate + 10 LU, so the relative gate stays above the absolute one).
            decayed = max(momentary_db, self.speech_loudness() - REFERENCE_DECAY_LU_PER_S * self._interval_s,
                          measured_gate + RELATIVE_GATE_LU)
            self._set_speech_loudness(decayed)
        if self._gated:
            self._target_db = gain_db                # pause: freeze
            return
        if not falling:
            # The speech loudness is learnt from rising/steady intervals only: the decay
            # phases into pauses would drag it down and weaken the relative gate.
            power = 10.0 ** ((momentary_db - LOUDNESS_OFFSET_DB) / 10.0)
            self._speech_power = self._speech_coef * self._speech_power + (1.0 - self._speech_coef) * power
            self._speech_count += 1
        if self._speech_count == 0:
            self._target_db = gain_db                # nothing learnt yet (first interval is falling)
            return
        gain_cap = min(self.max_gain_db, self.target_lufs - PAUSE_HEADROOM_LU - self._floor_db)
        gain_cap = max(gain_cap, self.min_gain_db)
        target = float(np.clip(self.target_lufs - self.speech_loudness(), self.min_gain_db, gain_cap))
        if falling:
            target = min(target, gain_db)             # never climb on a decaying envelope
        self._target_db = target

    # -- processing --------------------------------------------------------------------
    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        n = x.size
        if n == 0:
            return x
        start = self._pos                     # absolute index of x[0]
        momentary = self.momentary_curve(x)
        gains = np.empty(n, dtype=np.float64)
        grid = self._update_samples
        # bypass: measuring continues (gate state, speech loudness), the gain stays where it is
        up_step = self._up_step if self.enabled else 0.0
        down_step = self._down_step if self.enabled else 0.0
        i = 0
        gain = self._gain_db
        while i < n:
            abs_i = start + i
            if abs_i % grid == 0:
                reference = momentary[i - 1] if i > 0 else self._last_momentary
                self._grid_update(float(reference), gain)
            seg_end = min(n, (abs_i // grid + 1) * grid - start)
            steps = np.arange(1, seg_end - i + 1, dtype=np.float64)
            np.clip(self._target_db, gain - down_step * steps, gain + up_step * steps, out=gains[i:seg_end])
            gain = float(gains[seg_end - 1])
            i = seg_end
        self._last_momentary = float(momentary[-1])
        self._gain_db = gain
        if not self.enabled:
            return x
        return x * db_to_amp(gains)

    def meters(self) -> dict:
        return {
            "enabled": float(self.enabled),
            "gain_db": self._gain_db,
            "loudness_lufs": self.speech_loudness(),
            "momentary_lufs": self._last_momentary,
            "noise_floor_lufs": self.noise_floor(),
            "effective_gate_lufs": float(self._effective_gate),
            "gated": float(self._gated),
        }

    def _clear_signal_path(self) -> None:
        self._kfilter.reset()
        self._momentary.reset()
        self._pos = 0
        self._last_momentary = DB_FLOOR_DBFS
        self._prev_momentary = DB_FLOOR_DBFS
        self._gated = True

    def recover(self) -> None:
        """After a non-finite block: clear filters and detector, keep gain, reference and floor."""
        self._clear_signal_path()
        self._target_db = self._gain_db

    def reset(self) -> None:
        self._clear_signal_path()
        self._gain_db = 0.0
        self._target_db = 0.0
        self._speech_power = 0.0
        self._speech_count = 0
        self._floor_ring[...] = np.inf
        self._floor_index = 0
        self._floor_raw_db = DB_FLOOR_DBFS
        self._floor_db = DB_FLOOR_DBFS
        self._floor_known = False
        self._floor_stale = 0
        self._effective_gate = self.gate_lufs
