"""Live control of the running chain (Logi MX Creative Console, ``ctl`` CLI).

``LiveControl`` turns console commands into offsets on the *calibrated* values
(docs/06 section 2: in a meeting one corrects symptoms, not parameters):

* ``loudness_lu``  -> ``normalizer.target_lufs`` = base + offset (clamped to -24...-12 LUFS)
* ``room_db``      -> expander threshold: + = less room = threshold higher
* ``deess_db``     -> de-esser threshold: - = less sibilance = threshold lower
* ``intensity``    -> compressor threshold -2 dB per step and de-esser threshold -1 dB per step
* ``tone_db``      -> ``eq.tilt_db`` (+ = brighter)

With an :class:`~voicechain.adaptive.AdaptiveController` the threshold offsets go
into the controller's setpoints and rest values (``set_user_offsets``), so the
regulation keeps them instead of regulating them away; the change is also
applied to the parameter at once so a key press is audible immediately.
Without a controller the offsets are applied directly to the calibrated values.
Offsets live only in the running session; ``snapshot`` writes them into a YAML.

Every command is answered with a dict (docs/06 section 4): ``{"ok": True,
"state": {...}, "message": "<kurz, deutsch>"}`` or ``{"ok": False, "error": "..."}``.
``handle`` never raises: the control channel runs in the service thread next to
the audio stream and must not take it down.

``state`` keys: offsets (dict of the five offsets), target_lufs, frozen, muted,
bypass, low_snr, snr_lu, loudness_lufs, adaptive_enabled, learning, tilt_db,
expander_threshold_db, deesser_threshold_db, compressor_threshold_db.
"""
from __future__ import annotations

import copy
import datetime as _dt
import json
import time
from pathlib import Path

from voicechain.calibrate import clamp, parse_bounds, tracked_bounds
from voicechain.config import default_config_path, save_config

# --------------------------------------------------------------------------- #
# Contract constants (docs/06 section 4)
# --------------------------------------------------------------------------- #

#: name -> (min, max, step per key press)
OFFSETS: dict[str, tuple[float, float, float]] = {
    "loudness_lu": (-6.0, 6.0, 0.5),
    "room_db": (-6.0, 6.0, 2.0),
    "deess_db": (-6.0, 6.0, 2.0),
    "tone_db": (-3.0, 3.0, 1.0),
    "intensity": (-3.0, 3.0, 1.0),
}
#: Normalizer target range reachable with the loudness offset.
LOUDNESS_TARGET_BOUNDS_LUFS = (-24.0, -12.0)
#: One intensity step: compressor threshold -2 dB (denser), de-esser threshold -1 dB.
INTENSITY_COMPRESSOR_DB_PER_STEP = -2.0
INTENSITY_DEESSER_DB_PER_STEP = -1.0
#: EQ tilt range of the Equalizer's ``tilt_db`` parameter.
TILT_BOUNDS_DB = (-3.0, 3.0)
#: Default snapshot file (relative to the repository root, like the CLI defaults).
DEFAULT_SNAPSHOT_PATH = Path("config") / "chain.live.yaml"

COMMANDS: dict[str, set[str]] = {
    "offset": {"name", "delta"},
    "set_offset": {"name", "value"},
    "reset": set(),
    "freeze": {"on"},
    "mute": {"on"},
    "bypass": {"on"},
    "relearn": set(),
    "snapshot": {"path"},
    "marker": {"text"},
    "status": set(),
}

OFFSET_LABELS = {
    "loudness_lu": "Lautheit",
    "room_db": "Raum",
    "deess_db": "Zischen",
    "tone_db": "Klang",
    "intensity": "Intensität",
}


def _fmt(value: float, unit: str = "dB", signed: bool = True) -> str:
    text = f"{value:+.1f}" if signed else f"{value:.1f}"
    return text.replace(".", ",").replace(",0", "") + (f" {unit}" if unit else "")


def _repo_root() -> Path:
    return default_config_path().parent.parent


