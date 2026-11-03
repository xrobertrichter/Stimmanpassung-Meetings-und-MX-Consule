"""Live audio runtime: Rode NT-USB -> processing chain -> BlackHole 2ch.

Design rules (see docs/00_Beauftragung.md, section 4.3):

* ``sounddevice`` (PortAudio) is imported lazily inside functions, so every other
  module of the package, and the whole test suite, works in a container without
  any audio library.
* The PortAudio callback does nothing but copy, analyze, process and copy back.
  It allocates no large buffers (the mono work buffer is pre-allocated; the
  chain's small per-block temporaries cost well under the 1 ms block budget),
  never logs and never touches the adaptive controller.
* The adaptive controller and the console status line run in a separate
  service thread (``threading``), decoupled from the audio thread. The same
  thread applies a freeze toggle requested by SIGUSR1.
* ``record_wav`` captures a calibration sample from the configured input
  device without the chain (24-bit WAV, 48 kHz mono).
"""

from __future__ import annotations

import math
import os
import signal
import sys
import threading
import time
import unicodedata
from typing import Callable

import numpy as np

# Letters that have no Unicode decomposition but which users type as plain ASCII
# (the Rode brand name is spelled "RØDE" by CoreAudio and "RODE" by most people).
_ASCII_FOLD = str.maketrans(
    {
        "Ø": "O",
        "ø": "o",
        "Æ": "AE",
        "æ": "ae",
        "Œ": "OE",
        "œ": "oe",
        "ß": "ss",
        "Ð": "D",
        "ð": "d",
        "Đ": "D",
        "đ": "d",
        "Ł": "L",
        "ł": "l",
        "Þ": "TH",
        "þ": "th",
    }
)

# Floor used when converting a zero signal to dB, so the status line never
# shows "-inf" or raises on log10(0); the same floor the stage meters use.
from voicechain.dsp import DB_FLOOR_DBFS as _DB_FLOOR  # noqa: E402

# How often the console status line is refreshed (seconds). Required by the
# assignment ("Konsolen-Statuszeile alle 1 s").
STATUS_INTERVAL_S = 1.0


class AudioBackendError(RuntimeError):
    """Raised when PortAudio / sounddevice is not usable on this machine."""


class DeviceNotFoundError(LookupError):
    """Raised when a device name matches no device or more than one device."""


def _import_sounddevice():
    """Import sounddevice lazily and translate a missing PortAudio into a clear error."""
    try:
        import sounddevice as sd
    except OSError as exc:
        # sounddevice raises OSError("PortAudio library not found") at import time.
        raise AudioBackendError(
            "PortAudio is not available: {exc}. "
            "On macOS install it with 'brew install portaudio' and reinstall the Python "
            "package with 'pip install --force-reinstall sounddevice'. "
            "Offline commands (process, calibrate, selftest, make-sample) work without it.".format(
                exc=exc
            )
        ) from exc
    except ImportError as exc:
        raise AudioBackendError(
            "The Python package 'sounddevice' is not installed: {exc}. "
            "Run 'pip install -r requirements.txt'.".format(exc=exc)
        ) from exc
    return sd


def fold_device_name(name: str) -> str:
    """Normalize a device name for matching.

    NFKC/NFKD normalization unifies composed and decomposed characters, combining
    marks are stripped (so "é" matches "e"), letters without a decomposition are
    folded by table (so "RØDE" matches "RODE"), and the result is case-folded.
    """
    text = unicodedata.normalize("NFC", name)
    text = text.translate(_ASCII_FOLD)
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return without_marks.casefold().strip()


def _to_dbfs(linear: float) -> float:
    if not math.isfinite(linear) or linear <= 0.0:
        return _DB_FLOOR
    return max(_DB_FLOOR, 20.0 * math.log10(linear))


