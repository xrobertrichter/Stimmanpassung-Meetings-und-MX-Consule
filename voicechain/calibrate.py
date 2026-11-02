"""Calibration: analyse a speech sample, derive chain parameters, verify, report.

Workflow (:func:`run_calibration`):

1. read the WAV (soundfile), mix to mono, require the configured sample rate;
2. :func:`analyze` -> measurements (noise floor, speech level, sibilance, tilt,
   peaks, loudness, band energies);
3. :func:`recommend` -> new configuration (deep copy of the base config) with a
   reason per changed value under ``cfg["_calibration"]["reasons"]``;
4. process the sample through the chain built from the new configuration
   (``voicechain.dsp.chain.Chain`` is imported lazily so this module stays
   importable without the DSP package; tests may pass a ``processor`` callable);
5. :func:`evaluate` -> before/after measurements and the acceptance criteria of
   docs/00_Beauftragung.md section 6.3;
6. write the YAML and a German Markdown report.

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

# --------------------------------------------------------------------------- #
# Control-law constants (see docs/03 for the German explanation)
# --------------------------------------------------------------------------- #

#: Speech RMS after input trim (memory/04: -20 dBFS +-3 dB). Leaves ~14 dB of
#: headroom for speech peaks (crest factor of speech ~12-15 dB) before the limiter.
REFERENCE_SPEECH_LEVEL_DBFS = -20.0
#: Input trim range: -12 dB catches hot gain settings, +24 dB quiet USB preamps.
TRIM_BOUNDS_DB = (-12.0, 24.0)
#: Expander threshold above the noise floor (memory/04, E1): 8 dB keeps the
#: expander closed during noise fluctuation (+-3 dB for fan/room noise).
EXPANDER_FLOOR_MARGIN_DB = 8.0
#: ... but at least this far below the speech level so soft syllables pass.
EXPANDER_SPEECH_MARGIN_DB = 12.0
#: Sibilance ratio (5-10 kHz minus 0.3-3 kHz energy, active frames) of
#: unproblematic speech. Assumption derived from the long-term average speech
#: spectrum (LTASS, approx. -6 dB/oct above 500 Hz, steeper above 4 kHz): about
#: -15 dB. Values above it call for de-essing.
SIBILANCE_REFERENCE_DB = -15.0
#: A sibilant is short; during the burst the band level exceeds the long-term
#: sibilance ratio. Assumption: 6 dB (bursts occupy roughly a quarter of the
#: active time, 10*log10(4) = 6 dB).
SIBILANCE_BURST_CREST_DB = 6.0
#: Minimum gain reduction the de-esser should reach on measured sibilants.
DEESSER_MIN_REDUCTION_DB = 4.0
#: De-esser centre frequency limits (memory/04: 5-9 kHz).
DEESSER_FREQ_BOUNDS_HZ = (5000.0, 9000.0)
#: Compressor threshold relative to the speech RMS level. The chain's compressor
#: uses a sample-peak detector; syllable peaks of speech lie ~10 dB above the
#: frame RMS (measured crest of 50 ms frames, see ``speech_crest_db``), so a
#: threshold *at* the RMS level already reduces the peaks by ~6 dB at 3:1.
#: 0 dB matches memory/04 (speech -20 dBFS RMS, threshold -20 dBFS). A margin
#: of 6 dB below the RMS (first draft) gave ~10 dB gain reduction and destroyed
#: the expander's signal-to-noise gain (measured on the real chain: +3 dB instead
#: of +7 dB), so it was dropped.
COMPRESSOR_SPEECH_MARGIN_DB = 0.0
#: Static soft-knee characteristic (Giannoulis, Massberg, Reiss 2012) used to
#: predict the compressor's mean gain reduction from the syllable peak level.
#: The prediction matched the real chain within 0.6 dB for thresholds from
#: -26 to -14 dBFS on synthetic speech.
#: Makeup gain limits; 12 dB mirrors the normalizer's max_gain_db.
MAKEUP_BOUNDS_DB = (0.0, 12.0)
#: EQ reference curve for speech: flat power spectral density below the knee,
#: -6 dB/oct above. Assumption based on the typical long-term speech spectrum.
EQ_REFERENCE_KNEE_HZ = 500.0
EQ_REFERENCE_SLOPE_DB_PER_OCT = -6.0
#: Only half of a measured deviation is corrected: the measurement mixes the
#: microphone response with the speaker's own timbre, which should survive.
#: Assumption.
EQ_CORRECTION_FRACTION = 0.5
#: Deviations below this are left alone (measurement noise, not timbre).
EQ_MIN_DEVIATION_DB = 1.0
#: Hard limit per EQ band (task specification).
EQ_MAX_GAIN_DB = 4.0
#: Default Q values when a band has to be created (RBJ cookbook typical values).
EQ_SHELF_Q = 0.7
EQ_PEAK_Q = 1.0
#: EQ bands the calibration may touch: (type, centre, analysis band lo, hi).
EQ_TARGET_BANDS = (
    ("low_shelf", 120.0, 80.0, 170.0),
    ("peak", 250.0, 170.0, 350.0),
    ("peak", 3000.0, 2000.0, 4500.0),
    ("high_shelf", 10000.0, 7000.0, 14000.0),
)
#: Anchor band for relative band levels (speech core, between the EQ bands).
EQ_ANCHOR_BAND_HZ = (350.0, 1000.0)
#: Relative tolerance when matching an existing EQ band by frequency.
EQ_BAND_MATCH_TOLERANCE = 0.2

#: Acceptance criteria (docs/00 section 6.3 and memory/04).
NOISE_FLOOR_MIN_REDUCTION_DB = 6.0
LOUDNESS_TOLERANCE_LU = 2.0
#: Sibilance must not get worse by more than this when the de-esser is not
#: needed (assumption: 1 dB is below audibility for a band-energy ratio).
SIBILANCE_MAX_INCREASE_DB = 1.0
#: Difference tolerated between the limiter's internal peak estimate and the
#: 4x-oversampled true-peak meter (assumption: 0.1 dB).
TRUE_PEAK_TOLERANCE_DB = 0.1

#: Sample length limits: docs/00 section 3.2 asks for 30-120 s. Below 10 s the
#: noise-floor percentile cannot be estimated reliably, so that is an error.
RECOMMENDED_DURATION_S = (30.0, 120.0)
MIN_DURATION_S = 10.0

#: Fallback configuration (schema of docs/00 section 4.1) used when no base
#: configuration path is given. ``config/chain.default.yaml`` is the normal source.
DEFAULT_CONFIG: dict = {
    "sample_rate": 48000,
    "block_size": 256,
    "input": {"device": "RØDE NT-USB", "channel": 0},
    "output": {"device": "BlackHole 2ch", "channels": 2},
    "chain": {
        "input_trim": {"gain_db": 0.0},
        "highpass": {"enabled": True, "freq_hz": 80.0, "order": 4},
        "expander": {"enabled": True, "threshold_db": -45.0, "ratio": 2.5, "range_db": 20.0,
                     "attack_ms": 5.0, "hold_ms": 50.0, "release_ms": 150.0, "knee_db": 6.0},
        "deesser": {"enabled": True, "freq_hz": 6500.0, "bandwidth_oct": 1.0, "threshold_db": -30.0,
                    "ratio": 4.0, "max_reduction_db": 8.0, "attack_ms": 1.0, "release_ms": 60.0},
        "eq": {"enabled": True, "bands": [
            {"type": "low_shelf", "freq_hz": 120.0, "gain_db": 0.0, "q": EQ_SHELF_Q},
            {"type": "peak", "freq_hz": 250.0, "gain_db": 0.0, "q": EQ_PEAK_Q},
            {"type": "peak", "freq_hz": 3000.0, "gain_db": 0.0, "q": EQ_PEAK_Q},
            {"type": "high_shelf", "freq_hz": 10000.0, "gain_db": 0.0, "q": EQ_SHELF_Q},
        ]},
        "compressor": {"enabled": True, "threshold_db": -20.0, "ratio": 3.0, "knee_db": 6.0,
                       "attack_ms": 10.0, "release_ms": 100.0, "makeup_db": 0.0},
        "normalizer": {"enabled": True, "target_lufs": -18.0, "window_s": 3.0, "max_gain_db": 12.0,
                       "min_gain_db": -12.0, "rate_db_per_s": 1.0, "gate_lufs": -40.0},
        "limiter": {"enabled": True, "ceiling_dbfs": -1.0, "lookahead_ms": 5.0, "release_ms": 50.0},
    },
    "adaptive": {
        "enabled": True,
        "learn_s": 30.0,
        "interval_s": 2.0,
        "max_step_db": 0.5,
        "log_path": "logs/adaptive.jsonl",
        "bounds": {
            "expander.threshold_db": [-70.0, -30.0],
            "deesser.threshold_db": [-45.0, -15.0],
            "compressor.threshold_db": [-35.0, -10.0],
        },
    },
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def clamp(value: float, lo: float, hi: float) -> float:
    if lo > hi:
        raise ValueError(f"invalid bounds: lower {lo} > upper {hi}")
    return float(min(max(value, lo), hi))


def parse_bounds(cfg: dict) -> dict[tuple[str, str], tuple[float, float]]:
    """``cfg["adaptive"]["bounds"]`` -> ``{(stage, name): (lo, hi)}``."""
    raw = cfg.get("adaptive", {}).get("bounds", {})
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


def bounds_for(bounds: dict, stage: str, name: str, default: tuple[float, float]) -> tuple[float, float]:
    return bounds.get((stage, name), default)


# --------------------------------------------------------------------------- #
# Control laws (shared with voicechain.adaptive)
# --------------------------------------------------------------------------- #

def input_trim_target(speech_level_dbfs: float) -> float:
    """Gain that moves the measured speech RMS to the reference level."""
    return clamp(REFERENCE_SPEECH_LEVEL_DBFS - speech_level_dbfs, *TRIM_BOUNDS_DB)


def expander_threshold_target(noise_floor_dbfs: float, speech_level_dbfs: float | None) -> float:
    """Noise floor + margin, but at least ``EXPANDER_SPEECH_MARGIN_DB`` below speech.

    ``speech_level_dbfs`` may be None when no speech is present (then only the
    floor rule applies). Bounds are applied by the caller.
    """
    target = noise_floor_dbfs + EXPANDER_FLOOR_MARGIN_DB
    if speech_level_dbfs is not None:
        target = min(target, speech_level_dbfs - EXPANDER_SPEECH_MARGIN_DB)
    return float(target)


def deesser_threshold_target(speech_level_dbfs: float, sibilance_ratio_db: float,
                             ratio: float, default_threshold_db: float) -> tuple[float, bool]:
    """De-esser threshold so that measured sibilants get >= DEESSER_MIN_REDUCTION_DB.

    Returns ``(threshold_db, engaged)``. If the sibilance ratio is at or below
    the reference, the default threshold is returned and ``engaged`` is False.
    Model: sideband level during a sibilant ~ speech level + sibilance ratio +
    burst crest; a ratio R reduces (level - threshold) * (1 - 1/R). The result
    is never above the default threshold: a lower default already gives more
    than the minimum reduction, and raising it would de-ess *less* on a
    sibilant voice.
    """
    if ratio <= 1.0:
        raise ValueError(f"de-esser ratio must be > 1, got {ratio}")
    if sibilance_ratio_db <= SIBILANCE_REFERENCE_DB:
        return float(default_threshold_db), False
    sibilant_level = speech_level_dbfs + sibilance_ratio_db + SIBILANCE_BURST_CREST_DB
    overshoot_needed = DEESSER_MIN_REDUCTION_DB / (1.0 - 1.0 / ratio)
    return float(min(default_threshold_db, sibilant_level - overshoot_needed)), True


def compressor_threshold_target(speech_level_dbfs: float) -> float:
    return float(speech_level_dbfs - COMPRESSOR_SPEECH_MARGIN_DB)


def static_compressor_reduction_db(level_db: float, threshold_db: float, ratio: float,
                                   knee_db: float) -> float:
    """Gain reduction (>= 0 dB) of a soft-knee compressor at a detector level."""
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
    """Makeup that restores the level the compressor removes from syllable peaks.

    ``speech_peak_level_dbfs`` is the mean 50 ms frame peak level of active
    speech (``analyze()["speech_peak_level_dbfs"]`` plus the input trim).
    """
    return clamp(static_compressor_reduction_db(speech_peak_level_dbfs, threshold_db, ratio, knee_db),
                 *MAKEUP_BOUNDS_DB)


def eq_reference_db(freq_hz: float) -> float:
    """Reference power spectral density (dB, relative) at ``freq_hz``."""
    if freq_hz <= EQ_REFERENCE_KNEE_HZ:
        return 0.0
    return EQ_REFERENCE_SLOPE_DB_PER_OCT * math.log2(freq_hz / EQ_REFERENCE_KNEE_HZ)


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


def analyze(x: np.ndarray, sample_rate: int) -> dict:
    """Measure everything the recommendation and the report need.

    Keys: duration_s, sample_rate, rms_dbfs, sample_peak_dbfs, true_peak_dbtp,
    noise_floor_dbfs, speech_level_dbfs, snr_db, speech_detected,
    speech_activity_ratio, sibilance_ratio_db, sibilance_centroid_hz,
    spectral_tilt_db_per_oct, integrated_lufs, short_term_lufs_max,
    eq_bands (dict label -> {lo_hz, hi_hz, centre_hz, energy_db, density_db}),
    eq_anchor (same structure for the anchor band).
    """
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 2:
        x = x.mean(axis=1)
    if x.size < sample_rate * MIN_DURATION_S:
        raise ValueError(
            f"sample is {x.size / sample_rate:.1f} s long; at least {MIN_DURATION_S:.0f} s "
            f"with speech pauses are needed to estimate the noise floor")
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
    speech_peak = float(np.mean(20.0 * np.log10(np.maximum(np.max(peak_frames, axis=1),
                                                            math.sqrt(meters.POWER_FLOOR)))))

    def band_info(lo: float, hi: float) -> dict:
        energy = meters.band_energies_db(x, sample_rate, [lo, hi])[0]
        return {"lo_hz": lo, "hi_hz": hi, "centre_hz": math.sqrt(lo * hi),
                "energy_db": energy, "density_db": energy - 10.0 * math.log10(hi - lo)}

    eq_bands = {}
    for kind, fc, lo, hi in _usable_eq_bands(sample_rate):
        eq_bands[f"{kind}@{fc:.0f}"] = {"type": kind, "freq_hz": fc, **band_info(lo, hi)}
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
        "speech_detected": bool(detected),
        "speech_activity_ratio": float(np.mean(mask)) if mask.size else 0.0,
        "sibilance_ratio_db": meters.sibilance_ratio_db(x, sample_rate),
        "sibilance_centroid_hz": meters.sibilance_centroid_hz(x, sample_rate),
        "spectral_tilt_db_per_oct": meters.spectral_tilt_db_per_oct(x, sample_rate),
        "integrated_lufs": meters.integrated_lufs(x, sample_rate),
        "short_term_lufs_max": float(np.max(st_series)) if st_series.size else meters.DB_FLOOR,
        "eq_bands": eq_bands,
        "eq_anchor": band_info(*EQ_ANCHOR_BAND_HZ),
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
    leading underscore are metadata and must be ignored by config validators.
    """
    cfg = copy.deepcopy(base_cfg)
    chain = cfg.setdefault("chain", {})
    bounds = parse_bounds(cfg)
    reasons: dict[str, str] = {}
    m = measurements
    speech_in = float(m["speech_level_dbfs"])
    floor_in = float(m["noise_floor_dbfs"])
    speech_detected = bool(m.get("speech_detected", True))

    # 0. Input trim
    trim = input_trim_target(speech_in)
    chain.setdefault("input_trim", {})["gain_db"] = round(trim, 2)
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
    exp = chain.setdefault("expander", {})
    exp_lo, exp_hi = bounds_for(bounds, "expander", "threshold_db", (-70.0, -30.0))
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
    de = chain.setdefault("deesser", {})
    de_lo, de_hi = bounds_for(bounds, "deesser", "threshold_db", (-45.0, -15.0))
    sib = float(m["sibilance_ratio_db"])
    centroid = float(m["sibilance_centroid_hz"])
    de_freq = clamp(centroid, *DEESSER_FREQ_BOUNDS_HZ)
    de["freq_hz"] = round(de_freq, 0)
    reasons["chain.deesser.freq_hz"] = (
        f"Schwerpunkt der Zischenergie 4–10 kHz bei {centroid:.0f} Hz, begrenzt auf "
        f"{DEESSER_FREQ_BOUNDS_HZ[0]:.0f}…{DEESSER_FREQ_BOUNDS_HZ[1]:.0f} Hz → {de_freq:.0f} Hz.")
    de_ratio = float(de.get("ratio", DEFAULT_CONFIG["chain"]["deesser"]["ratio"]))
    de_default = float(de.get("threshold_db", DEFAULT_CONFIG["chain"]["deesser"]["threshold_db"]))
    de_raw, engaged = deesser_threshold_target(speech_after, sib, de_ratio, de_default)
    de_thr = clamp(de_raw, de_lo, de_hi)
    de["threshold_db"] = round(de_thr, 2)
    if engaged:
        sibilant_level = speech_after + sib + SIBILANCE_BURST_CREST_DB
        computed = sibilant_level - DEESSER_MIN_REDUCTION_DB / (1 - 1 / de_ratio)
        reasons["chain.deesser.threshold_db"] = (
            f"Zischanteil {sib:.1f} dB liegt über der Referenz {SIBILANCE_REFERENCE_DB:.0f} dB (Annahme). "
            f"Zischpegel ≈ Sprache {speech_after:.1f} + Zischanteil {sib:.1f} + Burst-Crest "
            f"{SIBILANCE_BURST_CREST_DB:.0f} dB (Annahme) = {sibilant_level:.1f} dBFS; "
            f"für ≥ {DEESSER_MIN_REDUCTION_DB:.0f} dB Reduktion bei Ratio {de_ratio:g} muss die Schwelle "
            f"{DEESSER_MIN_REDUCTION_DB / (1 - 1 / de_ratio):.1f} dB darunter liegen → rechnerisch {computed:.1f}; ")
        if computed >= de_default:
            reasons["chain.deesser.threshold_db"] += (
                f"die Standardschwelle {de_default:.1f} liegt bereits tiefer und bleibt "
                f"(Reduktion ≥ {DEESSER_MIN_REDUCTION_DB:.0f} dB gesichert, Obergrenze max_reduction_db); ")
        reasons["chain.deesser.threshold_db"] += f"Grenzen {de_lo:.0f}…{de_hi:.0f} → {de_thr:.1f} dBFS."
    else:
        reasons["chain.deesser.threshold_db"] = (
            f"Zischanteil {sib:.1f} dB liegt nicht über der Referenz {SIBILANCE_REFERENCE_DB:.0f} dB (Annahme); "
            f"Standardschwelle {de_default:.1f} dBFS bleibt (Grenzen {de_lo:.0f}…{de_hi:.0f} → {de_thr:.1f}).")

    # 4. EQ: the measured correction is *added* to the base value of the matching
    # band. The base value describes the intended voicing for the codec
    # (docs/02), the correction describes microphone and speaker; both apply.
    eq = chain.setdefault("eq", {})
    bands = eq.setdefault("bands", [])
    anchor = m["eq_anchor"]
    for info in m["eq_bands"].values():
        kind, fc = info["type"], float(info["freq_hz"])
        measured_rel = info["density_db"] - anchor["density_db"]
        reference_rel = eq_reference_db(info["centre_hz"]) - eq_reference_db(anchor["centre_hz"])
        deviation = measured_rel - reference_rel
        if abs(deviation) < EQ_MIN_DEVIATION_DB:
            correction = 0.0
        else:
            correction = clamp(-EQ_CORRECTION_FRACTION * deviation, -EQ_MAX_GAIN_DB, EQ_MAX_GAIN_DB)
        band = _find_eq_band(bands, kind, fc)
        if band is None:
            band = {"type": kind, "freq_hz": fc, "gain_db": 0.0,
                    "q": EQ_SHELF_Q if kind.endswith("shelf") else EQ_PEAK_Q}
            bands.append(band)
            created = True
        else:
            created = False
        base_gain = float(band.get("gain_db", 0.0))
        gain = clamp(base_gain + correction, -EQ_MAX_GAIN_DB, EQ_MAX_GAIN_DB)
        band["gain_db"] = round(gain, 2)
        actual_freq = float(band["freq_hz"])
        reasons[f"chain.eq.{kind}@{actual_freq:.0f}.gain_db"] = (
            f"Band {info['lo_hz']:.0f}–{info['hi_hz']:.0f} Hz liegt {measured_rel:+.1f} dB zum Ankerband "
            f"{anchor['lo_hz']:.0f}–{anchor['hi_hz']:.0f} Hz; Referenzkurve (flach bis {EQ_REFERENCE_KNEE_HZ:.0f} Hz, "
            f"dann {EQ_REFERENCE_SLOPE_DB_PER_OCT:+.0f} dB/Okt, Annahme) erwartet {reference_rel:+.1f} dB → "
            f"Abweichung {deviation:+.1f} dB; Korrektur {EQ_CORRECTION_FRACTION:.0%} davon, "
            f"Totzone ±{EQ_MIN_DEVIATION_DB:.0f} dB → {correction:+.1f} dB "
            + (f"auf neues Band bei {actual_freq:.0f} Hz" if created
               else f"zum Basiswert {base_gain:+.1f} dB des Bands bei {actual_freq:.0f} Hz")
            + f"; Grenze ±{EQ_MAX_GAIN_DB:.0f} dB → {gain:+.1f} dB.")

    # 5. Compressor
    comp = chain.setdefault("compressor", {})
    comp_lo, comp_hi = bounds_for(bounds, "compressor", "threshold_db", (-35.0, -10.0))
    comp_thr = clamp(compressor_threshold_target(speech_after), comp_lo, comp_hi)
    comp["threshold_db"] = round(comp_thr, 2)
    comp_ratio = float(comp.get("ratio", DEFAULT_CONFIG["chain"]["compressor"]["ratio"]))
    comp_knee = float(comp.get("knee_db", DEFAULT_CONFIG["chain"]["compressor"]["knee_db"]))
    peak_after = float(m.get("speech_peak_level_dbfs", speech_in)) + trim
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

    # 5b / 6. Normalizer and limiter: keep configured values, document why.
    norm = chain.setdefault("normalizer", {})
    norm.setdefault("target_lufs", DEFAULT_CONFIG["chain"]["normalizer"]["target_lufs"])
    reasons["chain.normalizer.target_lufs"] = (
        f"Bleibt bei {float(norm['target_lufs']):.0f} LUFS (Zielwert aus Konfiguration, "
        "memory/04: −18 LUFS ± 2 LU für Konferenz, E3).")
    lim = chain.setdefault("limiter", {})
    lim.setdefault("ceiling_dbfs", DEFAULT_CONFIG["chain"]["limiter"]["ceiling_dbfs"])
    reasons["chain.limiter.ceiling_dbfs"] = (
        f"Bleibt bei {float(lim['ceiling_dbfs']):.0f} dBTP (True-Peak-Obergrenze, E3).")

    cfg["_calibration"] = {
        "created": _dt.datetime.now().isoformat(timespec="seconds"),
        "measurements": _plain(measurements),
        "reasons": reasons,
    }
    return cfg


