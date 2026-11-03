"""Calibration: analyse a speech sample, derive chain parameters, verify, report.

Workflow (:func:`run_calibration`):

1. load the base configuration (``voicechain.config.load_config``; default
   ``config/chain.default.yaml``) and read the WAV (:func:`read_mono_wav`,
   mixed to mono, sample rate must match, values must be finite);
2. :func:`analyze` -> measurements (noise floor, speech level and peaks,
   sibilance, de-esser band peaks, fundamental frequency, tilt, loudness, band
   energies, longest pause);
3. :func:`recommend` -> new configuration (deep copy of the base config) with a
   reason per changed value under ``cfg["_calibration"]["reasons"]``;
4. first pass through the chain, then :func:`refine_normalizer` derives the
   normalizer gate and gain limit from the pause loudness after the chain;
5. second pass and :func:`evaluate` -> before/after measurements and the
   acceptance criteria of docs/00_Beauftragung.md section 6.3;
6. write the YAML (``voicechain.config.save_config``) and a German Markdown report.

The control laws (``*_target`` functions) are shared with ``voicechain.adaptive``
so that calibration and live tracking aim at the same operating point.
"""
from __future__ import annotations

import copy
import datetime as _dt
import math
from pathlib import Path

import numpy as np

from voicechain import meters
from voicechain.config import default_config_path, load_config, save_config
from voicechain.dsp.chain import Chain

# --------------------------------------------------------------------------- #
# Control-law constants (see docs/03 for the German explanation)
# --------------------------------------------------------------------------- #

#: Speech RMS after input trim (project reference, docs/02: -20 dBFS +-3 dB). Leaves
#: ~14 dB of headroom for speech peaks (crest factor of speech ~12-15 dB) before the limiter.
REFERENCE_SPEECH_LEVEL_DBFS = -20.0
#: Input trim range: -12 dB catches hot gain settings, +24 dB quiet USB preamps.
TRIM_BOUNDS_DB = (-12.0, 24.0)
#: Expander threshold above the noise floor. Common practice for gate/expander
#: thresholds is 8-12 dB above the noise floor (docs/02, expander): the expander
#: stays closed during noise fluctuation (+-3 dB for fan/room noise). 10 dB is
#: chosen because the chain's static curve (ratio 2.5, knee 6 dB, Giannoulis)
#: attenuates a signal 10 dB below the threshold by 15 dB, which leaves enough
#: reserve against the compressor's ~6 dB gain reduction to meet the >= 6 dB
#: SNR criterion (with 8 dB the self-test measured 5.8 dB SNR gain, with 10 dB
#: 8.7 dB). Shared with voicechain.adaptive via expander_threshold_target();
#: project decision 2026-11-03.
EXPANDER_FLOOR_MARGIN_DB = 10.0
#: ... but at least this far below the speech level so soft syllables pass.
EXPANDER_SPEECH_MARGIN_DB = 12.0
#: Sibilance ratio (5-10 kHz minus 0.3-3 kHz energy, active frames) of
#: unproblematic speech. Assumption derived from the long-term average speech
#: spectrum (LTASS, approx. -6 dB/oct above 500 Hz, steeper above 4 kHz): about
#: -15 dB. Values above it call for de-essing.
SIBILANCE_REFERENCE_DB = -15.0
#: Minimum gain reduction the de-esser should reach on the loud sibilants
#: (95th percentile of the band peaks, see meters.band_peak_dbfs).
DEESSER_MIN_REDUCTION_DB = 4.0
#: For a voice without a sibilance problem the de-esser rests: its threshold sits
#: this far *above* the loud sibilants' band peak, so only outliers (a sharp "s"
#: into the microphone) are touched. Assumption: 2 dB above the 95th percentile
#: leaves the normal sibilants alone while still catching the rare louder ones.
DEESSER_REST_MARGIN_DB = 2.0
#: Frames whose band peak lies within this margin of the loud-sibilant level are
#: treated as sibilant frames (excluded from the high-shelf timbre measurement,
#: which must not see the sibilants the de-esser handles). Assumption: 6 dB.
SIBILANT_FRAME_MARGIN_DB = 6.0
#: De-esser centre frequency limits (sibilance band of the NT-USB, docs/02: 5-9 kHz).
DEESSER_FREQ_BOUNDS_HZ = (5000.0, 9000.0)
#: Band width used by analyze() when no configuration is given; equals the
#: shipped default so stand-alone analyses match the calibrated chain.
DEESSER_ANALYSIS_BANDWIDTH_OCT = 1.0
#: Compressor threshold relative to the speech RMS level. The chain's compressor
#: uses a sample-peak detector; syllable peaks of speech lie ~10 dB above the
#: frame RMS (measured crest of 50 ms frames, see ``speech_crest_db``), so a
#: threshold *at* the RMS level already reduces the peaks by ~6 dB at 3:1.
#: 0 dB matches the shipped default (speech -20 dBFS RMS, threshold -20 dBFS). A margin
#: of 6 dB below the RMS (first draft) gave ~10 dB gain reduction and destroyed
#: the expander's signal-to-noise gain (measured on the real chain: +3 dB instead
#: of +7 dB), so it was dropped.
COMPRESSOR_SPEECH_MARGIN_DB = 0.0
#: Makeup gain limits; 12 dB mirrors the normalizer's default max_gain_db.
MAKEUP_BOUNDS_DB = (0.0, 12.0)
#: EQ reference curve for speech (power spectral density): flat below 500 Hz,
#: -6 dB/oct up to 4 kHz, -10 dB/oct above (the long-term speech spectrum falls
#: clearly steeper above 4 kHz; -10 is the middle of the -9...-12 dB/oct range).
#: Assumption based on the typical long-term speech spectrum, not a norm.
EQ_REFERENCE_KNEE_HZ = 500.0
EQ_REFERENCE_SLOPE_MID_DB_PER_OCT = -6.0
EQ_REFERENCE_KNEE_HIGH_HZ = 4000.0
EQ_REFERENCE_SLOPE_HIGH_DB_PER_OCT = -10.0
#: Only half of a measured deviation is corrected: the measurement mixes the
#: microphone response with the speaker's own timbre, which should survive.
#: Assumption.
EQ_CORRECTION_FRACTION = 0.5
#: Deviations below this are left alone (measurement noise, not timbre).
EQ_MIN_DEVIATION_DB = 1.0
#: Hard limit per EQ band (task specification).
EQ_MAX_GAIN_DB = 4.0
#: The 10 kHz shelf cut is limited harder: together with the de-esser a deep
#: top-octave cut makes a voice dull, and the band is dominated by sibilants and
#: preamp noise that the measurement can only partly exclude. Assumption: 3 dB.
EQ_HIGH_SHELF_MAX_CUT_DB = 3.0
#: Default Q values when a band has to be created (RBJ cookbook typical values).
EQ_SHELF_Q = 0.7
EQ_PEAK_Q = 1.0
#: EQ bands the calibration may touch: (type, centre, analysis band lo, hi).
#: Shelves are cut-only (a low-frequency or top-octave boost lifts rumble,
#: fan tone, preamp noise and sibilant remains rather than speech); peaks may
#: boost or cut.
EQ_TARGET_BANDS = (
    ("low_shelf", 120.0, 80.0, 170.0),
    ("peak", 250.0, 170.0, 350.0),
    ("peak", 3000.0, 2000.0, 4500.0),
    ("high_shelf", 10000.0, 7000.0, 14000.0),
)
EQ_CUT_ONLY_TYPES = ("low_shelf", "high_shelf")
#: The 80-170 Hz band only carries speech when the fundamental lies in or near
#: it. Voices with f0 above this limit (many female and children's voices) have
#: no speech energy there, so the low shelf is left alone. Assumption.
EQ_LOW_SHELF_MAX_F0_HZ = 150.0
#: Anchor band for relative band levels (speech core, between the EQ bands).
EQ_ANCHOR_BAND_HZ = (350.0, 1000.0)
#: Relative tolerance when matching an existing EQ band by frequency.
EQ_BAND_MATCH_TOLERANCE = 0.2

#: Normalizer gate: above the pause loudness after the chain by this margin so
#: room noise never opens the gate (assumption: 8 dB, like the expander margin
#: before the knee), never below GATE_MIN_LUFS (the shipped default; quieter
#: pauses need no lower gate) and never closer than GATE_SPEECH_MARGIN_LU to the
#: target so soft speech still opens it.
NORMALIZER_GATE_MARGIN_DB = 8.0
NORMALIZER_GATE_MIN_LUFS = -40.0
NORMALIZER_GATE_SPEECH_MARGIN_LU = 15.0
#: Normalizer gain limit: the pause loudness plus the maximum gain must stay
#: this far below the target, so the normalizer can never lift room noise to
#: speech loudness even if its gate failed to close.
NORMALIZER_PAUSE_HEADROOM_LU = 6.0
NORMALIZER_MAX_GAIN_LIMIT_DB = 12.0

