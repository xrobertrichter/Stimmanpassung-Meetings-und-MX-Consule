"""Tests for the command line interface. No audio hardware is needed.

Live commands are exercised with a fake ``sounddevice`` module; the offline
commands run the real chain, meters, synth and calibration modules.
"""

from __future__ import annotations

import _thread
import importlib.abc
import os
import importlib.machinery
import sys
import threading
import types
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from voicechain import cli
from voicechain.cli import EXIT_ERROR, EXIT_FAILED, EXIT_OK, SELFTEST_ROWS, main
from voicechain.config import default_config_path, load_config, save_config, validate_config
from voicechain.meters import true_peak_dbfs
from voicechain.synth import make_speech_like

SAMPLE_RATE = 48000


# ----------------------------------------------------------------------------- fakes
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


@pytest.fixture
def no_portaudio(monkeypatch):
    """Make ``import sounddevice`` fail the way it does without PortAudio."""
    monkeypatch.delitem(sys.modules, "sounddevice", raising=False)
    monkeypatch.setattr(sys, "meta_path", [_RaisingFinder()] + list(sys.meta_path))


RAW_DEVICES = [
    {"name": "RØDE NT-USB", "hostapi": 0, "max_input_channels": 1, "max_output_channels": 2, "default_samplerate": 48000.0},
    {"name": "BlackHole 2ch", "hostapi": 0, "max_input_channels": 2, "max_output_channels": 2, "default_samplerate": 48000.0},
]


def _make_fake_sd(blocks_to_feed: int) -> types.ModuleType:
    """Fake sounddevice whose stream feeds speech blocks, then sends Ctrl-C."""
    module = types.ModuleType("sounddevice")
    module.query_devices = lambda: [dict(d) for d in RAW_DEVICES]
    module.query_hostapis = lambda: [{"name": "Core Audio"}]
    module.check_input_settings = lambda **kwargs: None
    module.check_output_settings = lambda **kwargs: None
    module.CallbackAbort = type("CallbackAbort", (Exception,), {})
    module.fed_outputs: list[np.ndarray] = []

    class Stream:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.latency = (0.004, 0.006)
            self.stopped = False
            self.closed = False
            module.stream = self

        def start(self):
            block = self.kwargs["blocksize"]
            signal = make_speech_like(blocks_to_feed * block / SAMPLE_RATE + 1.0, SAMPLE_RATE, seed=3)

            def feeder():
                for i in range(blocks_to_feed):
                    indata = signal[i * block : (i + 1) * block].reshape(-1, 1)
                    outdata = np.zeros((block, self.kwargs["channels"][1]), dtype=np.float32)
                    self.kwargs["callback"](indata, outdata, block, None, None)
                    module.fed_outputs.append(outdata.copy())
                _thread.interrupt_main()  # like the user pressing Ctrl-C

            threading.Thread(target=feeder, daemon=True).start()

        def stop(self):
            self.stopped = True

        def close(self):
            self.closed = True

    module.Stream = Stream

    class CallbackStop(Exception):
        """Stands in for sounddevice.CallbackStop."""

    module.CallbackStop = CallbackStop
    module.record_source = np.zeros(0, dtype=np.float32)

    class InputStream:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.closed = False
            module.input_stream = self

        def start(self):
            block = self.kwargs["blocksize"]
            channels = self.kwargs["channels"]
            source = module.record_source

            def feeder():
                pos = 0
                while pos < len(source):
                    frames = min(block, len(source) - pos)
                    indata = np.zeros((frames, channels), dtype=np.float32)
                    indata[:, channels - 1] = source[pos : pos + frames]
                    try:
                        self.kwargs["callback"](indata, frames, None, None)
                    except CallbackStop:
                        return
                    pos += frames

            threading.Thread(target=feeder, daemon=True).start()

        def stop(self):
            self.closed = False  # stop precedes close; nothing else to release in the fake

        def close(self):
            self.closed = True

    module.InputStream = InputStream
    return module


def _write_wav(path: Path, x: np.ndarray, rate: int) -> None:
    sf.write(str(path), x, rate, subtype="PCM_16")


