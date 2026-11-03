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
TRUE_PEAK_TOLERANCE_DB = 0.1  # inter-sample measurement vs. sample-domain limiter action
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
    "Sprachpegel",
    "Zischanteil-Absenkung",
    "Lautheit (integriert; Short-Term max)",
    "True Peak",
    "Rechenzeit je 256er-Block",
    "Limiter-Ceiling bei Vollaussteuerungs-Sinus",
    "Limiter-Ceiling bei Impulsen",
    "Stille -> Stille",
)


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


def resolve_output_path(path_str: str) -> Path:
    """Resolve an output path (absolute as-is, else repo root) and create its directory."""
    path = Path(path_str).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
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


def _read_wav_mono(path: Path, channel: int) -> tuple[np.ndarray, int]:
    """Read a WAV as float32 mono. Multichannel files use ``channel`` (fallback 0)."""
    import soundfile as sf

    if not path.exists():
        raise FileNotFoundError("WAV file not found: {}".format(path))
    data, rate = sf.read(str(path), dtype="float32", always_2d=True)
    if data.shape[1] > 1:
        if channel >= data.shape[1]:
            print(
                "note: file has {} channels, channel {} does not exist, using channel 0".format(
                    data.shape[1], channel
                ),
                file=sys.stderr,
            )
            channel = 0
        mono = np.ascontiguousarray(data[:, channel])
    else:
        mono = np.ascontiguousarray(data[:, 0])
    return mono.astype(np.float32, copy=False), int(rate)


def process_blockwise(chain, x: np.ndarray, block_size: int) -> np.ndarray:
    """Feed ``x`` through ``chain`` in blocks of ``block_size``; output has the same length."""
    n = int(x.shape[0])
    out = np.zeros(n, dtype=np.float32)
    for start in range(0, n, block_size):
        stop = min(start + block_size, n)
        if stop - start == block_size:
            block = x[start:stop]
        else:
            block = np.zeros(block_size, dtype=np.float32)
            block[: stop - start] = x[start:stop]
        y = np.asarray(chain.process(block), dtype=np.float32)
        out[start:stop] = y[: stop - start]
    return out


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


def _clone_cfg_with_rate(cfg: dict, sample_rate: int) -> dict:
    import copy

    clone = copy.deepcopy(cfg)
    clone["sample_rate"] = int(sample_rate)
    return clone


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
            log_path = str(resolve_output_path(str(log_path)))
        controller = AdaptiveController(cfg, chain, log_path=log_path)
        analyzer = LiveAnalyzer(int(cfg["sample_rate"]))
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