#: Acceptance criteria (docs/00 section 6.3). TRUE_PEAK_TOLERANCE_DB is the single
#: source for the CLI/self-test as well.
NOISE_FLOOR_MIN_REDUCTION_DB = 6.0
PAUSE_LOUDNESS_MARGIN_LU = 20.0
MIN_PAUSE_S = 5.0
LOUDNESS_TOLERANCE_LU = 2.0
#: Sibilance must not get worse by more than this when the de-esser is not
#: needed (assumption: 1 dB is below audibility for a band-energy ratio).
SIBILANCE_MAX_INCREASE_DB = 1.0
#: A voice up to this far above the sibilance reference is a borderline voice:
#: unproblematic to listen to, the de-esser only keeps it where it is.
#: Assumption: 3 dB (the spread of the reference estimate itself).
SIBILANCE_BORDERLINE_DB = 3.0
#: Difference tolerated between the limiter's internal peak estimate and the
#: 4x-oversampled true-peak meter (assumption: 0.1 dB).
TRUE_PEAK_TOLERANCE_DB = 0.1

#: Sample length limits: docs/00 section 3.2 asks for 30-120 s. Below 10 s the
#: noise-floor percentile cannot be estimated reliably, so that is an error.
RECOMMENDED_DURATION_S = (30.0, 120.0)
MIN_DURATION_S = 10.0

#: Top-level keys starting with this prefix are calibration metadata, not schema.
METADATA_PREFIX = "_"
#: Common dB floor (voicechain.dsp.DB_FLOOR_DBFS via meters).
DB_FLOOR = meters.DB_FLOOR_DBFS

NO_PAUSE_HINT = (
    f"Das Sample enthält keine Sprechpause von mindestens {MIN_PAUSE_S:.0f} s nach dem ersten "
    "gesprochenen Satz. Die Beauftragung §6.3 verlangt sie, weil nur dort messbar ist, wie leise "
    "die Kette in Pausen bleibt. Bitte neu aufnehmen: 10 s Stille am Anfang, normal sprechen und in "
    f"der Mitte eine Pause von mindestens {MIN_PAUSE_S:.0f} s einlegen (nicht sprechen, nicht tippen).")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def clamp(value: float, lo: float, hi: float) -> float:
    if lo > hi:
        raise ValueError(f"invalid bounds: lower {lo} > upper {hi}")
    return float(min(max(value, lo), hi))


def parse_bounds(cfg: dict) -> dict[tuple[str, str], tuple[float, float]]:
    """``cfg["adaptive"]["bounds"]`` -> ``{(stage, name): (lo, hi)}``."""
    raw = cfg["adaptive"]["bounds"]
    out = {}
    for key, pair in raw.items():
        if "." not in key:
            raise ValueError(f"bounds key '{key}' must look like 'stage.param'")
        stage, name = key.split(".", 1)
        lo, hi = float(pair[0]), float(pair[1])
        if lo > hi:
            raise ValueError(f"bounds for {key} are inverted: {pair}")
        out[(stage, name)] = (lo, hi)
    return out


def tracked_bounds(bounds: dict, stage: str, name: str) -> tuple[float, float]:
    """Bounds of a tracked parameter; a missing entry is a configuration error."""
    try:
        return bounds[(stage, name)]
    except KeyError:
        raise ValueError(f"adaptive.bounds lacks an entry for {stage}.{name}") from None


def strip_metadata(cfg: dict) -> dict:
    """Deep copy of ``cfg`` without top-level keys that start with ``METADATA_PREFIX``."""
    return {k: copy.deepcopy(v) for k, v in cfg.items() if not str(k).startswith(METADATA_PREFIX)}


def _plain(obj):
    """Convert numpy scalars/arrays to plain Python for YAML/JSON output."""
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return [_plain(v) for v in obj.tolist()]
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


# --------------------------------------------------------------------------- #
# Control laws (shared with voicechain.adaptive)
# --------------------------------------------------------------------------- #

def input_trim_target(speech_level_dbfs: float) -> float:
    """Gain that moves the measured speech RMS to the reference level."""
    return clamp(REFERENCE_SPEECH_LEVEL_DBFS - speech_level_dbfs, *TRIM_BOUNDS_DB)


def expander_threshold_target(noise_floor_dbfs: float, speech_level_dbfs: float | None) -> float:
    """Noise floor + margin, but at least ``EXPANDER_SPEECH_MARGIN_DB`` below speech.

    ``speech_level_dbfs`` may be None when no plausible speech is present (then
    only the floor rule applies). Bounds are applied by the caller.
    """
    target = noise_floor_dbfs + EXPANDER_FLOOR_MARGIN_DB
    if speech_level_dbfs is not None:
        target = min(target, speech_level_dbfs - EXPANDER_SPEECH_MARGIN_DB)
    return float(target)


def deesser_overshoot_db(ratio: float) -> float:
    """Band level above the threshold needed for DEESSER_MIN_REDUCTION_DB at a hard knee."""
    if ratio <= 1.0:
        raise ValueError(f"de-esser ratio must be > 1, got {ratio}")
    return DEESSER_MIN_REDUCTION_DB / (1.0 - 1.0 / ratio)


def deesser_threshold_target(band_peak_dbfs: float, sibilance_ratio_db: float,
                             ratio: float) -> tuple[float, bool]:
    """De-esser threshold from the measured band peak level of the loud sibilants.

    ``band_peak_dbfs`` is what the de-esser's peak detector sees on the loud
    sibilants (95th percentile of the 50 ms frame peaks of the band-passed
    signal, after the input trim). Returns ``(threshold_db, engaged)``: if the
    sibilance ratio is above the reference the threshold is placed
    ``deesser_overshoot_db(ratio)`` below the peak so those sibilants receive
    at least ``DEESSER_MIN_REDUCTION_DB`` (hard knee: reduction = (L - T) * (1 - 1/R));
    otherwise the voice needs no de-essing and the threshold rests
    ``DEESSER_REST_MARGIN_DB`` *above* the peak, touching only outliers. Both
    branches derive from the same measurement, so the de-esser never works
    harder on a clean voice than on a sibilant one. Bounds are applied by the caller.
    """
    overshoot = deesser_overshoot_db(ratio)
    if sibilance_ratio_db <= SIBILANCE_REFERENCE_DB:
        return float(band_peak_dbfs + DEESSER_REST_MARGIN_DB), False
    return float(band_peak_dbfs - overshoot), True


def compressor_threshold_target(speech_level_dbfs: float) -> float:
    return float(speech_level_dbfs - COMPRESSOR_SPEECH_MARGIN_DB)


def static_compressor_reduction_db(level_db: float, threshold_db: float, ratio: float,
                                   knee_db: float) -> float:
    """Gain reduction (>= 0 dB) of a soft-knee compressor at a detector level
    (Giannoulis, Massberg, Reiss 2012; same curve as the chain's compressor).

    The prediction matched the real chain within 0.6 dB for thresholds from
    -26 to -14 dBFS on synthetic speech when evaluated at the mean 50 ms frame
    peak level of the active speech.
    """
    if ratio <= 0:
        raise ValueError(f"compressor ratio must be positive, got {ratio}")
    if knee_db < 0:
        raise ValueError(f"knee_db must be >= 0, got {knee_db}")
    over = level_db - threshold_db
    slope = 1.0 - 1.0 / ratio
    if knee_db > 0.0 and abs(2.0 * over) <= knee_db:
        return float(slope * (over + knee_db / 2.0) ** 2 / (2.0 * knee_db))
    if over > knee_db / 2.0:
        return float(slope * over)
    return 0.0


def compressor_makeup_target(speech_peak_level_dbfs: float, threshold_db: float, ratio: float,
                             knee_db: float) -> float:
    """Makeup that restores the level the compressor removes from syllable peaks."""
    return clamp(static_compressor_reduction_db(speech_peak_level_dbfs, threshold_db, ratio, knee_db),
                 *MAKEUP_BOUNDS_DB)


def eq_reference_db(freq_hz: float) -> float:
    """Reference power spectral density (dB, relative) at ``freq_hz``."""
    if freq_hz <= EQ_REFERENCE_KNEE_HZ:
        return 0.0
    mid = EQ_REFERENCE_SLOPE_MID_DB_PER_OCT * math.log2(
        min(freq_hz, EQ_REFERENCE_KNEE_HIGH_HZ) / EQ_REFERENCE_KNEE_HZ)
    if freq_hz <= EQ_REFERENCE_KNEE_HIGH_HZ:
        return mid
    return mid + EQ_REFERENCE_SLOPE_HIGH_DB_PER_OCT * math.log2(freq_hz / EQ_REFERENCE_KNEE_HIGH_HZ)


