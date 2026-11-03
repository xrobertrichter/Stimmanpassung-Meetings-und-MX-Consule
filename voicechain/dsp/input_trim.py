"""Stage 0: static input gain that brings the microphone level to the reference."""

from __future__ import annotations

import numpy as np

from voicechain.dsp import DB_FLOOR_DBFS, StageBase
from voicechain.dsp.envelope import PARAM_RAMP_MS, GainRamp, peak_dbfs, rms_dbfs


class InputTrim(StageBase):
    """Multiplies the signal by ``10**(gain_db/20)``. Set by the calibration, not adaptively."""

    # +-40 dB covers everything from a hot line-level feed to a whisper-quiet USB mic.
    PARAM_RANGES = {"gain_db": (-40.0, 40.0)}

    def __init__(self, sample_rate: int, **params):
        super().__init__(sample_rate)
        self._apply_params(params)
        self._ramp = GainRamp(PARAM_RAMP_MS * 1e-3 * self.sample_rate, self.gain_db)
        self._last_peak = DB_FLOOR_DBFS
        self._last_rms = DB_FLOOR_DBFS
        self._install_current()

    def _design_from(self, params: dict) -> float:
        return float(params["gain_db"])

    def _install(self, design: float) -> None:
        self._ramp.set_gain_db(design)

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        y = x * self._ramp.process(x.size)
        self._last_peak = peak_dbfs(y)
        self._last_rms = rms_dbfs(y)
        return y

    def meters(self) -> dict:
        return {"gain_db": float(self.gain_db), "peak_dbfs": self._last_peak, "rms_dbfs": self._last_rms}

    def reset(self) -> None:
        self._ramp.snap()
        self._last_peak = DB_FLOOR_DBFS
        self._last_rms = DB_FLOOR_DBFS
