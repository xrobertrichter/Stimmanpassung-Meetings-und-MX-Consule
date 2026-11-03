"""Stage 2: downward expander (soft gate) that lowers room noise in speech pauses.

Signal flow (all vectorised, see ``envelope.py``)::

    x -> RMS detector (box-car mean of x^2 over detector_ms) -> level L [dBFS]
      -> static curve g_s(L) in [-range_db, 0] (soft knee)
      -> hold: target forced to 0 dB while the gate was fully open (g_s == 0)
         within the last hold_ms (running maximum of the open flag)
      -> ReleaseDecay(release_ms)  (gain may only *fall* at the release rate)
      -> OnePole(attack_ms)        (gain *rises* with the attack time)
      -> y = x * 10**(g/20)

Conventions: ``attack`` is the time the gate needs to open (gain towards 0 dB),
``release`` the time constant (63 %) with which it closes, as usual for
gates/expanders. The short-time RMS detector (rather than a peak detector) makes
the threshold comparable with the noise floor measured as frame RMS by the
calibration; its rectangular window forgets a loud signal completely within
``detector_ms`` so that closing is not delayed by a slowly decaying average.
The hold is triggered only by a fully open gate (classic gate timer), not by
the small fluctuations of the static gain in stationary noise: a running
maximum of the gain itself would bias the attenuation in noise by about 1 dB
towards "open".
"""

from __future__ import annotations

import numpy as np

from voicechain.dsp import DB_FLOOR_DBFS, StageBase
from voicechain.dsp.envelope import (
    AttackReleaseSmoother, HoldMax, MovingAverage, ReductionAccumulator, db_to_amp, power_to_db,
)


def expander_gain_db(level_db: np.ndarray, threshold_db: float, ratio: float,
                     knee_db: float, range_db: float) -> np.ndarray:
    """Static downward-expander gain (<= 0 dB) with a quadratic soft knee.

    Giannoulis et al. (2012), eq. for the expander: below the knee the output
    level is ``T + (L - T) * ratio``; the gain is clamped at ``-range_db``.
    """
    over = level_db - threshold_db
    gain = np.zeros_like(over)
    if knee_db > 0.0:
        in_knee = np.abs(2.0 * over) <= knee_db
        gain[in_knee] = (1.0 - ratio) * np.square(over[in_knee] - knee_db / 2.0) / (2.0 * knee_db)
        below = 2.0 * over < -knee_db
    else:
        below = over < 0.0
    gain[below] = over[below] * (ratio - 1.0)
    np.maximum(gain, -range_db, out=gain)
    return gain


class Expander(StageBase):
    PARAM_RANGES = {
        "threshold_db": (-90.0, 0.0),
        "ratio": (1.0, 10.0),          # 1 = no expansion; > 10 behaves like a hard gate
        "range_db": (0.0, 60.0),       # maximum attenuation in pauses
        "attack_ms": (0.1, 500.0),
        "hold_ms": (0.0, 1000.0),
        "release_ms": (1.0, 5000.0),
        "knee_db": (0.0, 24.0),
        "detector_ms": (0.5, 100.0),
    }
    BOOL_PARAMS = ("enabled",)
    # 10 ms RMS window: about one period of the lowest male fundamental (85 Hz =
    # 11.8 ms), so the level estimate ripples less than 1 dB at the pitch rate while
    # it still reacts within a syllable.
    PARAM_DEFAULTS = {"detector_ms": 10.0}

    def __init__(self, sample_rate: int, **params):
        super().__init__(sample_rate)
        self._apply_params(params)
        self._detector = MovingAverage(1)
        self._hold = HoldMax(1, fill_value=0.0)
        self._smoother = AttackReleaseSmoother(self.sample_rate, self.attack_ms, self.release_ms)
        self._reduction = ReductionAccumulator()
        self._last_level = DB_FLOOR_DBFS
        self._install_current()

    def _design_from(self, params: dict) -> dict:
        return {
            "detector_samples": int(round(params["detector_ms"] * 1e-3 * self.sample_rate)),
            "hold_samples": int(round(params["hold_ms"] * 1e-3 * self.sample_rate)),
            "attack_ms": params["attack_ms"],
            "release_ms": params["release_ms"],
        }

    def _install(self, design: dict) -> None:
        self._detector.set_window(design["detector_samples"])
        self._hold.set_hold(design["hold_samples"])
        self._smoother.set_times(design["attack_ms"], design["release_ms"])

    def gain_curve(self, x: np.ndarray) -> np.ndarray:
        """Return the smoothed gain in dB for a block *without* applying it (analysis)."""
        level = power_to_db(self._detector.process(np.square(x)))
        self._last_level = float(level[-1])
        target = expander_gain_db(level, self.threshold_db, self.ratio, self.knee_db, self.range_db)
        recently_open = self._hold.process((target >= 0.0).astype(np.float64))
        held = np.where(recently_open > 0.0, 0.0, target)
        return self._smoother.process(held)

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        if not self.enabled or x.size == 0:
            self._reduction.add_zeros(x.size)
            return x
        gain_db = self.gain_curve(x)
        self._reduction.add(-gain_db)
        return x * db_to_amp(gain_db)

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
        self._detector.reset()
        self._hold.reset()
        self._smoother.reset()
        self._reduction.reset()
        self._last_level = DB_FLOOR_DBFS