def normalizer_gate_target(pause_loudness_lufs: float, target_lufs: float) -> float:
    """Gate above the pause loudness, inside [GATE_MIN, target - speech margin]."""
    hi = target_lufs - NORMALIZER_GATE_SPEECH_MARGIN_LU
    return clamp(pause_loudness_lufs + NORMALIZER_GATE_MARGIN_DB, min(NORMALIZER_GATE_MIN_LUFS, hi), hi)


def normalizer_max_gain_target(pause_loudness_lufs: float, target_lufs: float) -> float:
    """Largest gain that keeps pause loudness + gain below target - headroom."""
    return clamp(target_lufs - (pause_loudness_lufs + NORMALIZER_PAUSE_HEADROOM_LU),
                 0.0, NORMALIZER_MAX_GAIN_LIMIT_DB)


# --------------------------------------------------------------------------- #
# analyze
# --------------------------------------------------------------------------- #

def _usable_eq_bands(sample_rate: int) -> list[tuple[str, float, float, float]]:
    f_max = meters.MAX_USABLE_FREQ_FRACTION * sample_rate
    out = []
    for kind, fc, lo, hi in EQ_TARGET_BANDS:
        if lo >= f_max:
            continue
        out.append((kind, fc, lo, min(hi, f_max)))
    return out


def analyze(x: np.ndarray, sample_rate: int, deesser_freq_hz: float | None = None,
            deesser_bandwidth_oct: float = DEESSER_ANALYSIS_BANDWIDTH_OCT) -> dict:
    """Measure everything the recommendation and the report need.

    ``deesser_freq_hz`` / ``deesser_bandwidth_oct`` define the band in which
    the sibilant peak level is measured; None uses the measured sibilance
    centroid clipped to the de-esser range (what ``recommend`` will choose).

    Keys: duration_s, sample_rate, rms_dbfs, sample_peak_dbfs, true_peak_dbtp,
    noise_floor_dbfs, speech_level_dbfs, speech_peak_level_dbfs, speech_crest_db,
    snr_db, dynamic_range_db, speech_detected, speech_activity_ratio, f0_hz,
    voiced_ratio, sibilance_ratio_db, sibilance_centroid_hz,
    sibilant_band_peak_dbfs, sibilant_band_freq_hz, sibilant_band_width_oct,
    spectral_tilt_db_per_oct, integrated_lufs, short_term_lufs_max,
    eq_bands (dict label -> {type, freq_hz, lo_hz, hi_hz, centre_hz, energy_db,
    density_db}), eq_anchor (same band structure), pause (None or {start_s,
    end_s, max_lufs, median_lufs, rms_dbfs}).
    """
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 2:
        x = x.mean(axis=1)
    if x.size < sample_rate * MIN_DURATION_S:
        raise ValueError(
            f"sample is {x.size / sample_rate:.1f} s long; at least {MIN_DURATION_S:.0f} s "
            f"with speech pauses are needed to estimate the noise floor")
    if not np.all(np.isfinite(x)):
        raise ValueError("Sample enthält nicht-finite Werte (NaN/Inf); Analyse nicht möglich")
    mask, levels = meters.speech_activity(x, sample_rate)
    _, detected = meters.activity_threshold_db(levels)
    noise_floor = meters.noise_floor_dbfs(x, sample_rate)
    speech = meters.speech_level_dbfs(x, sample_rate)
    st_series = meters.short_term_lufs_series(x, sample_rate)
    # Mean peak level of the active 50 ms frames: what a peak-detector compressor
    # with ~10 ms attack / 100 ms release effectively reacts to.
    frame = int(round(sample_rate * meters.ANALYSIS_FRAME_MS / 1000.0))
    frames = np.abs(x[:mask.size * frame].reshape(mask.size, frame))
    peak_frames = frames[mask] if np.any(mask) else frames
    speech_peak = float(np.mean(meters._amp_to_db(np.max(peak_frames, axis=1))))
    centroid = meters.sibilance_centroid_hz(x, sample_rate)
    band_freq = float(deesser_freq_hz) if deesser_freq_hz is not None else clamp(centroid, *DEESSER_FREQ_BOUNDS_HZ)
    f0, voiced_ratio = meters.fundamental_frequency_hz(x, sample_rate)
    pause_span = meters.longest_pause(x, sample_rate, MIN_PAUSE_S)
    pause = None
    if pause_span is not None:
        pause = meters.pause_loudness_lufs(x, sample_rate, *pause_span)

    # Sibilant frames: active frames whose band peak lies within
    # SIBILANT_FRAME_MARGIN_DB of the loud-sibilant level. The high-shelf band is
    # measured without them (the de-esser handles sibilants; the shelf must
    # describe the timbre of the voice, not its "s" sounds).
    band_peaks = meters.band_peak_frames_dbfs(x, sample_rate, band_freq, deesser_bandwidth_oct)
    band_peak_p95 = float(np.percentile(band_peaks[mask], meters.BAND_PEAK_PERCENTILE)) if np.any(mask) \
        else float(np.percentile(band_peaks, meters.BAND_PEAK_PERCENTILE))
    sibilance_ratio = meters.sibilance_ratio_db(x, sample_rate)
    # Only a sibilant voice has frames the de-esser treats; a clean voice's band
    # peaks are plain speech and stay in the timbre measurement.
    if sibilance_ratio > SIBILANCE_REFERENCE_DB:
        sibilant = mask & (band_peaks >= band_peak_p95 - SIBILANT_FRAME_MARGIN_DB)
    else:
        sibilant = np.zeros_like(mask)
    non_sibilant = mask & ~sibilant
    sibilant_ratio = float(np.sum(sibilant) / max(1, np.sum(mask)))

    def band_info(lo: float, hi: float, frame_mask=None) -> dict:
        energy = meters.band_energies_db(x, sample_rate, [lo, hi], frame_mask=frame_mask)[0]
        return {"lo_hz": lo, "hi_hz": hi, "centre_hz": math.sqrt(lo * hi),
                "energy_db": energy, "density_db": energy - 10.0 * math.log10(hi - lo)}

    eq_bands = {}
    for kind, fc, lo, hi in _usable_eq_bands(sample_rate):
        info = {"type": kind, "freq_hz": fc}
        if kind == "high_shelf" and np.any(non_sibilant):
            info.update(band_info(lo, hi, non_sibilant))
            info["excluded_sibilant_ratio"] = sibilant_ratio
        else:
            info.update(band_info(lo, hi))
            info["excluded_sibilant_ratio"] = 0.0
        eq_bands[f"{kind}@{fc:.0f}"] = info
    return {
        "duration_s": x.size / sample_rate,
        "sample_rate": int(sample_rate),
        "rms_dbfs": meters.rms_dbfs(x),
        "sample_peak_dbfs": meters.sample_peak_dbfs(x),
        "true_peak_dbtp": meters.true_peak_dbfs(x, sample_rate),
        "noise_floor_dbfs": noise_floor,
        "speech_level_dbfs": speech,
        "speech_peak_level_dbfs": speech_peak,
        "speech_crest_db": speech_peak - speech,
        "snr_db": speech - noise_floor,
        "dynamic_range_db": meters.dynamic_range_db(levels),
        "speech_detected": bool(detected),
        "speech_activity_ratio": float(np.mean(mask)) if mask.size else 0.0,
        "f0_hz": f0,
        "voiced_ratio": voiced_ratio,
        "sibilance_ratio_db": sibilance_ratio,
        "sibilance_centroid_hz": centroid,
        "sibilant_band_peak_dbfs": band_peak_p95,
        "sibilant_band_freq_hz": band_freq,
        "sibilant_band_width_oct": float(deesser_bandwidth_oct),
        "sibilant_frame_ratio": sibilant_ratio,
        "spectral_tilt_db_per_oct": meters.spectral_tilt_db_per_oct(x, sample_rate),
        "integrated_lufs": meters.integrated_lufs(x, sample_rate),
        "short_term_lufs_max": float(np.max(st_series)) if st_series.size else DB_FLOOR,
        "eq_bands": eq_bands,
        "eq_anchor": band_info(*EQ_ANCHOR_BAND_HZ),
        "pause": pause,
    }


# --------------------------------------------------------------------------- #
# recommend
# --------------------------------------------------------------------------- #

def _find_eq_band(bands: list[dict], kind: str, freq_hz: float) -> dict | None:
    for band in bands:
        if band.get("type") != kind:
            continue
        f = float(band.get("freq_hz", 0.0))
        if abs(f - freq_hz) <= EQ_BAND_MATCH_TOLERANCE * freq_hz:
            return band
    return None