# ------------------------------------------------------------------------- devices
def test_devices_without_portaudio_reports_error_not_traceback(no_portaudio, capsys):
    code = main(["devices"])
    captured = capsys.readouterr()
    assert code == EXIT_ERROR
    assert "PortAudio" in captured.err
    assert "brew install portaudio" in captured.err
    assert "Traceback" not in captured.err


def test_run_without_portaudio_reports_error(no_portaudio, capsys):
    code = main(["run"])
    captured = capsys.readouterr()
    assert code == EXIT_ERROR
    assert "PortAudio" in captured.err
    assert "Traceback" not in captured.err


# ----------------------------------------------------------------------- make-sample
def test_make_sample_writes_48k_mono_wav(tmp_path):
    out = tmp_path / "nested" / "sample.wav"
    assert main(["make-sample", "--out", str(out)]) == EXIT_OK
    info = sf.info(str(out))
    assert info.samplerate == SAMPLE_RATE
    assert info.channels == 1
    assert 30.0 <= info.duration <= 120.0  # usable as a calibration sample
    data, _ = sf.read(str(out), dtype="float32")
    assert np.max(np.abs(data)) > 0.01  # not silent


# --------------------------------------------------------------------------- process
def test_process_writes_output_of_same_length_and_prints_measurements(tmp_path, capsys):
    x = make_speech_like(12.0, SAMPLE_RATE, level_dbfs=-26.0, noise_floor_dbfs=-40.0, sibilance_db=6.0, seed=1)
    src = tmp_path / "in.wav"
    dst = tmp_path / "out" / "processed.wav"
    _write_wav(src, x, SAMPLE_RATE)

    assert main(["process", "--in", str(src), "--out", str(dst)]) == EXIT_OK
    y, rate = sf.read(str(dst), dtype="float32")
    assert rate == SAMPLE_RATE
    assert y.ndim == 1
    assert len(y) == len(x)
    assert np.all(np.isfinite(y))
    assert np.max(np.abs(y)) > 0.01

    cfg = load_config(str(default_config_path()))
    ceiling = float(cfg["chain"]["limiter"]["ceiling_dbfs"])
    assert true_peak_dbfs(y, SAMPLE_RATE) <= ceiling + cli.TRUE_PEAK_TOLERANCE_DB

    out = capsys.readouterr().out
    lines = out.splitlines()
    before = [ln for ln in lines if ln.startswith("before")]
    after = [ln for ln in lines if ln.startswith("after")]
    assert len(before) == 2 and len(after) == 2  # basic meters + speech statistics
    for ln in (before[0], after[0]):
        assert "rms" in ln and "true peak" in ln and "short-term" in ln and "LUFS" in ln
    for ln in (before[1], after[1]):
        assert "noise floor" in ln and "speech" in ln and "sibilance" in ln
    assert "written" in out


def test_process_mixes_stereo_to_mono(tmp_path, capsys):
    mono = make_speech_like(3.0, SAMPLE_RATE, seed=2)
    stereo = np.stack([mono, np.zeros_like(mono)], axis=1)
    src = tmp_path / "stereo.wav"
    dst = tmp_path / "out.wav"
    _write_wav(src, stereo, SAMPLE_RATE)
    assert main(["process", "--in", str(src), "--out", str(dst)]) == EXIT_OK
    y, out_rate = sf.read(str(dst), dtype="float32")
    assert out_rate == SAMPLE_RATE
    assert len(y) == len(mono)
    assert np.max(np.abs(y)) > 0.01
    captured = capsys.readouterr()
    assert "speech statistics skipped" in captured.out  # 3 s is too short for noise-floor statistics
    assert captured.out.count("rms") == 2


def test_process_rejects_other_sample_rate_with_message(tmp_path, capsys):
    rate = 44100
    src = tmp_path / "in44.wav"
    _write_wav(src, make_speech_like(3.0, rate, seed=2), rate)
    code = main(["process", "--in", str(src), "--out", str(tmp_path / "o.wav")])
    captured = capsys.readouterr()
    assert code == EXIT_ERROR
    assert "44100" in captured.err and "48000" in captured.err
    assert "Traceback" not in captured.err
    assert not (tmp_path / "o.wav").exists()