def list_devices() -> list[dict]:
    """Return all PortAudio devices as plain dicts.

    Keys: index, name, max_input_channels, max_output_channels,
    default_samplerate, hostapi (host API name, e.g. "Core Audio").
    """
    sd = _import_sounddevice()
    try:
        raw_devices = sd.query_devices()
        hostapis = sd.query_hostapis()
    except Exception as exc:  # PortAudio reports errors as sd.PortAudioError
        raise AudioBackendError("Could not query audio devices: {exc}".format(exc=exc)) from exc

    devices: list[dict] = []
    for index, dev in enumerate(raw_devices):
        hostapi_index = int(dev.get("hostapi", -1))
        if 0 <= hostapi_index < len(hostapis):
            hostapi_name = str(hostapis[hostapi_index].get("name", hostapi_index))
        else:
            hostapi_name = str(hostapi_index)
        devices.append(
            {
                "index": int(dev.get("index", index)),
                "name": str(dev.get("name", "")),
                "max_input_channels": int(dev.get("max_input_channels", 0)),
                "max_output_channels": int(dev.get("max_output_channels", 0)),
                "default_samplerate": float(dev.get("default_samplerate", 0.0)),
                "hostapi": hostapi_name,
            }
        )
    return devices


def format_device_table(devices: list[dict]) -> str:
    """Render the device list as an aligned text table for the console."""
    lines = ["{:>5}  {:>3}  {:>3}  {:>8}  {:<12}  {}".format("index", "in", "out", "rate", "host api", "name")]
    for dev in devices:
        lines.append(
            "{:>5}  {:>3}  {:>3}  {:>8.0f}  {:<12}  {}".format(
                dev["index"],
                dev["max_input_channels"],
                dev["max_output_channels"],
                dev["default_samplerate"],
                dev["hostapi"][:12],
                dev["name"],
            )
        )
    return "\n".join(lines)


def find_device(name_substring: str, kind: str, devices: list[dict] | None = None) -> int:
    """Find exactly one device whose name contains ``name_substring``.

    ``kind`` is "input" or "output" and restricts the search to devices that
    have at least one channel of that direction. Matching is case-insensitive
    and Unicode-folded (see :func:`fold_device_name`). If the substring is a
    decimal number it is taken as a device index.

    Raises :class:`DeviceNotFoundError` with the full device list when no
    device or more than one device matches.
    """
    if kind not in ("input", "output"):
        raise ValueError("kind must be 'input' or 'output', got {!r}".format(kind))
    if devices is None:
        devices = list_devices()
    channel_key = "max_input_channels" if kind == "input" else "max_output_channels"
    candidates = [d for d in devices if d[channel_key] > 0]

    query = str(name_substring).strip()
    # isdecimal() + isascii(): plain 0-9 only; isdigit() would also accept "¹" or
    # full-width digits, which int() then rejects with a confusing ValueError.
    if query.isdecimal() and query.isascii():
        index = int(query)
        for dev in candidates:
            if dev["index"] == index:
                return index
        raise DeviceNotFoundError(
            "Device index {} is not an {} device.\nAvailable devices:\n{}".format(
                index, kind, format_device_table(devices)
            )
        )

    needle = fold_device_name(query)
    if not needle:
        raise DeviceNotFoundError("Empty device name given for the {} device.".format(kind))

    matches = [d for d in candidates if needle in fold_device_name(d["name"])]
    if len(matches) == 1:
        return matches[0]["index"]
    if not matches:
        raise DeviceNotFoundError(
            "No {} device matches {!r}.\nAvailable devices:\n{}".format(
                kind, name_substring, format_device_table(devices)
            )
        )
    # Prefer an exact (folded) name match, so "BlackHole 2ch" does not collide
    # with "BlackHole 2ch (Aggregate)".
    exact = [d for d in matches if fold_device_name(d["name"]) == needle]
    if len(exact) == 1:
        return exact[0]["index"]
    raise DeviceNotFoundError(
        "{} {} devices match {!r}; give a longer name or the index:\n{}".format(
            len(matches), kind, name_substring, format_device_table(matches)
        )
    )