def _plain(obj):
    """Convert numpy scalars/arrays to plain Python for YAML/JSON output."""
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return [_plain(v) for v in obj.tolist()]
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


# --------------------------------------------------------------------------- #
# evaluate
# --------------------------------------------------------------------------- #

def evaluate(x_in: np.ndarray, x_out: np.ndarray, sample_rate: int, cfg: dict) -> dict:
    """Before/after measurements and the acceptance criteria of docs/00 section 6.3.

    Returns ``{"before": dict, "after": dict, "criteria": {key: {"passed": bool,
    "target": str, "actual": str, "note": str}}, "passed": bool}``.
    """
    before = analyze(x_in, sample_rate)
    after = analyze(x_out, sample_rate)
    chain = cfg.get("chain", {})
    target_lufs = float(chain.get("normalizer", {}).get("target_lufs",
                                                        DEFAULT_CONFIG["chain"]["normalizer"]["target_lufs"]))
    ceiling = float(chain.get("limiter", {}).get("ceiling_dbfs",
                                                 DEFAULT_CONFIG["chain"]["limiter"]["ceiling_dbfs"]))
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

    sib_delta = after["sibilance_ratio_db"] - before["sibilance_ratio_db"]
    deesser_needed = before["sibilance_ratio_db"] > SIBILANCE_REFERENCE_DB
    if deesser_needed:
        sib_pass = sib_delta < 0.0
        note = "De-Esser aktiv, Zischanteil muss sinken."
    else:
        sib_pass = sib_delta <= SIBILANCE_MAX_INCREASE_DB
        note = (f"Zischanteil lag mit {before['sibilance_ratio_db']:.1f} dB unter der Referenz "
                f"{SIBILANCE_REFERENCE_DB:.0f} dB, De-Esser nicht erforderlich; Kriterium: nicht schlechter "
                f"als +{SIBILANCE_MAX_INCREASE_DB:.0f} dB.")
    criteria["sibilance_reduced"] = {
        "passed": bool(sib_pass),
        "target": "Zischanteil ↓" if deesser_needed else f"Zischanteil ≤ +{SIBILANCE_MAX_INCREASE_DB:.0f} dB",
        "actual": f"{sib_delta:+.1f} dB ({before['sibilance_ratio_db']:.1f} → {after['sibilance_ratio_db']:.1f} dB)",
        "note": note,
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

    speech_err = after["speech_level_dbfs"] - REFERENCE_SPEECH_LEVEL_DBFS
    return {
        "before": before,
        "after": after,
        "criteria": criteria,
        "passed": bool(all(c["passed"] for c in criteria.values())),
        "info": {
            "speech_level_after_dbfs": after["speech_level_dbfs"],
            "speech_level_error_db": speech_err,
            "snr_gain_db": after["snr_db"] - before["snr_db"],
        },
    }


# --------------------------------------------------------------------------- #
# Chain processing and I/O
# --------------------------------------------------------------------------- #

def process_with_chain(x: np.ndarray, cfg: dict) -> np.ndarray:
    """Run ``x`` block-wise through ``voicechain.dsp.chain.Chain`` built from ``cfg``."""
    try:
        from voicechain.dsp.chain import Chain  # lazy: keeps calibrate importable without DSP
    except ImportError as exc:
        raise RuntimeError(
            "voicechain.dsp.chain is not available; pass processor=... to run_calibration "
            "or install the DSP package") from exc
    chain = Chain.from_config(cfg)
    block = int(cfg.get("block_size", DEFAULT_CONFIG["block_size"]))
    if block <= 0:
        raise ValueError(f"block_size must be positive, got {block}")
    x32 = np.asarray(x, dtype=np.float32)
    out = np.empty_like(x32)
    for start in range(0, x32.size, block):
        y = chain.process(x32[start:start + block])
        out[start:start + block] = np.asarray(y, dtype=np.float32)[: x32.size - start]
    return out


#: Top-level keys starting with this prefix are calibration metadata, not schema.
METADATA_PREFIX = "_"


def strip_metadata(cfg: dict) -> dict:
    """Deep copy of ``cfg`` without top-level keys that start with ``METADATA_PREFIX``."""
    return {k: copy.deepcopy(v) for k, v in cfg.items() if not str(k).startswith(METADATA_PREFIX)}


def _validate_with_config_module(cfg: dict, context: str) -> None:
    """Run ``voicechain.config.validate_config`` on the schema part of ``cfg`` if the module exists.

    The validator is strict about unknown keys, so the ``_calibration`` metadata
    is removed before validation. Schema errors are re-raised with context.
    """
    try:
        from voicechain.config import validate_config
    except ImportError:
        return
    try:
        validate_config(strip_metadata(cfg))
    except ValueError as exc:
        raise ValueError(f"{context}: {exc}") from exc


def load_base_config(base_cfg_path) -> dict:
    """Load the base YAML (PyYAML), validate it with ``voicechain.config`` if available.

    ``None`` returns a copy of :data:`DEFAULT_CONFIG`. A previously calibrated
    file (with ``_calibration`` metadata) is accepted; its metadata is dropped so
    the new run starts from the plain schema.
    """
    if base_cfg_path is None:
        return copy.deepcopy(DEFAULT_CONFIG)
    path = Path(base_cfg_path)
    if not path.exists():
        raise FileNotFoundError(f"base configuration not found: {path}")
    import yaml
    with open(path, "r", encoding="utf-8") as fh:
        try:
            cfg = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            raise ValueError(f"{path}: YAML syntax error: {exc}") from exc
    if not isinstance(cfg, dict):
        raise ValueError(f"{path} does not contain a YAML mapping")
    cfg = strip_metadata(cfg)
    _validate_with_config_module(cfg, f"base configuration {path}")
    return cfg


def save_config(cfg: dict, out_cfg_path) -> Path:
    """Validate the schema part with ``voicechain.config`` (if present) and write the YAML.

    The file keeps the ``_calibration`` metadata block (measurements and reasons)
    after the schema keys; readers must ignore top-level keys starting with "_".
    """
    path = Path(out_cfg_path)
    _validate_with_config_module(cfg, "calibrated configuration")
    path.parent.mkdir(parents=True, exist_ok=True)
    import yaml
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# Von `python -m voicechain calibrate` erzeugt.\n"
                 "# Schluessel mit fuehrendem Unterstrich (_calibration) sind Metadaten: Messwerte und\n"
                 "# Begruendungen je Parameter. Bedeutung der Werte: docs/02_Parameter_Begruendung.md,\n"
                 "# Ablauf: docs/03_Betrieb_und_Kalibrierung.md.\n")
        yaml.safe_dump(_plain(cfg), fh, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return path


def read_wav_mono(wav_path) -> tuple[np.ndarray, int]:
    """Read a WAV with soundfile and mix it to mono float32."""
    import soundfile as sf
    path = Path(wav_path)
    if not path.exists():
        raise FileNotFoundError(f"WAV file not found: {path}")
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    if data.shape[0] == 0:
        raise ValueError(f"{path} contains no samples")
    mono = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]
    return np.ascontiguousarray(mono, dtype=np.float32), int(sr)