def test_process_missing_input_is_reported(tmp_path, capsys):
    code = main(["process", "--in", str(tmp_path / "missing.wav"), "--out", str(tmp_path / "o.wav")])
    assert code == EXIT_ERROR
    err = capsys.readouterr().err
    assert "not found" in err and "Traceback" not in err


@pytest.mark.parametrize("command", ["process", "calibrate"])
def test_non_wav_input_is_reported_without_traceback(tmp_path, capsys, command):
    text = tmp_path / "text.wav"
    text.write_text("this is not audio", encoding="utf-8")
    if command == "process":
        code = main(["process", "--in", str(text), "--out", str(tmp_path / "o.wav")])
    else:
        code = main(["calibrate", "--wav", str(text), "--out", str(tmp_path / "c.yaml"), "--report", str(tmp_path / "r.md")])
    err = capsys.readouterr().err
    assert code == EXIT_ERROR
    assert "text.wav" in err
    assert "Traceback" not in err


def test_empty_file_is_reported(tmp_path, capsys):
    empty = tmp_path / "empty.wav"
    empty.write_bytes(b"")
    code = main(["process", "--in", str(empty), "--out", str(tmp_path / "o.wav")])
    err = capsys.readouterr().err
    assert code == EXIT_ERROR and "empty.wav" in err and "Traceback" not in err


def test_directory_as_input_is_reported(tmp_path, capsys):
    folder = tmp_path / "folder.wav"
    folder.mkdir()
    code = main(["process", "--in", str(folder), "--out", str(tmp_path / "o.wav")])
    err = capsys.readouterr().err
    assert code == EXIT_ERROR and "directory" in err and "Traceback" not in err
    code = main(["calibrate", "--wav", str(folder)])
    err = capsys.readouterr().err
    assert code == EXIT_ERROR and "Traceback" not in err


def test_calibrate_with_directory_is_reported_as_directory(tmp_path, capsys):
    folder = tmp_path / "samples"
    folder.mkdir()
    code = main(["calibrate", "--wav", str(folder)])
    err = capsys.readouterr().err
    assert code == EXIT_ERROR
    assert "is a directory, expected a WAV file" in err and "Traceback" not in err


def test_output_path_that_is_a_directory_is_reported(tmp_path, capsys):
    folder = tmp_path / "existing_dir"
    folder.mkdir()
    code = main(["make-sample", "--out", str(folder)])
    err = capsys.readouterr().err
    assert code == EXIT_ERROR
    assert "is a directory" in err and ".wav" in err and "Traceback" not in err
    assert "LibsndfileError" not in err


def test_output_path_without_extension_is_reported(tmp_path, capsys):
    src = tmp_path / "in.wav"
    _write_wav(src, make_speech_like(2.0, SAMPLE_RATE, seed=5), SAMPLE_RATE)
    code = main(["process", "--in", str(src), "--out", str(tmp_path / "out" / "processed")])
    err = capsys.readouterr().err
    assert code == EXIT_ERROR
    assert "must end in .wav" in err and "no extension" in err and "Traceback" not in err
    assert not (tmp_path / "out").exists()  # nothing is created for a rejected target
    code = main(["calibrate", "--wav", str(src), "--out", str(tmp_path / "cfg.txt")])
    err = capsys.readouterr().err
    assert code == EXIT_ERROR and ".yaml" in err
    code = main(["selftest", "--report", str(tmp_path / "report.wav")])
    err = capsys.readouterr().err
    assert code == EXIT_ERROR and ".md" in err


@pytest.mark.parametrize("command", ["process", "make-sample", "selftest-report"])
def test_unwritable_output_path_is_reported(tmp_path, capsys, command):
    blocker = tmp_path / "blocker"
    blocker.write_text("I am a file, not a directory", encoding="utf-8")
    target = blocker / "sub" / "out.wav"
    if command == "process":
        src = tmp_path / "in.wav"
        _write_wav(src, make_speech_like(2.0, SAMPLE_RATE, seed=5), SAMPLE_RATE)
        code = main(["process", "--in", str(src), "--out", str(target)])
    elif command == "make-sample":
        code = main(["make-sample", "--out", str(target)])
    else:
        code = main(["selftest", "--report", str(blocker / "report.md")])
    err = capsys.readouterr().err
    assert code == EXIT_ERROR
    assert "blocker" in err
    assert "Traceback" not in err


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_permission_denied_output_is_reported(tmp_path, capsys):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        code = main(["make-sample", "--out", str(locked / "x.wav")])
    finally:
        locked.chmod(0o700)
    err = capsys.readouterr().err
    assert code == EXIT_ERROR and "PermissionError" in err and "Traceback" not in err


