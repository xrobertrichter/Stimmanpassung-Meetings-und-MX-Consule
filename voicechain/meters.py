"""Audio measurement functions for voicechain (ITU-R BS.1770 loudness, true peak,
noise floor, speech level, sibilance, spectral tilt, band energies).

All functions take a mono float signal with full scale = 1.0 and return values
in dBFS / LUFS / dBTP. The module depends only on numpy and scipy so that it can
be imported without the DSP chain or audio devices.

References:
    E2  ITU-R BS.1770: K-weighting (two biquads: high shelf ~+4 dB above ~1.5 kHz,
        high-pass ~38 Hz), loudness L = -0.691 + 10*log10(mean square),
        momentary 400 ms, short-term 3 s, integrated with absolute gate -70 LUFS
        and relative gate -10 LU, true peak via 4x oversampling.
"""
from __future__ import annotations

import math

import numpy as np
from scipy import signal

# --------------------------------------------------------------------------- #
# Numerical constants (each one justified here; see also docs/03)
# --------------------------------------------------------------------------- #

#: Smallest mean square that is converted to dB. 1e-12 corresponds to -120 dB,
#: which is below 24-bit quantisation noise (~-144 dBFS peak, but a real
#: measurement chain never reaches it); it keeps log10 finite for digital silence.
POWER_FLOOR = 1e-12
DB_FLOOR = 10.0 * math.log10(POWER_FLOOR)  # -120.0 dB

#: BS.1770 loudness offset so that a 0 dBFS 997 Hz sine reads -3.01 LKFS (E2).
LUFS_OFFSET = -0.691

#: BS.1770 gating window: 400 ms blocks with 75 % overlap (hop 100 ms) (E2).
GATING_BLOCK_S = 0.400
GATING_HOP_S = 0.100
MOMENTARY_WINDOW_S = 0.400
SHORT_TERM_WINDOW_S = 3.000
ABSOLUTE_GATE_LUFS = -70.0
RELATIVE_GATE_LU = -10.0

#: BS.1770 Annex 2: 4x oversampling is sufficient for 48 kHz true-peak metering.
TRUE_PEAK_OVERSAMPLING = 4

#: Default analysis frame for frame-RMS based statistics (noise floor, speech
#: activity). 50 ms resolves single syllables (typical 100-250 ms) without being
#: dominated by pitch periods (>= 8 ms). The noise-floor default of 100 ms follows
#: the interface contract (section 4.2).
ANALYSIS_FRAME_MS = 50.0

#: Percentile of frame RMS that is taken as the noise floor. The 10th percentile
#: lies inside the speech pauses as long as at least ~10 % of the recording is
#: pause, which the recording instructions in docs/03 guarantee (10 s silence at
#: the start of a 60 s sample). Assumption, not a norm value.
NOISE_FLOOR_PERCENTILE = 10.0

#: Percentile that represents "loud speech frames" for the adaptive activity
#: threshold; 95 % excludes isolated clicks/plosives.
SPEECH_TOP_PERCENTILE = 95.0

#: Speech activity threshold lies at this fraction of the span between the noise
#: floor and the loud-speech percentile (0.5 = midpoint, in dB). Assumption:
#: the midpoint is robust for signal-to-noise ratios from 10 dB to 60 dB.
ACTIVITY_THRESHOLD_FRACTION = 0.5

#: If the span between the noise floor and the loud frames is smaller than this,
#: the recording contains no detectable speech (pure noise or pure tone).
#: Assumption: 6 dB is well above the frame-to-frame fluctuation of stationary
#: noise (about +-1.5 dB for 50 ms frames of white noise).
MIN_SPEECH_SNR_DB = 6.0

#: Band limits of the sibilance measure (interface contract section 4.2):
#: sibilant energy 5-10 kHz against the speech core band 0.3-3 kHz.
SIBILANCE_BAND_HZ = (5000.0, 10000.0)
SPEECH_CORE_BAND_HZ = (300.0, 3000.0)

#: Search range for the sibilance centroid used to place the de-esser.
SIBILANCE_SEARCH_BAND_HZ = (4000.0, 10000.0)

#: Default frequency range for the spectral tilt regression. 200 Hz..8 kHz
#: covers the speech band without the high-pass region or the microphone's
#: top-octave roll-off. Assumption.
TILT_RANGE_HZ = (200.0, 8000.0)

