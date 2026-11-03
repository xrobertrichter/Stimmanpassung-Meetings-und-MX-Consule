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

* absolute gate: ``L_m < gate_lufs`` -> pause, the gain is frozen;
* relative gate (BS.1770 style): ``L_m < L_speech - 10 LU`` -> frozen as well,
  where ``L_speech`` is the exponentially smoothed loudness (time constant
  ``window_s / 2``, i.e. the 3 s short-term window) of the intervals that passed
  both gates and are not falling - the loudness of the speech itself, not of
  the pauses or of the decays into them;
* otherwise the gain target is ``clip(target_lufs - L_speech, min_gain_db,
  max_gain_db)``; while ``L_m`` is falling faster than ``FALLING_LU_PER_S``
  (a decaying word or sentence end) the target may not lie above the current
  gain, so the gain never climbs on a dying envelope;
* the applied gain ramps towards the target by at most ``rate_db_per_s``
  upwards and ``release_rate_db_per_s`` downwards, evaluated per sample in
  closed form.

The normaliser is feed-forward (it measures its own input), so it cannot
oscillate; the rate limits are the only dynamic elements. Gating on the fast
momentary detector (instead of on the smoothed loudness) is what keeps the gain
from creeping up during pauses longer than the smoothing window (review PO-1).
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


class Normalizer(StageBase):
    PARAM_RANGES = {
        "target_lufs": (-40.0, -6.0),
        "window_s": (0.4, 10.0),        # 0.4 s = momentary, 3 s = short-term, 10 s = very slow
        "max_gain_db": (0.0, 30.0),
        "min_gain_db": (-30.0, 0.0),
        "rate_db_per_s": (0.1, 20.0),           # upward rate
        "release_rate_db_per_s": (0.1, 30.0),   # downward rate (a too-loud start is corrected faster)
        "gate_lufs": (-80.0, -20.0),
    }
    BOOL_PARAMS = ("enabled",)
    # 3 dB/s downwards: a sentence that starts 6 dB too loud is tamed within two seconds,
    # still far from audible pumping; upwards stays at the configured 1 dB/s.
    PARAM_DEFAULTS = {"release_rate_db_per_s": 3.0}

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

    def _grid_update(self, momentary_db: float, gain_db: float) -> None:
        """Gate decision and new gain target at one grid point (``gain_db`` = gain right now)."""
        gated = momentary_db < self.gate_lufs
        if not gated and self._speech_count > 0:
            gated = momentary_db < self.speech_loudness() - RELATIVE_GATE_LU
        falling = momentary_db < self._prev_momentary - FALLING_LU_PER_S * self._interval_s
        self._prev_momentary = momentary_db
        if falling and self._speech_count > 0 and momentary_db >= self.speech_loudness():
            falling = False   # still above the speech loudness: a syllable, not a decay into a pause
        self._gated = gated
        if gated:
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
        target = float(np.clip(self.target_lufs - self.speech_loudness(), self.min_gain_db, self.max_gain_db))
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
            "gated": float(self._gated),
        }

    def reset(self) -> None:
        self._kfilter.reset()
        self._momentary.reset()
        self._pos = 0
        self._gain_db = 0.0
        self._target_db = 0.0
        self._last_momentary = DB_FLOOR_DBFS
        self._prev_momentary = DB_FLOOR_DBFS
        self._speech_power = 0.0
        self._speech_count = 0
        self._gated = True
