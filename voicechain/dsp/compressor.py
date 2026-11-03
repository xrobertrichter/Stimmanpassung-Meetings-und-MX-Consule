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

from voicechain.dsp import DB_FLOOR_DBFS, StageBase
from voicechain.dsp.envelope import (
    PARAM_RAMP_MS, AttackReleaseSmoother, GainRamp, ReductionAccumulator, amp_to_db, db_to_amp,
)


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
        # 1.1 is the smallest ratio that still compresses measurably; exactly 1.0
        # ("off") would make the calibration's threshold derivation divide by zero.
        "ratio": (1.1, 20.0),
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
        self._reduction = ReductionAccumulator()
        self._last_level = DB_FLOOR_DBFS
        self._install_current()

    def _design_from(self, params: dict) -> dict:
        return {"attack_ms": params["attack_ms"], "release_ms": params["release_ms"],
                "makeup_db": params["makeup_db"]}

    def _install(self, design: dict) -> None:
        self._smoother.set_times(design["attack_ms"], design["release_ms"])
        self._makeup.set_gain_db(design["makeup_db"])

    def reduction_curve(self, x: np.ndarray) -> np.ndarray:
        """Smoothed gain reduction in dB (>= 0) for a block, without applying it."""
        level = amp_to_db(x)
        self._last_level = float(np.max(level))
        target = compressor_reduction_db(level, self.threshold_db, self.ratio, self.knee_db)
        return self._smoother.process(target)

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        if not self.enabled or x.size == 0:
            self._reduction.add_zeros(x.size)
            return x
        reduction = self.reduction_curve(x)
        self._reduction.add(reduction)
        return x * (db_to_amp(-reduction) * self._makeup.process(x.size))

    def meters(self) -> dict:
        mean, peak = self._reduction.read()
        return {
            "enabled": float(self.enabled),
            "gain_reduction_db": self._reduction.last_mean,
            "gain_reduction_mean_db": mean,
            "gain_reduction_max_db": peak,
            "level_dbfs": self._last_level,
        }

    def reset(self) -> None:
        self._smoother.reset()
        self._makeup.snap()
        self._reduction.reset()
        self._last_level = DB_FLOOR_DBFS