class LiveEngine:
    """Duplex stream that runs the chain between an input and an output device.

    Parameters
    ----------
    cfg:
        Configuration dict (schema in docs/00_Beauftragung.md, section 4.1).
    chain:
        Object with ``process(x) -> y`` and ``get_meters() -> dict``.
    controller:
        Optional adaptive controller with ``update(meters) -> list[dict]``.
        It is called from the service thread, never from the audio callback.
    analyzer:
        Optional live analyzer with ``push(x)`` (audio thread) and
        ``snapshot() -> dict`` (service thread).
    on_status:
        Callable receiving the status line string once per second. Defaults
        to printing on stderr.
    input_device / output_device:
        Optional overrides (name substring or index) for the config values.
        The same device may be given for both (Aggregate Device).
    """

    def __init__(
        self,
        cfg: dict,
        chain,
        controller=None,
        analyzer=None,
        on_status: Callable[[str], None] | None = None,
        input_device: str | int | None = None,
        output_device: str | int | None = None,
    ) -> None:
        self.cfg = cfg
        self.chain = chain
        self.controller = controller
        self.analyzer = analyzer
        self.on_status = on_status if on_status is not None else self._print_status
        self.input_device = input_device
        self.output_device = output_device

        self.sample_rate = int(cfg["sample_rate"])
        self.block_size = int(cfg["block_size"])
        self.input_channel = int(cfg["input"]["channel"])
        self.output_channels = int(cfg["output"]["channels"])
        adaptive_cfg = cfg.get("adaptive", {})
        self.adaptive_interval_s = float(adaptive_cfg.get("interval_s", 2.0))
        if self.adaptive_interval_s <= 0.0:
            raise ValueError("adaptive.interval_s must be > 0")
        if self.input_channel < 0:
            raise ValueError("input.channel must be >= 0")
        if self.output_channels < 1:
            raise ValueError("output.channels must be >= 1")

        # Counters and meters written by the audio thread, read by the service thread.
        self.blocks_processed = 0
        self.xruns = 0
        self.input_overflows = 0
        self.output_underflows = 0
        self.callback_errors = 0
        self.last_error: BaseException | None = None
        self._in_peak = 0.0
        self._out_peak = 0.0
        self._in_rms_acc = 0.0
        self._in_rms_blocks = 0

        # Pre-allocated contiguous work buffer for the mono input block.
        self._in_buf = np.zeros(self.block_size, dtype=np.float32)

        self.last_changes: list[dict] = []
        self.last_change_text = "-"
        self.resolved_input_index: int | None = None
        self.resolved_output_index: int | None = None
        self.stream = None
        self._stop_event = threading.Event()
        self._service_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        # Freeze state of the adaptive controller as seen by the status line, and the
        # number of SIGUSR1 toggles not yet applied by the service thread.
        self.frozen = bool(getattr(controller, "frozen", False)) if controller is not None else False
        self._freeze_toggle_requests = 0
        self._signal_handlers_installed = False
        self._last_meters: dict | None = None

    # ------------------------------------------------------------------ devices
    def resolve_devices(self, devices: list[dict] | None = None) -> tuple[int, int]:
        """Resolve input and output device indices (override > config)."""
        if devices is None:
            devices = list_devices()
        in_query = self.input_device if self.input_device is not None else self.cfg["input"]["device"]
        out_query = self.output_device if self.output_device is not None else self.cfg["output"]["device"]
        in_index = find_device(str(in_query), "input", devices)
        out_index = find_device(str(out_query), "output", devices)

        by_index = {d["index"]: d for d in devices}
        in_dev = by_index[in_index]
        out_dev = by_index[out_index]
        if self.input_channel >= in_dev["max_input_channels"]:
            raise DeviceNotFoundError(
                "input.channel={} but device {!r} has only {} input channel(s). "
                "For an Aggregate Device, count the channels of the sub-devices "
                "that come before the microphone.".format(
                    self.input_channel, in_dev["name"], in_dev["max_input_channels"]
                )
            )
        if self.output_channels > out_dev["max_output_channels"]:
            raise DeviceNotFoundError(
                "output.channels={} but device {!r} has only {} output channel(s).".format(
                    self.output_channels, out_dev["name"], out_dev["max_output_channels"]
                )
            )
        self.resolved_input_index = in_index
        self.resolved_output_index = out_index
        return in_index, out_index

    # ---------------------------------------------------------------- callback
    def audio_callback(self, indata, outdata, frames, time_info, status) -> None:
        """PortAudio callback. Runs on the audio thread, must not block or allocate."""
        if status:
            # CallbackFlags: input_overflow / output_underflow are the two xrun kinds
            # that matter for a duplex stream; the others are informational.
            if getattr(status, "input_overflow", False):
                self.input_overflows += 1
                self.xruns += 1
            if getattr(status, "output_underflow", False):
                self.output_underflows += 1
                self.xruns += 1

        if frames <= self._in_buf.shape[0]:
            x = self._in_buf[:frames]
        else:
            # Only happens if PortAudio ignores the requested block size; a rare,
            # one-off allocation is acceptable there and keeps the stream alive.
            self._in_buf = np.zeros(frames, dtype=np.float32)
            x = self._in_buf
        np.copyto(x, indata[:, self.input_channel])

        try:
            in_peak = float(np.max(np.abs(x))) if frames else 0.0
            if in_peak > self._in_peak:
                self._in_peak = in_peak
            self._in_rms_acc += float(np.dot(x, x))
            self._in_rms_blocks += frames

            if self.analyzer is not None:
                self.analyzer.push(x)
            y = self.chain.process(x)
            y = np.asarray(y, dtype=np.float32)
            # Mono -> N channels: broadcast into the output buffer, no new arrays.
            outdata[:, : self.output_channels] = y.reshape(-1, 1)
            if outdata.shape[1] > self.output_channels:
                outdata[:, self.output_channels :] = 0.0
            out_peak = float(np.max(np.abs(y))) if frames else 0.0
            if out_peak > self._out_peak:
                self._out_peak = out_peak
            self.blocks_processed += 1
        except Exception as exc:  # noqa: BLE001 - must not propagate into PortAudio
            # Mute instead of sending garbage into the meeting, remember the error
            # and tell PortAudio to stop the stream; run_forever() re-raises it.
            outdata.fill(0.0)
            self.callback_errors += 1
            self.last_error = exc
            self._stop_event.set()
            sd = sys.modules.get("sounddevice")
            abort = getattr(sd, "CallbackAbort", None) if sd is not None else None
            if abort is not None:
                raise abort() from exc
            raise

    # ------------------------------------------------------------------ freeze
    def handle_sigusr1(self, signum=None, frame=None) -> None:
        """Signal handler: request a freeze toggle. Only increments a counter; the
        service thread applies it (``apply_pending_requests``), so the handler is
        safe to run at any point of the main thread."""
        self._freeze_toggle_requests += 1

    def install_signal_handlers(self) -> bool:
        """Install the SIGUSR1 handler when running on the main thread; returns success."""
        if threading.current_thread() is not threading.main_thread():
            return False
        if not hasattr(signal, "SIGUSR1"):
            return False
        signal.signal(signal.SIGUSR1, self.handle_sigusr1)
        self._signal_handlers_installed = True
        return True

    def set_frozen(self, on: bool) -> None:
        """Freeze/unfreeze the adaptive controller and remember the state for the status line."""
        self.frozen = bool(on)
        if self.controller is not None:
            self.controller.freeze(self.frozen)
        with self._lock:
            self.last_change_text = "FROZEN by request" if self.frozen else "released (adaptive active)"

    def apply_pending_requests(self) -> int:
        """Apply freeze toggles requested by SIGUSR1 since the last call; returns their number."""
        count = self._freeze_toggle_requests
        if count:
            self._freeze_toggle_requests -= count
            if count % 2 == 1:
                self.set_frozen(not self.frozen)
        return count

    # ----------------------------------------------------------------- service
    def read_meters(self) -> dict:
        """Read the chain meters once and cache them.

        ``Chain.get_meters()`` drains the per-stage accumulators
        (``gain_reduction_mean_db`` / ``gain_reduction_max_db`` since the previous
        read), so the service thread reads once per adaptive interval and the
        status line reuses that reading instead of calling the chain again.
        """
        meters = self.chain.get_meters() or {}
        with self._lock:
            self._last_meters = meters
        return meters

    def adaptive_tick(self, meters: dict | None = None) -> list[dict]:
        """One adaptive evaluation: meters (+ analysis) -> controller.update().

        ``meters`` is the shared reading from :meth:`read_meters`; when omitted a
        fresh reading is taken."""
        if self.controller is None:
            return []
        if meters is None:
            meters = self.read_meters()
        if self.analyzer is not None:
            meters["analysis"] = self.analyzer.snapshot()
        changes = self.controller.update(meters)
        changes = list(changes) if changes else []
        if changes:
            with self._lock:
                self.last_changes = changes
                self.last_change_text = "; ".join(self._format_change(c) for c in changes)
        return changes

    @staticmethod
    def _format_change(change: dict) -> str:
        stage = change.get("stage", "?")
        name = change.get("name", "?")
        old = change.get("old")
        new = change.get("new")
        reason = change.get("reason", "")
        try:
            values = "{:.1f}->{:.1f}".format(float(old), float(new))
        except (TypeError, ValueError):
            values = "{}->{}".format(old, new)
        return "{}.{} {}{}".format(stage, name, values, " ({})".format(reason) if reason else "")

    def status_line(self, meters: dict | None = None) -> str:
        """Build the one-second console status line and reset the peak holds.

        ``meters`` is the shared reading from :meth:`read_meters`; when omitted the
        cached reading is used (fresh one if nothing was read yet)."""
        in_peak = self._in_peak
        out_peak = self._out_peak
        if self._in_rms_blocks > 0:
            in_rms = math.sqrt(self._in_rms_acc / self._in_rms_blocks)
        else:
            in_rms = 0.0
        self._in_peak = 0.0
        self._out_peak = 0.0
        self._in_rms_acc = 0.0
        self._in_rms_blocks = 0

        if meters is None:
            with self._lock:
                meters = self._last_meters
            if meters is None:
                meters = self.read_meters()

        def gr(stage: str, key: str = "gain_reduction_db") -> str:
            value = meters.get(stage, {}).get(key)
            if value is None:
                return "  -- "
            return "{:5.1f}".format(float(value))

        with self._lock:
            change_text = self.last_change_text
        nonfinite = meters.get("input", {}).get("nonfinite_blocks")
        nonfinite_text = " | nonfinite {}".format(int(nonfinite)) if nonfinite is not None else ""
        # Normalizer pause estimate and effective gate; older chains without these
        # meters simply omit the field (dict.get with default, no exception path).
        normalizer_meters = meters.get("normalizer", {})
        floor_lufs = normalizer_meters.get("noise_floor_lufs")
        gate_lufs = normalizer_meters.get("effective_gate_lufs")
        gate_text = ""
        if floor_lufs is not None or gate_lufs is not None:
            gate_text = " | floor {} gate {} LUFS".format(
                "{:6.1f}".format(float(floor_lufs)) if floor_lufs is not None else "  -- ",
                "{:6.1f}".format(float(gate_lufs)) if gate_lufs is not None else "  -- ",
            )
        if self.controller is None:
            mode = "off"
        elif self.frozen:
            mode = "FROZEN"
        else:
            mode = "on"
        return (
            "in {:6.1f} dBFS (rms {:6.1f}) | out {:6.1f} dBFS | GR exp {} des {} comp {} "
            "norm {} lim {}{} | xruns {}{} | blocks {} | adaptive {}: {}"
        ).format(
            _to_dbfs(in_peak),
            _to_dbfs(in_rms),
            _to_dbfs(out_peak),
            gr("expander"),
            gr("deesser"),
            gr("compressor"),
            gr("normalizer", "gain_db"),
            gr("limiter"),
            gate_text,
            self.xruns,
            nonfinite_text,
            self.blocks_processed,
            mode,
            change_text,
        )

    @staticmethod
    def _print_status(line: str) -> None:
        print(line, file=sys.stderr, flush=True)

    def _service_loop(self) -> None:
        """Service thread: adaptive updates every interval_s, status every second."""
        now = time.monotonic()
        next_adaptive = now + self.adaptive_interval_s
        next_status = now + STATUS_INTERVAL_S
        while not self._stop_event.is_set():
            now = time.monotonic()
            wait_s = min(next_adaptive, next_status) - now
            if wait_s > 0 and self._stop_event.wait(wait_s):
                break
            now = time.monotonic()
            try:
                self.apply_pending_requests()
            except Exception as exc:  # noqa: BLE001 - report, keep audio running
                print("freeze toggle error: {!r}".format(exc), file=sys.stderr, flush=True)
            # One meter reading per wake-up, shared by controller and status line.
            # With a controller the chain is read only at adaptive ticks, so its
            # mean/max statistics cover the whole interval; without one the status
            # line reads every second.
            meters = None
            if now >= next_adaptive:
                next_adaptive += self.adaptive_interval_s
                try:
                    meters = self.read_meters()
                    self.adaptive_tick(meters)
                except Exception as exc:  # noqa: BLE001 - report, keep audio running
                    print("adaptive controller error: {!r}".format(exc), file=sys.stderr, flush=True)
                    with self._lock:
                        self.last_change_text = "ERROR {!r}".format(exc)
            if now >= next_status:
                next_status += STATUS_INTERVAL_S
                try:
                    if meters is None and self.controller is None:
                        meters = self.read_meters()
                    self.on_status(self.status_line(meters))
                except Exception as exc:  # noqa: BLE001 - a broken console must not stop audio
                    print("status callback error: {!r}".format(exc), file=sys.stderr, flush=True)

    # ------------------------------------------------------------------ control
    def start(self) -> None:
        """Open and start the duplex stream and the service thread."""
        if self.stream is not None:
            raise RuntimeError("LiveEngine is already running")
        sd = _import_sounddevice()
        in_index, out_index = self.resolve_devices()
        in_channels = self.input_channel + 1
        try:
            sd.check_input_settings(
                device=in_index, channels=in_channels, dtype="float32", samplerate=self.sample_rate
            )
            sd.check_output_settings(
                device=out_index,
                channels=self.output_channels,
                dtype="float32",
                samplerate=self.sample_rate,
            )
        except Exception as exc:
            raise AudioBackendError(
                "The devices do not support {} Hz / float32 with {} input and {} output "
                "channel(s): {}. Set both devices to {} Hz in Audio-MIDI-Setup.".format(
                    self.sample_rate, in_channels, self.output_channels, exc, self.sample_rate
                )
            ) from exc

        self._stop_event.clear()
        self.last_error = None
        try:
            self.stream = sd.Stream(
                device=(in_index, out_index),
                samplerate=self.sample_rate,
                blocksize=self.block_size,
                dtype="float32",
                channels=(in_channels, self.output_channels),
                latency="low",
                callback=self.audio_callback,
            )
        except Exception as exc:
            self.stream = None
            raise AudioBackendError("Could not open the audio stream: {}".format(exc)) from exc
        try:
            self.stream.start()
        except Exception as exc:
            # The PortAudio handle exists: release it, or the device stays busy until GC.
            stream, self.stream = self.stream, None
            stream.close()
            raise AudioBackendError("Could not start the audio stream: {}".format(exc)) from exc

        self._service_thread = threading.Thread(
            target=self._service_loop, name="voicechain-service", daemon=True
        )
        self._service_thread.start()

    def stop(self) -> None:
        """Stop the service thread and close the stream (idempotent)."""
        self._stop_event.set()
        thread = self._service_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(2.0, 2.0 * self.adaptive_interval_s))
        self._service_thread = None
        stream = self.stream
        self.stream = None
        if stream is not None:
            stream.stop()
            stream.close()

    def latency_info(self) -> str:
        """Human-readable latency of the open stream (input + output as reported by PortAudio)."""
        if self.stream is None:
            return "stream not open"
        latency = getattr(self.stream, "latency", None)
        if isinstance(latency, (tuple, list)) and len(latency) == 2:
            return "latency in {:.1f} ms / out {:.1f} ms (block {:.1f} ms)".format(
                1000.0 * float(latency[0]),
                1000.0 * float(latency[1]),
                1000.0 * self.block_size / self.sample_rate,
            )
        return "block {:.1f} ms".format(1000.0 * self.block_size / self.sample_rate)

    def run_forever(self) -> None:
        """Start, block until Ctrl-C or a callback error, then stop.

        SIGUSR1 toggles the freeze state of the adaptive controller while running
        (``kill -USR1 <pid>``)."""
        self.start()
        try:
            signals_ok = self.install_signal_handlers() if self.controller is not None else False
            print(
                "voicechain running: input #{} channel {} -> output #{} ({} ch), {} Hz, {}. "
                "Press Ctrl-C to stop.{}".format(
                    self.resolved_input_index,
                    self.input_channel,
                    self.resolved_output_index,
                    self.output_channels,
                    self.sample_rate,
                    self.latency_info(),
                    " Send SIGUSR1 to toggle freeze (kill -USR1 {}).".format(os.getpid()) if signals_ok else "",
                ),
                file=sys.stderr,
                flush=True,
            )
            # Poll interval of the main thread; only affects how quickly a callback
            # error is noticed, Ctrl-C is delivered immediately by the OS.
            while not self._stop_event.is_set():
                time.sleep(0.2)
        except KeyboardInterrupt:
            print("\nstopping ...", file=sys.stderr, flush=True)
        finally:
            self.stop()
            if self._signal_handlers_installed:
                signal.signal(signal.SIGUSR1, signal.SIG_DFL)
                self._signal_handlers_installed = False
        if self.last_error is not None:
            raise RuntimeError(
                "audio callback failed after {} blocks: {!r}".format(self.blocks_processed, self.last_error)
            ) from self.last_error


