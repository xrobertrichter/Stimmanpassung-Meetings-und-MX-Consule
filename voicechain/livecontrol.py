"""Live control of the running chain (Logi MX Creative Console, ``ctl`` CLI).

``LiveControl`` turns console commands into offsets on the *calibrated* values
(docs/06 section 2: in a meeting one corrects symptoms, not parameters):

* ``loudness_lu``  -> ``normalizer.target_lufs`` = base + offset (clamped to -24...-14 LUFS)
* ``room_db``      -> expander threshold: + = less room = threshold higher; the speech
  cap (speech - 12 dB) stays on top of the offset
* ``deess_db``     -> de-esser threshold: - = less sibilance = threshold lower
* ``intensity``    -> compressor threshold -2 dB per step (the de-esser is not coupled)
* ``tone_db``      -> ``eq.tilt_db`` (+ = brighter)

With an :class:`~voicechain.adaptive.AdaptiveController` the threshold offsets go
into the controller's setpoints and rest values (``set_user_offsets``), so the
regulation keeps them instead of regulating them away; the change is also
applied to the parameter at once so a key press is audible immediately.
Without a controller the offsets are applied directly to the calibrated values.
Offsets live only in the running session; ``snapshot`` writes the values the
chain actually uses (tracked thresholds plus offsets) into a YAML.

Stored offsets always reflect the *effective* value: when the range, the
``adaptive.bounds`` or the speech cap stop a step, the answer is ``ok: False``
("am Anschlag") or names the limitation, and the offset is the value that
really took effect (read back from the clamped parameter).

Every command is answered with a dict (docs/06 section 4): ``{"ok": True,
"state": {...}, "message": "<kurz, deutsch>"}`` or ``{"ok": False, "error": "..."}``.
``handle`` never raises and never returns NaN/Infinity: the control channel runs
in the service thread next to the audio stream and must not take it down.

Meters for answers are read with ``chain.peek_meters()`` (non-draining), so a
key press never empties the accumulators the regulation reads at its interval.

``state`` keys: offsets (dict of the five offsets), target_lufs, frozen, muted,
bypass, processing, low_snr, snr_lu, loudness_lufs, adaptive_enabled, learning,
tilt_db, expander_threshold_db, deesser_threshold_db, compressor_threshold_db.

Hooks (all ``fn(bool)``, provided by the runtime): ``mute`` (30 ms output fade),
``bypass`` (``Chain.set_bypass``), ``processing`` (kill switch:
``Chain.set_processing`` plus pausing analyzer and regulation).

Reset = operating state back (offsets 0, loudness and tilt to calibration,
processing on, mute/bypass off, freeze off, normalizer gain limit and protection
state to configuration); the thresholds the regulation has tracked stay, only
the live offsets are taken off them. Relearn = thresholds back to the calibrated
rest values (plus offsets), learning phase restarted, analyzer history cleared.
"""
from __future__ import annotations

import copy
import datetime as _dt
import json
import math
import time
from pathlib import Path

from voicechain.calibrate import clamp, parse_bounds, tracked_bounds
from voicechain.config import default_config_path, save_config

# --------------------------------------------------------------------------- #
# Contract constants (docs/06 section 4 and 7)
# --------------------------------------------------------------------------- #

#: name -> (min, max, step per key press)
OFFSETS: dict[str, tuple[float, float, float]] = {
    "loudness_lu": (-6.0, 6.0, 0.5),
    "room_db": (-6.0, 6.0, 2.0),
    "deess_db": (-6.0, 6.0, 2.0),
    "tone_db": (-3.0, 3.0, 1.0),
    "intensity": (-3.0, 3.0, 1.0),
}
#: Normalizer target range reachable with the loudness offset: above -14 LUFS the
#: conference systems' own AGC only turns the level down again (PO review).
LOUDNESS_TARGET_BOUNDS_LUFS = (-24.0, -14.0)
#: One intensity step: compressor threshold -2 dB (denser).
INTENSITY_COMPRESSOR_DB_PER_STEP = -2.0
#: EQ tilt range of the Equalizer's ``tilt_db`` parameter.
TILT_BOUNDS_DB = (-3.0, 3.0)
#: Default snapshot file (relative to the repository root, like the CLI defaults).
DEFAULT_SNAPSHOT_PATH = Path("config") / "chain.live.yaml"
#: Tolerance for "the parameter did not move" (parameters are stored as floats).
EPS = 1e-6

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
    "processing": {"on"},
}

