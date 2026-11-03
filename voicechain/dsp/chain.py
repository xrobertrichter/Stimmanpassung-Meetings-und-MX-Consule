"""The complete processing chain (docs/00_Beauftragung.md sections 2 and 4.2)."""

from __future__ import annotations

import threading

import numpy as np

from voicechain.dsp import DB_FLOOR_DBFS
from voicechain.dsp.compressor import Compressor
from voicechain.dsp.deesser import DeEsser
from voicechain.dsp.envelope import peak_dbfs, rms_dbfs
from voicechain.dsp.eq import Equalizer
from voicechain.dsp.expander import Expander
from voicechain.dsp.highpass import HighPass
from voicechain.dsp.input_trim import InputTrim
from voicechain.dsp.limiter import Limiter
from voicechain.dsp.normalizer import Normalizer

# Fixed processing order from the "On Air" reference chain.
STAGE_ORDER = ("input_trim", "highpass", "expander", "deesser", "eq", "compressor", "normalizer", "limiter")
# Bypass (A/B listening) keeps the first and the last stage: the trim so that both paths have
# the same level reference, the limiter so that raw speech stays peak-safe. The stages in
# between are cross-faded out over BYPASS_FADE_MS; they keep running so that their states,
# the normaliser's learnt gain and the meters stay current and the return is click-free.
BYPASS_STAGES = ("highpass", "expander", "deesser", "eq", "compressor", "normalizer")
# 30 ms: long enough for a level step of a few dB to pass without a click (< 0.5 dB per
# 5 ms block for differences up to ~3 dB), short enough to feel instantaneous. The same
# fade serves the kill switch (``set_processing``), which - unlike the bypass - really
# stops computing the middle stages and recovers them when switched on again.
BYPASS_FADE_MS = 30.0
STAGE_CLASSES = {
    "input_trim": InputTrim,
    "highpass": HighPass,
    "expander": Expander,
    "deesser": DeEsser,
    "eq": Equalizer,
    "compressor": Compressor,
    "normalizer": Normalizer,
    "limiter": Limiter,
}