class LiveControl:
    """Offsets, reset, freeze, relearn, snapshot, marker and status for the live chain."""

    def __init__(self, cfg: dict, chain, controller=None, hooks=None, clock=time.monotonic,
                 analyzer=None):
        self.cfg = cfg
        self.chain = chain
        self.controller = controller
        self.hooks = dict(hooks or {})
        self.clock = clock
        self.analyzer = analyzer
        self.bounds = parse_bounds(cfg)
        chain_cfg = cfg["chain"]
        #: Calibrated base values (the loaded configuration).
        self.base = {
            "target_lufs": float(chain_cfg["normalizer"]["target_lufs"]),
            "expander.threshold_db": float(chain_cfg["expander"]["threshold_db"]),
            "deesser.threshold_db": float(chain_cfg["deesser"]["threshold_db"]),
            "compressor.threshold_db": float(chain_cfg["compressor"]["threshold_db"]),
            "tilt_db": float(chain_cfg["eq"].get("tilt_db", 0.0)),
        }
        self.offsets: dict[str, float] = {name: 0.0 for name in OFFSETS}
        self.muted = False
        self.bypass = False

    # ------------------------------------------------------------------ public
    def handle(self, cmd: dict) -> dict:
        """Execute one command; never raises (errors come back as ``ok: False``)."""
        try:
            return self._dispatch(cmd)
        except Exception as exc:  # noqa: BLE001 - API boundary towards the socket/CLI:
            # a failing key press must be reported, never take the service thread down.
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def state(self) -> dict:
        meters = self.chain.get_meters()
        norm = meters.get("normalizer", {}) if isinstance(meters, dict) else {}
        controller = self.controller
        return {
            "offsets": dict(self.offsets),
            "target_lufs": float(self.chain.get_param("normalizer", "target_lufs")),
            "frozen": bool(controller.frozen) if controller is not None else False,
            "muted": self.muted,
            "bypass": self.bypass,
            "low_snr": bool(norm.get("low_snr", 0.0)),
            "snr_lu": self._float_or_none(norm.get("snr_lu")),
            "loudness_lufs": self._float_or_none(norm.get("loudness_lufs")),
            "adaptive_enabled": controller is not None,
            "learning": bool(controller.learning) if controller is not None else False,
            "tilt_db": self._tilt_or_none(),
            "expander_threshold_db": float(self.chain.get_param("expander", "threshold_db")),
            "deesser_threshold_db": float(self.chain.get_param("deesser", "threshold_db")),
            "compressor_threshold_db": float(self.chain.get_param("compressor", "threshold_db")),
        }

    # ---------------------------------------------------------------- dispatch
    def _dispatch(self, cmd: dict) -> dict:
        if not isinstance(cmd, dict):
            return self._error(f"Kommando muss ein Objekt sein, nicht {type(cmd).__name__}")
        name = cmd.get("cmd")
        if name not in COMMANDS:
            return self._error(f"Unbekanntes Kommando: {name!r}; bekannt: {', '.join(COMMANDS)}")
        unknown = set(cmd) - {"cmd"} - COMMANDS[name]
        if unknown:
            return self._error(f"Unbekannte Felder für '{name}': {', '.join(sorted(unknown))}")
        handler = getattr(self, f"_cmd_{name}")
        return handler(cmd)

    @staticmethod
    def _error(text: str) -> dict:
        return {"ok": False, "error": text}

    def _ok(self, message: str) -> dict:
        return {"ok": True, "state": self.state(), "message": message}

    # --------------------------------------------------------------- commands
    def _cmd_offset(self, cmd: dict) -> dict:
        name, spec = self._offset_spec(cmd)
        delta = float(cmd["delta"])
        lo, hi, step = spec
        # One key press moves by at most one step; the result stays inside the range.
        delta = clamp(delta, -step, step)
        return self._apply_offset(name, clamp(self.offsets[name] + delta, lo, hi))

    def _cmd_set_offset(self, cmd: dict) -> dict:
        name, spec = self._offset_spec(cmd)
        lo, hi, _ = spec
        return self._apply_offset(name, clamp(float(cmd["value"]), lo, hi))

    def _offset_spec(self, cmd: dict) -> tuple[str, tuple[float, float, float]]:
        name = cmd.get("name")
        if name not in OFFSETS:
            raise ValueError(f"Unbekannter Offset: {name!r}; bekannt: {', '.join(OFFSETS)}")
        for field in ("delta", "value"):
            if field in cmd and isinstance(cmd[field], bool):
                raise ValueError(f"'{field}' muss eine Zahl sein")
        return name, OFFSETS[name]

    def _apply_offset(self, name: str, value: float) -> dict:
        if name == "intensity":
            value = float(round(value))
        old = self.offsets[name]
        self.offsets[name] = value
        if name == "loudness_lu":
            target = self._loudness_target()
            self.chain.set_param("normalizer", "target_lufs", target)
            text = f"{OFFSET_LABELS[name]} {_fmt(value, 'LU')} (Ziel {_fmt(target, 'LUFS', signed=False)})"
        elif name == "tone_db":
            tilt = clamp(self.base["tilt_db"] + value, *TILT_BOUNDS_DB)
            try:
                self.chain.set_param("eq", "tilt_db", tilt)
            except (KeyError, ValueError) as exc:
                self.offsets[name] = old
                raise ValueError(f"Klang-Tilt in dieser Kette nicht verfügbar: {exc}") from exc
            text = f"{OFFSET_LABELS[name]} {_fmt(value)} ({'heller' if value > 0 else 'wärmer' if value < 0 else 'neutral'})"
        else:
            self._apply_threshold_offsets()
            text = {
                "room_db": f"Raum {_fmt(value)} ({'weniger Raum' if value > 0 else 'mehr Raum' if value < 0 else 'neutral'}, "
                           f"Expander {_fmt(self.chain.get_param('expander', 'threshold_db'), 'dBFS', signed=False)})",
                "deess_db": f"Zischen {_fmt(value)} ({'weniger Zischen' if value < 0 else 'mehr Zischen' if value > 0 else 'neutral'}, "
                            f"De-Esser {_fmt(self.chain.get_param('deesser', 'threshold_db'), 'dBFS', signed=False)})",
                "intensity": f"Intensität {int(value):+d} ({'dichter' if value > 0 else 'natürlicher' if value < 0 else 'neutral'}, "
                             f"Kompressor {_fmt(self.chain.get_param('compressor', 'threshold_db'), 'dBFS', signed=False)})",
            }[name]
        return self._ok(text)

    def _loudness_target(self) -> float:
        return clamp(self.base["target_lufs"] + self.offsets["loudness_lu"], *LOUDNESS_TARGET_BOUNDS_LUFS)

    def threshold_offsets(self) -> dict[str, float]:
        """Offsets in dB per tracked threshold, combined from room/deess/intensity."""
        intensity = self.offsets["intensity"]
        return {
            "expander.threshold_db": self.offsets["room_db"],
            "deesser.threshold_db": self.offsets["deess_db"] + INTENSITY_DEESSER_DB_PER_STEP * intensity,
            "compressor.threshold_db": INTENSITY_COMPRESSOR_DB_PER_STEP * intensity,
        }

    def _apply_threshold_offsets(self) -> None:
        """Push the threshold offsets to the chain (and the controller's setpoints)."""
        offsets = self.threshold_offsets()
        previous = getattr(self, "_applied_threshold_offsets", {k: 0.0 for k in offsets})
        for key, value in offsets.items():
            stage, name = key.split(".", 1)
            lo, hi = tracked_bounds(self.bounds, stage, name)
            if self.controller is None:
                new = clamp(self.base[key] + value, lo, hi)
            else:
                # Immediate, audible step by the change of the offset; the controller
                # then holds the new setpoint (target + offset) from the next interval.
                new = clamp(float(self.chain.get_param(stage, name)) + (value - previous[key]), lo, hi)
            if abs(new - float(self.chain.get_param(stage, name))) > 1e-9:
                self.chain.set_param(stage, name, new)
        self._applied_threshold_offsets = dict(offsets)
        if self.controller is not None:
            self.controller.set_user_offsets(offsets)

    def _cmd_reset(self, cmd: dict) -> dict:
        self.offsets = {name: 0.0 for name in OFFSETS}
        self._applied_threshold_offsets = {k: 0.0 for k in self.threshold_offsets()}
        self.chain.set_param("normalizer", "target_lufs", self.base["target_lufs"])
        if self._tilt_or_none() is not None:
            self.chain.set_param("eq", "tilt_db", self.base["tilt_db"])
        if self.controller is not None:
            self.controller.set_user_offsets({})
            self.controller.apply_rest_values(reason="reset")
        else:
            for key in ("expander.threshold_db", "deesser.threshold_db", "compressor.threshold_db"):
                stage, name = key.split(".", 1)
                self.chain.set_param(stage, name, self.base[key])
        self._call_hook("mute", False)
        self._call_hook("bypass", False)
        return self._ok("Reset: Kalibrierzustand, Offsets 0, Mute und Bypass aus")

    def _cmd_freeze(self, cmd: dict) -> dict:
        if self.controller is None:
            return self._error("Adaptive Regelung ist nicht aktiv; Freeze hat keine Wirkung")
        on = self._resolve_toggle(cmd.get("on"), self.controller.frozen)
        self.controller.freeze(on)
        return self._ok("Regelung eingefroren (FROZEN)" if on else "Regelung läuft weiter")

    def _cmd_mute(self, cmd: dict) -> dict:
        on = self._resolve_toggle(cmd.get("on"), self.muted)
        self._call_hook("mute", on, required=True)
        return self._ok("MUTE an: Ausgang stumm" if on else "MUTE aus")

    def _cmd_bypass(self, cmd: dict) -> dict:
        on = self._resolve_toggle(cmd.get("on"), self.bypass)
        self._call_hook("bypass", on, required=True)
        return self._ok("BYPASS an: Kette umgangen (nur Trim + Limiter)" if on else "BYPASS aus: Kette aktiv")

    def _cmd_relearn(self, cmd: dict) -> dict:
        if self.controller is None:
            return self._error("Adaptive Regelung ist nicht aktiv; nichts neu zu lernen")
        self.controller.relearn()
        if self.analyzer is not None:
            self.analyzer.reset()
        return self._ok(f"Lernphase neu gestartet ({self.controller.learn_s:.0f} s), Offsets bleiben")

    def _cmd_snapshot(self, cmd: dict) -> dict:
        path = cmd.get("path")
        path = Path(path) if path else DEFAULT_SNAPSHOT_PATH
        if not path.is_absolute():
            path = _repo_root() / path
        snapshot = self.snapshot_config()
        save_config(snapshot, path)
        return self._ok(f"Snapshot gespeichert: {path}")

    def snapshot_config(self) -> dict:
        """Loaded configuration with base values + offsets written into the chain section."""
        cfg = copy.deepcopy(self.cfg)
        chain = cfg["chain"]
        chain["normalizer"]["target_lufs"] = round(self._loudness_target(), 2)
        for key, value in self.threshold_offsets().items():
            stage, name = key.split(".", 1)
            lo, hi = tracked_bounds(self.bounds, stage, name)
            chain[stage][name] = round(clamp(self.base[key] + value, lo, hi), 2)
        if self.offsets["tone_db"] != 0.0 or "tilt_db" in chain["eq"]:
            chain["eq"]["tilt_db"] = round(clamp(self.base["tilt_db"] + self.offsets["tone_db"], *TILT_BOUNDS_DB), 2)
        cfg["_live"] = {
            "created": _dt.datetime.now().isoformat(timespec="seconds"),
            "offsets": dict(self.offsets),
        }
        return cfg

    def _cmd_marker(self, cmd: dict) -> dict:
        text = cmd.get("text")
        if not isinstance(text, str) or not text.strip():
            return self._error("Marker braucht einen Text")
        entry = {"event": "marker", "text": text.strip()}
        if self.controller is not None:
            self.controller.log_event(entry)
        else:
            log_path = self.cfg["adaptive"]["log_path"]
            path = Path(log_path)
            if not path.is_absolute():
                path = _repo_root() / path
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"t": float(self.clock()), **entry}, ensure_ascii=False) + "\n")
        return self._ok(f"Marker gesetzt: {text.strip()}")

    def _cmd_status(self, cmd: dict) -> dict:
        st = self.state()
        active = [f"{OFFSET_LABELS[n]} {_fmt(v, 'LU' if n == 'loudness_lu' else '' if n == 'intensity' else 'dB')}"
                  for n, v in st["offsets"].items() if v != 0.0]
        parts = [
            f"Lautheit {_fmt(st['loudness_lufs'], 'LUFS', signed=False) if st['loudness_lufs'] is not None else '–'}"
            f" (Ziel {_fmt(st['target_lufs'], 'LUFS', signed=False)})",
            f"Störabstand {_fmt(st['snr_lu'], 'LU', signed=False) if st['snr_lu'] is not None else '–'}"
            + (" SNR-LOW" if st["low_snr"] else ""),
            "Offsets: " + (", ".join(active) if active else "keine"),
        ]
        flags = [f for f, on in (("FROZEN", st["frozen"]), ("MUTE", st["muted"]), ("BYPASS", st["bypass"]),
                                 ("LERNPHASE", st["learning"])) if on]
        if flags:
            parts.append(" ".join(flags))
        if not st["adaptive_enabled"]:
            parts.append("Regelung aus")
        return self._ok(" | ".join(parts))

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _resolve_toggle(on, current: bool) -> bool:
        if on == "toggle" or on is None:
            return not current
        if isinstance(on, bool):
            return on
        raise ValueError(f"'on' muss true, false oder \"toggle\" sein, nicht {on!r}")

    def _call_hook(self, name: str, on: bool, required: bool = False) -> None:
        hook = self.hooks.get(name)
        if hook is None:
            if required:
                raise ValueError(f"Funktion '{name}' ist in dieser Runtime nicht verfügbar (kein Hook)")
            return
        hook(bool(on))
        if name == "mute":
            self.muted = bool(on)
        elif name == "bypass":
            self.bypass = bool(on)

    def _tilt_or_none(self) -> float | None:
        try:
            return float(self.chain.get_param("eq", "tilt_db"))
        except KeyError:
            return None

    @staticmethod
    def _float_or_none(value) -> float | None:
        if value is None:
            return None
        value = float(value)
        return value if value == value else None   # NaN -> None
