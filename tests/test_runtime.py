"""Tests for voicechain.runtime without any audio hardware.

``sounddevice`` is replaced by a fake module in ``sys.modules`` so the lazy
imports inside runtime.py pick it up. The chain, analyzer and controller are
small recording fakes; the real DSP is covered by its own tests.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import sys
import threading
import time
import types

import numpy as np
import pytest

from voicechain import runtime
from voicechain.runtime import (
    AudioBackendError,
    DeviceNotFoundError,
    LiveEngine,
    find_device,
    fold_device_name,
    list_devices,
)

SAMPLE_RATE = 48000
BLOCK = 256

RAW_DEVICES = [
    {"name": "MacBook Pro Microphone", "hostapi": 0, "max_input_channels": 1, "max_output_channels": 0, "default_samplerate": 48000.0},
    {"name": "MacBook Pro Speakers", "hostapi": 0, "max_input_channels": 0, "max_output_channels": 2, "default_samplerate": 48000.0},
    {"name": "RØDE NT-USB", "hostapi": 0, "max_input_channels": 1, "max_output_channels": 2, "default_samplerate": 48000.0},
    {"name": "BlackHole 2ch", "hostapi": 0, "max_input_channels": 2, "max_output_channels": 2, "default_samplerate": 48000.0},
    {"name": "BlackHole 16ch", "hostapi": 0, "max_input_channels": 16, "max_output_channels": 16, "default_samplerate": 48000.0},
    {"name": "Rode+BlackHole", "hostapi": 0, "max_input_channels": 3, "max_output_channels": 4, "default_samplerate": 48000.0},
]
IDX_MIC, IDX_SPK, IDX_RODE, IDX_BH2, IDX_BH16, IDX_AGG = range(6)


def base_cfg(**overrides) -> dict:
    cfg = {
        "sample_rate": SAMPLE_RATE,
        "block_size": BLOCK,
        "input": {"device": "RØDE NT-USB", "channel": 0},
        "output": {"device": "BlackHole 2ch", "channels": 2},
        "chain": {},
        "adaptive": {"enabled": True, "learn_s": 30.0, "interval_s": 2.0},
    }
    for key, value in overrides.items():
        cfg[key] = value
    return cfg


# ----------------------------------------------------------------------------- fakes
class FakeStatus:
    def __init__(self, input_overflow=False, output_underflow=False):
        self.input_overflow = input_overflow
        self.output_underflow = output_underflow
        self.input_underflow = False
        self.output_overflow = False
        self.priming_output = False

    def __bool__(self):
        return bool(self.input_overflow or self.output_underflow)


class FakeStream:
    instances: list["FakeStream"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.callback = kwargs["callback"]
        self.started = False
        self.stopped = False
        self.closed = False
        self.latency = (0.004, 0.006)
        FakeStream.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True

    def feed(self, indata: np.ndarray, status=None) -> np.ndarray:
        """Call the callback like PortAudio would and return the output buffer."""
        frames = indata.shape[0]
        outdata = np.full((frames, self.kwargs["channels"][1]), 7.0, dtype=np.float32)
        self.callback(indata, outdata, frames, None, status)
        return outdata


class CallbackAbort(Exception):
    """Stands in for sounddevice.CallbackAbort."""


def make_fake_sd(devices=RAW_DEVICES, fail_check=False) -> types.ModuleType:
    module = types.ModuleType("sounddevice")
    module.query_devices = lambda: [dict(d) for d in devices]
    module.query_hostapis = lambda: [{"name": "Core Audio"}]
    module.Stream = FakeStream
    module.CallbackAbort = CallbackAbort
    module.checks: list[tuple] = []

    def check_input_settings(**kwargs):
        module.checks.append(("input", kwargs))
        if fail_check:
            raise ValueError("Invalid sample rate")

    def check_output_settings(**kwargs):
        module.checks.append(("output", kwargs))
        if fail_check:
            raise ValueError("Invalid sample rate")

    module.check_input_settings = check_input_settings
    module.check_output_settings = check_output_settings
    return module


class RecordingChain:
    """Multiplies by ``gain`` and records every call in a shared log."""

    def __init__(self, log: list, gain: float = 0.5, fail: bool = False):
        self.log = log
        self.gain = gain
        self.fail = fail
        self.meter_calls = 0

    def process(self, x):
        self.log.append(("chain.process", np.array(x, copy=True)))
        if self.fail:
            raise ValueError("boom in chain")
        return (x * self.gain).astype(np.float32)

    def get_meters(self):
        self.meter_calls += 1
        return {
            "expander": {"gain_reduction_db": 3.0},
            "deesser": {"gain_reduction_db": 0.5},
            "compressor": {"gain_reduction_db": 2.0},
            "normalizer": {"gain_db": 1.5},
            "limiter": {"gain_reduction_db": 0.0},
            "output": {"peak_dbfs": -6.0, "rms_dbfs": -20.0},
        }


class RecordingAnalyzer:
    def __init__(self, log: list):
        self.log = log
        self.pushed_samples = 0

    def push(self, x):
        self.log.append(("analyzer.push", np.array(x, copy=True)))
        self.pushed_samples += len(x)

    def snapshot(self):
        return {"noise_floor_dbfs": -52.0, "speech_level_dbfs": -22.0}


class RecordingController:
    def __init__(self, changes=None):
        self.calls: list[dict] = []
        self.changes = changes or []

    def update(self, meters, now=None):
        self.calls.append(meters)
        return list(self.changes)

    def freeze(self, on: bool):
        self.frozen = on


@pytest.fixture
def fake_sd(monkeypatch):
    FakeStream.instances = []
    module = make_fake_sd()
    monkeypatch.setitem(sys.modules, "sounddevice", module)
    return module


# ------------------------------------------------------------------- device lookup
def test_fold_device_name_unifies_unicode_and_case():
    assert fold_device_name("RØDE NT-USB") == fold_device_name("rode nt-usb")
    assert fold_device_name("RØDE NT-USB") == "rode nt-usb"
    # composed vs. decomposed "é"
    assert fold_device_name("Café") == fold_device_name("Café") == "cafe"


def test_find_device_matches_unicode_variants(fake_sd):
    devices = list_devices()
    assert find_device("RØDE NT-USB", "input", devices) == IDX_RODE
    assert find_device("rode nt-usb", "input", devices) == IDX_RODE
    assert find_device("RODE NT", "input", devices) == IDX_RODE
    with pytest.raises(DeviceNotFoundError):  # "RODE" also hits the aggregate "Rode+BlackHole"
        find_device("RODE", "input", devices)
    assert find_device("røde nt", "output", devices) == IDX_RODE  # headphone output
    assert find_device(str(IDX_BH2), "output", devices) == IDX_BH2  # numeric index


def test_find_device_prefers_exact_name_over_longer_names(fake_sd):
    # "BlackHole 2ch" is a substring of nothing else, but "blackhole" is ambiguous.
    devices = list_devices()
    assert find_device("BlackHole 2ch", "output", devices) == IDX_BH2
    with pytest.raises(DeviceNotFoundError) as excinfo:
        find_device("blackhole", "output", devices)
    message = str(excinfo.value)
    assert "3 output devices match" in message
    assert "BlackHole 2ch" in message and "BlackHole 16ch" in message and "Rode+BlackHole" in message


def test_find_device_reports_missing_device_with_list(fake_sd):
    devices = list_devices()
    with pytest.raises(DeviceNotFoundError) as excinfo:
        find_device("Shure SM7B", "input", devices)
    message = str(excinfo.value)
    assert "No input device matches 'Shure SM7B'" in message
    for dev in RAW_DEVICES:
        assert dev["name"] in message
    # kind restricts to devices with channels in that direction
    with pytest.raises(DeviceNotFoundError):
        find_device("MacBook Pro Speakers", "input", devices)
    with pytest.raises(ValueError):
        find_device("x", "sideways", devices)


def test_list_devices_returns_plain_dicts(fake_sd):
    devices = list_devices()
    assert len(devices) == len(RAW_DEVICES)
    rode = devices[IDX_RODE]
    assert rode == {
        "index": IDX_RODE,
        "name": "RØDE NT-USB",
        "max_input_channels": 1,
        "max_output_channels": 2,
        "default_samplerate": 48000.0,
        "hostapi": "Core Audio",
    }
    table = runtime.format_device_table(devices)
    assert "RØDE NT-USB" in table and "48000" in table


class _RaisingLoader(importlib.abc.Loader):
    def create_module(self, spec):
        raise OSError("PortAudio library not found")

    def exec_module(self, module):
        raise OSError("PortAudio library not found")


class _RaisingFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "sounddevice":
            return importlib.machinery.ModuleSpec(fullname, _RaisingLoader())
        return None


def test_missing_portaudio_gives_clear_error(monkeypatch):
    monkeypatch.delitem(sys.modules, "sounddevice", raising=False)
    monkeypatch.setattr(sys, "meta_path", [_RaisingFinder()] + list(sys.meta_path))
    with pytest.raises(AudioBackendError) as excinfo:
        list_devices()
    assert "PortAudio is not available" in str(excinfo.value)
    assert "brew install portaudio" in str(excinfo.value)


def test_missing_sounddevice_package_gives_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", None)  # makes "import sounddevice" fail
    with pytest.raises(AudioBackendError) as excinfo:
        list_devices()
    assert "not installed" in str(excinfo.value)


# ------------------------------------------------------------------------- callback
def test_callback_duplicates_mono_to_stereo_and_calls_in_order(fake_sd):
    log: list = []
    chain = RecordingChain(log, gain=0.5)
    analyzer = RecordingAnalyzer(log)
    engine = LiveEngine(base_cfg(), chain, analyzer=analyzer)

    x = np.linspace(-0.8, 0.8, BLOCK, dtype=np.float32)
    indata = x.reshape(-1, 1)
    outdata = np.full((BLOCK, 2), 7.0, dtype=np.float32)
    engine.audio_callback(indata, outdata, BLOCK, None, FakeStatus())

    np.testing.assert_allclose(outdata[:, 0], 0.5 * x, atol=1e-7)
    np.testing.assert_array_equal(outdata[:, 0], outdata[:, 1])
    assert [name for name, _ in log] == ["analyzer.push", "chain.process"]
    np.testing.assert_array_equal(log[0][1], x)
    np.testing.assert_array_equal(log[1][1], x)
    assert engine.blocks_processed == 1
    assert engine.xruns == 0


def test_callback_uses_configured_input_channel_and_zeroes_extra_outputs(fake_sd):
    log: list = []
    cfg = base_cfg(input={"device": "Rode+BlackHole", "channel": 2}, output={"device": "Rode+BlackHole", "channels": 2})
    engine = LiveEngine(cfg, RecordingChain(log, gain=1.0))
    indata = np.zeros((BLOCK, 3), dtype=np.float32)
    indata[:, 2] = 0.25
    outdata = np.full((BLOCK, 4), 7.0, dtype=np.float32)
    engine.audio_callback(indata, outdata, BLOCK, None, None)
    np.testing.assert_array_equal(outdata[:, 0], 0.25)
    np.testing.assert_array_equal(outdata[:, 1], 0.25)
    np.testing.assert_array_equal(outdata[:, 2:], 0.0)


def test_callback_counts_xruns_from_status_flags(fake_sd):
    engine = LiveEngine(base_cfg(), RecordingChain([]))
    indata = np.zeros((BLOCK, 1), dtype=np.float32)
    outdata = np.zeros((BLOCK, 2), dtype=np.float32)
    engine.audio_callback(indata, outdata, BLOCK, None, FakeStatus(input_overflow=True))
    engine.audio_callback(indata, outdata, BLOCK, None, FakeStatus(output_underflow=True))
    engine.audio_callback(indata, outdata, BLOCK, None, FakeStatus(input_overflow=True, output_underflow=True))
    engine.audio_callback(indata, outdata, BLOCK, None, FakeStatus())
    assert engine.input_overflows == 2
    assert engine.output_underflows == 2
    assert engine.xruns == 4
    assert engine.blocks_processed == 4


def test_callback_error_mutes_output_and_aborts(fake_sd):
    engine = LiveEngine(base_cfg(), RecordingChain([], fail=True))
    indata = np.ones((BLOCK, 1), dtype=np.float32)
    outdata = np.full((BLOCK, 2), 7.0, dtype=np.float32)
    with pytest.raises(CallbackAbort):
        engine.audio_callback(indata, outdata, BLOCK, None, None)
    np.testing.assert_array_equal(outdata, 0.0)
    assert engine.callback_errors == 1
    assert isinstance(engine.last_error, ValueError)
    assert engine._stop_event.is_set()


# ---------------------------------------------------------------- stream handling
def test_start_opens_duplex_stream_with_separate_devices(fake_sd):
    engine = LiveEngine(base_cfg(), RecordingChain([]), on_status=lambda line: None)
    engine.start()
    try:
        stream = FakeStream.instances[-1]
        assert stream.started
        assert stream.kwargs["device"] == (IDX_RODE, IDX_BH2)
        assert stream.kwargs["channels"] == (1, 2)
        assert stream.kwargs["samplerate"] == SAMPLE_RATE
        assert stream.kwargs["blocksize"] == BLOCK
        assert stream.kwargs["dtype"] == "float32"
        assert [kind for kind, _ in fake_sd.checks] == ["input", "output"]
        assert fake_sd.checks[0][1]["device"] == IDX_RODE
        assert fake_sd.checks[1][1]["device"] == IDX_BH2
        assert "latency in 4.0 ms / out 6.0 ms" in engine.latency_info()
        # the real callback is wired to the stream
        out = stream.feed(np.full((BLOCK, 1), 0.2, dtype=np.float32))
        np.testing.assert_allclose(out[:, 1], 0.1, atol=1e-7)
    finally:
        engine.stop()
    assert stream.stopped and stream.closed
    assert engine.stream is None


def test_overrides_allow_one_aggregate_device_for_both_directions(fake_sd):
    cfg = base_cfg(input={"device": "RØDE NT-USB", "channel": 2})
    engine = LiveEngine(
        cfg,
        RecordingChain([]),
        on_status=lambda line: None,
        input_device="rode+blackhole",
        output_device="Rode+BlackHole",
    )
    engine.start()
    try:
        stream = FakeStream.instances[-1]
        assert stream.kwargs["device"] == (IDX_AGG, IDX_AGG)
        assert stream.kwargs["channels"] == (3, 2)
    finally:
        engine.stop()


def test_resolve_devices_rejects_channel_beyond_device(fake_sd):
    cfg = base_cfg(input={"device": "RØDE NT-USB", "channel": 1})  # Rode has 1 input channel
    engine = LiveEngine(cfg, RecordingChain([]))
    with pytest.raises(DeviceNotFoundError) as excinfo:
        engine.resolve_devices()
    assert "input.channel=1" in str(excinfo.value)
    cfg = base_cfg(output={"device": "MacBook Pro Microphone", "channels": 2})
    with pytest.raises(DeviceNotFoundError):
        LiveEngine(cfg, RecordingChain([])).resolve_devices()


def test_start_reports_unsupported_settings(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", make_fake_sd(fail_check=True))
    engine = LiveEngine(base_cfg(), RecordingChain([]))
    with pytest.raises(AudioBackendError) as excinfo:
        engine.start()
    assert "48000 Hz" in str(excinfo.value)
    assert engine.stream is None


def test_run_forever_reraises_callback_error(fake_sd, monkeypatch):
    class FeedingStream(FakeStream):
        aborted = False

        def start(self):
            super().start()
            # PortAudio delivers the first block right after start; here synchronously.
            try:
                self.feed(np.ones((BLOCK, 1), dtype=np.float32))
            except CallbackAbort:
                self.aborted = True  # PortAudio swallows the abort and stops the stream

    monkeypatch.setattr(fake_sd, "Stream", FeedingStream)
    engine = LiveEngine(base_cfg(), RecordingChain([], fail=True), on_status=lambda line: None)
    with pytest.raises(RuntimeError) as excinfo:
        engine.run_forever()
    assert "audio callback failed" in str(excinfo.value)
    assert engine.callback_errors == 1
    assert FakeStream.instances[-1].aborted
    assert FakeStream.instances[-1].closed


# ------------------------------------------------------------ adaptive / status
def test_adaptive_tick_passes_meters_with_analysis_and_records_change(fake_sd):
    log: list = []
    chain = RecordingChain(log)
    analyzer = RecordingAnalyzer(log)
    controller = RecordingController(
        changes=[{"stage": "expander", "name": "threshold_db", "old": -45.0, "new": -44.5, "reason": "noise floor rose"}]
    )
    engine = LiveEngine(base_cfg(), chain, controller=controller, analyzer=analyzer)
    changes = engine.adaptive_tick()
    assert len(changes) == 1
    assert len(controller.calls) == 1
    meters = controller.calls[0]
    assert meters["analysis"] == analyzer.snapshot()
    assert meters["expander"]["gain_reduction_db"] == 3.0
    assert "expander.threshold_db -45.0->-44.5 (noise floor rose)" in engine.last_change_text
    assert "noise floor rose" in engine.status_line()
    # without a controller nothing is called and nothing breaks
    assert LiveEngine(base_cfg(), chain, analyzer=analyzer).adaptive_tick() == []


def test_service_thread_calls_controller_periodically(fake_sd):
    cfg = base_cfg(adaptive={"enabled": True, "learn_s": 0.0, "interval_s": 0.05})
    log: list = []
    controller = RecordingController()
    status_lines: list[str] = []
    engine = LiveEngine(
        cfg,
        RecordingChain(log),
        controller=controller,
        analyzer=RecordingAnalyzer(log),
        on_status=status_lines.append,
    )
    engine.start()
    try:
        deadline = time.monotonic() + 3.0
        while len(controller.calls) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        engine.stop()
    assert len(controller.calls) >= 3
    for meters in controller.calls:
        assert meters["analysis"] == {"noise_floor_dbfs": -52.0, "speech_level_dbfs": -22.0}
        assert "normalizer" in meters
    # the controller ran on the service thread, never on the caller's thread
    assert engine._service_thread is None
    assert not any(t.name == "voicechain-service" for t in threading.enumerate())


def test_status_line_reports_levels_gain_reduction_and_xruns(fake_sd):
    engine = LiveEngine(base_cfg(), RecordingChain([], gain=0.5))
    indata = np.zeros((BLOCK, 1), dtype=np.float32)
    indata[:, 0] = 0.5  # -6.0 dBFS peak, output -12.0 dBFS
    outdata = np.zeros((BLOCK, 2), dtype=np.float32)
    engine.audio_callback(indata, outdata, BLOCK, None, FakeStatus(input_overflow=True))
    line = engine.status_line()
    assert "in   -6.0 dBFS" in line
    assert "out  -12.0 dBFS" in line
    assert "exp   3.0" in line and "comp   2.0" in line and "norm   1.5" in line
    assert "xruns 1" in line
    assert "blocks 1" in line
    # peak hold is reset after each status line; silence shows the shared dB floor, not -inf
    from voicechain.dsp import DB_FLOOR_DBFS

    line2 = engine.status_line()
    assert "in {:6.1f} dBFS".format(DB_FLOOR_DBFS) in line2


def test_engine_validates_configuration():
    with pytest.raises(ValueError):
        LiveEngine(base_cfg(adaptive={"interval_s": 0.0}), RecordingChain([]))
    with pytest.raises(ValueError):
        LiveEngine(base_cfg(output={"device": "x", "channels": 0}), RecordingChain([]))
    with pytest.raises(ValueError):
        LiveEngine(base_cfg(input={"device": "x", "channel": -1}), RecordingChain([]))


# ------------------------------------------------------------- freeze via SIGUSR1
def test_sigusr1_handler_toggles_freeze_on_service_side(fake_sd):
    controller = RecordingController()
    controller.frozen = False
    engine = LiveEngine(base_cfg(), RecordingChain([]), controller=controller)
    assert engine.frozen is False
    assert "adaptive on:" in engine.status_line()

    engine.handle_sigusr1(10, None)  # what the signal module would call
    assert controller.frozen is False  # the handler itself does not touch the controller
    assert engine.apply_pending_requests() == 1
    assert controller.frozen is True
    assert engine.frozen is True
    assert "adaptive FROZEN:" in engine.status_line()
    assert "FROZEN by request" in engine.status_line()

    engine.handle_sigusr1()  # second toggle releases
    engine.apply_pending_requests()
    assert controller.frozen is False
    assert "adaptive on:" in engine.status_line()

    # two toggles between service ticks cancel each other
    engine.handle_sigusr1()
    engine.handle_sigusr1()
    assert engine.apply_pending_requests() == 2
    assert controller.frozen is False
    assert engine.apply_pending_requests() == 0


def test_engine_without_controller_reports_adaptive_off(fake_sd):
    engine = LiveEngine(base_cfg(), RecordingChain([]))
    assert "adaptive off:" in engine.status_line()
    engine.handle_sigusr1()
    engine.apply_pending_requests()  # nothing to freeze, must not fail
    assert "adaptive off:" in engine.status_line()


def test_engine_starts_frozen_when_controller_is_frozen(fake_sd):
    controller = RecordingController()
    controller.frozen = True
    engine = LiveEngine(base_cfg(), RecordingChain([]), controller=controller)
    assert engine.frozen is True
    assert "adaptive FROZEN:" in engine.status_line()


def test_install_signal_handlers_registers_sigusr1(fake_sd):
    import signal

    engine = LiveEngine(base_cfg(), RecordingChain([]), controller=RecordingController())
    previous = signal.getsignal(signal.SIGUSR1)
    try:
        assert engine.install_signal_handlers() is True
        assert signal.getsignal(signal.SIGUSR1) == engine.handle_sigusr1
    finally:
        signal.signal(signal.SIGUSR1, previous)


def test_status_line_shows_nonfinite_counter(fake_sd):
    chain = RecordingChain([])
    base = chain.get_meters

    def meters_with_input():
        meters = base()
        meters["input"] = {"peak_dbfs": -20.0, "rms_dbfs": -30.0, "nonfinite_blocks": 3}
        return meters

    chain.get_meters = meters_with_input
    engine = LiveEngine(base_cfg(), chain)
    assert "nonfinite 3" in engine.status_line()
    # without the counter in the meters the field is omitted, not shown as garbage
    assert "nonfinite" not in LiveEngine(base_cfg(), RecordingChain([])).status_line()


def test_find_device_treats_non_ascii_digits_as_name_not_index(fake_sd):
    devices = list_devices()
    # Superscript ones: isdigit() is True but int() would fail. Folded to "111" they
    # are an ordinary (unmatched) name, reported as such, not as a ValueError.
    with pytest.raises(DeviceNotFoundError) as excinfo:
        find_device("¹¹¹", "input", devices)
    assert "No input device matches" in str(excinfo.value)
    # Full-width "2" folds to "2" and matches the name "BlackHole 2ch" (index 3),
    # whereas index 2 would be the Rode's headphone output.
    assert find_device("２", "output", devices) == IDX_BH2


# ------------------------------------------------------------------- recording
class CallbackStop(Exception):
    """Stands in for sounddevice.CallbackStop."""


class FakeInputStream:
    """Feeds a prepared signal to the callback from a thread, like PortAudio would."""

    signal_source: np.ndarray | None = None
    interrupt_after_s: float | None = None
    instances: list["FakeInputStream"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.callback = kwargs["callback"]
        self.stopped = False
        self.closed = False
        self.delivered = 0
        FakeInputStream.instances.append(self)

    def start(self):
        block = self.kwargs["blocksize"]
        channels = self.kwargs["channels"]
        source = self.signal_source
        limit = None if self.interrupt_after_s is None else int(self.interrupt_after_s * SAMPLE_RATE)

        def feeder():
            import _thread

            pos = 0
            while pos < len(source):
                frames = min(block, len(source) - pos)
                indata = np.zeros((frames, channels), dtype=np.float32)
                indata[:, channels - 1] = source[pos : pos + frames]
                try:
                    self.callback(indata, frames, None, FakeStatus())
                except CallbackStop:
                    return
                pos += frames
                self.delivered += frames
                time.sleep(0.0005)  # pace a little, PortAudio delivers in real time
                if limit is not None and pos >= limit:
                    _thread.interrupt_main()
                    return

        threading.Thread(target=feeder, daemon=True).start()

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


@pytest.fixture
def fake_sd_record(fake_sd):
    FakeInputStream.instances = []
    FakeInputStream.interrupt_after_s = None
    fake_sd.InputStream = FakeInputStream
    fake_sd.CallbackStop = CallbackStop
    return fake_sd


def test_record_wav_writes_24bit_mono_and_reports_peak(fake_sd_record, tmp_path):
    import soundfile as sf

    seconds = 2.0
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    FakeInputStream.signal_source = (0.25 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)  # -12 dBFS peak
    progress: list[tuple] = []
    out = tmp_path / "sample.wav"

    result = runtime.record_wav(base_cfg(), out, seconds, on_progress=lambda r, p, hot: progress.append((r, p, hot)))

    info = sf.info(str(out))
    assert info.samplerate == SAMPLE_RATE and info.channels == 1 and info.subtype == "PCM_24"
    assert info.frames == int(seconds * SAMPLE_RATE)
    data, _ = sf.read(str(out), dtype="float32")
    assert abs(float(np.max(np.abs(data))) - 0.25) < 1e-3
    assert result["samples"] == info.frames
    assert abs(result["peak_dbfs"] - (-12.04)) < 0.1
    assert result["overflows"] == 0 and result["stopped_early"] is False
    stream = FakeInputStream.instances[-1]
    assert stream.kwargs["device"] == IDX_RODE
    assert stream.kwargs["channels"] == 1 and stream.kwargs["samplerate"] == SAMPLE_RATE
    assert stream.stopped and stream.closed
    # progress carries the remaining time and the peak, no clip warning at -12 dBFS
    assert progress, "progress callback was never called"
    assert all(not hot for _, _, hot in progress)
    assert all(0.0 <= remaining <= seconds for remaining, _, _ in progress)
    assert any(abs(peak - (-12.04)) < 0.2 for _, peak, _ in progress)


def test_record_wav_warns_when_too_hot_and_uses_configured_channel(fake_sd_record, tmp_path):
    seconds = 1.5
    FakeInputStream.signal_source = np.full(int(seconds * SAMPLE_RATE), 0.9, dtype=np.float32)  # -0.9 dBFS
    progress: list[tuple] = []
    cfg = base_cfg(input={"device": "Rode+BlackHole", "channel": 2})
    result = runtime.record_wav(cfg, tmp_path / "hot.wav", seconds, on_progress=lambda r, p, hot: progress.append(hot))
    assert FakeInputStream.instances[-1].kwargs["channels"] == 3  # channel 2 -> 3 channels opened
    assert FakeInputStream.instances[-1].kwargs["device"] == IDX_AGG
    assert result["peak_dbfs"] > runtime.RECORD_CLIP_WARN_DBFS
    assert any(progress)


def test_record_wav_ctrl_c_keeps_partial_recording(fake_sd_record, tmp_path):
    import soundfile as sf

    FakeInputStream.signal_source = np.full(10 * SAMPLE_RATE, 0.1, dtype=np.float32)
    FakeInputStream.interrupt_after_s = 1.5
    out = tmp_path / "partial.wav"
    result = runtime.record_wav(base_cfg(), out, 10.0)
    assert result["stopped_early"] is True
    assert 1.4 <= result["seconds"] <= 2.5
    assert sf.info(str(out)).frames == result["samples"]


def test_record_wav_rejects_too_short_recording(fake_sd_record, tmp_path):
    FakeInputStream.signal_source = np.full(10 * SAMPLE_RATE, 0.1, dtype=np.float32)
    FakeInputStream.interrupt_after_s = 0.2
    out = tmp_path / "short.wav"
    with pytest.raises(RuntimeError) as excinfo:
        runtime.record_wav(base_cfg(), out, 10.0)
    assert "nothing written" in str(excinfo.value)
    assert not out.exists()


def test_record_wav_validates_arguments(fake_sd_record, tmp_path):
    with pytest.raises(ValueError):
        runtime.record_wav(base_cfg(), tmp_path / "x.wav", 0.0)
    with pytest.raises(DeviceNotFoundError):
        runtime.record_wav(base_cfg(), tmp_path / "x.wav", 1.0, input_device="Shure SM7B")


def test_service_thread_reads_meters_once_per_adaptive_interval(fake_sd):
    """get_meters() drains accumulators: with a controller the chain is read only
    at adaptive ticks and the status line reuses that reading."""
    cfg = base_cfg(adaptive={"enabled": True, "learn_s": 0.0, "interval_s": 0.2})
    chain = RecordingChain([])
    controller = RecordingController()
    status_lines: list[str] = []
    engine = LiveEngine(cfg, chain, controller=controller, on_status=status_lines.append)
    engine.start()
    try:
        deadline = time.monotonic() + 3.0
        while len(controller.calls) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        engine.stop()
    assert len(controller.calls) >= 3
    # status lines were produced from the cached reading, not from extra reads
    assert chain.meter_calls == len(controller.calls)
    assert all("exp   3.0" in line for line in status_lines)


def test_status_line_uses_shared_meters_and_adaptive_tick_accepts_them(fake_sd):
    chain = RecordingChain([])
    controller = RecordingController()
    engine = LiveEngine(base_cfg(), chain, controller=controller)
    meters = engine.read_meters()
    assert chain.meter_calls == 1
    engine.adaptive_tick(meters)
    engine.status_line(meters)
    engine.status_line()  # cached reading, no new call
    assert chain.meter_calls == 1
    assert controller.calls[0] is meters
    # without a cached reading the status line reads once itself
    fresh = LiveEngine(base_cfg(), RecordingChain([]))
    assert "exp   3.0" in fresh.status_line()
    assert fresh.chain.meter_calls == 1


def test_start_closes_stream_when_start_fails(fake_sd, monkeypatch):
    class FailingStartStream(FakeStream):
        def start(self):
            raise RuntimeError("Device unavailable [PaErrorCode -9985]")

    monkeypatch.setattr(fake_sd, "Stream", FailingStartStream)
    engine = LiveEngine(base_cfg(), RecordingChain([]), on_status=lambda line: None)
    with pytest.raises(AudioBackendError) as excinfo:
        engine.start()
    assert "Could not start the audio stream" in str(excinfo.value)
    assert "-9985" in str(excinfo.value)
    stream = FakeStream.instances[-1]
    assert stream.closed  # the PortAudio handle is released, the device is not left busy
    assert engine.stream is None
    assert engine._service_thread is None


def test_record_wav_backend_error_during_start_becomes_audio_backend_error(fake_sd_record, tmp_path):
    class FailingInputStream(FakeInputStream):
        def start(self):
            raise RuntimeError("Unanticipated host error")

    fake_sd_record.InputStream = FailingInputStream
    out = tmp_path / "x.wav"
    with pytest.raises(AudioBackendError) as excinfo:
        runtime.record_wav(base_cfg(), out, 1.0)
    assert "Recording from 'RØDE NT-USB' failed" in str(excinfo.value)
    assert FakeInputStream.instances[-1].closed
    assert not out.exists()


def test_status_line_shows_normalizer_floor_and_gate_when_available(fake_sd):
    chain = RecordingChain([])
    base = chain.get_meters

    def meters_with_gate():
        meters = base()
        meters["normalizer"] = {"gain_db": 1.5, "noise_floor_lufs": -47.2, "effective_gate_lufs": -37.2}
        return meters

    chain.get_meters = meters_with_gate
    line = LiveEngine(base_cfg(), chain).status_line()
    assert "floor  -47.2 gate  -37.2 LUFS" in line
    # chains without these meters omit the field instead of failing
    assert "floor" not in LiveEngine(base_cfg(), RecordingChain([])).status_line()


def test_db_floor_matches_stage_meters():
    from voicechain.dsp import DB_FLOOR_DBFS

    engine = LiveEngine(base_cfg(), RecordingChain([]))
    assert "in {:6.1f} dBFS".format(DB_FLOOR_DBFS) in engine.status_line()