# ---------------------------------------------------------------------------- record
def test_record_writes_48k_24bit_wav_and_shows_progress(monkeypatch, tmp_path, capsys):
    fake_sd = _make_fake_sd(0)
    seconds = 2.0
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    fake_sd.record_source = (0.5 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)  # -6 dBFS
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    out = tmp_path / "rec" / "sample.wav"

    code = main(["record", "--out", str(out), "--seconds", str(seconds)])
    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    info = sf.info(str(out))
    assert info.samplerate == SAMPLE_RATE and info.channels == 1 and info.subtype == "PCM_24"
    assert info.frames == int(seconds * SAMPLE_RATE)
    assert fake_sd.input_stream.kwargs["device"] == 0  # RØDE from the config
    assert fake_sd.input_stream.closed
    assert "10 s still" in captured.err  # recording instructions
    assert "rest " in captured.err and "peak " in captured.err
    assert "ZU LAUT" not in captured.err
    assert "recorded 2.0 s" in captured.out and "peak -6.0 dBFS" in captured.out


def test_record_warns_when_too_hot_and_accepts_device_override(monkeypatch, tmp_path, capsys):
    fake_sd = _make_fake_sd(0)
    fake_sd.record_source = np.full(int(1.5 * SAMPLE_RATE), 0.95, dtype=np.float32)
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    code = main(["record", "--out", str(tmp_path / "hot.wav"), "--seconds", "1.5", "--input-device", "blackhole 2ch"])
    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert fake_sd.input_stream.kwargs["device"] == 1
    assert "ZU LAUT" in captured.err
    assert "warning: peak above -3 dBFS" in captured.err


def test_record_without_portaudio_reports_error(no_portaudio, tmp_path, capsys):
    code = main(["record", "--out", str(tmp_path / "x.wav"), "--seconds", "1"])
    err = capsys.readouterr().err
    assert code == EXIT_ERROR and "PortAudio" in err and "Traceback" not in err


@pytest.mark.parametrize("value", ["0", "-5", "nan", "abc"])
def test_record_rejects_invalid_seconds_before_creating_anything(monkeypatch, tmp_path, capsys, value):
    monkeypatch.setitem(sys.modules, "sounddevice", _make_fake_sd(0))
    out_dir = tmp_path / "never_created"
    with pytest.raises(SystemExit) as excinfo:
        main(["record", "--out", str(out_dir / "x.wav"), "--seconds", value])
    assert excinfo.value.code == 2
    assert "--seconds" in capsys.readouterr().err
    assert not out_dir.exists()  # argparse rejects the value before the output directory is created


def test_record_backend_failure_at_start_is_reported(monkeypatch, tmp_path, capsys):
    fake_sd = _make_fake_sd(0)

    class PortAudioError(Exception):
        """Stands in for sounddevice.PortAudioError (a direct Exception subclass)."""

    class FailingInputStream(fake_sd.InputStream):
        def start(self):
            raise PortAudioError("Unanticipated host error [PaErrorCode -9999]")

    fake_sd.PortAudioError = PortAudioError
    fake_sd.InputStream = FailingInputStream
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    code = main(["record", "--out", str(tmp_path / "x.wav"), "--seconds", "1"])
    err = capsys.readouterr().err
    assert code == EXIT_ERROR
    assert "Recording from 'RØDE NT-USB' failed" in err and "-9999" in err
    assert "Traceback" not in err
    assert fake_sd.input_stream.closed
    assert not (tmp_path / "x.wav").exists()


