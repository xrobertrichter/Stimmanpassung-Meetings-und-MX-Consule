"""The complete processing chain (docs/00_Beauftragung.md sections 2 and 4.2)."""

from __future__ import annotations

import threading

import numpy as np

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

    All public methods take one lock, so ``set_param`` from a control thread and
    ``process`` from the audio callback never interleave (the lock is held for a
    few microseconds per block, far below the block period).
    """

    def __init__(self, sample_rate: int, stages: dict):
        missing = [name for name in STAGE_ORDER if name not in stages]
        if missing:
            raise ValueError(f"Chain: missing stage(s) {missing}")
        self.sample_rate = int(sample_rate)
        self._stages = {name: stages[name] for name in STAGE_ORDER}
        self._lock = threading.RLock()
        self._input_peak = -150.0
        self._input_rms = -150.0
        self._output_peak = -150.0
        self._output_rms = -150.0

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

    # -- contract ---------------------------------------------------------------------
    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x)
        if x.ndim != 1:
            raise ValueError(f"Chain.process expects a mono block of shape (n,), got shape {x.shape}")
        with self._lock:
            y = x.astype(np.float64, copy=False)
            self._input_peak = peak_dbfs(y)
            self._input_rms = rms_dbfs(y)
            for stage in self._stages.values():
                y = stage.process(y)
            self._output_peak = peak_dbfs(y)
            self._output_rms = rms_dbfs(y)
            return y.astype(np.float32)

    def set_param(self, stage: str, name: str, value: float) -> None:
        with self._lock:
            self.stage(stage).set_param(name, value)

    def get_param(self, stage: str, name: str) -> float:
        with self._lock:
            return self.stage(stage).get_param(name)

    def get_meters(self) -> dict:
        with self._lock:
            meters = {name: stage.meters() for name, stage in self._stages.items()}
            meters["input"] = {"peak_dbfs": self._input_peak, "rms_dbfs": self._input_rms}
            meters["output"] = {"peak_dbfs": self._output_peak, "rms_dbfs": self._output_rms}
            return meters

    def reset(self) -> None:
        with self._lock:
            for stage in self._stages.values():
                stage.reset()
            self._input_peak = self._input_rms = -150.0
            self._output_peak = self._output_rms = -150.0
