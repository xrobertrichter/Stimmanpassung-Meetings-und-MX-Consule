"""DSP stages of voicechain and the common stage interface.

Processing order (fixed, see docs/00_Beauftragung.md section 2):
input_trim -> highpass -> expander -> deesser -> eq -> compressor -> normalizer -> limiter

:class:`StageBase` is defined here, *before* the stage modules are imported at
the bottom of this file, so that ``voicechain.dsp.<stage>`` can import it from
the package while the package itself is still initialising.
"""

from __future__ import annotations

import abc
import contextlib
import math

import numpy as np


# Floor for every level measurement in dBFS. -150 dBFS is 6 dB below the quantisation
# noise of a 24-bit converter (-144 dBFS) and far below any microphone self-noise, so
# clamping there never influences a real signal but keeps log10 finite for digital
# silence. Shared by all stages and meant to be imported by meters/runtime as well.
DB_FLOOR_DBFS = -150.0


class StageBase(abc.ABC):
    """Common contract of every stage (docs/00_Beauftragung.md section 4.2).

    Subclasses declare ``PARAM_RANGES`` (name -> (min, max) inclusive) for all
    numeric parameters that may be set at run time, ``BOOL_PARAMS`` for switches
    and ``PARAM_DEFAULTS`` for optional tuning values. Parameter changes are two-
    phase: ``_design_from(params)`` derives all coefficients from a parameter
    mapping *without touching the stage* (it may raise, in which case nothing
    changes), and ``_install(design)`` swaps them in while filter states are kept.
    ``set_param`` runs the design outside the optional ``lock`` and only the swap
    inside it, so the audio thread is blocked for microseconds, not for a filter
    design.
    """

    PARAM_RANGES: dict[str, tuple[float, float]] = {}
    BOOL_PARAMS: tuple[str, ...] = ()
    # Parameters that may be omitted in the configuration (internal tuning values
    # the adaptive controller never touches); the default is documented per stage.
    PARAM_DEFAULTS: dict[str, float] = {}

    def __init__(self, sample_rate: int):
        if int(sample_rate) != sample_rate or sample_rate < 8000 or sample_rate > 192000:
            raise ValueError(f"sample_rate={sample_rate!r} must be an integer in [8000, 192000] Hz")
        self.sample_rate = int(sample_rate)

    # -- parameter handling ---------------------------------------------------------
    @classmethod
    def param_names(cls) -> tuple[str, ...]:
        return tuple(cls.PARAM_RANGES) + tuple(cls.BOOL_PARAMS)

    def _check_range(self, name: str, value: float) -> float:
        lo, hi = self.PARAM_RANGES[name]
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise TypeError(f"{type(self).__name__}.{name}: expected a number, got {value!r}") from exc
        if not math.isfinite(value) or value < lo or value > hi:
            raise ValueError(
                f"{type(self).__name__}.{name}={value!r} outside the allowed range [{lo}, {hi}]"
            )
        return value

    def _validated(self, name: str, value):
        if name in self.PARAM_RANGES:
            return self._check_range(name, value)
        if name in self.BOOL_PARAMS:
            return _as_bool(name, value)
        raise KeyError(f"{type(self).__name__} has no parameter '{name}'; known: {self.param_names()}")

    def _apply_params(self, params: dict) -> None:
        """Validate and store constructor parameters; unknown names are an error."""
        unknown = set(params) - set(self.param_names())
        if unknown:
            raise ValueError(
                f"{type(self).__name__}: unknown parameter(s) {sorted(unknown)}; "
                f"allowed: {sorted(self.param_names())}"
            )
        for name in self.PARAM_RANGES:
            if name in params:
                value = params[name]
            elif name in self.PARAM_DEFAULTS:
                value = self.PARAM_DEFAULTS[name]
            else:
                raise ValueError(f"{type(self).__name__}: missing parameter '{name}'")
            setattr(self, name, self._check_range(name, value))
        for name in self.BOOL_PARAMS:
            if name not in params:
                raise ValueError(f"{type(self).__name__}: missing parameter '{name}'")
            setattr(self, name, _as_bool(name, params[name]))

    def _install_current(self) -> None:
        """Design from the stored parameters and install (constructor helper)."""
        self._install(self._design_from(self.get_params()))

    def set_param(self, name: str, value: float, lock=None) -> None:
        """Validate ``value``, design the new coefficients, then swap them in under ``lock``.

        If validation or design fails nothing is changed (the old value stays).
        """
        value = self._validated(name, value)
        params = self.get_params()
        params[name] = float(value)
        design = self._design_from(params)
        with (lock if lock is not None else contextlib.nullcontext()):
            setattr(self, name, value)
            self._install(design)

    def get_param(self, name: str) -> float:
        if name not in self.param_names():
            raise KeyError(f"{type(self).__name__} has no parameter '{name}'; known: {self.param_names()}")
        return float(getattr(self, name))

    def get_params(self) -> dict[str, float]:
        return {name: self.get_param(name) for name in self.param_names()}

    # -- processing contract --------------------------------------------------------
    @abc.abstractmethod
    def _design_from(self, params: dict):
        """Derive coefficients from a parameter mapping without touching the stage (pure)."""

    @abc.abstractmethod
    def _install(self, design) -> None:
        """Swap the coefficients of ``design`` in, keeping all filter/detector states."""

    @abc.abstractmethod
    def process(self, x: np.ndarray) -> np.ndarray:
        """Process one mono block (float64 in, float64 out), carrying state."""

    @abc.abstractmethod
    def meters(self) -> dict:
        """Return the measurements of the most recently processed block."""

    @abc.abstractmethod
    def reset(self) -> None:
        """Clear all filter and detector states (parameters are kept)."""

    def recover(self) -> None:
        """Recover from a corrupted (non-finite) signal: clear the signal-path states.

        Defaults to ``reset``; stages that hold slowly learnt control values which
        a single bad block cannot corrupt (the normaliser's gain, reference and
        noise floor) override this to keep them.
        """
        self.reset()


def _as_bool(name: str, value) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, float, np.integer, np.floating)) and value in (0, 1):
        return bool(value)
    raise TypeError(f"parameter '{name}' must be a boolean (or 0/1), got {value!r}")


# Imported last on purpose (see module docstring).
from voicechain.dsp.input_trim import InputTrim  # noqa: E402
from voicechain.dsp.highpass import HighPass  # noqa: E402
from voicechain.dsp.expander import Expander  # noqa: E402
from voicechain.dsp.deesser import DeEsser  # noqa: E402
from voicechain.dsp.eq import Equalizer  # noqa: E402
from voicechain.dsp.compressor import Compressor  # noqa: E402
from voicechain.dsp.normalizer import Normalizer  # noqa: E402
from voicechain.dsp.limiter import Limiter  # noqa: E402
from voicechain.dsp.chain import Chain, STAGE_ORDER  # noqa: E402

__all__ = [
    "StageBase", "DB_FLOOR_DBFS", "InputTrim", "HighPass", "Expander", "DeEsser", "Equalizer",
    "Compressor", "Normalizer", "Limiter", "Chain", "STAGE_ORDER",
]