def run_calibration(wav_path, base_cfg_path, out_cfg_path, report_path, processor=None) -> dict:
    """Full calibration run; see module docstring.

    ``processor`` is an optional callable ``x -> y`` (float32 mono) used instead
    of the real chain, mainly for tests. Returns a dict with keys ``measurements``,
    ``cfg``, ``evaluation``, ``passed``, ``out_cfg_path``, ``report_path``,
    ``warnings``.
    """
    x, sr = read_wav_mono(wav_path)
    base_cfg = load_base_config(base_cfg_path)
    cfg_rate = int(base_cfg.get("sample_rate", DEFAULT_CONFIG["sample_rate"]))
    if sr != cfg_rate:
        raise ValueError(
            f"{wav_path} has a sample rate of {sr} Hz but the configuration expects {cfg_rate} Hz "
            f"(Rode NT-USB works at 48 kHz). Record the sample at {cfg_rate} Hz or convert it, "
            f"e.g. `ffmpeg -i in.wav -ar {cfg_rate} out.wav`; the calibration does not resample.")
    warnings: list[str] = []
    duration = x.size / sr
    if not RECOMMENDED_DURATION_S[0] <= duration <= RECOMMENDED_DURATION_S[1]:
        warnings.append(
            f"Sample ist {duration:.1f} s lang; empfohlen sind {RECOMMENDED_DURATION_S[0]:.0f}–"
            f"{RECOMMENDED_DURATION_S[1]:.0f} s mit Sprechpausen.")
    measurements = analyze(x, sr)
    if not measurements["speech_detected"]:
        warnings.append("Keine Sprache erkannt: die Dynamik zwischen leisen und lauten Rahmen ist kleiner als "
                        f"{meters.MIN_SPEECH_SNR_DB:.0f} dB. Alle Sprach-bezogenen Werte sind Schätzungen.")
    cfg = recommend(measurements, base_cfg)
    y = processor(x) if processor is not None else process_with_chain(x, cfg)
    y = np.asarray(y, dtype=np.float32)
    if y.shape != x.shape:
        raise ValueError(f"processor returned shape {y.shape}, expected {x.shape}")
    evaluation = evaluate(x, y, sr, cfg)
    out_path = save_config(cfg, out_cfg_path)
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
    ("speech_activity_ratio", "Sprachanteil", "{:.0%}"),
    ("sibilance_ratio_db", "Zischanteil (5–10 kHz − 0,3–3 kHz)", "{:.1f} dB"),
    ("sibilance_centroid_hz", "Schwerpunkt Zischenergie 4–10 kHz", "{:.0f} Hz"),
    ("spectral_tilt_db_per_oct", "Spektrale Neigung 200 Hz–8 kHz", "{:.1f} dB/Okt"),
    ("integrated_lufs", "Lautheit integriert (gated)", "{:.1f} LUFS"),
    ("short_term_lufs_max", "Lautheit Short-Term maximal", "{:.1f} LUFS"),
)


