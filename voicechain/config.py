"""Configuration loading, validation and saving (YAML schema of docs/00_Beauftragung.md 4.1).

Validation is strict: unknown keys, missing keys, wrong types and out-of-range
values are all reported, with the full key path, in one :class:`ConfigError`.
The only exception are top-level keys starting with an underscore (for example
``_calibration`` written by the calibration with measurements and reasons):
they are metadata, not validated, ignored by ``Chain.from_config`` and kept
when saving.
The numeric ranges of the chain stages come from the stage classes themselves
(``PARAM_RANGES``), so the loader and the run-time ``set_param`` can never
disagree.

Saving uses ``yaml.safe_dump``; YAML comments of the source file are *not*
preserved (PyYAML drops them), which is accepted for generated configurations
(the calibration output). The reference file ``config/chain.default.yaml`` keeps
its comments because it is never overwritten by this module.
"""

from __future__ import annotations

import math
from pathlib import Path

import yaml

from voicechain.dsp import STAGE_ORDER
from voicechain.dsp.chain import STAGE_CLASSES
from voicechain.dsp.eq import Equalizer, validate_band


class ConfigError(ValueError):
    """Raised for an invalid configuration; the message lists every problem found."""


# Top-level scalar schema: key -> (type name, min, max). Ranges: 8 kHz..192 kHz covers
# every CoreAudio rate; block sizes below 16 are pointless, above 8192 (170 ms) the
# latency is unusable for a conversation.
_TOP_LEVEL_KEYS = ("sample_rate", "block_size", "input", "output", "chain", "adaptive")
# Optional top-level sections: ``control`` holds the Unix-socket path of the live control channel.
_OPTIONAL_TOP_LEVEL_KEYS = ("control",)
_ADAPTIVE_RANGES = {
    "learn_s": (0.0, 600.0),       # 0 = no learning phase, 10 min is the longest sensible wait
    "interval_s": (0.1, 60.0),
    "max_step_db": (0.01, 6.0),    # > 6 dB per step would be audible as a jump
}


def default_config_path() -> Path:
    """Path of ``config/chain.default.yaml`` relative to the package (repo layout)."""
    return Path(__file__).resolve().parent.parent / "config" / "chain.default.yaml"