# ------------------------------------------------------------------- recording
# Peak level above which the recording is considered too hot for calibration:
# 3 dB below full scale leaves no headroom for louder passages in the seminar.
RECORD_CLIP_WARN_DBFS = -3.0
# How often the progress callback is called while recording.
RECORD_PROGRESS_INTERVAL_S = 0.5
# Shortest recording worth keeping when the user stops early with Ctrl-C.
RECORD_MIN_KEEP_S = 1.0


def record_wav(
    cfg: dict,
    out_path,
    seconds: float,
    input_device: str | int | None = None,
    on_progress: Callable[[float, float, bool], None] | None = None,
) -> dict:
    """Record ``seconds`` of mono audio from the configured input device to a 24-bit WAV.

    The chain is not applied: the recording is the raw microphone signal that
    ``calibrate`` needs. ``on_progress(remaining_s, peak_dbfs, too_hot)`` is called
    every RECORD_PROGRESS_INTERVAL_S with the peak since the previous call.
    Ctrl-C stops early and keeps what was recorded (if at least RECORD_MIN_KEEP_S).
    Returns {"path", "seconds", "samples", "peak_dbfs", "overflows", "stopped_early"}.
    """
    if seconds <= 0:
        raise ValueError("seconds must be > 0, got {}".format(seconds))
    sd = _import_sounddevice()
    sample_rate = int(cfg["sample_rate"])
    block_size = int(cfg["block_size"])
    channel = int(cfg["input"]["channel"])
    devices = list_devices()
    query = input_device if input_device is not None else cfg["input"]["device"]
    in_index = find_device(str(query), "input", devices)
    in_dev = next(d for d in devices if d["index"] == in_index)
    if channel >= in_dev["max_input_channels"]:
        raise DeviceNotFoundError(
            "input.channel={} but device {!r} has only {} input channel(s).".format(
                channel, in_dev["name"], in_dev["max_input_channels"]
            )
        )

    n_total = int(round(seconds * sample_rate))
    buffer = np.zeros(n_total, dtype=np.float32)
    state = {"pos": 0, "peak": 0.0, "overall_peak": 0.0, "overflows": 0}
    done = threading.Event()

    def callback(indata, frames, time_info, status) -> None:
        if status and getattr(status, "input_overflow", False):
            state["overflows"] += 1
        pos = state["pos"]
        n = min(frames, n_total - pos)
        if n > 0:
            x = indata[:n, channel]
            buffer[pos : pos + n] = x
            peak = float(np.max(np.abs(x)))
            if peak > state["peak"]:
                state["peak"] = peak
            if peak > state["overall_peak"]:
                state["overall_peak"] = peak
            state["pos"] = pos + n
        if state["pos"] >= n_total:
            done.set()
            raise sd.CallbackStop()

    try:
        sd.check_input_settings(device=in_index, channels=channel + 1, dtype="float32", samplerate=sample_rate)
        stream = sd.InputStream(
            device=in_index,
            channels=channel + 1,
            samplerate=sample_rate,
            blocksize=block_size,
            dtype="float32",
            callback=callback,
        )
    except Exception as exc:
        raise AudioBackendError(
            "Could not open input device {!r} at {} Hz: {}".format(in_dev["name"], sample_rate, exc)
        ) from exc

    def report_progress() -> None:
        if on_progress is None:
            return
        peak = state["peak"]
        state["peak"] = 0.0
        peak_dbfs = _to_dbfs(peak)
        remaining = max(0.0, (n_total - state["pos"]) / sample_rate)
        on_progress(remaining, peak_dbfs, peak_dbfs > RECORD_CLIP_WARN_DBFS)

    stopped_early = False
    try:
        # start() inside the try: a Ctrl-C arriving while the stream spins up must
        # also end up as "stopped early", not as a traceback.
        stream.start()
        while not done.wait(RECORD_PROGRESS_INTERVAL_S):
            report_progress()
        report_progress()  # final line: remaining 0.0 s and the last peak
    except KeyboardInterrupt:
        stopped_early = True
    except Exception as exc:
        # sounddevice.PortAudioError (device vanished, format refused at start) or
        # any other backend failure: one clear message, no traceback.
        raise AudioBackendError(
            "Recording from {!r} failed: {}".format(in_dev["name"], exc)
        ) from exc
    finally:
        stream.stop()
        stream.close()

    recorded = buffer[: state["pos"]]
    if recorded.shape[0] < RECORD_MIN_KEEP_S * sample_rate:
        raise RuntimeError(
            "recording stopped after {:.2f} s; at least {:.0f} s are needed, nothing written".format(
                recorded.shape[0] / sample_rate, RECORD_MIN_KEEP_S
            )
        )
    import soundfile as sf

    sf.write(str(out_path), recorded, sample_rate, subtype="PCM_24")
    return {
        "path": str(out_path),
        "seconds": recorded.shape[0] / sample_rate,
        "samples": int(recorded.shape[0]),
        "peak_dbfs": _to_dbfs(state["overall_peak"]),
        "overflows": state["overflows"],
        "stopped_early": stopped_early,
    }