def _fmt(fmt: str, value) -> str:
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
    old_vals = _flatten_chain(base_cfg.get("chain", {}))
    new_vals = _flatten_chain(cfg.get("chain", {}))
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
        diff = f"{a - b:+.1f}" if isinstance(a, (int, float)) and isinstance(b, (int, float)) else "–"
        lines.append(f"| {label} | {_fmt(fmt, b)} | {_fmt(fmt, a)} | {diff} |")
    info = evaluation.get("info", {})
    lines.append("")
    lines.append(f"Sprachpegel nach Kette: {info.get('speech_level_after_dbfs', float('nan')):.1f} dBFS "
                 f"(Referenz {REFERENCE_SPEECH_LEVEL_DBFS:.0f} dBFS vor Normalizer; der Normalizer hebt auf "
                 f"die Ziel-Lautheit an). Störabstandsgewinn {info.get('snr_gain_db', float('nan')):+.1f} dB.")
    lines.append("")

    lines += [REPORT_SECTIONS[3], "", "| Kriterium | Ziel | Ist | Ergebnis | Hinweis |", "|---|---|---|---|---|"]
    titles = {
        "noise_floor_reduction": "Rauschteppich gesenkt",
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
        f"- Referenz-Sprachpegel {REFERENCE_SPEECH_LEVEL_DBFS:.0f} dBFS RMS nach Trim (Praxiswert, memory/04).",
        f"- Zischanteil-Referenz {SIBILANCE_REFERENCE_DB:.0f} dB und Burst-Crest {SIBILANCE_BURST_CREST_DB:.0f} dB "
        "sind Annahmen aus dem Langzeit-Sprachspektrum, keine Normwerte.",
        f"- EQ-Referenzkurve: flach bis {EQ_REFERENCE_KNEE_HZ:.0f} Hz, dann {EQ_REFERENCE_SLOPE_DB_PER_OCT:+.0f} dB/Okt "
        f"(Annahme), nur {EQ_CORRECTION_FRACTION:.0%} der Abweichung werden korrigiert, maximal ±{EQ_MAX_GAIN_DB:.0f} dB.",
        "- Die Messung 'nachher' läuft über dieselbe Datei; die Lautheitsregelung braucht einige Sekunden, "
        "bis sie das Ziel erreicht, was in den integrierten Wert eingeht.",
        "- Rauschteppich-Kriterium (Beauftragung §6.3) wird bei gleichem Sprachpegel bewertet, also als Gewinn an "
        "Störabstand: Der Normalizer hebt leise Aufnahmen insgesamt an, ein absoluter Vergleich würde das als "
        "Verschlechterung werten. Die absoluten Werte stehen daneben. Bei kleinem Störabstand "
        f"(< {EXPANDER_FLOOR_MARGIN_DB + EXPANDER_SPEECH_MARGIN_DB:.0f} dB) kann der Expander die 6 dB nicht erreichen, "
        "ohne Sprache abzuschneiden; dann hilft nur eine leisere Umgebung oder geringerer Mikrofonabstand.",
        "- Kompressor-Schwelle liegt auf Höhe des Sprach-RMS, weil der Kompressor der Kette Spitzen misst "
        f"(Silbenspitzen ≈ {evaluation['before'].get('speech_crest_db', float('nan')):.0f} dB über RMS); "
        "eine tiefere Schwelle verdichtet stärker, hebt aber mit Makeup/Normalizer auch das Rauschen wieder an.",
        "",
    ]
    return "\n".join(lines)
