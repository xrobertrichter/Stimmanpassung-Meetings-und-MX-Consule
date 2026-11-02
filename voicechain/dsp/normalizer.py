"""Stage 5b: slow loudness normaliser (AGC) on K-weighted short-term loudness.

Measurement: the input is K-weighted (BS.1770 pre-filter + RLB, designed locally
in ``biquad.k_weighting_sos``), squared and averaged by a one-pole whose time
constant is ``window_s / 2`` (an exponential window of tau is variance-equivalent
to a rectangular window of 2*tau, i.e. the 3 s "short-term" window of BS.1770).
The average is bias-corrected with the number of samples seen since ``reset`` so
the estimate is unbiased from the first sample on. Loudness is
``-0.691 + 10*log10(mean square)`` LUFS as in BS.1770 for one channel.

Control: every ``UPDATE_INTERVAL_MS`` (on an absolute sample grid, so the result
does not depend on the block size) the gain *target* is recomputed as
``clip(target_lufs - L, min_gain_db, max_gain_db)`` when L is above the gate,
otherwise the current gain is held. The applied gain ramps towards the target by
at most ``rate_db_per_s``; the ramp is evaluated per sample in closed form.
The normaliser is feed-forward (it measures its own input), so it cannot
oscillate; the rate limit is the only dynamic element.
"""

from __future__ import annotations

import numpy as np

from voicechain.dsp import StageBase
from voicechain.dsp.biquad import SosFilter, k_weighting_sos
from voicechain.dsp.envelope import DB_FLOOR, OnePole, db_to_amp, power_to_db

# BS.1770-4: loudness = -0.691 + 10*log10(sum of weighted mean squares)
LOUDNESS_OFFSET_DB = -0.691
# Re-evaluating the gain target every 10 ms is plenty for a controller limited to a
# few dB/s (0.2 dB per interval even at the maximum rate of 20 dB/s); the applied
# gain ramps per sample anyway.
UPDATE_INTERVAL_MS = 10.0


class Normalizer(StageBase):
    PARAM_RANGES = {
        "target_lufs": (-40.0, -6.0),
        "window_s": (0.4, 10.0),        # 0.4 s = momentary, 3 s = short-term, 10 s = very slow
        "max_gain_db": (0.0, 30.0),
        "min_gain_db": (-30.0, 0.0),
        "rate_db_per_s": (0.1, 20.0),
        "gate_lufs": (-80.0, -20.0),
    }
    BOOL_PARAMS = ("enabled",)

    def __init__(self, sample_rate: int, **params):
        super().__init__(sample_rate)
        self._apply_params(params)
        self._kfilter = SosFilter(k_weighting_sos(self.sample_rate))
        self._power = OnePole(self.sample_rate, 0.5 * self.window_s * 1e3)
        self._update_samples = max(1, int(round(UPDATE_INTERVAL_MS * 1e-3 * self.sample_rate)))
        self._pos = 0                    # absolute sample index since reset (bias correction + grid)
        self._gain_db = 0.0
        self._target_db = 0.0
        self._last_loudness = DB_FLOOR
        self._gated = True
        self._step_db = 0.0
        self._tau_samples = 1.0
        self._update_coefficients()

    def _update_coefficients(self) -> None:
        if self.min_gain_db > self.max_gain_db:
            raise ValueError(
                f"Normalizer: min_gain_db={self.min_gain_db} must not exceed max_gain_db={self.max_gain_db}"
            )
        self._power.set_time(0.5 * self.window_s * 1e3)
        self._tau_samples = 0.5 * self.window_s * self.sample_rate
        self._step_db = self.rate_db_per_s / self.sample_rate

    def _new_target(self, loudness_db: float) -> float:
        if loudness_db < self.gate_lufs:
            self._gated = True
            return self._gain_db          # hold during pauses / silence
        self._gated = False
        return float(np.clip(self.target_lufs - loudness_db, self.min_gain_db, self.max_gain_db))

    def loudness_curve(self, x: np.ndarray) -> np.ndarray:
        """Per-sample short-term loudness estimate in LUFS; advances the meter state and position."""
        weighted = self._kfilter.process(x)
        averaged = self._power.process(np.square(weighted))
        idx = np.arange(self._pos + 1, self._pos + x.size + 1, dtype=np.float64)
        bias = -np.expm1(-idx / self._tau_samples)       # 1 - a**(n+1), never 0 for n >= 0
        self._pos += x.size
        return LOUDNESS_OFFSET_DB + power_to_db(averaged / bias)

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        n = x.size
        if n == 0:
            return x
        if not self.enabled:
            # keep measuring so that re-enabling starts from a valid loudness estimate
            loudness = self.loudness_curve(x)
            self._last_loudness = float(loudness[-1])
            return x
        start = self._pos                     # absolute index of x[0]
        loudness = self.loudness_curve(x)
        gains = np.empty(n, dtype=np.float64)
        grid = self._update_samples
        i = 0
        gain = self._gain_db
        while i < n:
            abs_i = start + i
            if abs_i % grid == 0:
                reference = loudness[i - 1] if i > 0 else self._last_loudness
                self._target_db = self._new_target(float(reference))
            seg_end = min(n, (abs_i // grid + 1) * grid - start)
            steps = self._step_db * np.arange(1, seg_end - i + 1, dtype=np.float64)
            np.clip(self._target_db, gain - steps, gain + steps, out=gains[i:seg_end])
            gain = float(gains[seg_end - 1])
            i = seg_end
        self._gain_db = gain
        self._last_loudness = float(loudness[-1])
        return x * db_to_amp(gains)

    def meters(self) -> dict:
        return {
            "enabled": float(self.enabled),
            "gain_db": self._gain_db,
            "loudness_lufs": self._last_loudness,
            "gated": float(self._gated),
        }

    def reset(self) -> None:
        self._kfilter.reset()
        self._power.reset()
        self._pos = 0
        self._gain_db = 0.0
        self._target_db = 0.0
        self._last_loudness = DB_FLOOR
        self._gated = True
