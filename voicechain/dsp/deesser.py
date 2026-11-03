"""Stage 3: split-band de-esser.

Design choice (see docs/02_Parameter_Begruendung.md): a parallel band-pass
(RBJ, constant 0 dB peak gain, ``freq_hz`` / ``bandwidth_oct``) isolates the
sibilance band. Its output is both the *detector* signal and the signal that is
*subtracted* from the input::

    bp = BPF(x)
    GR[n] = min(max_reduction_db, comp_curve(20*log10|bp[n]|))   # dB, >= 0
    GR_s  = AttackReleaseSmoother(GR)
    y     = x - (1 - 10**(-GR_s/20)) * bp

At the centre frequency ``H_bp = 1`` (real), so the attenuation there is exactly
``GR_s`` dB; away from the band the band-pass vanishes and the signal is left
untouched. Compared with a dynamically re-tuned peak EQ this needs no per-sample
coefficient recomputation, is sample-accurate (no zipper noise at block
boundaries) and therefore independent of the block size; compared with a
wide-band de-esser it does not dull the whole signal on every "s".
"""

from __future__ import annotations

import numpy as np

from voicechain.dsp import DB_FLOOR_DBFS, StageBase
from voicechain.dsp.biquad import SosFilter, bandpass_sos, bandwidth_to_q
from voicechain.dsp.compressor import compressor_reduction_db
from voicechain.dsp.envelope import (
    PARAM_RAMP_MS, AttackReleaseSmoother, ReductionAccumulator, amp_to_db, db_to_amp,
)


class DeEsser(StageBase):
    PARAM_RANGES = {
        "freq_hz": (2000.0, 12000.0),        # sibilance lives between 4 and 10 kHz
        "bandwidth_oct": (0.25, 3.0),
        "threshold_db": (-60.0, 0.0),
        # 1.1 is the smallest ratio with a measurable effect; exactly 1.0 ("off")
        # would make the calibration's threshold derivation divide by zero.
        "ratio": (1.1, 20.0),
        "max_reduction_db": (0.0, 24.0),
        "attack_ms": (0.1, 50.0),
        "release_ms": (1.0, 1000.0),
    }
    BOOL_PARAMS = ("enabled",)

    def __init__(self, sample_rate: int, **params):
        super().__init__(sample_rate)
        self._apply_params(params)
        design = self._design_from(self.get_params())
        self._band = SosFilter(design["sos"], fade_samples=int(round(PARAM_RAMP_MS * 1e-3 * self.sample_rate)))
        self._smoother = AttackReleaseSmoother(self.sample_rate, self.attack_ms, self.release_ms)
        self._reduction = ReductionAccumulator()
        self._last_band_level = DB_FLOOR_DBFS

    def _design_from(self, params: dict) -> dict:
        q = bandwidth_to_q(params["bandwidth_oct"], params["freq_hz"], self.sample_rate)
        return {"sos": bandpass_sos(params["freq_hz"], q, self.sample_rate),
                "attack_ms": params["attack_ms"], "release_ms": params["release_ms"]}

    def _install(self, design: dict) -> None:
        self._band.set_sos(design["sos"])
        self._smoother.set_times(design["attack_ms"], design["release_ms"])

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        if not self.enabled or x.size == 0:
            self._reduction.add_zeros(x.size)
            return x
        band = self._band.process(x)
        level = amp_to_db(band)
        self._last_band_level = float(np.max(level))
        # hard knee (knee_db=0): the de-esser should react decisively once sibilance exceeds the threshold
        target = compressor_reduction_db(level, self.threshold_db, self.ratio, 0.0)
        np.minimum(target, self.max_reduction_db, out=target)
        reduction = self._smoother.process(target)
        self._reduction.add(reduction)
        return x - (1.0 - db_to_amp(-reduction)) * band

    def meters(self) -> dict:
        mean, peak = self._reduction.read()
        return {
            "enabled": float(self.enabled),
            "gain_reduction_db": self._reduction.last_mean,
            "gain_reduction_mean_db": mean,
            "gain_reduction_max_db": peak,
            "band_level_dbfs": self._last_band_level,
        }

    def reset(self) -> None:
        self._band.reset()
        self._smoother.reset()
        self._reduction.reset()
        self._last_band_level = DB_FLOOR_DBFS
