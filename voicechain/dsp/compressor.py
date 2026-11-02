"""Stage 5: feed-forward compressor with soft knee, log-domain gain smoothing.

Design after Giannoulis, Massberg & Reiss (JAES 2012): the instantaneous sample
level ``20*log10|x|`` goes through the static gain computer, the resulting gain
reduction (in dB, >= 0) is smoothed by the decoupled peak detector of
``envelope.AttackReleaseSmoother`` (release decay, then one-pole attack) and
applied together with the static make-up gain.

With a peak (sample) detector a sine of peak level P dBFS above the threshold T
is reduced by ``(P - T) * (1 - 1/ratio)`` dB, which is the textbook static
characteristic the tests check.
"""

from __future__ import annotations

import numpy as np

from voicechain.dsp import StageBase
from voicechain.dsp.envelope import PARAM_RAMP_MS, AttackReleaseSmoother, GainRamp, amp_to_db, db_to_amp


def compressor_reduction_db(level_db: np.ndarray, threshold_db: float, ratio: float,
                            knee_db: float) -> np.ndarray:
    """Static gain reduction (>= 0 dB) of a compressor with quadratic soft knee."""
    over = level_db - threshold_db
    slope = 1.0 - 1.0 / ratio
    reduction = np.zeros_like(over)
    if knee_db > 0.0:
        in_knee = np.abs(2.0 * over) <= knee_db
        reduction[in_knee] = slope * np.square(over[in_knee] + knee_db / 2.0) / (2.0 * knee_db)
        above = 2.0 * over > knee_db
    else:
        above = over > 0.0
    reduction[above] = slope * over[above]
    return reduction


class Compressor(StageBase):
    PARAM_RANGES = {
        "threshold_db": (-60.0, 0.0),
        "ratio": (1.0, 20.0),          # 1 = off, 20 = practically limiting
        "knee_db": (0.0, 24.0),
        "attack_ms": (0.1, 500.0),
        "release_ms": (1.0, 5000.0),
        "makeup_db": (-12.0, 24.0),
    }
    BOOL_PARAMS = ("enabled",)

    def __init__(self, sample_rate: int, **params):
        super().__init__(sample_rate)
        self._apply_params(params)
        self._smoother = AttackReleaseSmoother(self.sample_rate, self.attack_ms, self.release_ms)
        self._makeup = GainRamp(PARAM_RAMP_MS * 1e-3 * self.sample_rate, self.makeup_db)
        self._last_reduction_mean = 0.0
        self._last_reduction_max = 0.0
        self._last_level = -150.0
        self._update_coefficients()

    def _update_coefficients(self) -> None:
        self._smoother.set_times(self.attack_ms, self.release_ms)
        self._makeup.set_gain_db(self.makeup_db)

    def reduction_curve(self, x: np.ndarray) -> np.ndarray:
        """Smoothed gain reduction in dB (>= 0) for a block, without applying it."""
        level = amp_to_db(x)
        self._last_level = float(np.max(level))
        target = compressor_reduction_db(level, self.threshold_db, self.ratio, self.knee_db)
        return self._smoother.process(target)

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        if not self.enabled or x.size == 0:
            self._last_reduction_mean = 0.0
            self._last_reduction_max = 0.0
            return x
        reduction = self.reduction_curve(x)
        self._last_reduction_mean = float(np.mean(reduction))
        self._last_reduction_max = float(np.max(reduction))
        return x * (db_to_amp(-reduction) * self._makeup.process(x.size))

    def meters(self) -> dict:
        return {
            "enabled": float(self.enabled),
            "gain_reduction_db": self._last_reduction_mean,
            "gain_reduction_max_db": self._last_reduction_max,
            "level_dbfs": self._last_level,
        }

    def reset(self) -> None:
        self._smoother.reset()
        self._makeup.snap()
        self._last_reduction_mean = 0.0
        self._last_reduction_max = 0.0
        self._last_level = -150.0
