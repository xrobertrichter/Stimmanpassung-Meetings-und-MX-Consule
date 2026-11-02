"""Stage 1: Butterworth high-pass against rumble, handling noise and plosive energy."""

from __future__ import annotations

import numpy as np

from voicechain.dsp import StageBase
from voicechain.dsp.biquad import SosFilter, butter_highpass_sos
from voicechain.dsp.envelope import PARAM_RAMP_MS


class HighPass(StageBase):
    """Butterworth high-pass (``order`` 1..8) realised as an SOS cascade with carried state."""

    # 20 Hz..400 Hz: below 20 Hz the filter does nothing audible, above 400 Hz it
    # removes speech fundamentals. Order 8 (48 dB/oct) is the steepest that is
    # still sensible for speech without audible ringing.
    PARAM_RANGES = {"freq_hz": (20.0, 400.0), "order": (1.0, 8.0)}
    BOOL_PARAMS = ("enabled",)

    def __init__(self, sample_rate: int, **params):
        super().__init__(sample_rate)
        self._apply_params(params)
        self._filter = SosFilter(self._design(), fade_samples=int(round(PARAM_RAMP_MS * 1e-3 * self.sample_rate)))
        self._update_coefficients()

    def _design(self) -> np.ndarray:
        order = int(round(self.order))
        if abs(order - self.order) > 1e-9:
            raise ValueError(f"HighPass.order must be an integer, got {self.order!r}")
        return butter_highpass_sos(self.freq_hz, order, self.sample_rate)

    def _update_coefficients(self) -> None:
        self._filter.set_sos(self._design())

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        if not self.enabled:
            return x
        return self._filter.process(x)

    def meters(self) -> dict:
        return {"enabled": float(self.enabled), "freq_hz": float(self.freq_hz)}

    def reset(self) -> None:
        self._filter.reset()