# -------------------------------------------------------------------------- selftest
def test_selftest_passes_and_report_has_mandatory_rows(tmp_path, capsys):
    report = tmp_path / "reports" / "selftest.md"
    code = main(["selftest", "--report", str(report)])
    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    text = report.read_text(encoding="utf-8")
    for row in SELFTEST_ROWS:
        assert row in text, row
        assert row in out
    assert "BESTANDEN" in text
    assert "NICHT BESTANDEN" not in text
    assert "NEIN" not in text
    # the table carries numbers, not just labels
    assert "dBTP" in text and "LUFS" in text and "ms" in text


def test_selftest_fails_with_exit_code_1_when_chain_is_disabled(tmp_path, capsys):
    cfg = load_config(str(default_config_path()))
    for stage in ("highpass", "expander", "deesser", "eq", "compressor", "normalizer"):
        cfg["chain"][stage]["enabled"] = False
    validate_config(cfg)
    cfg_path = tmp_path / "disabled.yaml"
    save_config(cfg, str(cfg_path))

    code = main(["selftest", "--config", str(cfg_path)])
    out = capsys.readouterr().out
    assert code == EXIT_FAILED
    assert "NICHT BESTANDEN" in out
    # noise reduction without expander is ~0 dB, so that row must be the one failing
    noise_row = [ln for ln in out.splitlines() if SELFTEST_ROWS[0] in ln][0]
    assert "NEIN" in noise_row


def test_selftest_rows_are_structured():
    cfg = load_config(str(default_config_path()))
    rows = cli.run_selftest(cfg)
    assert [r["name"] for r in rows] == list(SELFTEST_ROWS)
    for row in rows:
        assert isinstance(row["passed"], bool)
        assert row["target"] and row["actual"]
    assert all(r["passed"] for r in rows)


# ------------------------------------------------------------------------- calibrate
def test_calibrate_writes_config_and_report(tmp_path, capsys):
    sample = tmp_path / "sample.wav"
    assert main(["make-sample", "--out", str(sample)]) == EXIT_OK
    out_cfg = tmp_path / "cfg" / "chain.calibrated.yaml"
    report = tmp_path / "rep" / "calibration.md"
    code = main(["calibrate", "--wav", str(sample), "--out", str(out_cfg), "--report", str(report)])
    captured = capsys.readouterr()
    stdout = captured.out
    assert code == EXIT_OK, stdout + captured.err
    for key in ("noise_floor_reduction", "sibilance_reduced", "loudness_in_target", "true_peak_below_ceiling"):
        assert key in stdout  # the acceptance criteria are echoed on the console
    assert out_cfg.exists() and report.exists()
    cfg = load_config(str(out_cfg))
    validate_config(cfg)
    assert cfg["sample_rate"] == SAMPLE_RATE
    assert report.stat().st_size > 200
    assert "calibration written" in stdout


def test_calibrate_missing_wav_is_reported(tmp_path, capsys):
    code = main(["calibrate", "--wav", str(tmp_path / "nope.wav")])
    assert code == EXIT_ERROR
    assert "not found" in capsys.readouterr().err


# ------------------------------------------------------------------------------ run
def test_run_with_fake_stream_processes_blocks_and_stops_on_ctrl_c(monkeypatch, tmp_path, capsys):
    blocks = 40
    fake_sd = _make_fake_sd(blocks)
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    cfg = load_config(str(default_config_path()))
    cfg["adaptive"]["log_path"] = str(tmp_path / "logs" / "adaptive.jsonl")
    cfg["adaptive"]["interval_s"] = 0.1  # smallest value validate_config allows
    cfg_path = tmp_path / "run.yaml"
    save_config(cfg, str(cfg_path))

    code = main(["run", "--config", str(cfg_path), "--freeze"])
    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert fake_sd.stream.kwargs["device"] == (0, 1)
    assert fake_sd.stream.kwargs["channels"] == (1, 2)
    assert fake_sd.stream.stopped and fake_sd.stream.closed
    assert len(fake_sd.fed_outputs) == blocks
    stacked = np.concatenate(fake_sd.fed_outputs)
    assert np.all(np.isfinite(stacked))
    np.testing.assert_array_equal(stacked[:, 0], stacked[:, 1])  # mono duplicated
    assert np.max(np.abs(stacked)) > 0.001  # the chain produced output
    assert "stopped after {} blocks".format(blocks) in captured.err
    assert "adaptive: on (frozen)" in captured.err