#: Spectral tilt regression uses third-octave bands so that every octave has the
#: same weight (an FFT has far more bins per octave at high frequencies).
TILT_BANDS_PER_OCTAVE = 3

#: Upper usable frequency as a fraction of the sample rate; keeps the band
#: analyses away from the anti-alias transition region near Nyquist.
MAX_USABLE_FREQ_FRACTION = 0.45


# --------------------------------------------------------------------------- #
# K-weighting filter design (BS.1770)
# --------------------------------------------------------------------------- #

# Exact coefficients from the BS.1770 tables for 48 kHz.
_K_SHELF_48K = ([1.53512485958697, -2.69169618940638, 1.19839281085285],
                [1.0, -1.69065929318241, 0.73248077421585])
_K_HIGHPASS_48K = ([1.0, -2.0, 1.0],
                   [1.0, -1.99004745483398, 0.99007225036621])

# Analogue prototype parameters of the two K-weighting stages. They are the
# values that reproduce the 48 kHz table coefficients to better than 1e-11 via
# the bilinear transform below (verified in tests/test_meters.py), so the same
# design can be used for other sample rates.
_K_SHELF_F0_HZ = 1681.9744509555319
_K_SHELF_GAIN_DB = 3.99984385397
_K_SHELF_Q = 0.7071752369554193
_K_SHELF_MID_EXPONENT = 0.499666774155   # shelf midpoint gain exponent (Vb = Vh**p)
_K_HIGHPASS_F0_HZ = 38.13547087613982
_K_HIGHPASS_Q = 0.5003270373253953


def k_weighting_sos(sample_rate: int) -> np.ndarray:
    """Return the BS.1770 K-weighting filter as second-order sections.

    At 48 kHz the exact table coefficients are used; at other rates the stages
    are re-designed by bilinear transform of the analogue prototypes.
    """
    if sample_rate <= 0:
        raise ValueError(f"sample_rate must be positive, got {sample_rate}")
    if int(sample_rate) == 48000:
        b1, a1 = _K_SHELF_48K
        b2, a2 = _K_HIGHPASS_48K
        return np.array([b1 + a1, b2 + a2], dtype=np.float64)
    return design_k_weighting_sos(sample_rate)


def design_k_weighting_sos(sample_rate: float) -> np.ndarray:
    """Design the K-weighting stages for any sample rate by bilinear transform.

    Reproduces the BS.1770 48 kHz table coefficients to ~1e-12 (tested), so it is
    the reference implementation; :func:`k_weighting_sos` only short-cuts 48 kHz.
    """
    if sample_rate <= 0:
        raise ValueError(f"sample_rate must be positive, got {sample_rate}")
    # Stage 1: high shelf
    k = math.tan(math.pi * _K_SHELF_F0_HZ / sample_rate)
    vh = 10.0 ** (_K_SHELF_GAIN_DB / 20.0)
    vb = vh ** _K_SHELF_MID_EXPONENT
    q = _K_SHELF_Q
    den = 1.0 + k / q + k * k
    b1 = [(vh + vb * k / q + k * k) / den,
          2.0 * (k * k - vh) / den,
          (vh - vb * k / q + k * k) / den]
    a1 = [1.0, 2.0 * (k * k - 1.0) / den, (1.0 - k / q + k * k) / den]
    # Stage 2: high-pass (numerator fixed to [1, -2, 1] as in the standard)
    k = math.tan(math.pi * _K_HIGHPASS_F0_HZ / sample_rate)
    q = _K_HIGHPASS_Q
    den = 1.0 + k / q + k * k
    b2 = [1.0, -2.0, 1.0]
    a2 = [1.0, 2.0 * (k * k - 1.0) / den, (1.0 - k / q + k * k) / den]
    return np.array([b1 + a1, b2 + a2], dtype=np.float64)


def _power_to_db(power: float | np.ndarray) -> float | np.ndarray:
    """10*log10 with a floor at POWER_FLOOR (-120 dB)."""
    return 10.0 * np.log10(np.maximum(power, POWER_FLOOR))