def load_config(path: str | Path) -> dict:
    """Read and validate a YAML configuration; raises ConfigError / FileNotFoundError."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        try:
            cfg = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            raise ConfigError(f"{path}: YAML syntax error: {exc}") from exc
    if not isinstance(cfg, dict):
        raise ConfigError(f"{path}: top level must be a mapping, got {type(cfg).__name__}")
    validate_config(cfg)
    return cfg


def save_config(cfg: dict, path: str | Path) -> None:
    """Validate and write ``cfg`` as YAML (comments are not preserved, see module docstring)."""
    validate_config(cfg)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# voicechain configuration written by voicechain.config.save_config.\n"
        "# Comments of the source file are not carried over; see docs/02_Parameter_Begruendung.md\n"
        "# for the meaning of every value.\n"
    )
    with path.open("w", encoding="utf-8") as fh:
        fh.write(header)
        yaml.safe_dump(cfg, fh, sort_keys=False, allow_unicode=True, default_flow_style=False)


# -- validation ---------------------------------------------------------------------------

def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class _Collector:
    def __init__(self):
        self.errors: list[str] = []

    def add(self, path: str, message: str) -> None:
        self.errors.append(f"{path}: {message}")

    def mapping(self, value, path: str) -> bool:
        if not isinstance(value, dict):
            self.add(path, f"must be a mapping, got {type(value).__name__}")
            return False
        return True

    def number(self, value, path: str, lo: float, hi: float) -> None:
        if not _is_number(value):
            self.add(path, f"must be a number, got {value!r}")
        elif not (lo <= value <= hi):
            self.add(path, f"value {value!r} outside the allowed range [{lo}, {hi}]")

    def integer(self, value, path: str, lo: int, hi: int) -> None:
        if not _is_int(value):
            self.add(path, f"must be an integer, got {value!r}")
        elif not (lo <= value <= hi):
            self.add(path, f"value {value!r} outside the allowed range [{lo}, {hi}]")

    def boolean(self, value, path: str) -> None:
        if not isinstance(value, bool):
            self.add(path, f"must be true or false, got {value!r}")

    def string(self, value, path: str) -> None:
        if not isinstance(value, str) or not value:
            self.add(path, f"must be a non-empty string, got {value!r}")

    def keys(self, value: dict, path: str, required: tuple, optional: tuple = ()) -> None:
        for key in required:
            if key not in value:
                self.add(f"{path}.{key}" if path else key, "missing")
        unknown = set(value) - set(required) - set(optional)
        for key in sorted(unknown, key=str):
            self.add(f"{path}.{key}" if path else str(key), "unknown key")


def _validate_stage(col: _Collector, name: str, params, sample_rate) -> None:
    path = f"chain.{name}"
    if not col.mapping(params, path):
        return
    cls = STAGE_CLASSES[name]
    if cls is Equalizer:
        col.keys(params, path, required=("enabled", "bands"), optional=("tilt_db",))
        if "enabled" in params:
            col.boolean(params["enabled"], f"{path}.enabled")
        if "tilt_db" in params:
            col.number(params["tilt_db"], f"{path}.tilt_db", *Equalizer.PARAM_RANGES["tilt_db"])
        bands = params.get("bands")
        if bands is not None:
            if not isinstance(bands, list):
                col.add(f"{path}.bands", f"must be a list, got {type(bands).__name__}")
            elif _is_int(sample_rate):
                for i, band in enumerate(bands):
                    try:
                        validate_band(band, sample_rate, i)
                    except ValueError as exc:
                        # validate_band messages already start with "eq.bands[i]..."
                        col.errors.append(f"chain.{exc}")
        return
    required = tuple(p for p in cls.PARAM_RANGES if p not in cls.PARAM_DEFAULTS) + tuple(cls.BOOL_PARAMS)
    optional = tuple(cls.PARAM_DEFAULTS)
    col.keys(params, path, required=required, optional=optional)
    for key, value in params.items():
        if key in cls.PARAM_RANGES:
            lo, hi = cls.PARAM_RANGES[key]
            col.number(value, f"{path}.{key}", lo, hi)
        elif key in cls.BOOL_PARAMS:
            col.boolean(value, f"{path}.{key}")
    if name == "highpass" and _is_number(params.get("order")) and int(params["order"]) != params["order"]:
        col.add(f"{path}.order", f"must be an integer, got {params['order']!r}")
    if name == "normalizer" and _is_number(params.get("min_gain_db")) and _is_number(params.get("max_gain_db")):
        if params["min_gain_db"] > params["max_gain_db"]:
            col.add(f"{path}.min_gain_db", "must not exceed max_gain_db")
    # frequency parameters must also stay below Nyquist for the configured sample rate
    if _is_int(sample_rate) and _is_number(params.get("freq_hz")) and params["freq_hz"] >= sample_rate / 2:
        col.add(f"{path}.freq_hz", f"must be below the Nyquist frequency {sample_rate / 2:.0f} Hz")


def _validate_adaptive(col: _Collector, adaptive, chain) -> None:
    path = "adaptive"
    if not col.mapping(adaptive, path):
        return
    col.keys(adaptive, path, required=("enabled", "learn_s", "interval_s", "max_step_db", "log_path", "bounds"))
    if "enabled" in adaptive:
        col.boolean(adaptive["enabled"], f"{path}.enabled")
    for key, (lo, hi) in _ADAPTIVE_RANGES.items():
        if key in adaptive:
            col.number(adaptive[key], f"{path}.{key}", lo, hi)
    if "log_path" in adaptive:
        col.string(adaptive["log_path"], f"{path}.log_path")
    bounds = adaptive.get("bounds")
    if bounds is None or not col.mapping(bounds, f"{path}.bounds"):
        return
    for key, limits in bounds.items():
        bpath = f"{path}.bounds.{key}"
        parts = str(key).split(".")
        if len(parts) != 2 or parts[0] not in STAGE_CLASSES or parts[1] not in STAGE_CLASSES[parts[0]].PARAM_RANGES:
            col.add(bpath, "key must be '<stage>.<numeric parameter>' of an existing stage")
            continue
        lo_allowed, hi_allowed = STAGE_CLASSES[parts[0]].PARAM_RANGES[parts[1]]
        if not (isinstance(limits, list) and len(limits) == 2 and all(_is_number(v) for v in limits)):
            col.add(bpath, f"must be a list [min, max] of two numbers, got {limits!r}")
            continue
        lo, hi = limits
        if lo >= hi:
            col.add(bpath, f"min {lo!r} must be smaller than max {hi!r}")
        if lo < lo_allowed or hi > hi_allowed:
            col.add(bpath, f"[{lo}, {hi}] exceeds the parameter range [{lo_allowed}, {hi_allowed}]")
        if isinstance(chain, dict) and isinstance(chain.get(parts[0]), dict):
            current = chain[parts[0]].get(parts[1])
            if _is_number(current) and not (lo <= current <= hi):
                col.add(bpath, f"current value chain.{key}={current!r} lies outside these bounds")


def validate_config(cfg: dict) -> None:
    """Raise :class:`ConfigError` listing every schema violation; return None when valid."""
    col = _Collector()
    if not col.mapping(cfg, "<root>"):
        raise ConfigError(col.errors[0])
    metadata = tuple(key for key in cfg if isinstance(key, str) and key.startswith("_"))
    col.keys(cfg, "", required=_TOP_LEVEL_KEYS, optional=_OPTIONAL_TOP_LEVEL_KEYS + metadata)
    if "control" in cfg and col.mapping(cfg["control"], "control"):
        col.keys(cfg["control"], "control", required=("socket_path",))
        if "socket_path" in cfg["control"]:
            col.string(cfg["control"]["socket_path"], "control.socket_path")
    sample_rate = cfg.get("sample_rate")
    if "sample_rate" in cfg:
        col.integer(sample_rate, "sample_rate", 8000, 192000)
    if "block_size" in cfg:
        col.integer(cfg["block_size"], "block_size", 16, 8192)
    if "input" in cfg and col.mapping(cfg["input"], "input"):
        col.keys(cfg["input"], "input", required=("device", "channel"))
        if "device" in cfg["input"]:
            col.string(cfg["input"]["device"], "input.device")
        if "channel" in cfg["input"]:
            col.integer(cfg["input"]["channel"], "input.channel", 0, 63)
    if "output" in cfg and col.mapping(cfg["output"], "output"):
        col.keys(cfg["output"], "output", required=("device", "channels"))
        if "device" in cfg["output"]:
            col.string(cfg["output"]["device"], "output.device")
        if "channels" in cfg["output"]:
            col.integer(cfg["output"]["channels"], "output.channels", 1, 8)
    chain = cfg.get("chain")
    if "chain" in cfg and col.mapping(chain, "chain"):
        col.keys(chain, "chain", required=STAGE_ORDER)
        for name in STAGE_ORDER:
            if name in chain:
                _validate_stage(col, name, chain[name], sample_rate)
    if "adaptive" in cfg:
        _validate_adaptive(col, cfg["adaptive"], chain)
    if col.errors:
        raise ConfigError("invalid configuration:\n  - " + "\n  - ".join(col.errors))