def recommend(measurements: dict, base_cfg: dict) -> dict:
    """Derive a complete configuration from the measurements.

    Returns a deep copy of ``base_cfg`` with changed chain values and an extra
    top-level key ``_calibration`` holding ``measurements``, ``reasons`` (one
    German sentence per changed parameter path) and a timestamp. Keys with a
    leading underscore are metadata and are ignored by the config validator.
    The normalizer gate and gain limit need the pause loudness *after* the
    chain and are set by :func:`refine_normalizer`.
    """
    cfg = copy.deepcopy(base_cfg)
    chain = cfg["chain"]
    bounds = parse_bounds(cfg)
    reasons: dict[str, str] = {}
    m = measurements
    speech_in = float(m["speech_level_dbfs"])
    floor_in = float(m["noise_floor_dbfs"])
    speech_detected = bool(m["speech_detected"])

    # 0. Input trim
    trim = input_trim_target(speech_in)
    chain["input_trim"]["gain_db"] = round(trim, 2)
    speech_after = speech_in + trim
    floor_after = floor_in + trim
    reasons["chain.input_trim.gain_db"] = (
        f"Gemessener Sprachpegel {speech_in:.1f} dBFS, Referenz {REFERENCE_SPEECH_LEVEL_DBFS:.0f} dBFS "
        f"→ Trim {trim:+.1f} dB (begrenzt auf {TRIM_BOUNDS_DB[0]:+.0f}…{TRIM_BOUNDS_DB[1]:+.0f} dB). "
        f"Nach Trim: Sprache {speech_after:.1f} dBFS, Rauschteppich {floor_after:.1f} dBFS.")
    if not speech_detected:
        reasons["chain.input_trim.gain_db"] += (
            " ACHTUNG: keine Sprache erkannt (Dynamik < "
            f"{meters.MIN_SPEECH_SNR_DB:.0f} dB), Sprachpegel ist nur ein Schätzwert.")

    # 2. Expander
    exp = chain["expander"]
    exp_lo, exp_hi = tracked_bounds(bounds, "expander", "threshold_db")
    exp_raw = expander_threshold_target(floor_after, speech_after if speech_detected else None)
    exp_thr = clamp(exp_raw, exp_lo, exp_hi)
    exp["threshold_db"] = round(exp_thr, 2)
    limiting_rule = ("Rauschteppich + %.0f dB" % EXPANDER_FLOOR_MARGIN_DB
                     if exp_raw >= floor_after + EXPANDER_FLOOR_MARGIN_DB - 1e-9
                     else "Sprachpegel − %.0f dB (Sprache darf nicht abgeschnitten werden)"
                     % EXPANDER_SPEECH_MARGIN_DB)
    reasons["chain.expander.threshold_db"] = (
        f"min(Rauschteppich {floor_after:.1f} + {EXPANDER_FLOOR_MARGIN_DB:.0f}, "
        f"Sprache {speech_after:.1f} − {EXPANDER_SPEECH_MARGIN_DB:.0f}) = {exp_raw:.1f} dBFS; "
        f"maßgebliche Regel: {limiting_rule}; Grenzen {exp_lo:.0f}…{exp_hi:.0f} → {exp_thr:.1f} dBFS.")

    # 3. De-esser
    de = chain["deesser"]
    de_lo, de_hi = tracked_bounds(bounds, "deesser", "threshold_db")
    sib = float(m["sibilance_ratio_db"])
    centroid = float(m["sibilance_centroid_hz"])
    de_freq = clamp(centroid, *DEESSER_FREQ_BOUNDS_HZ)
    de["freq_hz"] = round(de_freq, 0)
    reasons["chain.deesser.freq_hz"] = (
        f"Schwerpunkt der Zischenergie 4–10 kHz bei {centroid:.0f} Hz, begrenzt auf "
        f"{DEESSER_FREQ_BOUNDS_HZ[0]:.0f}…{DEESSER_FREQ_BOUNDS_HZ[1]:.0f} Hz → {de_freq:.0f} Hz.")
    de_ratio = float(de["ratio"])
    band_peak_after = float(m["sibilant_band_peak_dbfs"]) + trim
    de_raw, engaged = deesser_threshold_target(band_peak_after, sib, de_ratio)
    de_thr = clamp(de_raw, de_lo, de_hi)
    de["threshold_db"] = round(de_thr, 2)
    if engaged:
        reasons["chain.deesser.threshold_db"] = (
            f"Zischanteil {sib:.1f} dB liegt über der Referenz {SIBILANCE_REFERENCE_DB:.0f} dB (Annahme). "
            f"Spitzenpegel der lauten Zischlaute im De-Esser-Band ({m['sibilant_band_freq_hz']:.0f} Hz, "
            f"{m['sibilant_band_width_oct']:.1f} Okt, 95. Perzentil der 50-ms-Spitzen) nach Trim: "
            f"{band_peak_after:.1f} dBFS; für ≥ {DEESSER_MIN_REDUCTION_DB:.0f} dB Reduktion bei Ratio {de_ratio:g} "
            f"(hartes Knie) liegt die Schwelle {deesser_overshoot_db(de_ratio):.1f} dB darunter → {de_raw:.1f}; "
            f"Grenzen {de_lo:.0f}…{de_hi:.0f} → {de_thr:.1f} dBFS.")
    else:
        reasons["chain.deesser.threshold_db"] = (
            f"Zischanteil {sib:.1f} dB liegt nicht über der Referenz {SIBILANCE_REFERENCE_DB:.0f} dB (Annahme): "
            f"kein Zischproblem. Der De-Esser bleibt in Ruhe: Schwelle = Spitzenpegel der lauten Zischlaute im "
            f"De-Esser-Band nach Trim {band_peak_after:.1f} dBFS + {DEESSER_REST_MARGIN_DB:.0f} dB (Annahme) = "
            f"{de_raw:.1f}, nur Ausreißer werden angefasst; Grenzen {de_lo:.0f}…{de_hi:.0f} → {de_thr:.1f} dBFS.")

    # 4. EQ: the measured correction is *added* to the base value of the matching
    # band. The base value describes the intended voicing for the codec
    # (docs/02), the correction describes microphone and speaker; both apply.
    # Shelves are cut-only; the low shelf is skipped for voices with f0 above
    # EQ_LOW_SHELF_MAX_F0_HZ because their 80-170 Hz band holds no speech.
    eq = chain["eq"]
    bands = eq.setdefault("bands", [])
    anchor = m["eq_anchor"]
    f0 = float(m["f0_hz"])
    ref_curve = (f"flach bis {EQ_REFERENCE_KNEE_HZ:.0f} Hz, {EQ_REFERENCE_SLOPE_MID_DB_PER_OCT:+.0f} dB/Okt bis "
                 f"{EQ_REFERENCE_KNEE_HIGH_HZ / 1000:.0f} kHz, darüber {EQ_REFERENCE_SLOPE_HIGH_DB_PER_OCT:+.0f} dB/Okt, Annahme")
    for info in m["eq_bands"].values():
        kind, fc = info["type"], float(info["freq_hz"])
        measured_rel = info["density_db"] - anchor["density_db"]
        reference_rel = eq_reference_db(info["centre_hz"]) - eq_reference_db(anchor["centre_hz"])
        deviation = measured_rel - reference_rel
        band = _find_eq_band(bands, kind, fc)
        label_freq = float(band["freq_hz"]) if band is not None else fc
        key = f"chain.eq.{kind}@{label_freq:.0f}.gain_db"
        prefix = (f"Band {info['lo_hz']:.0f}–{info['hi_hz']:.0f} Hz liegt {measured_rel:+.1f} dB zum Ankerband "
                  f"{anchor['lo_hz']:.0f}–{anchor['hi_hz']:.0f} Hz; Referenzkurve ({ref_curve}) erwartet "
                  f"{reference_rel:+.1f} dB → Abweichung {deviation:+.1f} dB; ")
        if kind == "low_shelf" and not (math.isfinite(f0) and f0 <= EQ_LOW_SHELF_MAX_F0_HZ):
            f0_text = f"{f0:.0f} Hz" if math.isfinite(f0) else "nicht messbar"
            reasons[key] = prefix + (
                f"Grundfrequenz {f0_text} liegt über {EQ_LOW_SHELF_MAX_F0_HZ:.0f} Hz, das Band enthält "
                "keine Sprache (nur Trittschall/Lüfter) → kein Eingriff"
                + (f", Basiswert {float(band['gain_db']):+.1f} dB bleibt." if band is not None else "."))
            continue
        if abs(deviation) < EQ_MIN_DEVIATION_DB:
            correction = 0.0
        else:
            correction = clamp(-EQ_CORRECTION_FRACTION * deviation, -EQ_MAX_GAIN_DB, EQ_MAX_GAIN_DB)
        cut_only = kind in EQ_CUT_ONLY_TYPES
        if cut_only:
            correction = min(correction, 0.0)
        if band is None and correction == 0.0:
            reasons[key] = prefix + (f"Korrektur {EQ_CORRECTION_FRACTION:.0%} davon, Totzone ±{EQ_MIN_DEVIATION_DB:.0f} dB"
                                     + (", Shelf nur als Absenkung" if cut_only else "")
                                     + " → 0 dB, kein Band angelegt.")
            continue
        if band is None:
            band = {"type": kind, "freq_hz": fc, "gain_db": 0.0,
                    "q": EQ_SHELF_Q if kind.endswith("shelf") else EQ_PEAK_Q}
            bands.append(band)
            created = True
        else:
            created = False
        base_gain = float(band.get("gain_db", 0.0))
        hi_gain = 0.0 if cut_only else EQ_MAX_GAIN_DB
        lo_gain = -EQ_HIGH_SHELF_MAX_CUT_DB if kind == "high_shelf" else -EQ_MAX_GAIN_DB
        gain = clamp(base_gain + correction, lo_gain, hi_gain)
        band["gain_db"] = round(gain, 2)
        excluded = float(info.get("excluded_sibilant_ratio", 0.0))
        reasons[key] = prefix + (
            f"Korrektur {EQ_CORRECTION_FRACTION:.0%} davon, Totzone ±{EQ_MIN_DEVIATION_DB:.0f} dB"
            + (", Shelf nur als Absenkung" if cut_only else "")
            + (f", gemessen ohne Zischrahmen ({excluded:.0%} der aktiven Rahmen ausgeschlossen)"
               if kind == "high_shelf" else "")
            + f" → {correction:+.1f} dB "
            + (f"auf neues Band bei {fc:.0f} Hz" if created
               else f"zum Basiswert {base_gain:+.1f} dB des Bands bei {label_freq:.0f} Hz")
            + f"; Grenze {lo_gain:+.0f}…{hi_gain:+.0f} dB → {gain:+.1f} dB.")

    # 5. Compressor
    comp = chain["compressor"]
    comp_lo, comp_hi = tracked_bounds(bounds, "compressor", "threshold_db")
    comp_thr = clamp(compressor_threshold_target(speech_after), comp_lo, comp_hi)
    comp["threshold_db"] = round(comp_thr, 2)
    comp_ratio = float(comp["ratio"])
    comp_knee = float(comp["knee_db"])
    peak_after = float(m["speech_peak_level_dbfs"]) + trim
    makeup = compressor_makeup_target(peak_after, comp_thr, comp_ratio, comp_knee)
    comp["makeup_db"] = round(makeup, 2)
    reasons["chain.compressor.threshold_db"] = (
        f"Sprachpegel nach Trim {speech_after:.1f} dBFS − {COMPRESSOR_SPEECH_MARGIN_DB:.0f} dB = "
        f"{compressor_threshold_target(speech_after):.1f} (der Kompressor misst Spitzen, die "
        f"{peak_after - speech_after:.1f} dB über dem RMS liegen; die Schwelle auf RMS-Höhe trifft nur diese Spitzen); "
        f"Grenzen {comp_lo:.0f}…{comp_hi:.0f} → {comp_thr:.1f} dBFS.")
    reasons["chain.compressor.makeup_db"] = (
        f"Silbenspitzen liegen im Mittel bei {peak_after:.1f} dBFS, also {peak_after - comp_thr:.1f} dB über der "
        f"Schwelle; Ratio {comp_ratio:g} mit {comp_knee:.0f} dB Knie nimmt dort {makeup:.1f} dB weg → Makeup "
        f"{makeup:+.1f} dB (Grenzen {MAKEUP_BOUNDS_DB[0]:.0f}…{MAKEUP_BOUNDS_DB[1]:.0f} dB), damit Ausgang ≈ Eingang.")

    # 5b / 6. Normalizer target and limiter ceiling: keep configured values.
    norm = chain["normalizer"]
    reasons["chain.normalizer.target_lufs"] = (
        f"Bleibt bei {float(norm['target_lufs']):.0f} LUFS (Zielwert aus Konfiguration, "
        "Praxiswert −18 LUFS ± 2 LU für Konferenzsysteme, E3).")
    reasons["chain.limiter.ceiling_dbfs"] = (
        f"Bleibt bei {float(chain['limiter']['ceiling_dbfs']):.0f} dBTP (True-Peak-Obergrenze, E3).")

    cfg["_calibration"] = {
        "created": _dt.datetime.now().isoformat(timespec="seconds"),
        "measurements": _plain(measurements),
        "reasons": reasons,
    }
    return cfg


