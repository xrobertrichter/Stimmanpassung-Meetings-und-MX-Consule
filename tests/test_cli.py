"""Tests for the command line interface. No audio hardware is needed.

Live commands are exercised with a fake ``sounddevice`` module; the offline
commands run the real chain, meters, synth and calibration modules.
"""

from __future__ import annotations

import _thread
import importlib.abc
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


def test_process_accepts_other_sample_rate_and_stereo(tmp_path, capsys):
    rate = 44100
    mono = make_speech_like(3.0, rate, seed=2)
    stereo = np.stack([mono, np.zeros_like(mono)], axis=1)
    src = tmp_path / "stereo44.wav"
    dst = tmp_path / "out44.wav"
    _write_wav(src, stereo, rate)
    assert main(["process", "--in", str(src), "--out", str(dst)]) == EXIT_OK
    y, out_rate = sf.read(str(dst), dtype="float32")
    assert out_rate == rate
    assert len(y) == len(mono)
    assert np.max(np.abs(y)) > 0.01  # channel 0 (the configured input channel) was used
    captured = capsys.readouterr()
    assert "44100 Hz differs" in captured.err
    assert "speech statistics skipped" in captured.out  # 3 s is too short for noise-floor statistics
    assert captured.out.count("rms") == 2


def test_process_missing_input_is_reported(tmp_path, capsys):
    code = main(["process", "--in", str(tmp_path / "missing.wav"), "--out", str(tmp_path / "o.wav")])
    assert code == EXIT_ERROR
    err = capsys.readouterr().err
    assert "not found" in err and "Traceback" not in err


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


def test_run_with_unknown_device_override_is_reported(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "sounddevice", _make_fake_sd(0))
    code = main(["run", "--no-adaptive", "--input-device", "Shure SM7B"])
    err = capsys.readouterr().err
    assert code == EXIT_ERROR
    assert "No input device matches 'Shure SM7B'" in err
    assert "BlackHole 2ch" in err  # the available devices are listed


# ------------------------------------------------------------------------------ paths
def test_relative_paths_resolve_against_repo_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.resolve_input_path("config/chain.default.yaml") == cli.REPO_ROOT / "config" / "chain.default.yaml"
    # an existing file relative to the current directory is still found
    local = tmp_path / "local.wav"
    local.write_bytes(b"")
    assert cli.resolve_input_path("local.wav") == local
    out = cli.resolve_output_path(str(Path("logs") / "sub" / "x.jsonl"))
    assert out == cli.REPO_ROOT / "logs" / "sub" / "x.jsonl"
    assert out.parent.is_dir()
    absolute = cli.resolve_output_path(str(tmp_path / "a" / "b.txt"))
    assert absolute == tmp_path / "a" / "b.txt" and absolute.parent.is_dir()


def test_help_lists_all_commands(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for command in ("devices", "run", "calibrate", "process", "selftest", "make-sample"):
        assert command in out