def cmd_calibrate(args: argparse.Namespace) -> int:
    from voicechain.calibrate import run_calibration

    wav_path = resolve_input_path(args.wav)
    if not wav_path.exists():
        raise FileNotFoundError("WAV file not found: {}".format(wav_path))
    base_cfg_path = _config_path(args.config)
    if not base_cfg_path.exists():
        raise FileNotFoundError("configuration file not found: {}".format(base_cfg_path))
    out_cfg_path = resolve_output_path(args.out or str(DEFAULT_CALIBRATED_CONFIG))
    report_path = resolve_output_path(args.report or str(DEFAULT_CALIBRATION_REPORT))

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
    from voicechain.dsp.chain import Chain

    cfg, _ = _load_config(args.config)
    in_path = resolve_input_path(args.infile)
    out_path = resolve_output_path(args.outfile)
    x, rate = _read_wav_mono(in_path, int(cfg["input"]["channel"]))
    if rate != int(cfg["sample_rate"]):
        print(
            "note: file sample rate {} Hz differs from config {} Hz; the chain is built for {} Hz "
            "for this run".format(rate, cfg["sample_rate"], rate),
            file=sys.stderr,
        )
        cfg = _clone_cfg_with_rate(cfg, rate)
    chain = Chain.from_config(cfg)
    block_size = int(cfg["block_size"])

    t0 = time.perf_counter()
    y = process_blockwise(chain, x, block_size)
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
    from voicechain.calibrate import analyze, evaluate, recommend

    x = _selftest_speech_signal(sample_rate)
    fitted_cfg = recommend(analyze(x, sample_rate), cfg)
    chain = Chain.from_config(fitted_cfg)
    y = process_blockwise(chain, x, block_size)
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
    speech_ok = (
        math.isfinite(after["speech_level_dbfs"])
        and after["speech_level_dbfs"] > after["noise_floor_dbfs"] + SPEECH_ABOVE_FLOOR_MIN_DB
    )
    add(
        SELFTEST_ROWS[1],
        "messbar, > Rauschteppich + {:.0f} dB".format(SPEECH_ABOVE_FLOOR_MIN_DB),
        "{:.1f} -> {:.1f} dBFS (SNR {:.1f} -> {:.1f} dB)".format(
            before["speech_level_dbfs"], after["speech_level_dbfs"], before["snr_db"], after["snr_db"]
        ),
        speech_ok,
    )
    sibilance = criteria["sibilance_reduced"]
    add(SELFTEST_ROWS[2], sibilance["target"], sibilance["actual"], sibilance["passed"])
    loudness = criteria["loudness_in_target"]
    add(
        SELFTEST_ROWS[3],
        loudness["target"],
        "{}; Short-Term max {:.1f} LUFS (vorher integriert {:.1f})".format(
            loudness["actual"], after["short_term_lufs_max"], before["integrated_lufs"]
        ),
        loudness["passed"],
    )
    true_peak = criteria["true_peak_below_ceiling"]
    add(
        SELFTEST_ROWS[4],
        true_peak["target"],
        "{} (vorher {:.2f} dBTP)".format(true_peak["actual"], before["true_peak_dbtp"]),
        true_peak["passed"],
    )

    # --- compute budget ---------------------------------------------------------------
    per_block_ms = _time_per_block_ms(Chain.from_config(cfg), sample_rate, COMPUTE_BUDGET_BLOCK)
    add(
        SELFTEST_ROWS[5],
        "< {:.1f} ms".format(COMPUTE_BUDGET_MS),
        "{:.3f} ms".format(per_block_ms),
        per_block_ms < COMPUTE_BUDGET_MS,
    )

    # --- full-scale sine: ceiling must hold -----------------------------------------
    n_sine = int(SINE_DURATION_S * sample_rate)
    t = np.arange(n_sine, dtype=np.float64) / sample_rate
    sine = (10.0 ** (SINE_LEVEL_DBFS / 20.0) * np.sin(2.0 * math.pi * SINE_FREQ_HZ * t)).astype(np.float32)
    chain_sine = Chain.from_config(cfg)
    y_sine = process_blockwise(chain_sine, sine, block_size)
    tp_sine = float(m.true_peak_dbfs(y_sine, sample_rate))
    add(
        SELFTEST_ROWS[6],
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
    chain_imp = Chain.from_config(cfg)
    y_imp = process_blockwise(chain_imp, impulses, block_size)
    tp_imp = float(m.true_peak_dbfs(y_imp, sample_rate))
    add(
        SELFTEST_ROWS[7],
        "<= {:.1f} dBTP".format(ceiling),
        "{:.2f} dBTP".format(tp_imp),
        tp_imp <= ceiling + TRUE_PEAK_TOLERANCE_DB and np.all(np.isfinite(y_imp)),
    )

    # --- silence in -> silence out ---------------------------------------------------
    silence = np.zeros(int(SILENCE_DURATION_S * sample_rate), dtype=np.float32)
    chain_sil = Chain.from_config(cfg)
    y_sil = process_blockwise(chain_sil, silence, block_size)
    finite = bool(np.all(np.isfinite(y_sil)))
    peak = float(np.max(np.abs(y_sil))) if finite else float("inf")
    peak_dbfs = 20.0 * math.log10(peak) if peak > 0.0 else -float("inf")
    add(
        SELFTEST_ROWS[8],
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
        "Zischen {:+.0f} dB; Schwellen der Kette dafuer per calibrate.recommend abgeleitet, "
        "Kriterien nach Abschnitt 6.3 per calibrate.evaluate bewertet.".format(
            SELFTEST_DURATION_S, SELFTEST_NOISE_FLOOR_DBFS, SELFTEST_SPEECH_LEVEL_DBFS, SELFTEST_SIBILANCE_BOOST_DB
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
        report_path = resolve_output_path(args.report)
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
    except FileNotFoundError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return EXIT_ERROR
    except (ValueError, KeyError, TypeError) as exc:
        # Typically an invalid configuration (validate_config) or a malformed WAV.
        print("error: {}: {}".format(type(exc).__name__, exc), file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