OFFSET_LABELS = {
    "loudness_lu": "Lautheit",
    "room_db": "Raum",
    "deess_db": "Zischen",
    "tone_db": "Klang",
    "intensity": "Intensität",
}
#: Offset -> (stage, parameter) of the threshold it moves.
THRESHOLD_OF = {
    "room_db": ("expander", "threshold_db"),
    "deess_db": ("deesser", "threshold_db"),
    "intensity": ("compressor", "threshold_db"),
}
STAGE_LABELS = {"expander": "Expander", "deesser": "De-Esser", "compressor": "Kompressor"}
TRACKED_KEYS = ("expander.threshold_db", "deesser.threshold_db", "compressor.threshold_db")


def _fmt(value: float, unit: str = "dB", signed: bool = True) -> str:
    text = f"{value:+.1f}" if signed else f"{value:.1f}"
    return text.replace(".", ",").replace(",0", "") + (f" {unit}" if unit else "")


def _repo_root() -> Path:
    return default_config_path().parent.parent


def _finite(value, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"'{field}' muss eine Zahl sein")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"'{field}' muss endlich sein (kein NaN/Infinity)")
    return value


class LiveControl:
    """Offsets, reset, freeze, relearn, snapshot, marker, status and kill switch for the live chain."""

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
            "normalizer.max_gain_db": float(chain_cfg["normalizer"]["max_gain_db"]),
            "tilt_db": float(chain_cfg["eq"].get("tilt_db", 0.0)),
        }
        self.offsets: dict[str, float] = {name: 0.0 for name in OFFSETS}
        self.muted = False
        self.bypass = False
        #: Kill switch state (hook "processing"): False = only input trim and limiter run.
        self.processing = True
        #: Freeze state of the controller before the kill switch turned processing off,
        #: restored when it is turned on again (None = processing is on).
        self._frozen_before_off: bool | None = None

    # ------------------------------------------------------------------ public
    def handle(self, cmd: dict) -> dict:
        """Execute one command; never raises (errors come back as ``ok: False``)."""
        try:
            return self._dispatch(cmd)
        except Exception as exc:  # noqa: BLE001 - API boundary towards the socket/CLI:
            # a failing key press must be reported, never take the service thread down.
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def state(self) -> dict:
        self._sync_room_offset()
        meters = self.chain.peek_meters()
        norm = meters.get("normalizer", {}) if isinstance(meters, dict) else {}
        controller = self.controller
        return {
            "offsets": {name: self._finite_or_none(v) for name, v in self.offsets.items()},
            "target_lufs": float(self.chain.get_param("normalizer", "target_lufs")),
            "frozen": bool(controller.frozen) if controller is not None else False,
            "muted": self.muted,
            "bypass": self.bypass,
            "processing": self.processing,
            "low_snr": bool(norm.get("low_snr", 0.0)),
            "snr_lu": self._finite_or_none(norm.get("snr_lu")),
            "loudness_lufs": self._finite_or_none(norm.get("loudness_lufs")),
            "adaptive_enabled": controller is not None,
            "learning": bool(controller.learning) if controller is not None else False,
            "tilt_db": self._tilt_or_none(),
            "expander_threshold_db": float(self.chain.get_param("expander", "threshold_db")),
            "deesser_threshold_db": float(self.chain.get_param("deesser", "threshold_db")),
            "compressor_threshold_db": float(self.chain.get_param("compressor", "threshold_db")),
        }

    def threshold_offsets(self) -> dict[str, float]:
        """Effective offsets in dB per tracked threshold."""
        return {
            "expander.threshold_db": self.offsets["room_db"],
            "deesser.threshold_db": self.offsets["deess_db"],
            "compressor.threshold_db": INTENSITY_COMPRESSOR_DB_PER_STEP * self.offsets["intensity"],
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
        if not self.processing:
            message += " – Verarbeitung AUS: wirkt beim Einschalten" if "Verarbeitung" not in message else ""
        return {"ok": True, "state": self.state(), "message": message}

    # ----------------------------------------------------------------- offsets
    def _cmd_offset(self, cmd: dict) -> dict:
        name = self._offset_name(cmd)
        self._sync_room_offset()
        lo, hi, step = OFFSETS[name]
        delta = _finite(cmd.get("delta"), "delta")
        # One key press moves by at most one step; the result stays inside the range.
        delta = clamp(delta, -step, step)
        return self._apply_offset(name, clamp(self.offsets[name] + delta, lo, hi))

    def _cmd_set_offset(self, cmd: dict) -> dict:
        name = self._offset_name(cmd)
        self._sync_room_offset()
        lo, hi, _ = OFFSETS[name]
        return self._apply_offset(name, clamp(_finite(cmd.get("value"), "value"), lo, hi), absolute=True)

    def _sync_room_offset(self) -> None:
        """Take over the room offset the controller may have shrunk through speech_cap
        steps, so the stored value never drifts from the effective one."""
        if self.controller is not None:
            self.offsets["room_db"] = float(self.controller.user_offsets.get("expander.threshold_db",
                                                                              self.offsets["room_db"]))

    @staticmethod
    def _offset_name(cmd: dict) -> str:
        name = cmd.get("name")
        if name not in OFFSETS:
            raise ValueError(f"Unbekannter Offset: {name!r}; bekannt: {', '.join(OFFSETS)}")
        return name

    def _apply_offset(self, name: str, requested: float, absolute: bool = False) -> dict:
        """Apply ``requested`` (already inside the range); the stored offset becomes the
        value that really took effect. No change at all: a step (``offset``) is a stop ->
        ``ok: False`` "am Anschlag"; a ``set_offset`` to the value already in effect is a
        normal answer "unverändert" (the "back to default" press)."""
        if name == "intensity":
            requested = float(round(requested))
        previous = self.offsets[name]

        def unchanged(effect: str, value_text: str, limit: str = "") -> dict:
            if absolute:
                return self._ok(f"{OFFSET_LABELS[name]} unverändert ({OFFSET_LABELS[name]} {value_text}, {effect})")
            return self._error(f"{OFFSET_LABELS[name]} am Anschlag (wirksam: {effect}, {OFFSET_LABELS[name]} {value_text})"
                               + (f" – {limit}" if limit else ""))

        if name == "loudness_lu":
            target = clamp(self.base["target_lufs"] + requested, *LOUDNESS_TARGET_BOUNDS_LUFS)
            effective = target - self.base["target_lufs"]
            effect = f"Ziel {_fmt(target, 'LUFS', signed=False)}"
            limit = f"Ziel-Lautheit auf {_fmt(target, 'LUFS', signed=False)} begrenzt" if abs(effective - requested) > EPS else ""
            if abs(effective - previous) <= EPS:
                return unchanged(effect, _fmt(effective, "LU"))
            self.chain.set_param("normalizer", "target_lufs", target)
            self.offsets[name] = effective
            text = f"{OFFSET_LABELS[name]} {_fmt(effective, 'LU')} ({effect})"
        elif name == "tone_db":
            tilt = clamp(self.base["tilt_db"] + requested, *TILT_BOUNDS_DB)
            effective = tilt - self.base["tilt_db"]
            limit = f"Tilt auf {_fmt(tilt)} begrenzt" if abs(effective - requested) > EPS else ""
            if abs(effective - previous) <= EPS:
                return unchanged(f"Tilt {_fmt(tilt)}", _fmt(effective))
            try:
                self.chain.set_param("eq", "tilt_db", tilt)
            except (KeyError, ValueError) as exc:
                raise ValueError(f"Klang-Tilt in dieser Kette nicht verfügbar: {exc}") from exc
            self.offsets[name] = effective
            text = f"Klang {_fmt(effective)} ({'heller' if effective > 0 else 'wärmer' if effective < 0 else 'neutral'})"
        else:
            effective, limit = self._apply_threshold_offset(name, requested)
            if abs(effective - previous) <= EPS:
                stage, param = THRESHOLD_OF[name]
                value = _fmt(self.chain.get_param(stage, param), 'dBFS', signed=False)
                unit = "" if name == "intensity" else "dB"
                return unchanged(f"{STAGE_LABELS[stage]} {value}", _fmt(effective, unit), limit)
            self.offsets[name] = effective
            text = self._threshold_text(name, effective)
        if limit:
            text += f" – {limit}"
        return self._ok(text)

    def _threshold_text(self, name: str, value: float) -> str:
        stage, param = THRESHOLD_OF[name]
        current = _fmt(self.chain.get_param(stage, param), "dBFS", signed=False)
        if name == "room_db":
            sense = "weniger Raum" if value > 0 else "mehr Raum" if value < 0 else "neutral"
            return f"Raum {_fmt(value)} ({sense}, Expander {current})"
        if name == "deess_db":
            sense = "weniger Zischen" if value < 0 else "mehr Zischen" if value > 0 else "neutral"
            return f"Zischen {_fmt(value)} ({sense}, De-Esser {current})"
        sense = "dichter" if value > 0 else "natürlicher" if value < 0 else "neutral"
        return f"Intensität {_fmt(value, '')} ({sense}, Kompressor {current})"

    def _apply_threshold_offset(self, name: str, requested: float) -> tuple[float, str]:
        """Move the threshold behind ``name`` for the requested offset; returns the
        effective offset (read back from the parameter) and a limitation text."""
        stage, param = THRESHOLD_OF[name]
        lo, hi = tracked_bounds(self.bounds, stage, param)
        per_unit = INTENSITY_COMPRESSOR_DB_PER_STEP if name == "intensity" else 1.0
        previous = self.offsets[name]
        current = float(self.chain.get_param(stage, param))
        if self.controller is None:
            wanted = self.base[f"{stage}.{param}"] + per_unit * requested
        else:
            # Immediate, audible step by the change of the offset; the controller then
            # holds the new setpoint (target + offset) from the next interval.
            wanted = current + per_unit * (requested - previous)
        new = clamp(wanted, lo, hi)
        limit = ""
        if abs(new - wanted) > EPS:
            limit = f"durch Grenze auf {_fmt(new, 'dBFS', signed=False)} begrenzt"
        if name == "room_db":
            cap = self._expander_cap()
            # Raising never passes the speech cap; a threshold already above it (the
            # regulation pulls it down) is not raised further either.
            if cap is not None and new > max(cap, current) + EPS:
                new = max(cap, current)
                limit = f"durch Sprach-Kappe auf {_fmt(cap, 'dBFS', signed=False)} begrenzt"
        if abs(new - current) > EPS:
            self.chain.set_param(stage, param, new)
        if self.controller is None:
            effective = (new - self.base[f"{stage}.{param}"]) / per_unit
        else:
            effective = previous + (new - current) / per_unit
        offsets = self.threshold_offsets()
        offsets[f"{stage}.{param}"] = per_unit * effective
        if self.controller is not None:
            self.controller.set_user_offsets(offsets)
        return effective, limit

    def _expander_cap(self) -> float | None:
        """Speech cap (speech - 12 dB after trim) from the controller's last analyzer
        snapshot; None without regulation or before the first decision (then only
        the bounds limit the step)."""
        if self.controller is None:
            return None
        cap = self.controller.last_expander_cap
        if cap is None:
            return None
        lo, hi = tracked_bounds(self.bounds, "expander", "threshold_db")
        return clamp(cap, lo, hi)

    # ------------------------------------------------------- reset / relearn
    def _cmd_reset(self, cmd: dict) -> dict:
        """Operating state back: offsets 0, loudness/tilt to calibration, processing on,
        mute/bypass/freeze off, normalizer limit and protection to configuration; the
        thresholds the regulation has tracked stay (only the live offsets come off)."""
        if not self.processing:
            self._set_processing(True)
        applied = self.threshold_offsets()
        self.offsets = {name: 0.0 for name in OFFSETS}
        self.chain.set_param("normalizer", "target_lufs", self.base["target_lufs"])
        if self._tilt_or_none() is not None:
            self.chain.set_param("eq", "tilt_db", self.base["tilt_db"])
        if self.controller is not None:
            for key, offset in applied.items():
                stage, param = key.split(".", 1)
                lo, hi = tracked_bounds(self.bounds, stage, param)
                current = float(self.chain.get_param(stage, param))
                new = clamp(current - offset, lo, hi)
                if abs(new - current) > EPS:
                    self.chain.set_param(stage, param, new)
            self.controller.set_user_offsets({})
            self.controller.freeze(False)
            self.controller.reset_protection()
        else:
            for key in TRACKED_KEYS:
                stage, param = key.split(".", 1)
                self.chain.set_param(stage, param, self.base[key])
            self.chain.set_param("normalizer", "max_gain_db", self.base["normalizer.max_gain_db"])
        self._frozen_before_off = None
        self._call_hook("mute", False)
        self._call_hook("bypass", False)
        return self._ok("Reset: Offsets 0, Lautheit und Klang auf Kalibrierwert, Freeze/Mute/Bypass aus, "
                        "Verarbeitung an; nachgeführte Schwellen bleiben")

    def _cmd_relearn(self, cmd: dict) -> dict:
        if self.controller is None:
            return self._error("Adaptive Regelung ist nicht aktiv; nichts neu zu lernen")
        if not self.processing:
            return self._error("Verarbeitung ist AUS; erst einschalten, dann neu lernen")
        self.controller.apply_rest_values(reason="relearn")
        self.controller.relearn()
        if self.analyzer is not None:
            self.analyzer.reset()
        return self._ok(f"Neu lernen: Regelung auf Kalibrierwerte, Lernphase {self.controller.learn_s:.0f} s, "
                        "Offsets bleiben")

    # ------------------------------------------------- freeze / mute / bypass / power
    def _cmd_freeze(self, cmd: dict) -> dict:
        if self.controller is None:
            return self._error("Adaptive Regelung ist nicht aktiv; Freeze hat keine Wirkung")
        if not self.processing:
            # While the kill switch is off the controller stays frozen; the wish is
            # remembered and becomes the state restored on power-up.
            on = self._resolve_toggle(cmd.get("on"), bool(self._frozen_before_off))
            self._frozen_before_off = on
            return self._ok("Regelung eingefroren (FROZEN)" if on else "Regelung läuft weiter")
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

    def _cmd_processing(self, cmd: dict) -> dict:
        on = self._resolve_toggle(cmd.get("on"), self.processing)
        self._set_processing(on)
        if on:
            return self._ok("Verarbeitung AN: Kette aktiv" + self._restored_hint())
        return self._ok("Verarbeitung AUS (nur Trim + Limiter, Prozessorlast gesenkt)")

    def _set_processing(self, on: bool) -> None:
        """Kill switch: hook first (the runtime switches the chain and pauses analyzer and
        regulation); with a controller its clock pauses and its freeze state is parked
        while off and restored when on again - without a new learning phase."""
        if on == self.processing:
            self._call_hook("processing", on, required=True)
            return
        if not on:
            if self.controller is not None:
                self._frozen_before_off = bool(self.controller.frozen)
                self.controller.freeze(True)
                self.controller.pause()
            self._call_hook("processing", False, required=True)
            return
        self._call_hook("processing", True, required=True)
        # Offsets taken while off are stored in the chain parameters already; re-send
        # the absolute ones so a stage reset on power-up cannot have dropped them.
        self.chain.set_param("normalizer", "target_lufs",
                             clamp(self.base["target_lufs"] + self.offsets["loudness_lu"], *LOUDNESS_TARGET_BOUNDS_LUFS))
        if self._tilt_or_none() is not None:
            self.chain.set_param("eq", "tilt_db", clamp(self.base["tilt_db"] + self.offsets["tone_db"], *TILT_BOUNDS_DB))
        if self.controller is not None:
            self.controller.set_user_offsets(self.threshold_offsets())
            self.controller.resume()
            if self._frozen_before_off is not None:
                self.controller.freeze(self._frozen_before_off)
        self._frozen_before_off = None

    def _restored_hint(self) -> str:
        if self.controller is None:
            return ""
        return ", Regelung eingefroren (FROZEN)" if self.controller.frozen else ", Regelung läuft weiter"

    # ------------------------------------------------------ snapshot / marker
    def _cmd_snapshot(self, cmd: dict) -> dict:
        path = cmd.get("path")
        path = Path(path) if path else DEFAULT_SNAPSHOT_PATH
        if not path.is_absolute():
            path = _repo_root() / path
        save_config(self.snapshot_config(), path)
        return self._ok(f"Snapshot gespeichert: {path}")

    def snapshot_config(self) -> dict:
        """Loaded configuration with the values the chain uses right now (tracked
        thresholds including regulation and offsets, target loudness, tilt)."""
        cfg = copy.deepcopy(self.cfg)
        chain = cfg["chain"]
        tracked = {}
        for key in TRACKED_KEYS:
            stage, param = key.split(".", 1)
            value = round(float(self.chain.get_param(stage, param)), 2)
            chain[stage][param] = value
            tracked[key] = value
        chain["normalizer"]["target_lufs"] = round(float(self.chain.get_param("normalizer", "target_lufs")), 2)
        tilt = self._tilt_or_none()
        if tilt is not None and (self.offsets["tone_db"] != 0.0 or "tilt_db" in chain["eq"]):
            chain["eq"]["tilt_db"] = round(tilt, 2)
        cfg["_live"] = {
            "created": _dt.datetime.now().isoformat(timespec="seconds"),
            "offsets": dict(self.offsets),
            "tracked": tracked,
            "base": {k: self.base[k] for k in TRACKED_KEYS},
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

    # ------------------------------------------------------------------ status
    def _cmd_status(self, cmd: dict) -> dict:
        st = self.state()
        active = [f"{OFFSET_LABELS[n]} {_fmt(v, 'LU' if n == 'loudness_lu' else '' if n == 'intensity' else 'dB')}"
                  for n, v in st["offsets"].items() if v]
        loud = _fmt(st["loudness_lufs"], "LUFS", signed=False) if st["loudness_lufs"] is not None else "unbekannt"
        snr = _fmt(st["snr_lu"], "LU", signed=False) if st["snr_lu"] is not None else "unbekannt"
        parts = [
            f"Lautheit {loud} (Ziel {_fmt(st['target_lufs'], 'LUFS', signed=False)})",
            f"Störabstand {snr}" + (" SNR-LOW" if st["low_snr"] else ""),
            "Offsets: " + (", ".join(active) if active else "keine"),
        ]
        flags = [f for f, on in (("OFF", not st["processing"]), ("FROZEN", st["frozen"]), ("MUTE", st["muted"]),
                                 ("BYPASS", st["bypass"]), ("LERNPHASE", st["learning"])) if on]
        if flags:
            parts.append(" ".join(flags))
        if not st["adaptive_enabled"]:
            parts.append("Regelung aus")
        return {"ok": True, "state": st, "message": " | ".join(parts)}

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
        elif name == "processing":
            self.processing = bool(on)

    def _tilt_or_none(self) -> float | None:
        try:
            return float(self.chain.get_param("eq", "tilt_db"))
        except KeyError:
            return None

    @staticmethod
    def _finite_or_none(value) -> float | None:
        """Finite float or None (NaN/Infinity never reach the protocol)."""
        if value is None:
            return None
        value = float(value)
        return value if math.isfinite(value) else None