def test_run_builds_analyzer_with_deesser_band_of_loaded_config(monkeypatch, tmp_path, capsys):
    """The live analyzer must measure in the de-esser band of the (calibrated) configuration."""
    import voicechain.adaptive as adaptive

    created: list[dict] = []
    real_analyzer = adaptive.LiveAnalyzer

    class RecordingAnalyzer(real_analyzer):
        def __init__(self, sample_rate, **kwargs):
            created.append({"sample_rate": sample_rate, **kwargs})
            super().__init__(sample_rate, **kwargs)

    monkeypatch.setattr(adaptive, "LiveAnalyzer", RecordingAnalyzer)
    fake_sd = _make_fake_sd(5)
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    cfg = load_config(str(default_config_path()))
    cfg["chain"]["deesser"]["freq_hz"] = 7200.0  # calibrated to a sibilance centroid off the default
    cfg["chain"]["deesser"]["bandwidth_oct"] = 0.5
    cfg["adaptive"]["log_path"] = str(tmp_path / "logs" / "adaptive.jsonl")
    cfg_path = tmp_path / "calibrated.yaml"
    save_config(cfg, str(cfg_path))

    code = main(["run", "--config", str(cfg_path)])
    assert code == EXIT_OK, capsys.readouterr().err
    assert len(created) == 1
    assert created[0]["sample_rate"] == SAMPLE_RATE
    assert created[0]["deesser_freq_hz"] == 7200.0
    assert created[0]["deesser_bandwidth_oct"] == 0.5
    assert real_analyzer(SAMPLE_RATE, deesser_freq_hz=7200.0, deesser_bandwidth_oct=0.5).band_hz == (7200.0, 0.5)


def test_run_with_unknown_device_override_is_reported(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "sounddevice", _make_fake_sd(0))
    code = main(["run", "--no-adaptive", "--input-device", "Shure SM7B"])
    err = capsys.readouterr().err
    assert code == EXIT_ERROR
    assert "No input device matches 'Shure SM7B'" in err
    assert "BlackHole 2ch" in err  # the available devices are listed


# ------------------------------------------------------------------------------ paths
def test_relative_paths_resolve_against_repo_root(tmp_path, monkeypatch):
    fake_root = tmp_path / "repo"
    (fake_root / "config").mkdir(parents=True)
    (fake_root / "config" / "chain.default.yaml").write_text("", encoding="utf-8")
    monkeypatch.setattr(cli, "REPO_ROOT", fake_root)
    workdir = tmp_path / "elsewhere"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    assert cli.resolve_input_path("config/chain.default.yaml") == fake_root / "config" / "chain.default.yaml"
    # an existing file relative to the current directory is still found
    local = workdir / "local.wav"
    local.write_bytes(b"")
    assert cli.resolve_input_path("local.wav") == local
    # a missing relative file resolves to the repo root (for the error message)
    assert cli.resolve_input_path("nope.wav") == fake_root / "nope.wav"
    out = cli.resolve_output_path(str(Path("logs") / "sub" / "x.jsonl"), suffixes=(".jsonl",))
    assert out == fake_root / "logs" / "sub" / "x.jsonl"
    assert out.parent.is_dir()
    absolute = cli.resolve_output_path(str(tmp_path / "a" / "b.txt"), suffixes=(".md", ".txt"))
    assert absolute == tmp_path / "a" / "b.txt" and absolute.parent.is_dir()
    with pytest.raises(ValueError):
        cli.resolve_output_path(str(tmp_path / "a" / "b.txt"))  # default suffix set is .wav


def test_true_peak_tolerance_is_shared_with_calibrate():
    from voicechain import calibrate

    assert cli.TRUE_PEAK_TOLERANCE_DB is calibrate.TRUE_PEAK_TOLERANCE_DB


def test_help_lists_all_commands(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for command in ("devices", "record", "run", "calibrate", "process", "selftest", "make-sample"):
        assert command in out