class Chain:
    """Runs the stages in order; ``process`` is block-size agnostic and keeps state.

    Threading: one re-entrant lock guards the stage states. ``process`` holds it
    for the whole block (about 0.4 ms for 256 samples at 48 kHz, i.e. < 10 % of
    the block period). ``set_param`` validates and designs the new coefficients
    *outside* the lock and holds it only for the swap (microseconds), so a control
    thread can never delay the audio callback by a filter design.

    Bypass (A/B listening): ``set_bypass(True)`` cross-fades over ``BYPASS_FADE_MS``
    to the path Input Trim -> Limiter while the middle stages keep running;
    ``get_meters()["chain"]["bypass"]`` shows the requested state, ``bypass_mix``
    the fade position.

    Kill switch: ``set_processing(False)`` fades to the same path and then stops
    calling the middle stages altogether (CPU saving); ``set_processing(True)``
    recovers them (fresh filter states, the normaliser keeps gain, reference and
    floor) and fades back. Processing off takes precedence over the bypass.
    ``get_meters()["chain"]["processing"]`` / ``processing_mix``.

    Robustness: a block containing NaN or Inf would poison every filter state
    for good. ``process`` therefore checks ``np.isfinite`` first, replaces such a
    block by silence, clears the signal-path states of all stages (``recover``,
    the normaliser keeps its gain) and counts the event
    (``get_meters()["input"]["nonfinite_blocks"]``).
    """

    def __init__(self, sample_rate: int, stages: dict):
        missing = [name for name in STAGE_ORDER if name not in stages]
        if missing:
            raise ValueError(f"Chain: missing stage(s) {missing}")
        self.sample_rate = int(sample_rate)
        self._stages = {name: stages[name] for name in STAGE_ORDER}
        self._lock = threading.RLock()
        self._nonfinite_blocks = 0
        self._bypass = False                 # requested state
        self._bypass_mix = 0.0               # 0 = full chain, 1 = bypass path (ramps towards the request)
        self._bypass_step = 1.0 / max(1, int(round(BYPASS_FADE_MS * 1e-3 * self.sample_rate)))
        self._processing = True              # kill switch: requested state
        self._processing_mix = 0.0           # 0 = middle stages in the output, 1 = switched off
        self._middle_asleep = False          # middle stages are not being called (fully off)
        self._input_peak = DB_FLOOR_DBFS
        self._input_rms = DB_FLOOR_DBFS
        self._output_peak = DB_FLOOR_DBFS
        self._output_rms = DB_FLOOR_DBFS

    @classmethod
    def from_config(cls, cfg: dict) -> "Chain":
        """Build the chain from the complete configuration dict (needs ``sample_rate`` and ``chain``)."""
        if not isinstance(cfg, dict):
            raise TypeError(f"Chain.from_config expects a dict, got {type(cfg).__name__}")
        for key in ("sample_rate", "chain"):
            if key not in cfg:
                raise ValueError(f"Chain.from_config: configuration lacks the top-level key '{key}'")
        sample_rate = cfg["sample_rate"]
        chain_cfg = cfg["chain"]
        if not isinstance(chain_cfg, dict):
            raise TypeError("Chain.from_config: 'chain' must be a mapping of stage name -> parameters")
        stages = {}
        for name in STAGE_ORDER:
            if name not in chain_cfg:
                raise ValueError(f"Chain.from_config: chain.{name} is missing")
            params = chain_cfg[name]
            if not isinstance(params, dict):
                raise TypeError(f"Chain.from_config: chain.{name} must be a mapping, got {type(params).__name__}")
            try:
                stages[name] = STAGE_CLASSES[name](sample_rate, **params)
            except (TypeError, ValueError, KeyError) as exc:
                raise ValueError(f"chain.{name}: {exc}") from exc
        return cls(sample_rate, stages)

    # -- introspection ----------------------------------------------------------------
    @property
    def stages(self) -> dict:
        return dict(self._stages)

    def stage(self, name: str):
        if name not in self._stages:
            raise KeyError(f"Chain has no stage '{name}'; stages: {STAGE_ORDER}")
        return self._stages[name]

    @property
    def latency_samples(self) -> int:
        limiter = self._stages["limiter"]
        return limiter.latency_samples if limiter.enabled else 0

    @property
    def nonfinite_blocks(self) -> int:
        return self._nonfinite_blocks

    # -- bypass -----------------------------------------------------------------------
    def set_bypass(self, on: bool) -> None:
        """Cross-fade to (True) or from (False) the bypass path Input Trim -> Limiter."""
        with self._lock:
            self._bypass = bool(on)

    def get_bypass(self) -> bool:
        return self._bypass

    def set_processing(self, on: bool) -> None:
        """Kill switch: False fades to Input Trim -> Limiter and stops computing the middle stages."""
        with self._lock:
            self._processing = bool(on)

    def get_processing(self) -> bool:
        return self._processing

    def _ramp(self, current: float, target: float, n: int) -> tuple[np.ndarray | float, float]:
        """Linear 30 ms ramp from ``current`` towards ``target``; returns (weights, new position)."""
        if current == target:
            return target, current
        direction = 1.0 if target > current else -1.0
        weights = current + direction * self._bypass_step * np.arange(1, n + 1, dtype=np.float64)
        np.clip(weights, 0.0, 1.0, out=weights)
        return weights, float(weights[-1])

    def _bypass_weights(self, n: int) -> np.ndarray | float:
        """Per-sample weight of the bypass path for the next block (scalar when settled)."""
        weights, self._bypass_mix = self._ramp(self._bypass_mix, 1.0 if self._bypass else 0.0, n)
        return weights

    def _processing_weights(self, n: int) -> np.ndarray | float:
        """Per-sample weight of the kill-switch path; wakes the middle stages up when switched on."""
        if self._processing and self._middle_asleep:
            # fresh filter/detector states, the normaliser keeps gain, reference and floor
            for name in BYPASS_STAGES:
                self._stages[name].recover()
            self._middle_asleep = False
        weights, self._processing_mix = self._ramp(self._processing_mix, 0.0 if self._processing else 1.0, n)
        if not self._processing and self._processing_mix == 1.0:
            self._middle_asleep = True
        return weights

    # -- contract ---------------------------------------------------------------------
    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x)
        if x.ndim != 1:
            raise ValueError(f"Chain.process expects a mono block of shape (n,), got shape {x.shape}")
        with self._lock:
            y = x.astype(np.float64, copy=False)
            if not np.isfinite(y).all():
                # Replace the block by silence and clear the signal-path states of every
                # stage (the normaliser keeps its learnt gain, reference and noise floor:
                # a finite scalar cannot be poisoned, and losing it would cost seconds of
                # wrong loudness). The silent block is not run through the stages.
                self._nonfinite_blocks += 1
                for stage in self._stages.values():
                    stage.recover()
                self._input_peak = self._input_rms = DB_FLOOR_DBFS
                self._output_peak = self._output_rms = DB_FLOOR_DBFS
                return np.zeros(y.shape, dtype=np.float32)
            self._input_peak = peak_dbfs(y)
            self._input_rms = rms_dbfs(y)
            y = self._stages["input_trim"].process(y)
            off_weight = self._processing_weights(y.size)
            if isinstance(off_weight, float) and off_weight == 1.0:
                mixed = y                                   # switched off: middle stages are not called
            else:
                processed = y
                for name in BYPASS_STAGES:
                    processed = self._stages[name].process(processed)
                weight = np.maximum(off_weight, self._bypass_weights(y.size))
                if isinstance(weight, float):
                    mixed = y if weight == 1.0 else processed
                else:
                    mixed = processed + weight * (y - processed)
            y = self._stages["limiter"].process(mixed)
            self._output_peak = peak_dbfs(y)
            self._output_rms = rms_dbfs(y)
            return y.astype(np.float32)

    def set_param(self, stage: str, name: str, value: float) -> None:
        """Validate and design outside the lock; the stage swaps coefficients under it."""
        self.stage(stage).set_param(name, value, lock=self._lock)

    def get_param(self, stage: str, name: str) -> float:
        return self.stage(stage).get_param(name)

    def get_meters(self) -> dict:
        """Meters of all stages plus input/output levels.

        Reading drains the per-stage accumulators ``gain_reduction_mean_db`` /
        ``gain_reduction_max_db`` (statistics since the previous read).
        """
        with self._lock:
            meters = {name: stage.meters() for name, stage in self._stages.items()}
            if (self._bypass and self._bypass_mix == 1.0) or self._middle_asleep:
                # bypass: the stages keep running for state and learning; kill switch: they are
                # not called at all. Either way nothing of their gain reduction reaches the
                # output: report what the listener gets
                for name in BYPASS_STAGES:
                    for key in ("gain_reduction_db", "gain_reduction_mean_db", "gain_reduction_max_db"):
                        if key in meters[name]:
                            meters[name][key] = 0.0
                    meters[name]["bypassed"] = 1.0
            meters["input"] = {"peak_dbfs": self._input_peak, "rms_dbfs": self._input_rms,
                               "nonfinite_blocks": float(self._nonfinite_blocks)}
            meters["output"] = {"peak_dbfs": self._output_peak, "rms_dbfs": self._output_rms}
            meters["chain"] = {"bypass": float(self._bypass), "bypass_mix": self._bypass_mix,
                               "processing": float(self._processing), "processing_mix": self._processing_mix,
                               "latency_samples": float(self.latency_samples)}
            return meters

    def reset(self) -> None:
        with self._lock:
            for stage in self._stages.values():
                stage.reset()
            self._bypass_mix = 1.0 if self._bypass else 0.0
            self._processing_mix = 0.0 if self._processing else 1.0
            self._middle_asleep = not self._processing
            self._input_peak = self._input_rms = DB_FLOOR_DBFS
            self._output_peak = self._output_rms = DB_FLOOR_DBFS