def refine_normalizer(cfg: dict, after_measurements: dict) -> dict:
    """Set ``normalizer.gate_lufs`` and ``normalizer.max_gain_db`` from the pause
    loudness measured after a first pass through the chain *with the normalizer
    disabled* (the pause level the normalizer's gate and gain limit must be
    designed for is the one at its input, without its own gain).

    ``after_measurements["pause"]`` must exist (``analyze`` of the processed
    sample with a pause >= MIN_PAUSE_S). Returns a deep copy with reasons added
    under ``_calibration.reasons`` and the after-measurements under
    ``_calibration.measurements_first_pass``.
    """
    pause = after_measurements.get("pause")
    if pause is None:
        raise ValueError(NO_PAUSE_HINT)
    out = copy.deepcopy(cfg)
    norm = out["chain"]["normalizer"]
    target = float(norm["target_lufs"])
    pause_lufs = float(pause["median_lufs"])
    gate = normalizer_gate_target(pause_lufs, target)
    max_gain = normalizer_max_gain_target(pause_lufs, target)
    norm["gate_lufs"] = round(gate, 2)
    norm["max_gain_db"] = round(max_gain, 2)
    reasons = out.setdefault("_calibration", {}).setdefault("reasons", {})
    reasons["chain.normalizer.gate_lufs"] = (
        f"Pausenlautheit nach Kette ohne Normalizer-Gain (Median der Momentary-Lautheit in der Pause "
        f"{pause['start_s']:.1f}–{pause['end_s']:.1f} s, erster Durchlauf mit abgeschaltetem Normalizer) {pause_lufs:.1f} LUFS + "
        f"{NORMALIZER_GATE_MARGIN_DB:.0f} dB Abstand (Annahme) = {pause_lufs + NORMALIZER_GATE_MARGIN_DB:.1f}; "
        f"mindestens {NORMALIZER_GATE_MIN_LUFS:.0f} LUFS, höchstens Ziel {target:.0f} − "
        f"{NORMALIZER_GATE_SPEECH_MARGIN_LU:.0f} LU → {gate:.1f} LUFS. Unterhalb hält der Normalizer den Gain, "
        "Pausenrauschen wird nicht hochgezogen.")
    reasons["chain.normalizer.max_gain_db"] = (
        f"Ziel {target:.0f} LUFS − (Pausenlautheit {pause_lufs:.1f} + {NORMALIZER_PAUSE_HEADROOM_LU:.0f} LU Abstand) = "
        f"{target - (pause_lufs + NORMALIZER_PAUSE_HEADROOM_LU):.1f} dB, begrenzt auf 0…{NORMALIZER_MAX_GAIN_LIMIT_DB:.0f} dB "
        f"→ {max_gain:.1f} dB: Rauschen kann rechnerisch nie auf Sprachlautheit angehoben werden."
        + (" ACHTUNG: 0 dB bedeutet, dass die Pausen in diesem Raum bereits zu laut sind; die Kette kann hier "
           "keine Lautheit hinzufügen." if max_gain <= 0.0 else ""))
    out["_calibration"]["measurements_first_pass"] = _plain(after_measurements)
    return out


# --------------------------------------------------------------------------- #
# evaluate
# --------------------------------------------------------------------------- #

