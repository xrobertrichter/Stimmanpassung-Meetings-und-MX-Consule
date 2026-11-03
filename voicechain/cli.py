"""Command line interface: ``python -m voicechain <command>``.

Commands (docs/00_Beauftragung.md, section 3.1):

* ``devices``      list audio devices
* ``run``          live operation Rode -> BlackHole with adaptive control
* ``calibrate``    analyze a speech sample, write a new YAML and a report
* ``process``      run a WAV offline through the chain (A/B listening)
* ``selftest``     synthetic signals through the chain, pass/fail table
* ``make-sample``  write a synthetic speech-like WAV to rehearse calibration

Relative paths are resolved against the repository root, output directories
are created. Every command returns an exit code; ``main()`` never lets a
traceback through for expected error conditions (missing PortAudio, missing
files, unknown devices).
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

import numpy as np

from voicechain.calibrate import TRUE_PEAK_TOLERANCE_DB  # one tolerance for calibrate, selftest and tests
from voicechain.runtime import (
    AudioBackendError,
    DeviceNotFoundError,
    LiveEngine,
    format_device_table,
    list_devices,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# Exit codes: 0 ok, 1 selftest/calibration criteria failed, 2 usage or environment error.
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ERROR = 2

# Default output locations (relative to the repository root).
DEFAULT_CALIBRATED_CONFIG = Path("config") / "chain.calibrated.yaml"
DEFAULT_CALIBRATION_REPORT = Path("reports") / "calibration_report.md"

# Self-test signal: 20 s speech-like signal, -26 dBFS speech, +6 dB sibilance.
# Noise floor -50 dBFS (project decision, coordination note of the project lead):
# a Rode NT-USB in a quiet room measures around -53 dBFS (config/chain.default.yaml).
# With a floor of -40 dBFS (14 dB SNR) the calibration's protection rule
# "expander threshold <= speech level - 12 dB" keeps the threshold within 2 dB of
# the floor, so the expander can only attenuate a little by design and the
# 6 dB criterion cannot be met by any chain on such a signal.
SELFTEST_DURATION_S = 20.0
SELFTEST_NOISE_FLOOR_DBFS = -50.0
# Section 6.3 requires a pause >= 5 s for the pause-loudness criterion; 6 s in the
# middle of the signal leaves 7 s of speech on either side.
SELFTEST_LONG_PAUSE_S = 6.0
SELFTEST_SPEECH_LEVEL_DBFS = -26.0
SELFTEST_SIBILANCE_BOOST_DB = 6.0
SELFTEST_SEED = 20261102  # fixed seed: the self-test must be reproducible

# Acceptance criteria of docs/00_Beauftragung.md section 6.3 (noise floor, sibilance,
# loudness, true peak) are evaluated by voicechain.calibrate.evaluate, not re-implemented
# here. There the noise floor is judged relative to the speech level (SNR gain: speech
# level minus floor, after minus before), because the normalizer deliberately lifts a
# quiet recording as a whole; the absolute values are reported alongside.
# The remaining criteria below come from section 4.3 (runtime behaviour of the chain).
SPEECH_ABOVE_FLOOR_MIN_DB = 6.0  # speech must stay clearly measurable above the floor after processing
COMPUTE_BUDGET_MS = 1.0  # "48 kHz Block 256 muss in < 1 ms rechnen"
COMPUTE_BUDGET_BLOCK = 256  # block size the budget refers to
SILENCE_LIMIT_DBFS = -100.0  # "Stille rein -> Stille raus": anything below is numerically silent
SILENCE_DURATION_S = 2.0  # long enough for every release time constant to settle
IMPULSE_PERIOD_S = 0.5  # single-sample impulses, spaced so the limiter fully recovers
IMPULSE_COUNT = 6
SINE_FREQ_HZ = 1000.0  # reference tone for the limiter ceiling test
SINE_DURATION_S = 2.0
SINE_LEVEL_DBFS = 0.0  # full scale: the hardest case for the limiter

# Names of the mandatory report rows; tests check for them.
SELFTEST_ROWS = (
    "Rauschteppich-Absenkung (relativ zur Sprache)",
    "Pausenlautheit nach der Kette",
    "Sprachpegel",
    "Zischanteil-Absenkung",
    "Lautheit (integriert; Short-Term max)",
    "True Peak",
    "Nicht-finite Eingangsbloecke (Sprachsignal)",
    "NaN-Block -> gezaehlt, Ausgang endlich und still",
    "Rechenzeit je 256er-Block",
    "Limiter-Ceiling bei Vollaussteuerungs-Sinus",
    "Limiter-Ceiling bei Impulsen",
    "Stille -> Stille",
)
# A block containing NaN must be counted, muted and must not poison the chain:
# after this many clean silent blocks the output has to be silent again.
NAN_RECOVERY_BLOCKS = 200  # ~1 s at 256/48 kHz, longer than every release time
NAN_PROBE_CLEAN_BLOCKS = 200  # ~1 s of clean speech before the NaN block: enough to prove "0"


# ----------------------------------------------------------------------------- helpers
def resolve_input_path(path_str: str) -> Path:
    """Resolve a path given on the command line: absolute as-is, else repo root, then cwd."""
    path = Path(path_str).expanduser()
    if path.is_absolute():
        return path
    repo_candidate = REPO_ROOT / path
    if repo_candidate.exists():
        return repo_candidate
    cwd_candidate = Path.cwd() / path
    if cwd_candidate.exists():
        return cwd_candidate
    return repo_candidate


def resolve_output_path(path_str: str, suffixes: tuple[str, ...] = (".wav",)) -> Path:
    """Resolve an output path (absolute as-is, else repo root) and create its directory.

    The target must be a file name with one of ``suffixes`` (libsndfile and the
    YAML/Markdown writers derive the format from the extension and otherwise fail
    with messages that do not name the cause), and it must not be an existing
    directory.
    """
    path = Path(path_str).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    if path.is_dir():
        raise IsADirectoryError("{} is a directory; give a file name ending in {}".format(path, " or ".join(suffixes)))
    if path.suffix.lower() not in suffixes:
        raise ValueError(
            "{}: output file name must end in {} (got {!r})".format(
                path, " or ".join(suffixes), path.suffix or "no extension"
            )
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _config_path(arg: str | None) -> Path:
    from voicechain.config import default_config_path

    if arg is None:
        return Path(default_config_path())
    return resolve_input_path(arg)


def _load_config(arg: str | None) -> tuple[dict, Path]:
    """Load and validate a YAML configuration (``_calibration`` metadata is tolerated by the validator)."""
    from voicechain.config import load_config

    path = _config_path(arg)
    if not path.exists():
        raise FileNotFoundError("configuration file not found: {}".format(path))
    return load_config(str(path)), path


def read_input_wav(path: Path, expected_sr: int) -> np.ndarray:
    """Read a WAV as float32 mono via the shared reader ``calibrate.read_mono_wav``
    (multichannel files are mixed down; wrong sample rate, unreadable or empty
    files raise ValueError with the path, which ``main()`` reports without a
    traceback). A directory is reported as such instead of "file not found".
    """
    from voicechain.calibrate import read_mono_wav

    if path.is_dir():
        raise IsADirectoryError("{} is a directory, not a WAV file".format(path))
    mono, _ = read_mono_wav(str(path), expected_sr=expected_sr, channel=None)
    return mono


def basic_measurements(x: np.ndarray, sample_rate: int) -> dict:
    """RMS, true peak and loudness via voicechain.meters; works for signals of any length."""
    from voicechain import meters as m

    loudness = m.LoudnessMeter(sample_rate)
    loudness.push(x)
    return {
        "rms_dbfs": float(m.rms_dbfs(x)),
        "true_peak_dbtp": float(m.true_peak_dbfs(x, sample_rate)),
        "short_term_lufs": float(loudness.short_term_lufs()),
        "integrated_lufs": float(loudness.integrated_lufs()),
    }


def format_basic_line(label: str, meas: dict) -> str:
    """Console line from :func:`basic_measurements` (short-term = last 3 s of the file)."""
    return "{:<8} rms {:6.1f} dBFS | true peak {:6.1f} dBTP | short-term {:6.1f} LUFS | integrated {:6.1f} LUFS".format(
        label, meas["rms_dbfs"], meas["true_peak_dbtp"], meas["short_term_lufs"], meas["integrated_lufs"]
    )


def format_analysis_line(label: str, meas: dict) -> str:
    """Console line from a ``voicechain.calibrate.analyze`` result (speech statistics)."""
    return (
        "{:<8} noise floor {:6.1f} dBFS | speech {:6.1f} dBFS | SNR {:5.1f} dB | sibilance {:+5.1f} dB | "
        "short-term max {:6.1f} LUFS"
    ).format(
        label,
        meas["noise_floor_dbfs"],
        meas["speech_level_dbfs"],
        meas["snr_db"],
        meas["sibilance_ratio_db"],
        meas["short_term_lufs_max"],
    )


# ---------------------------------------------------------------------------- commands
def cmd_devices(args: argparse.Namespace) -> int:
    devices = list_devices()
    if not devices:
        print("No audio devices found.", file=sys.stderr)
        return EXIT_ERROR
    print(format_device_table(devices))
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    from voicechain.dsp.chain import Chain

    cfg, cfg_path = _load_config(args.config)
    chain = Chain.from_config(cfg)
    controller = None
    analyzer = None
    adaptive_cfg = cfg.get("adaptive", {})
    adaptive_on = bool(adaptive_cfg.get("enabled", True)) and not args.no_adaptive
    if adaptive_on:
        from voicechain.adaptive import AdaptiveController, LiveAnalyzer

        log_path = adaptive_cfg.get("log_path")
        if log_path:
            log_path = str(resolve_output_path(str(log_path), suffixes=(".jsonl", ".log", ".txt")))
        controller = AdaptiveController(cfg, chain, log_path=log_path)
        deesser_cfg = cfg["chain"]["deesser"]
        analyzer = LiveAnalyzer(
            int(cfg["sample_rate"]),
            deesser_freq_hz=float(deesser_cfg["freq_hz"]),
            deesser_bandwidth_oct=float(deesser_cfg["bandwidth_oct"]),
        )
        if args.freeze:
            controller.freeze(True)
    engine = LiveEngine(
        cfg,
        chain,
        controller=controller,
        analyzer=analyzer,
        input_device=args.input_device,
        output_device=args.output_device,
    )
    print(
        "config: {} | adaptive: {}{}".format(
            cfg_path,
            "on" if adaptive_on else "off",
            " (frozen)" if adaptive_on and args.freeze else "",
        ),
        file=sys.stderr,
    )
    try:
        engine.run_forever()
    finally:
        if controller is not None:
            controller.close()  # flushes and closes the JSONL change log
    print(
        "stopped after {} blocks, {} xruns.".format(engine.blocks_processed, engine.xruns),
        file=sys.stderr,
    )
    return EXIT_OK


RECORD_INSTRUCTIONS = (
    "Aufnahme fuer die Kalibrierung: zuerst 10 s still sein (Raumgeraeusch), dann normal und in "
    "Seminarlautstaerke sprechen, in der Mitte eine Pause von mindestens 5 s machen, dann weitersprechen."
)


def cmd_record(args: argparse.Namespace) -> int:
    from voicechain.runtime import RECORD_CLIP_WARN_DBFS, record_wav

    cfg, _ = _load_config(args.config)
    out_path = resolve_output_path(args.out)  # --seconds was already validated by argparse
    print(RECORD_INSTRUCTIONS, file=sys.stderr)
    print(
        "recording {:.0f} s at {} Hz mono from {!r} -> {} (Ctrl-C stops early and keeps the recording)".format(
            args.seconds,
            cfg["sample_rate"],
            args.input_device if args.input_device is not None else cfg["input"]["device"],
            out_path,
        ),
        file=sys.stderr,
    )

    def on_progress(remaining_s: float, peak_dbfs: float, too_hot: bool) -> None:
        warning = "  ZU LAUT (> {:.0f} dBFS): Abstand vergroessern".format(RECORD_CLIP_WARN_DBFS) if too_hot else ""
        print("rest {:5.1f} s | peak {:6.1f} dBFS{}".format(remaining_s, peak_dbfs, warning), file=sys.stderr, flush=True)

    result = record_wav(cfg, out_path, float(args.seconds), input_device=args.input_device, on_progress=on_progress)
    print(
        "recorded {:.1f} s ({} samples), peak {:.1f} dBFS, {} overflows{} -> {}".format(
            result["seconds"],
            result["samples"],
            result["peak_dbfs"],
            result["overflows"],
            " (stopped early)" if result["stopped_early"] else "",
            result["path"],
        )
    )
    if result["peak_dbfs"] > RECORD_CLIP_WARN_DBFS:
        print(
            "warning: peak above {:.0f} dBFS; the recording may be clipped, repeat it with more distance".format(
                RECORD_CLIP_WARN_DBFS
            ),
            file=sys.stderr,
        )
    if result["overflows"]:
        print("warning: {} input overflows (dropouts) during the recording".format(result["overflows"]), file=sys.stderr)
    return EXIT_OK


def cmd_calibrate(args: argparse.Namespace) -> int:
    from voicechain.calibrate import run_calibration

    wav_path = resolve_input_path(args.wav)
    if wav_path.is_dir():
        raise IsADirectoryError("{} is a directory, expected a WAV file".format(wav_path))
    if not wav_path.exists():
        raise FileNotFoundError("WAV file not found: {}".format(wav_path))
    base_cfg_path = _config_path(args.config)
    if not base_cfg_path.exists():
        raise FileNotFoundError("configuration file not found: {}".format(base_cfg_path))
    out_cfg_path = resolve_output_path(args.out or str(DEFAULT_CALIBRATED_CONFIG), suffixes=(".yaml", ".yml"))
    report_path = resolve_output_path(args.report or str(DEFAULT_CALIBRATION_REPORT), suffixes=(".md", ".txt"))

    result = run_calibration(str(wav_path), str(base_cfg_path), str(out_cfg_path), str(report_path))
    for warning in result.get("warnings", []):
        print("warning: {}".format(warning), file=sys.stderr)
    print("calibration written: {}".format(out_cfg_path))
    print("report written:      {}".format(report_path))
    criteria = result.get("evaluation", {}).get("criteria", {})
    for key, item in criteria.items():
        print(
            "  {:<24} {:<4} {}  (Soll {})".format(
                key, "ok" if item.get("passed") else "FAIL", item.get("actual", ""), item.get("target", "")
            )
        )
    passed = bool(result["passed"])
    print("acceptance: {}".format("passed" if passed else "FAILED (see report)"))
    return EXIT_OK if passed else EXIT_FAILED


def cmd_process(args: argparse.Namespace) -> int:
    from voicechain.calibrate import process_with_chain

    cfg, _ = _load_config(args.config)
    in_path = resolve_input_path(args.infile)
    out_path = resolve_output_path(args.outfile)
    rate = int(cfg["sample_rate"])
    x = read_input_wav(in_path, rate)
    block_size = int(cfg["block_size"])

    t0 = time.perf_counter()
    y = process_with_chain(x, cfg)  # same block handling as calibrate
    elapsed = time.perf_counter() - t0

    import soundfile as sf

    sf.write(str(out_path), y, rate, subtype="PCM_24")

    print(format_basic_line("before", basic_measurements(x, rate)))
    print(format_basic_line("after", basic_measurements(y, rate)))
    # Speech statistics (noise floor, speech level, sibilance) need pauses and a
    # minimum length; calibrate.analyze defines that minimum.
    from voicechain.calibrate import MIN_DURATION_S, analyze

    if len(x) >= MIN_DURATION_S * rate:
        print(format_analysis_line("before", analyze(x, rate)))
        print(format_analysis_line("after", analyze(y, rate)))
    else:
        print(
            "speech statistics skipped: file is {:.1f} s, at least {:.0f} s with pauses are needed".format(
                len(x) / rate, MIN_DURATION_S
            )
        )
    blocks = max(1, math.ceil(len(x) / block_size))
    print(
        "written {} ({} samples, {} Hz); {:.2f} s processing, {:.3f} ms per {}-block".format(
            out_path, len(y), rate, elapsed, 1000.0 * elapsed / blocks, block_size
        )
    )
    return EXIT_OK


def _selftest_speech_signal(sample_rate: int) -> np.ndarray:
    from voicechain.synth import make_speech_like

    x = make_speech_like(
        SELFTEST_DURATION_S,
        sample_rate,
        level_dbfs=SELFTEST_SPEECH_LEVEL_DBFS,
        noise_floor_dbfs=SELFTEST_NOISE_FLOOR_DBFS,
        sibilance_db=SELFTEST_SIBILANCE_BOOST_DB,
        seed=SELFTEST_SEED,
        long_pause_s=SELFTEST_LONG_PAUSE_S,
        long_pause_at_s=(SELFTEST_DURATION_S - SELFTEST_LONG_PAUSE_S) / 2.0,
    )
    return np.asarray(x, dtype=np.float32)


def _time_per_block_ms(chain, sample_rate: int, block_size: int) -> float:
    """Median wall time of chain.process() for one block of speech-like noise."""
    rng = np.random.default_rng(SELFTEST_SEED)
    # 0.1 full-scale noise is loud enough to exercise every dynamics stage.
    blocks = rng.standard_normal((200, block_size)).astype(np.float32) * 0.1
    for block in blocks[:20]:  # warm-up: first calls pay for lazy numpy/scipy paths
        chain.process(block)
    times = []
    for block in blocks:
        t0 = time.perf_counter()
        chain.process(block)
        times.append(time.perf_counter() - t0)
    return 1000.0 * float(np.median(times))


def run_selftest(cfg: dict) -> list[dict]:
    """Run all self-test checks and return rows {name, target, actual, passed}."""
    from voicechain import meters as m
    from voicechain.dsp.chain import Chain

    sample_rate = int(cfg["sample_rate"])
    block_size = int(cfg["block_size"])
    chain_cfg = cfg["chain"]
    ceiling = float(chain_cfg["limiter"]["ceiling_dbfs"])
    rows: list[dict] = []

    def add(name: str, target: str, actual: str, passed: bool) -> None:
        rows.append({"name": name, "target": target, "actual": actual, "passed": bool(passed)})

    # --- speech-like signal through the chain -------------------------------------
    # The thresholds of expander, de-esser and compressor only make sense relative
    # to the signal they see, so the synthetic signal is first analyzed and the
    # chain parameters derived from it exactly as ``calibrate`` would do
    # (section 6.3 is formulated for the calibrated chain). ``cfg`` supplies the
    # base: bounds, enabled flags, EQ, normalizer and limiter settings. The
    # section 6.3 criteria are then judged by ``calibrate.evaluate`` itself.
    from voicechain.calibrate import analyze, evaluate, process_with_chain, recommend

    x = _selftest_speech_signal(sample_rate)
    fitted_cfg = recommend(analyze(x, sample_rate), cfg)
    y = process_with_chain(x, fitted_cfg)
    evaluation = evaluate(x, y, sample_rate, fitted_cfg)
    before = evaluation["before"]
    after = evaluation["after"]
    criteria = evaluation["criteria"]

    noise = criteria["noise_floor_reduction"]
    add(
        SELFTEST_ROWS[0],
        noise["target"],
        "{}; absolut {:.1f} -> {:.1f} dBFS".format(
            noise["actual"], before["noise_floor_dbfs"], after["noise_floor_dbfs"]
        ),
        noise["passed"],
    )
    pause = criteria["pause_loudness"]
    add(SELFTEST_ROWS[1], pause["target"], "{} ({})".format(pause["actual"], pause.get("note", "")), pause["passed"])
    speech_ok = (
        math.isfinite(after["speech_level_dbfs"])
        and after["speech_level_dbfs"] > after["noise_floor_dbfs"] + SPEECH_ABOVE_FLOOR_MIN_DB
    )
    add(
        SELFTEST_ROWS[2],
        "messbar, > Rauschteppich + {:.0f} dB".format(SPEECH_ABOVE_FLOOR_MIN_DB),
        "{:.1f} -> {:.1f} dBFS (SNR {:.1f} -> {:.1f} dB)".format(
            before["speech_level_dbfs"], after["speech_level_dbfs"], before["snr_db"], after["snr_db"]
        ),
        speech_ok,
    )
    sibilance = criteria["sibilance_reduced"]
    add(SELFTEST_ROWS[3], sibilance["target"], sibilance["actual"], sibilance["passed"])
    loudness = criteria["loudness_in_target"]
    add(
        SELFTEST_ROWS[4],
        loudness["target"],
        "{}; Short-Term max {:.1f} LUFS (vorher integriert {:.1f})".format(
            loudness["actual"], after["short_term_lufs_max"], before["integrated_lufs"]
        ),
        loudness["passed"],
    )
    true_peak = criteria["true_peak_below_ceiling"]
    add(
        SELFTEST_ROWS[5],
        true_peak["target"],
        "{} (vorher {:.2f} dBTP)".format(true_peak["actual"], before["true_peak_dbtp"]),
        true_peak["passed"],
    )
    # --- non-finite counter: clean speech must not count, one NaN block must ------
    # The same chain first sees NAN_PROBE_CLEAN_BLOCKS blocks of the speech signal
    # (counter must stay 0), then one NaN block (counter 1, output muted), then
    # silence (it must recover).
    chain_nan = Chain.from_config(cfg)
    for start in range(0, NAN_PROBE_CLEAN_BLOCKS * block_size, block_size):
        chain_nan.process(x[start : start + block_size])
    nonfinite_speech = int(chain_nan.get_meters()["input"]["nonfinite_blocks"])
    add(
        SELFTEST_ROWS[6],
        "0 nach {} Bloecken".format(NAN_PROBE_CLEAN_BLOCKS),
        str(nonfinite_speech),
        nonfinite_speech == 0 and bool(np.all(np.isfinite(x))),
    )
    nan_block = np.full(block_size, np.nan, dtype=np.float32)
    y_nan = np.asarray(chain_nan.process(nan_block), dtype=np.float32)
    silent_block = np.zeros(block_size, dtype=np.float32)
    tail_peak = 0.0
    tail_finite = True
    for _ in range(NAN_RECOVERY_BLOCKS):
        y_tail = np.asarray(chain_nan.process(silent_block), dtype=np.float32)
        if not np.all(np.isfinite(y_tail)):
            tail_finite = False
            break
        tail_peak = max(tail_peak, float(np.max(np.abs(y_tail))))
    nonfinite_counted = int(chain_nan.get_meters()["input"]["nonfinite_blocks"])
    nan_ok = (
        nonfinite_counted == 1
        and bool(np.all(np.isfinite(y_nan)))
        and tail_finite
        and (tail_peak == 0.0 or 20.0 * math.log10(tail_peak) <= SILENCE_LIMIT_DBFS)
    )
    add(
        SELFTEST_ROWS[7],
        "Zaehler 1, Ausgang endlich, danach Stille <= {:.0f} dBFS".format(SILENCE_LIMIT_DBFS),
        "Zaehler {}, NaN-Block {}, danach Spitze {} dBFS".format(
            nonfinite_counted,
            "endlich" if np.all(np.isfinite(y_nan)) else "NaN/inf",
            "-inf" if tail_peak == 0.0 else "{:.1f}".format(20.0 * math.log10(tail_peak)) if tail_finite else "NaN/inf",
        ),
        nan_ok,
    )

    # --- compute budget ---------------------------------------------------------------
    per_block_ms = _time_per_block_ms(Chain.from_config(cfg), sample_rate, COMPUTE_BUDGET_BLOCK)
    add(
        SELFTEST_ROWS[8],
        "< {:.1f} ms".format(COMPUTE_BUDGET_MS),
        "{:.3f} ms".format(per_block_ms),
        per_block_ms < COMPUTE_BUDGET_MS,
    )

    # --- full-scale sine: ceiling must hold -----------------------------------------
    n_sine = int(SINE_DURATION_S * sample_rate)
    t = np.arange(n_sine, dtype=np.float64) / sample_rate
    sine = (10.0 ** (SINE_LEVEL_DBFS / 20.0) * np.sin(2.0 * math.pi * SINE_FREQ_HZ * t)).astype(np.float32)
    y_sine = process_with_chain(sine, cfg)
    tp_sine = float(m.true_peak_dbfs(y_sine, sample_rate))
    add(
        SELFTEST_ROWS[9],
        "<= {:.1f} dBTP".format(ceiling),
        "{:.2f} dBTP".format(tp_sine),
        tp_sine <= ceiling + TRUE_PEAK_TOLERANCE_DB and np.all(np.isfinite(y_sine)),
    )

    # --- impulses: ceiling must hold, no NaN --------------------------------------------
    n_imp = int(IMPULSE_PERIOD_S * sample_rate * IMPULSE_COUNT)
    impulses = np.zeros(n_imp, dtype=np.float32)
    period = int(IMPULSE_PERIOD_S * sample_rate)
    for k in range(IMPULSE_COUNT):
        impulses[k * period + period // 2] = 1.0
    y_imp = process_with_chain(impulses, cfg)
    tp_imp = float(m.true_peak_dbfs(y_imp, sample_rate))
    add(
        SELFTEST_ROWS[10],
        "<= {:.1f} dBTP".format(ceiling),
        "{:.2f} dBTP".format(tp_imp),
        tp_imp <= ceiling + TRUE_PEAK_TOLERANCE_DB and np.all(np.isfinite(y_imp)),
    )

    # --- silence in -> silence out ---------------------------------------------------
    silence = np.zeros(int(SILENCE_DURATION_S * sample_rate), dtype=np.float32)
    y_sil = process_with_chain(silence, cfg)
    finite = bool(np.all(np.isfinite(y_sil)))
    peak = float(np.max(np.abs(y_sil))) if finite else float("inf")
    peak_dbfs = 20.0 * math.log10(peak) if peak > 0.0 else -float("inf")
    add(
        SELFTEST_ROWS[11],
        "Spitze <= {:.0f} dBFS, keine NaN".format(SILENCE_LIMIT_DBFS),
        "{} dBFS, {}".format(
            "-inf" if peak == 0.0 else "{:.1f}".format(peak_dbfs), "finite" if finite else "NaN/inf"
        ),
        finite and peak_dbfs <= SILENCE_LIMIT_DBFS,
    )
    return rows


def format_selftest_table(rows: list[dict], cfg_path: Path) -> str:
    name_w = max(len(r["name"]) for r in rows)
    target_w = max(len(r["target"]) for r in rows)
    actual_w = max(len(r["actual"]) for r in rows)
    lines = [
        "voicechain selftest ({})".format(cfg_path),
        "Testsignal: {:.0f} s synthetische Sprache, Rauschteppich {:.0f} dBFS, Sprachpegel {:.0f} dBFS, "
        "Zischen {:+.0f} dB, Pause {:.0f} s in der Mitte; Schwellen der Kette dafuer per calibrate.recommend abgeleitet, "
        "Kriterien nach Abschnitt 6.3 per calibrate.evaluate bewertet.".format(
            SELFTEST_DURATION_S,
            SELFTEST_NOISE_FLOOR_DBFS,
            SELFTEST_SPEECH_LEVEL_DBFS,
            SELFTEST_SIBILANCE_BOOST_DB,
            SELFTEST_LONG_PAUSE_S,
        ),
        "",
        "| {:<{}} | {:<{}} | {:<{}} | {} |".format("Kriterium", name_w, "Soll", target_w, "Ist", actual_w, "bestanden"),
        "|{}|{}|{}|{}|".format("-" * (name_w + 2), "-" * (target_w + 2), "-" * (actual_w + 2), "-" * 11),
    ]
    for r in rows:
        lines.append(
            "| {:<{}} | {:<{}} | {:<{}} | {:<9} |".format(
                r["name"], name_w, r["target"], target_w, r["actual"], actual_w, "ja" if r["passed"] else "NEIN"
            )
        )
    n_pass = sum(1 for r in rows if r["passed"])
    lines.append("")
    lines.append(
        "Ergebnis: {}/{} bestanden -> {}".format(
            n_pass, len(rows), "BESTANDEN" if n_pass == len(rows) else "NICHT BESTANDEN"
        )
    )
    return "\n".join(lines)


def cmd_selftest(args: argparse.Namespace) -> int:
    cfg, cfg_path = _load_config(args.config)
    rows = run_selftest(cfg)
    table = format_selftest_table(rows, cfg_path)
    print(table)
    if args.report:
        report_path = resolve_output_path(args.report, suffixes=(".md", ".txt"))
        report_path.write_text(table + "\n", encoding="utf-8")
        print("report written: {}".format(report_path))
    return EXIT_OK if all(r["passed"] for r in rows) else EXIT_FAILED


def cmd_make_sample(args: argparse.Namespace) -> int:
    from voicechain.synth import write_sample_wav

    out_path = resolve_output_path(args.out)
    write_sample_wav(str(out_path))
    print("sample written: {}".format(out_path))
    return EXIT_OK


# ------------------------------------------------------------------------------ parser
def positive_seconds(text: str) -> float:
    """argparse type for --seconds: a finite number > 0, rejected before anything is created."""
    try:
        value = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--seconds must be a number, got {!r}".format(text)) from exc
    if not math.isfinite(value) or value <= 0.0:
        raise argparse.ArgumentTypeError("--seconds must be > 0, got {}".format(text))
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m voicechain",
        description="Speech processing chain Rode NT-USB -> BlackHole 2ch -> Zoom/Teams. "
        "Relative paths are resolved against the repository root ({}).".format(REPO_ROOT),
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    p_dev = sub.add_parser("devices", help="list audio devices (index, channels, sample rate)")
    p_dev.set_defaults(func=cmd_devices)

    p_run = sub.add_parser("run", help="live operation: input device -> chain -> output device")
    p_run.add_argument("--config", help="YAML configuration (default: config/chain.default.yaml)")
    p_run.add_argument("--input-device", help="input device name substring or index (overrides config)")
    p_run.add_argument("--output-device", help="output device name substring or index (overrides config)")
    p_run.add_argument("--no-adaptive", action="store_true", help="disable the adaptive controller")
    p_run.add_argument("--freeze", action="store_true", help="run the adaptive controller frozen (log only, no changes)")
    p_run.set_defaults(func=cmd_run)

    p_rec = sub.add_parser("record", help="record a calibration sample (raw microphone, 48 kHz mono, 24-bit WAV)")
    p_rec.add_argument("--out", required=True, help="output WAV path")
    p_rec.add_argument("--seconds", type=positive_seconds, default=60.0, help="recording length in seconds (default 60)")
    p_rec.add_argument("--input-device", help="input device name substring or index (overrides config)")
    p_rec.add_argument("--config", help="YAML configuration (default: config/chain.default.yaml)")
    p_rec.set_defaults(func=cmd_record)

    p_cal = sub.add_parser("calibrate", help="analyze a speech sample and write a new configuration + report")
    p_cal.add_argument("--wav", required=True, help="speech sample (WAV, 30-120 s, speech with pauses)")
    p_cal.add_argument("--config", help="base configuration (default: config/chain.default.yaml)")
    p_cal.add_argument("--out", help="output YAML (default: {})".format(DEFAULT_CALIBRATED_CONFIG))
    p_cal.add_argument("--report", help="report path (default: {})".format(DEFAULT_CALIBRATION_REPORT))
    p_cal.set_defaults(func=cmd_calibrate)

    p_proc = sub.add_parser("process", help="run a WAV offline through the chain")
    p_proc.add_argument("--in", dest="infile", required=True, help="input WAV")
    p_proc.add_argument("--out", dest="outfile", required=True, help="output WAV (24-bit PCM)")
    p_proc.add_argument("--config", help="YAML configuration (default: config/chain.default.yaml)")
    p_proc.set_defaults(func=cmd_process)

    p_self = sub.add_parser("selftest", help="synthetic signals through the chain, pass/fail table")
    p_self.add_argument("--config", help="YAML configuration (default: config/chain.default.yaml)")
    p_self.add_argument("--report", help="also write the table to this file")
    p_self.set_defaults(func=cmd_selftest)

    p_mk = sub.add_parser("make-sample", help="write a synthetic speech-like WAV (48 kHz) to rehearse calibration")
    p_mk.add_argument("--out", required=True, help="output WAV path")
    p_mk.set_defaults(func=cmd_make_sample)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except AudioBackendError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return EXIT_ERROR
    except DeviceNotFoundError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return EXIT_ERROR
    except OSError as exc:
        # FileNotFoundError, IsADirectoryError, NotADirectoryError, PermissionError,
        # FileExistsError (a parent of an output path is a file): report with the path.
        detail = str(exc)
        if exc.filename and str(exc.filename) not in detail:
            detail = "{} ({})".format(detail, exc.filename)
        print("error: {}: {}".format(type(exc).__name__, detail), file=sys.stderr)
        return EXIT_ERROR
    except (ValueError, KeyError, TypeError) as exc:
        # Typically an invalid configuration (validate_config), a malformed or
        # unreadable WAV, or a wrong sample rate.
        print("error: {}: {}".format(type(exc).__name__, exc), file=sys.stderr)
        return EXIT_ERROR
    except RuntimeError as exc:
        # soundfile.LibsndfileError (unreadable audio file) or a failed audio callback.
        print("error: {}: {}".format(type(exc).__name__, exc), file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:
        # sounddevice.PortAudioError derives directly from Exception; report it like
        # the other backend errors, let anything unexpected surface as a traceback.
        sd = sys.modules.get("sounddevice")
        portaudio_error = getattr(sd, "PortAudioError", None) if sd is not None else None
        if portaudio_error is not None and isinstance(exc, portaudio_error):
            print("error: PortAudio: {}".format(exc), file=sys.stderr)
            return EXIT_ERROR
        raise
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
