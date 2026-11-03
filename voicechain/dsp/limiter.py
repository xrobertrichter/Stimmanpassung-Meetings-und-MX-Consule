"""Stage 6: look-ahead peak limiter whose output never exceeds the ceiling.

Gain computation (all in dB of gain reduction, GR >= 0, vectorised)::

    GR_req[n]  = max(0, 20*log10|x[n]| - (ceiling_dbfs - true_peak_margin_db))
    GR_rel     = ReleaseDecay(GR_req, release_ms)      # exponential recovery
    GR_hold[n] = max(GR_rel[n-D .. n])                 # D = lookahead samples
    GR_avg[n]  = mean(GR_hold[n-D+1 .. n])             # linear D-sample attack ramp
    y[n]       = x[n-D] * 10**(-GR_avg[n]/20)

Proof of the ceiling guarantee: every term of the mean ``GR_avg[n]`` is a maximum
over a window that contains index ``n-D``, hence ``GR_avg[n] >= GR_rel[n-D] >=
GR_req[n-D]`` and the delayed sample ``x[n-D]`` is attenuated at least as much as
required. The ramp therefore reaches the full reduction exactly when the peak
arrives at the output, with a smooth (box-car) attack over the look-ahead time.

True peak: a sample-peak limiter cannot see inter-sample peaks, so the internal
sample ceiling sits ``true_peak_margin_db`` below the configured ceiling. The
default of 1.5 dB was chosen from measurements with 4x oversampling
(tests/test_dsp_limiter.py): band-limited speech-like material overshoots by
< 0.1 dB, heavily limited pink noise (the worst realistic case for a speech
microphone) by about 1.0 dB, leaving 0.5 dB reserve. Signals with full energy up
to Nyquist (white noise, square waves) can overshoot by 2-3 dB and are not
covered; they do not occur behind a microphone with an anti-aliasing filter.
"""

from __future__ import annotations

import numpy as np

from voicechain.dsp import StageBase
from voicechain.dsp.envelope import (
    HoldMax, MovingAverage, ReductionAccumulator, ReleaseDecay, amp_to_db, db_to_amp,
)


class Limiter(StageBase):
    PARAM_RANGES = {
        "ceiling_dbfs": (-20.0, 0.0),
        "lookahead_ms": (0.5, 20.0),     # < 0.5 ms cannot ramp smoothly, > 20 ms adds needless latency
        "release_ms": (1.0, 1000.0),
        "true_peak_margin_db": (0.0, 3.0),
    }
    BOOL_PARAMS = ("enabled",)
    PARAM_DEFAULTS = {"true_peak_margin_db": 1.5}

    def __init__(self, sample_rate: int, **params):
        super().__init__(sample_rate)
        self._apply_params(params)
        self._lookahead = 0
        self._delay = np.zeros(0)
        self._average = MovingAverage(1)
        self._hold = HoldMax(1, fill_value=0.0)
        self._release = ReleaseDecay(self.sample_rate, self.release_ms)
        self._ceiling_db = 0.0
        self._reduction = ReductionAccumulator()
        self._install_current()

    @property
    def latency_samples(self) -> int:
        return self._lookahead

    def _design_from(self, params: dict) -> dict:
        return {
            "release_ms": params["release_ms"],
            "ceiling_db": params["ceiling_dbfs"] - params["true_peak_margin_db"],
            "lookahead": max(1, int(round(params["lookahead_ms"] * 1e-3 * self.sample_rate))),
        }

    def _install(self, design: dict) -> None:
        self._release.set_time(design["release_ms"])
        self._ceiling_db = design["ceiling_db"]
        lookahead = design["lookahead"]
        if lookahead != self._lookahead:
            # A change of the look-ahead changes the latency; the buffers are re-created
            # (zero-filled). This is the one parameter change that is not click-free.
            self._lookahead = lookahead
            self._delay = np.zeros(lookahead)
            self._average = MovingAverage(lookahead)
            self._hold.set_hold(lookahead + 1)

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        n = x.size
        if not self.enabled or n == 0:
            self._reduction.add_zeros(n)
            return x
        required = amp_to_db(x) - self._ceiling_db
        np.maximum(required, 0.0, out=required)
        held = self._hold.process(self._release.process(required))
        reduction = self._average.process(held)
        delayed = np.concatenate([self._delay, x])
        self._delay = delayed[n:]
        self._reduction.add(reduction)
        return delayed[:n] * db_to_amp(-reduction)

    def meters(self) -> dict:
        mean, peak = self._reduction.read()
        return {
            "enabled": float(self.enabled),
            "gain_reduction_db": self._reduction.last_mean,
            "gain_reduction_mean_db": mean,
            "gain_reduction_max_db": peak,
            "latency_samples": float(self._lookahead),
        }

    def reset(self) -> None:
        self._delay[...] = 0.0
        self._average.reset()
        self._hold.reset()
        self._release.reset()
        self._reduction.reset()