def evaluate(x_in: np.ndarray, x_out: np.ndarray, sample_rate: int, cfg: dict) -> dict:
    """Before/after measurements and the acceptance criteria of docs/00 section 6.3.

    Criteria keys: ``noise_floor_reduction`` (SNR gain >= 6 dB),
    ``pause_loudness`` (max momentary loudness in the longest pause >= 5 s of
    the input, measured on the output, <= target - 20 LU), ``sibilance_reduced``,
    ``loudness_in_target``, ``true_peak_below_ceiling``. Each is
    ``{"passed": bool, "target": str, "actual": str, "note": str}``.
    Raises ValueError with a recording hint when the input has no pause >= 5 s.
    """
    chain = cfg["chain"]
    de = chain["deesser"]
    before = analyze(x_in, sample_rate, float(de["freq_hz"]), float(de["bandwidth_oct"]))
    after = analyze(x_out, sample_rate, float(de["freq_hz"]), float(de["bandwidth_oct"]))
    target_lufs = float(chain["normalizer"]["target_lufs"])
    ceiling = float(chain["limiter"]["ceiling_dbfs"])
    if before["pause"] is None:
        raise ValueError(NO_PAUSE_HINT)
    # The pause is located on the input (the output may be gated) and measured
    # on both signals in the same time span.
    span = (before["pause"]["start_s"], before["pause"]["end_s"])
    pause_out = meters.pause_loudness_lufs(x_out, sample_rate, *span)
    after["pause"] = pause_out
    criteria: dict[str, dict] = {}

    # The normalizer moves the whole signal to the target loudness, so the noise
    # floor is compared at equal speech level: floor relative to speech
    # (= -SNR) must drop by >= 6 dB. The absolute values are reported alongside.
    floor_drop_abs = before["noise_floor_dbfs"] - after["noise_floor_dbfs"]
    snr_gain = after["snr_db"] - before["snr_db"]
    criteria["noise_floor_reduction"] = {
        "passed": bool(snr_gain >= NOISE_FLOOR_MIN_REDUCTION_DB),
        "target": f"≥ {NOISE_FLOOR_MIN_REDUCTION_DB:.0f} dB (relativ zum Sprachpegel)",
        "actual": f"{snr_gain:+.1f} dB (Rauschteppich {before['noise_floor_dbfs'] - before['speech_level_dbfs']:+.1f} → "
                  f"{after['noise_floor_dbfs'] - after['speech_level_dbfs']:+.1f} dB unter Sprache)",
        "note": f"Absolut {before['noise_floor_dbfs']:.1f} → {after['noise_floor_dbfs']:.1f} dBFS ({floor_drop_abs:+.1f} dB); "
                f"Sprachpegel {before['speech_level_dbfs']:.1f} → {after['speech_level_dbfs']:.1f} dBFS.",
    }

    pause_limit = target_lufs - PAUSE_LOUDNESS_MARGIN_LU
    pause_in = before["pause"]
    criteria["pause_loudness"] = {
        "passed": bool(pause_out["max_lufs"] <= pause_limit),
        "target": f"≤ {pause_limit:.0f} LUFS (Ziel {target_lufs:.0f} − {PAUSE_LOUDNESS_MARGIN_LU:.0f} LU, "
                  f"Maximum der Momentary-Lautheit in der Pause {span[0]:.1f}–{span[1]:.1f} s)",
        "actual": f"{pause_out['max_lufs']:.1f} LUFS (Median {pause_out['median_lufs']:.1f}, RMS {pause_out['rms_dbfs']:.1f} dBFS)",
        "note": f"Vorher {pause_in['max_lufs']:.1f} LUFS maximal, Median {pause_in['median_lufs']:.1f}, "
                f"RMS {pause_in['rms_dbfs']:.1f} dBFS; absolute Änderung der Pause {pause_out['rms_dbfs'] - pause_in['rms_dbfs']:+.1f} dB."
                + ("" if pause_out["max_lufs"] <= pause_limit else
                   " Kette hebt die Pause zu weit an: Raum leiser machen (Lüfter, Abstand) oder normalizer.max_gain_db senken."),
    }

    sib_delta = after["sibilance_ratio_db"] - before["sibilance_ratio_db"]
    deesser_needed = before["sibilance_ratio_db"] > SIBILANCE_REFERENCE_DB
    if deesser_needed:
        # Hysteresis: a clear reduction passes. Compressor and normalizer lift the
        # 5-10 kHz band of soft fricatives relative to loud vowels by a few dB and
        # the de-esser takes that back, so a voice may end up where it started.
        # A borderline voice (at most SIBILANCE_BORDERLINE_DB above the reference)
        # therefore passes when it is not worse than +1 dB; a clearly sibilant
        # voice passes with +1 dB only if it ends up below the reference.
        borderline = before["sibilance_ratio_db"] <= SIBILANCE_REFERENCE_DB + SIBILANCE_BORDERLINE_DB
        sib_pass = (sib_delta <= -SIBILANCE_MAX_INCREASE_DB
                    or (sib_delta <= SIBILANCE_MAX_INCREASE_DB
                        and (borderline or after["sibilance_ratio_db"] <= SIBILANCE_REFERENCE_DB)))
        note = (f"De-Esser aktiv: Zischanteil muss um ≥ {SIBILANCE_MAX_INCREASE_DB:.0f} dB sinken"
                + (f" oder (Grenzstimme, höchstens {SIBILANCE_BORDERLINE_DB:.0f} dB über der Referenz) nicht mehr als "
                   f"+{SIBILANCE_MAX_INCREASE_DB:.0f} dB steigen" if borderline else
                   f" oder bei höchstens +{SIBILANCE_MAX_INCREASE_DB:.0f} dB unter der Referenz {SIBILANCE_REFERENCE_DB:.0f} dB liegen")
                + "; Kompressor und Normalizer heben das 5–10-kHz-Band leiser Frikative relativ an, der De-Esser nimmt das zurück.")
    else:
        # A clean voice stays clean: the chain may shift the band ratio a little
        # (compression acts more on the loud voiced peaks than on soft fricatives),
        # as long as the result stays below the sibilance reference, or it may not
        # get worse by more than SIBILANCE_MAX_INCREASE_DB.
        sib_pass = (after["sibilance_ratio_db"] <= SIBILANCE_REFERENCE_DB
                    or sib_delta <= SIBILANCE_MAX_INCREASE_DB)
        note = (f"Zischanteil lag mit {before['sibilance_ratio_db']:.1f} dB unter der Referenz "
                f"{SIBILANCE_REFERENCE_DB:.0f} dB, De-Esser ruht; Kriterium: nachher weiterhin unter der Referenz "
                f"oder nicht schlechter als +{SIBILANCE_MAX_INCREASE_DB:.0f} dB.")
    criteria["sibilance_reduced"] = {
        "passed": bool(sib_pass),
        "target": (f"Zischanteil ↓ ≥ {SIBILANCE_MAX_INCREASE_DB:.0f} dB oder ≤ {SIBILANCE_REFERENCE_DB:.0f} dB "
                   f"bei ≤ +{SIBILANCE_MAX_INCREASE_DB:.0f} dB" if deesser_needed
                   else f"Zischanteil ≤ {SIBILANCE_REFERENCE_DB:.0f} dB oder ≤ +{SIBILANCE_MAX_INCREASE_DB:.0f} dB"),
        "actual": f"{sib_delta:+.1f} dB ({before['sibilance_ratio_db']:.1f} → {after['sibilance_ratio_db']:.1f} dB)",
        "note": note + (f" Zischspitzen im De-Esser-Band {before['sibilant_band_peak_dbfs']:.1f} → "
                        f"{after['sibilant_band_peak_dbfs']:.1f} dBFS."),
    }

    lufs_err = after["integrated_lufs"] - target_lufs
    criteria["loudness_in_target"] = {
        "passed": bool(abs(lufs_err) <= LOUDNESS_TOLERANCE_LU),
        "target": f"{target_lufs:.0f} LUFS ± {LOUDNESS_TOLERANCE_LU:.0f} LU (integriert, gated)",
        "actual": f"{after['integrated_lufs']:.1f} LUFS ({lufs_err:+.1f} LU)",
        "note": f"Vorher {before['integrated_lufs']:.1f} LUFS.",
    }

    criteria["true_peak_below_ceiling"] = {
        "passed": bool(after["true_peak_dbtp"] <= ceiling + TRUE_PEAK_TOLERANCE_DB),
        "target": f"≤ {ceiling:.1f} dBTP (Messtoleranz {TRUE_PEAK_TOLERANCE_DB:.1f} dB)",
        "actual": f"{after['true_peak_dbtp']:.2f} dBTP",
        "note": f"Vorher {before['true_peak_dbtp']:.2f} dBTP.",
    }

    return {
        "before": before,
        "after": after,
        "criteria": criteria,
        "passed": bool(all(c["passed"] for c in criteria.values())),
        "info": {
            "speech_level_after_dbfs": after["speech_level_dbfs"],
            "speech_level_error_db": after["speech_level_dbfs"] - REFERENCE_SPEECH_LEVEL_DBFS,
            "snr_gain_db": snr_gain,
            "pause_rms_change_db": pause_out["rms_dbfs"] - pause_in["rms_dbfs"],
        },
    }


# --------------------------------------------------------------------------- #
# Chain processing and I/O
# --------------------------------------------------------------------------- #

def process_with_chain(x: np.ndarray, cfg: dict) -> np.ndarray:
    """Run ``x`` block-wise through a fresh ``Chain`` built from ``cfg``."""
    chain = Chain.from_config(cfg)
    block = int(cfg["block_size"])
    if block <= 0:
        raise ValueError(f"block_size must be positive, got {block}")
    x32 = np.asarray(x, dtype=np.float32)
    out = np.empty_like(x32)
    for start in range(0, x32.size, block):
        y = chain.process(x32[start:start + block])
        out[start:start + block] = np.asarray(y, dtype=np.float32)[: x32.size - start]
    return out


def load_base_config(base_cfg_path) -> dict:
    """Load and validate the base YAML; None means ``config/chain.default.yaml``.

    A previously calibrated file is accepted; its ``_calibration`` metadata is
    dropped so the new run starts from the plain schema.
    """
    path = Path(base_cfg_path) if base_cfg_path is not None else default_config_path()
    return strip_metadata(load_config(path))


