"""Stage 4: parametric equaliser (low shelf / peak / high shelf biquads in cascade)."""

from __future__ import annotations

import contextlib

import numpy as np

from voicechain.dsp import StageBase, _as_bool
from voicechain.dsp.biquad import SosFilter, high_shelf_sos, low_shelf_sos, peak_sos
from voicechain.dsp.envelope import PARAM_RAMP_MS

BAND_TYPES = ("low_shelf", "peak", "high_shelf")
BAND_FIELDS = ("freq_hz", "gain_db", "q")
# +-24 dB is far beyond any corrective use (the defaults stay within +-3 dB) but
# still numerically harmless; Q 0.1..20 spans broad shelves to narrow notches.
BAND_RANGES = {"gain_db": (-24.0, 24.0), "q": (0.1, 20.0)}
_IDENTITY_SOS = np.array([[1.0, 0.0, 0.0, 1.0, 0.0, 0.0]])


def validate_band(band: dict, sample_rate: int, index: int) -> dict:
    """Return a cleaned copy of one band definition or raise ValueError with context."""
    if not isinstance(band, dict):
        raise ValueError(f"eq.bands[{index}] must be a mapping, got {type(band).__name__}")
    unknown = set(band) - {"type", *BAND_FIELDS}
    if unknown:
        raise ValueError(f"eq.bands[{index}]: unknown key(s) {sorted(unknown)}")
    for key in ("type", *BAND_FIELDS):
        if key not in band:
            raise ValueError(f"eq.bands[{index}]: missing key '{key}'")
    if band["type"] not in BAND_TYPES:
        raise ValueError(f"eq.bands[{index}].type={band['type']!r} must be one of {BAND_TYPES}")
    clean = {"type": str(band["type"])}
    for key in BAND_FIELDS:
        value = band[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"eq.bands[{index}].{key} must be a number, got {value!r}")
        clean[key] = float(value)
    nyquist = 0.5 * sample_rate
    if not (20.0 <= clean["freq_hz"] < nyquist):
        raise ValueError(f"eq.bands[{index}].freq_hz={clean['freq_hz']} must be in [20, {nyquist})")
    for key, (lo, hi) in BAND_RANGES.items():
        if not (lo <= clean[key] <= hi):
            raise ValueError(f"eq.bands[{index}].{key}={clean[key]} outside [{lo}, {hi}]")
    return clean


def band_sos(band: dict, sample_rate: int) -> np.ndarray:
    design = {"low_shelf": low_shelf_sos, "peak": peak_sos, "high_shelf": high_shelf_sos}[band["type"]]
    return design(band["freq_hz"], band["gain_db"], band["q"], sample_rate)


class Equalizer(StageBase):
    """Cascade of RBJ biquads. Parameters are addressed as ``bands.<index>.<field>``.

    The number and type of bands is fixed at construction; gain, frequency and Q
    of each band can be changed at run time without clicks (states are kept).
    """

    BOOL_PARAMS = ("enabled",)

    def __init__(self, sample_rate: int, enabled: bool = True, bands: list | None = None, **unknown):
        super().__init__(sample_rate)
        if unknown:
            raise ValueError(f"Equalizer: unknown parameter(s) {sorted(unknown)}; allowed: ['enabled', 'bands']")
        if bands is None:
            raise ValueError("Equalizer: missing parameter 'bands' (use an empty list for a flat EQ)")
        if not isinstance(bands, list):
            raise ValueError(f"Equalizer: 'bands' must be a list, got {type(bands).__name__}")
        self.enabled = _as_bool("enabled", enabled)
        self.bands = [validate_band(b, self.sample_rate, i) for i, b in enumerate(bands)]
        self._filter = SosFilter(self._design_from(self.bands),
                                 fade_samples=int(round(PARAM_RAMP_MS * 1e-3 * self.sample_rate)))

    def _design_from(self, bands: list) -> np.ndarray:
        if not bands:
            return _IDENTITY_SOS.copy()
        return np.vstack([band_sos(b, self.sample_rate) for b in bands])

    def _install(self, design: np.ndarray) -> None:
        self._filter.set_sos(design)

    @property
    def sos(self) -> np.ndarray:
        return self._filter.sos

    def _split(self, name: str) -> tuple[int, str]:
        parts = name.split(".")
        if len(parts) != 3 or parts[0] != "bands" or parts[2] not in BAND_FIELDS:
            raise KeyError(
                f"Equalizer parameter names are 'enabled' or 'bands.<index>.<field>' "
                f"with field in {BAND_FIELDS}; got {name!r}"
            )
        try:
            index = int(parts[1])
        except ValueError as exc:
            raise KeyError(f"Equalizer: band index must be an integer, got {parts[1]!r}") from exc
        if not (0 <= index < len(self.bands)):
            raise KeyError(f"Equalizer: band index {index} out of range (have {len(self.bands)} bands)")
        return index, parts[2]

    def set_param(self, name: str, value: float, lock=None) -> None:
        """Validate and design outside ``lock``; swap bands and coefficients inside it."""
        if name == "enabled":
            self.enabled = _as_bool(name, value)
            return
        index, field = self._split(name)
        candidate = dict(self.bands[index])
        candidate[field] = float(value)
        new_bands = list(self.bands)
        new_bands[index] = validate_band(candidate, self.sample_rate, index)
        design = self._design_from(new_bands)
        with (lock if lock is not None else contextlib.nullcontext()):
            self.bands = new_bands
            self._install(design)

    def get_param(self, name: str) -> float:
        if name == "enabled":
            return float(self.enabled)
        index, field = self._split(name)
        return float(self.bands[index][field])

    def get_params(self) -> dict[str, float]:
        out = {"enabled": float(self.enabled)}
        for i, band in enumerate(self.bands):
            for field in BAND_FIELDS:
                out[f"bands.{i}.{field}"] = float(band[field])
        return out

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        if not self.enabled or not self.bands:
            return x
        return self._filter.process(x)

    def meters(self) -> dict:
        return {"enabled": float(self.enabled), "bands": float(len(self.bands))}

    def reset(self) -> None:
        self._filter.reset()