def _as_mono_float(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 2:
        # (n, channels) -> mono by averaging; keeps the function usable on
        # soundfile output without an extra conversion step.
        x = x.mean(axis=1)
    if x.ndim != 1:
        raise ValueError(f"expected a mono signal with shape (n,), got {x.shape}")
    return x


# --------------------------------------------------------------------------- #
# Loudness meter
# --------------------------------------------------------------------------- #

class LoudnessMeter:
    """ITU-R BS.1770 loudness meter for one mono channel.

    Call :meth:`push` with consecutive blocks of arbitrary size; the K-weighting
    filter state is carried across blocks, so block size does not change the
    result. Momentary (400 ms) and short-term (3 s) values are computed over the
    most recent samples; integrated loudness applies absolute (-70 LUFS) and
    relative (-10 LU) gating over the whole history in 400 ms blocks with 100 ms hop.
    """

    def __init__(self, sample_rate: int):
        if sample_rate <= 0:
            raise ValueError(f"sample_rate must be positive, got {sample_rate}")
        self.sample_rate = int(sample_rate)
        self._sos = k_weighting_sos(self.sample_rate)
        self._zi = np.zeros((self._sos.shape[0], 2))
        # Ring buffer of squared K-weighted samples covering the short-term window.
        self._ring_len = int(round(SHORT_TERM_WINDOW_S * self.sample_rate))
        self._momentary_len = int(round(MOMENTARY_WINDOW_S * self.sample_rate))
        self._ring = np.zeros(self._ring_len)
        self._write_pos = 0
        self._total_samples = 0
        # Integrated: mean square of every 100 ms hop, aggregated lazily.
        self._hop_len = int(round(GATING_HOP_S * self.sample_rate))
        self._hop_acc = 0.0
        self._hop_count = 0
        self._hop_means: list[float] = []

    def reset(self) -> None:
        self._zi[:] = 0.0
        self._ring[:] = 0.0
        self._write_pos = 0
        self._total_samples = 0
        self._hop_acc = 0.0
        self._hop_count = 0
        self._hop_means = []

    def push(self, x: np.ndarray) -> None:
        x = _as_mono_float(x)
        if x.size == 0:
            return
        y, self._zi = signal.sosfilt(self._sos, x, zi=self._zi)
        sq = y * y
        self._write_ring(sq)
        self._accumulate_hops(sq)
        self._total_samples += sq.size

    def _write_ring(self, sq: np.ndarray) -> None:
        n = sq.size
        if n >= self._ring_len:
            self._ring[:] = sq[-self._ring_len:]
            self._write_pos = 0
            return
        end = self._write_pos + n
        if end <= self._ring_len:
            self._ring[self._write_pos:end] = sq
        else:
            first = self._ring_len - self._write_pos
            self._ring[self._write_pos:] = sq[:first]
            self._ring[:n - first] = sq[first:]
        self._write_pos = end % self._ring_len

    def _accumulate_hops(self, sq: np.ndarray) -> None:
        pos = 0
        n = sq.size
        while pos < n:
            take = min(self._hop_len - self._hop_count, n - pos)
            self._hop_acc += float(np.sum(sq[pos:pos + take]))
            self._hop_count += take
            pos += take
            if self._hop_count == self._hop_len:
                self._hop_means.append(self._hop_acc / self._hop_len)
                self._hop_acc = 0.0
                self._hop_count = 0

    def _recent_mean_square(self, length: int) -> float:
        avail = min(length, self._total_samples, self._ring_len)
        if avail <= 0:
            return 0.0
        start = (self._write_pos - avail) % self._ring_len
        if start + avail <= self._ring_len:
            return float(np.mean(self._ring[start:start + avail]))
        tail = self._ring[start:]
        head = self._ring[:avail - tail.size]
        return (float(np.sum(tail)) + float(np.sum(head))) / avail

    @staticmethod
    def _lufs(mean_square: float) -> float:
        return float(LUFS_OFFSET + _power_to_db(mean_square))

    def momentary_lufs(self) -> float:
        """Loudness of the last 400 ms (less if fewer samples were pushed)."""
        return self._lufs(self._recent_mean_square(self._momentary_len))

    def short_term_lufs(self) -> float:
        """Loudness of the last 3 s (less if fewer samples were pushed)."""
        return self._lufs(self._recent_mean_square(self._ring_len))

    def _block_mean_squares(self) -> np.ndarray:
        hops = np.asarray(self._hop_means, dtype=np.float64)
        blocks_per_window = int(round(GATING_BLOCK_S / GATING_HOP_S))  # 4
        if hops.size < blocks_per_window:
            if hops.size == 0:
                return np.zeros(0)
            return np.array([hops.mean()])
        csum = np.concatenate(([0.0], np.cumsum(hops)))
        return (csum[blocks_per_window:] - csum[:-blocks_per_window]) / blocks_per_window

    def integrated_lufs(self) -> float:
        """Gated integrated loudness over everything pushed so far."""
        blocks = self._block_mean_squares()
        if blocks.size == 0:
            return float(LUFS_OFFSET + DB_FLOOR)
        block_lufs = LUFS_OFFSET + _power_to_db(blocks)
        above_abs = block_lufs > ABSOLUTE_GATE_LUFS
        if not np.any(above_abs):
            return float(LUFS_OFFSET + DB_FLOOR)
        relative_gate = self._lufs(float(np.mean(blocks[above_abs]))) + RELATIVE_GATE_LU
        gated = above_abs & (block_lufs > relative_gate)
        if not np.any(gated):
            gated = above_abs
        return self._lufs(float(np.mean(blocks[gated])))


def integrated_lufs(x: np.ndarray, sample_rate: int) -> float:
    """Convenience wrapper: gated integrated loudness of a whole signal."""
    meter = LoudnessMeter(sample_rate)
    meter.push(x)
    return meter.integrated_lufs()


def short_term_lufs_series(x: np.ndarray, sample_rate: int,
                           hop_s: float = GATING_HOP_S) -> np.ndarray:
    """Short-term loudness (3 s window) sampled every ``hop_s`` seconds."""
    x = _as_mono_float(x)
    sos = k_weighting_sos(sample_rate)
    sq = signal.sosfilt(sos, x) ** 2
    win = int(round(SHORT_TERM_WINDOW_S * sample_rate))
    hop = max(1, int(round(hop_s * sample_rate)))
    if sq.size < win:
        return np.array([LUFS_OFFSET + float(_power_to_db(sq.mean()))]) if sq.size else np.zeros(0)
    csum = np.concatenate(([0.0], np.cumsum(sq)))
    ends = np.arange(win, sq.size + 1, hop)
    means = (csum[ends] - csum[ends - win]) / win
    return LUFS_OFFSET + _power_to_db(means)


# --------------------------------------------------------------------------- #
# Peak / RMS
# --------------------------------------------------------------------------- #

def true_peak_dbfs(x: np.ndarray, sample_rate: int) -> float:
    """True peak in dBTP using 4x polyphase oversampling (BS.1770 Annex 2)."""
    x = _as_mono_float(x)
    if x.size == 0:
        return DB_FLOOR
    up = signal.resample_poly(x, TRUE_PEAK_OVERSAMPLING, 1)
    peak = float(np.max(np.abs(up)))
    # Oversampling can slightly undershoot the true inter-sample peak; the
    # sample peak is a hard lower bound, so never report less than it.
    peak = max(peak, float(np.max(np.abs(x))))
    return float(20.0 * math.log10(max(peak, math.sqrt(POWER_FLOOR))))


def sample_peak_dbfs(x: np.ndarray) -> float:
    x = _as_mono_float(x)
    if x.size == 0:
        return DB_FLOOR
    return float(20.0 * math.log10(max(float(np.max(np.abs(x))), math.sqrt(POWER_FLOOR))))


def rms_dbfs(x: np.ndarray) -> float:
    """RMS level of the whole signal in dBFS (floor -120 dBFS)."""
    x = _as_mono_float(x)
    if x.size == 0:
        return DB_FLOOR
    return float(_power_to_db(float(np.mean(x * x))))


# --------------------------------------------------------------------------- #
# Frame statistics, speech activity
# --------------------------------------------------------------------------- #

def _frame_length(sample_rate: int, frame_ms: float) -> int:
    n = int(round(sample_rate * frame_ms / 1000.0))
    if n < 1:
        raise ValueError(f"frame of {frame_ms} ms is shorter than one sample at {sample_rate} Hz")
    return n


def frame_rms_db(x: np.ndarray, sample_rate: int,
                 frame_ms: float = ANALYSIS_FRAME_MS) -> np.ndarray:
    """RMS level in dBFS of consecutive non-overlapping frames."""
    x = _as_mono_float(x)
    n = _frame_length(sample_rate, frame_ms)
    n_frames = x.size // n
    if n_frames == 0:
        raise ValueError(
            f"signal of {x.size} samples is shorter than one {frame_ms} ms frame "
            f"({n} samples at {sample_rate} Hz)")
    frames = x[:n_frames * n].reshape(n_frames, n)
    return _power_to_db(np.mean(frames * frames, axis=1))


def activity_threshold_db(levels_db: np.ndarray) -> tuple[float, bool]:
    """Adaptive speech-activity threshold from a set of frame levels.

    Returns ``(threshold_db, speech_detected)``. The threshold lies at
    ``ACTIVITY_THRESHOLD_FRACTION`` of the span between the noise-floor
    percentile and the loud-frame percentile. If that span is below
    ``MIN_SPEECH_SNR_DB`` no speech is assumed (``speech_detected`` False) and
    the threshold is set above every frame.
    """
    levels_db = np.asarray(levels_db, dtype=np.float64)
    floor = float(np.percentile(levels_db, NOISE_FLOOR_PERCENTILE))
    top = float(np.percentile(levels_db, SPEECH_TOP_PERCENTILE))
    if top - floor < MIN_SPEECH_SNR_DB:
        return float(np.max(levels_db)) + 1.0, False
    return floor + ACTIVITY_THRESHOLD_FRACTION * (top - floor), True


def speech_activity(x: np.ndarray, sample_rate: int,
                    frame_ms: float = ANALYSIS_FRAME_MS) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(active_mask, frame_levels_db)`` for consecutive frames."""
    levels = frame_rms_db(x, sample_rate, frame_ms)
    thr, detected = activity_threshold_db(levels)
    if not detected:
        return np.zeros(levels.shape, dtype=bool), levels
    return levels > thr, levels


def noise_floor_dbfs(x: np.ndarray, sample_rate: int, frame_ms: float = 100.0) -> float:
    """Noise floor: ``NOISE_FLOOR_PERCENTILE`` of the frame RMS levels."""
    levels = frame_rms_db(x, sample_rate, frame_ms)
    return float(np.percentile(levels, NOISE_FLOOR_PERCENTILE))


def speech_level_dbfs(x: np.ndarray, sample_rate: int) -> float:
    """Energy-mean RMS level (dBFS) of the frames classified as active speech.

    If no speech is detected (see :func:`activity_threshold_db`) the level of
    the loud-frame percentile is returned as the best available estimate.
    """
    mask, levels = speech_activity(x, sample_rate)
    if not np.any(mask):
        return float(np.percentile(levels, SPEECH_TOP_PERCENTILE))
    return float(_power_to_db(np.mean(10.0 ** (levels[mask] / 10.0))))


def speech_activity_ratio(x: np.ndarray, sample_rate: int) -> float:
    """Fraction of frames classified as active speech (0..1)."""
    mask, _ = speech_activity(x, sample_rate)
    return float(np.mean(mask)) if mask.size else 0.0


# --------------------------------------------------------------------------- #
# Spectral measures (average power spectrum of the active frames)
# --------------------------------------------------------------------------- #

def frame_power_spectrum(frames: np.ndarray, sample_rate: int) -> tuple[np.ndarray, np.ndarray]:
    """Per-frame one-sided power spectrum, scaled so that the bin sum equals
    the mean square of the (windowed) frame. Returns ``(freqs, power[frame, bin])``."""
    n = frames.shape[1]
    win = signal.windows.hann(n, sym=False)
    spec = np.fft.rfft(frames * win, axis=1)
    power = (np.abs(spec) ** 2)
    # One-sided spectrum: double every bin except DC and (even n) Nyquist.
    power[:, 1:] *= 2.0
    if n % 2 == 0:
        power[:, -1] /= 2.0
    power /= (n * np.sum(win * win))
    freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)
    return freqs, power


def active_power_spectrum(x: np.ndarray, sample_rate: int,
                          frame_ms: float = ANALYSIS_FRAME_MS) -> tuple[np.ndarray, np.ndarray]:
    """Average power spectrum over the active speech frames.

    Falls back to all frames when no speech is detected, so that the spectral
    functions still describe the recording (e.g. for a noise-only sample).
    """
    x = _as_mono_float(x)
    n = _frame_length(sample_rate, frame_ms)
    mask, _ = speech_activity(x, sample_rate, frame_ms)
    n_frames = mask.size
    frames = x[:n_frames * n].reshape(n_frames, n)
    if np.any(mask):
        frames = frames[mask]
    freqs, power = frame_power_spectrum(frames, sample_rate)
    return freqs, power.mean(axis=0)


def _band_power(freqs: np.ndarray, power: np.ndarray, lo: float, hi: float) -> float:
    sel = (freqs >= lo) & (freqs < hi)
    if not np.any(sel):
        return 0.0
    return float(np.sum(power[sel]))


def _clamp_band(sample_rate: int, lo: float, hi: float) -> tuple[float, float]:
    f_max = MAX_USABLE_FREQ_FRACTION * sample_rate
    if lo >= f_max:
        raise ValueError(f"band {lo}-{hi} Hz lies above the usable range at {sample_rate} Hz")
    return lo, min(hi, f_max)


def sibilance_ratio_db(x: np.ndarray, sample_rate: int) -> float:
    """Energy 5-10 kHz minus energy 0.3-3 kHz (dB), over active frames only."""
    freqs, power = active_power_spectrum(x, sample_rate)
    lo_s, hi_s = _clamp_band(sample_rate, *SIBILANCE_BAND_HZ)
    lo_c, hi_c = _clamp_band(sample_rate, *SPEECH_CORE_BAND_HZ)
    sib = _band_power(freqs, power, lo_s, hi_s)
    core = _band_power(freqs, power, lo_c, hi_c)
    return float(_power_to_db(sib) - _power_to_db(core))


def sibilance_centroid_hz(x: np.ndarray, sample_rate: int) -> float:
    """Power-weighted centroid of the sibilance search band (4-10 kHz)."""
    freqs, power = active_power_spectrum(x, sample_rate)
    lo, hi = _clamp_band(sample_rate, *SIBILANCE_SEARCH_BAND_HZ)
    sel = (freqs >= lo) & (freqs < hi)
    total = float(np.sum(power[sel]))
    if total <= POWER_FLOOR:
        return float(math.sqrt(lo * hi))  # geometric band centre when there is no energy
    return float(np.sum(freqs[sel] * power[sel]) / total)


def third_octave_density_db(freqs: np.ndarray, power: np.ndarray,
                            f_lo: float, f_hi: float,
                            bands_per_octave: int = TILT_BANDS_PER_OCTAVE
                            ) -> tuple[np.ndarray, np.ndarray]:
    """Mean power spectral density (dB) in fractional-octave bands between f_lo and f_hi.

    Returns ``(band_centres_hz, density_db)``; bands without bins are skipped.
    """
    df = float(freqs[1] - freqs[0]) if freqs.size > 1 else 1.0
    n_bands = int(math.floor(math.log2(f_hi / f_lo) * bands_per_octave))
    centres = []
    values = []
    for i in range(n_bands):
        lo = f_lo * 2.0 ** (i / bands_per_octave)
        hi = f_lo * 2.0 ** ((i + 1) / bands_per_octave)
        sel = (freqs >= lo) & (freqs < hi)
        if not np.any(sel):
            continue
        centres.append(math.sqrt(lo * hi))
        values.append(float(_power_to_db(np.mean(power[sel]) / df)))
    return np.asarray(centres), np.asarray(values)


def spectral_tilt_db_per_oct(x: np.ndarray, sample_rate: int,
                             f_lo: float = TILT_RANGE_HZ[0],
                             f_hi: float = TILT_RANGE_HZ[1]) -> float:
    """Slope (dB per octave) of a straight line fitted to the third-octave
    power spectral density of the active frames between ``f_lo`` and ``f_hi``."""
    freqs, power = active_power_spectrum(x, sample_rate)
    f_lo, f_hi = _clamp_band(sample_rate, f_lo, f_hi)
    centres, density = third_octave_density_db(freqs, power, f_lo, f_hi)
    if centres.size < 2:
        raise ValueError(
            f"need at least two third-octave bands between {f_lo} and {f_hi} Hz for a tilt fit")
    slope, _ = np.polyfit(np.log2(centres), density, 1)
    return float(slope)


def band_energies_db(x: np.ndarray, sample_rate: int, edges_hz: list[float]) -> list[float]:
    """Energy (dBFS) of the active frames in each band between consecutive edges."""
    if len(edges_hz) < 2:
        raise ValueError("edges_hz needs at least two values")
    if any(b <= a for a, b in zip(edges_hz[:-1], edges_hz[1:])):
        raise ValueError(f"edges_hz must be strictly increasing, got {edges_hz}")
    freqs, power = active_power_spectrum(x, sample_rate)
    out = []
    for lo, hi in zip(edges_hz[:-1], edges_hz[1:]):
        out.append(float(_power_to_db(_band_power(freqs, power, lo, hi))))
    return out