def read_mono_wav(path, expected_sr: int | None = None,
                  channel: int | None = None) -> tuple[np.ndarray, int]:
    """Read a WAV with soundfile and return ``(mono float32 samples, sample_rate)``.

    ``channel`` None mixes all channels to mono, an integer picks one channel.
    Raises FileNotFoundError for a missing file and ValueError (with the path in
    the message) for an unreadable file, an empty file, a channel that does not
    exist, non-finite samples or a sample rate different from ``expected_sr``.
    """
    import soundfile as sf
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"WAV file not found: {path}")
    try:
        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    except (RuntimeError, sf.LibsndfileError) as exc:
        raise ValueError(f"{path} ist keine lesbare WAV-Datei: {exc}") from exc
    if data.shape[0] == 0:
        raise ValueError(f"{path} enthält keine Samples")
    if channel is None:
        mono = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]
    else:
        if not 0 <= channel < data.shape[1]:
            raise ValueError(f"{path} hat {data.shape[1]} Kanäle, Kanal {channel} existiert nicht")
        mono = data[:, channel]
    mono = np.ascontiguousarray(mono, dtype=np.float32)
    bad = int(np.count_nonzero(~np.isfinite(mono)))
    if bad:
        raise ValueError(
            f"Sample enthält nicht-finite Werte (NaN/Inf): {bad} von {mono.size} Samples in {path}; "
            "die Datei ist beschädigt oder falsch konvertiert, bitte neu aufnehmen.")
    sr = int(sr)
    if expected_sr is not None and sr != int(expected_sr):
        raise ValueError(
            f"{path} has a sample rate of {sr} Hz but the configuration expects {int(expected_sr)} Hz "
            f"(Rode NT-USB works at 48 kHz). Record the sample at {int(expected_sr)} Hz or convert it, "
            f"e.g. `afconvert -f WAVE -d LEI16@{int(expected_sr)} in.wav out.wav` (macOS) or "
            f"`ffmpeg -i in.wav -ar {int(expected_sr)} out.wav`; the calibration does not resample.")
    return mono, sr


def run_calibration(wav_path, base_cfg_path, out_cfg_path, report_path, processor=None) -> dict:
    """Full calibration run; see module docstring.

    ``processor`` is an optional callable ``x -> y`` (float32 mono) used instead
    of the real chain, mainly for tests; it is called once per pass with the
    current configuration ignored. Returns a dict with keys ``measurements``,
    ``cfg``, ``evaluation``, ``passed``, ``out_cfg_path``, ``report_path``,
    ``warnings``.
    """
    base_cfg = load_base_config(base_cfg_path)
    x, sr = read_mono_wav(wav_path, expected_sr=int(base_cfg["sample_rate"]))
    warnings: list[str] = []
    duration = x.size / sr
    if not RECOMMENDED_DURATION_S[0] <= duration <= RECOMMENDED_DURATION_S[1]:
        warnings.append(
            f"Sample ist {duration:.1f} s lang; empfohlen sind {RECOMMENDED_DURATION_S[0]:.0f}–"
            f"{RECOMMENDED_DURATION_S[1]:.0f} s mit Sprechpausen.")
    measurements = analyze(x, sr, deesser_bandwidth_oct=float(base_cfg["chain"]["deesser"]["bandwidth_oct"]))
    if measurements["pause"] is None:
        raise ValueError(NO_PAUSE_HINT)
    if not measurements["speech_detected"]:
        warnings.append("Keine Sprache erkannt: die Dynamik zwischen leisen und lauten Rahmen ist kleiner als "
                        f"{meters.MIN_SPEECH_SNR_DB:.0f} dB. Alle Sprach-bezogenen Werte sind Schätzungen.")

    def run_chain(cfg: dict) -> np.ndarray:
        y = processor(x) if processor is not None else process_with_chain(x, cfg)
        y = np.asarray(y, dtype=np.float32)
        if y.shape != x.shape:
            raise ValueError(f"processor returned shape {y.shape}, expected {x.shape}")
        return y

    cfg_first = recommend(measurements, base_cfg)
    # First pass without the normalizer: the gate and the gain limit are designed
    # for the pause level at the normalizer's input, not for a level already
    # lifted by an uncalibrated AGC.
    cfg_probe = copy.deepcopy(cfg_first)
    cfg_probe["chain"]["normalizer"]["enabled"] = False
    y_first = run_chain(cfg_probe)
    de = cfg_first["chain"]["deesser"]
    after_first = analyze(y_first, sr, float(de["freq_hz"]), float(de["bandwidth_oct"]))
    if after_first["pause"] is None:
        # The output pause is located on the input span (the output may be gated).
        after_first["pause"] = meters.pause_loudness_lufs(
            y_first, sr, measurements["pause"]["start_s"], measurements["pause"]["end_s"])
    cfg = refine_normalizer(cfg_first, after_first)
    y = run_chain(cfg)
    evaluation = evaluate(x, y, sr, cfg)
    out_path = Path(out_cfg_path)
    save_config(_plain(cfg), out_path)
    rep_path = Path(report_path)
    rep_path.parent.mkdir(parents=True, exist_ok=True)
    rep_path.write_text(
        render_report(wav_path, base_cfg, cfg, measurements, evaluation, warnings), encoding="utf-8")
    return {
        "measurements": measurements,
        "cfg": cfg,
        "evaluation": evaluation,
        "passed": evaluation["passed"],
        "out_cfg_path": str(out_path),
        "report_path": str(rep_path),
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #

REPORT_SECTIONS = (
    "## 1. Messwerte vorher",
    "## 2. Abgeleitete Parameter",
    "## 3. Messwerte nachher",
    "## 4. Akzeptanzkriterien",
)

_MEASUREMENT_ROWS = (
    ("duration_s", "Dauer", "{:.1f} s"),
    ("rms_dbfs", "RMS gesamt", "{:.1f} dBFS"),
    ("sample_peak_dbfs", "Sample-Peak", "{:.1f} dBFS"),
    ("true_peak_dbtp", "True Peak", "{:.2f} dBTP"),
    ("noise_floor_dbfs", "Rauschteppich (10. Perzentil, 100-ms-Rahmen)", "{:.1f} dBFS"),
    ("speech_level_dbfs", "Sprachpegel (aktive Rahmen, Energiemittel)", "{:.1f} dBFS"),
    ("speech_peak_level_dbfs", "Silbenspitzen (Mittel der 50-ms-Rahmenspitzen)", "{:.1f} dBFS"),
    ("speech_crest_db", "Crest-Faktor Sprache (Spitze − RMS)", "{:.1f} dB"),
    ("snr_db", "Störabstand Sprache/Rauschen", "{:.1f} dB"),
    ("dynamic_range_db", "Dynamik (95. − 10. Perzentil der Rahmen)", "{:.1f} dB"),
    ("speech_activity_ratio", "Sprachanteil", "{:.0%}"),
    ("f0_hz", "Grundfrequenz (Median stimmhafter Rahmen)", "{:.0f} Hz"),
    ("voiced_ratio", "Anteil stimmhafter Rahmen", "{:.0%}"),
    ("sibilance_ratio_db", "Zischanteil (5–10 kHz − 0,3–3 kHz)", "{:.1f} dB"),
    ("sibilance_centroid_hz", "Schwerpunkt Zischenergie 4–10 kHz", "{:.0f} Hz"),
    ("sibilant_band_peak_dbfs", "Zischspitzen im De-Esser-Band (95. Perzentil)", "{:.1f} dBFS"),
    ("sibilant_frame_ratio", "Anteil Zischrahmen an den aktiven Rahmen", "{:.0%}"),
    ("spectral_tilt_db_per_oct", "Spektrale Neigung 200 Hz–8 kHz", "{:.1f} dB/Okt"),
    ("integrated_lufs", "Lautheit integriert (gated)", "{:.1f} LUFS"),
    ("short_term_lufs_max", "Lautheit Short-Term maximal", "{:.1f} LUFS"),
)

_PAUSE_ROWS = (
    ("max_lufs", "Pause: Momentary-Lautheit maximal", "{:.1f} LUFS"),
    ("median_lufs", "Pause: Momentary-Lautheit Median", "{:.1f} LUFS"),
    ("rms_dbfs", "Pause: RMS", "{:.1f} dBFS"),
)


def _fmt(fmt: str, value) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "–"
    try:
        return fmt.format(value)
    except (ValueError, TypeError):
        return str(value)


def _flatten_chain(chain: dict) -> dict[str, float]:
    out = {}
    for stage, params in chain.items():
        if not isinstance(params, dict):
            continue
        for name, value in params.items():
            if name == "bands" and isinstance(value, list):
                for band in value:
                    out[f"chain.{stage}.{band.get('type')}@{float(band.get('freq_hz', 0)):.0f}.gain_db"] = band.get("gain_db")
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                out[f"chain.{stage}.{name}"] = value
    return out


def render_report(wav_path, base_cfg: dict, cfg: dict, measurements: dict,
                  evaluation: dict, warnings: list[str]) -> str:
    """German Markdown report with the mandatory sections (see REPORT_SECTIONS)."""
    before = evaluation["before"]
    after = evaluation["after"]
    reasons = cfg.get("_calibration", {}).get("reasons", {})
    old_vals = _flatten_chain(base_cfg["chain"])
    new_vals = _flatten_chain(cfg["chain"])
    lines = [
        "# Kalibrierungsbericht voicechain",
        "",
        f"- Datei: `{wav_path}`",
        f"- Samplerate: {measurements['sample_rate']} Hz, Dauer {measurements['duration_s']:.1f} s",
        f"- Erstellt: {cfg.get('_calibration', {}).get('created', '')}",
        f"- Gesamtergebnis: **{'bestanden' if evaluation['passed'] else 'nicht bestanden'}**",
        "",
    ]
    if warnings:
        lines.append("### Hinweise")
        lines.extend(f"- {w}" for w in warnings)
        lines.append("")

    lines += [REPORT_SECTIONS[0], "", "| Messgröße | Wert |", "|---|---|"]
    for key, label, fmt in _MEASUREMENT_ROWS:
        lines.append(f"| {label} | {_fmt(fmt, measurements.get(key))} |")
    pause = measurements.get("pause")
    if pause is not None:
        lines.append(f"| Längste Pause (≥ {MIN_PAUSE_S:.0f} s, nach Sprache) | {pause['start_s']:.1f}–{pause['end_s']:.1f} s |")
        for key, label, fmt in _PAUSE_ROWS:
            lines.append(f"| {label} | {_fmt(fmt, pause.get(key))} |")
    lines.append("")
    lines.append("Bandenergien der aktiven Sprachrahmen (Dichte relativ zum Ankerband "
                 f"{measurements['eq_anchor']['lo_hz']:.0f}–{measurements['eq_anchor']['hi_hz']:.0f} Hz):")
    lines.append("")
    lines.append("| Band | Energie | Dichte relativ |")
    lines.append("|---|---|---|")
    anchor_density = measurements["eq_anchor"]["density_db"]
    for info in measurements["eq_bands"].values():
        lines.append(f"| {info['lo_hz']:.0f}–{info['hi_hz']:.0f} Hz | {info['energy_db']:.1f} dBFS | "
                     f"{info['density_db'] - anchor_density:+.1f} dB |")
    lines.append("")

    lines += [REPORT_SECTIONS[1], "", "| Parameter | vorher | nachher | Begründung |", "|---|---|---|---|"]
    for path_key, reason in reasons.items():
        old = old_vals.get(path_key)
        new = new_vals.get(path_key)
        old_s = _fmt("{:.2f}", old) if old is not None else "–"
        new_s = _fmt("{:.2f}", new) if new is not None else "–"
        lines.append(f"| `{path_key}` | {old_s} | {new_s} | {reason} |")
    lines.append("")

    lines += [REPORT_SECTIONS[2], "", "| Messgröße | vorher | nachher | Differenz |", "|---|---|---|---|"]
    for key, label, fmt in _MEASUREMENT_ROWS:
        b, a = before.get(key), after.get(key)
        diff = (f"{a - b:+.1f}" if isinstance(a, (int, float)) and isinstance(b, (int, float))
                and math.isfinite(a) and math.isfinite(b) else "–")
        lines.append(f"| {label} | {_fmt(fmt, b)} | {_fmt(fmt, a)} | {diff} |")
    pb, pa = before.get("pause"), after.get("pause")
    if pb is not None and pa is not None:
        for key, label, fmt in _PAUSE_ROWS:
            lines.append(f"| {label} ({pb['start_s']:.1f}–{pb['end_s']:.1f} s) | {_fmt(fmt, pb[key])} | "
                         f"{_fmt(fmt, pa[key])} | {pa[key] - pb[key]:+.1f} |")
    info = evaluation.get("info", {})
    lines.append("")
    lines.append(f"Sprachpegel nach Kette: {info.get('speech_level_after_dbfs', float('nan')):.1f} dBFS "
                 f"(Referenz {REFERENCE_SPEECH_LEVEL_DBFS:.0f} dBFS vor Normalizer; der Normalizer hebt auf "
                 f"die Ziel-Lautheit an). Störabstandsgewinn {info.get('snr_gain_db', float('nan')):+.1f} dB, "
                 f"absolute Änderung des Pausenpegels {info.get('pause_rms_change_db', float('nan')):+.1f} dB.")
    lines.append("")

    lines += [REPORT_SECTIONS[3], "", "| Kriterium | Ziel | Ist | Ergebnis | Hinweis |", "|---|---|---|---|---|"]
    titles = {
        "noise_floor_reduction": "Störabstand verbessert",
        "pause_loudness": "Pausenlautheit nach Kette",
        "sibilance_reduced": "Zischanteil",
        "loudness_in_target": "Lautheit im Ziel",
        "true_peak_below_ceiling": "True Peak unter Ceiling",
    }
    for key, crit in evaluation["criteria"].items():
        lines.append(f"| {titles.get(key, key)} | {crit['target']} | {crit['actual']} | "
                     f"**{'bestanden' if crit['passed'] else 'nicht bestanden'}** | {crit['note']} |")
    lines += [
        "",
        "## 5. Annahmen und Grenzen",
        "",
        f"- Referenz-Sprachpegel {REFERENCE_SPEECH_LEVEL_DBFS:.0f} dBFS RMS nach Trim (Praxiswert, docs/02).",
        f"- Zischanteil-Referenz {SIBILANCE_REFERENCE_DB:.0f} dB ist eine Annahme aus dem Langzeit-Sprachspektrum; "
        "die De-Esser-Schwelle wird aus dem gemessenen Spitzenpegel der Zischlaute im De-Esser-Band abgeleitet.",
        f"- EQ-Referenzkurve: flach bis {EQ_REFERENCE_KNEE_HZ:.0f} Hz, {EQ_REFERENCE_SLOPE_MID_DB_PER_OCT:+.0f} dB/Okt bis "
        f"{EQ_REFERENCE_KNEE_HIGH_HZ / 1000:.0f} kHz, darüber {EQ_REFERENCE_SLOPE_HIGH_DB_PER_OCT:+.0f} dB/Okt (Annahme: "
        f"typisches Langzeit-Sprachspektrum fällt oberhalb 4 kHz steiler); nur {EQ_CORRECTION_FRACTION:.0%} der Abweichung "
        f"werden korrigiert, maximal ±{EQ_MAX_GAIN_DB:.0f} dB; Shelves nur als Absenkung (High-Shelf höchstens "
        f"−{EQ_HIGH_SHELF_MAX_CUT_DB:.0f} dB, gemessen ohne Zischrahmen); Low-Shelf nur bei Grundfrequenz "
        f"≤ {EQ_LOW_SHELF_MAX_F0_HZ:.0f} Hz.",
        f"- Normalizer-Gate und -Gainbegrenzung stammen aus der Pausenlautheit nach dem ersten Durchlauf mit "
        f"abgeschaltetem Normalizer (Abstände {NORMALIZER_GATE_MARGIN_DB:.0f} dB bzw. {NORMALIZER_PAUSE_HEADROOM_LU:.0f} LU, Annahmen).",
        "- Lautheitskriterium: integrierte Lautheit mit BS.1770-Gating (absolut −70 LUFS, relativ −10 LU), also "
        "die Lautheit der Sprache ohne Pausen; der Normalizer regelt auf dieselbe gegatete Sprachlautheit. Ungegatete "
        "3-s-Fenster lesen bei 40 % Pausenanteil 2–3 LU tiefer und sind kein Maßstab. Die Messung 'nachher' läuft "
        "über dieselbe Datei; die Lautheitsregelung braucht einige Sekunden, bis sie das Ziel erreicht.",
        "- Rauschteppich-Kriterium (Beauftragung §6.3) wird bei gleichem Sprachpegel bewertet (Störabstandsgewinn); "
        "die absolute Pausenlautheit nach Kette wird zusätzlich gegen Ziel − "
        f"{PAUSE_LOUDNESS_MARGIN_LU:.0f} LU geprüft und die Absolutwerte stehen daneben. Bei kleinem Störabstand "
        f"(< {EXPANDER_FLOOR_MARGIN_DB + EXPANDER_SPEECH_MARGIN_DB:.0f} dB) kann der Expander die 6 dB nicht erreichen, "
        "ohne Sprache abzuschneiden; dann hilft nur eine leisere Umgebung oder geringerer Mikrofonabstand.",
        "- Kompressor-Schwelle liegt auf Höhe des Sprach-RMS, weil der Kompressor der Kette Spitzen misst "
        f"(Silbenspitzen ≈ {evaluation['before']['speech_crest_db']:.0f} dB über RMS); "
        "eine tiefere Schwelle verdichtet stärker, hebt aber mit Makeup/Normalizer auch das Rauschen wieder an.",
        "",
    ]
    return "\n".join(lines)
