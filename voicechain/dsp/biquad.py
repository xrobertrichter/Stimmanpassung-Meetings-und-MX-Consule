"""Biquad coefficient design (RBJ Audio EQ Cookbook) and stateful SOS filtering.

All design functions return second-order sections in the scipy ``sos`` layout
``[b0, b1, b2, a0, a1, a2]`` with ``a0`` normalised to 1, so the sections can
be stacked and run through :func:`scipy.signal.sosfilt` with a carried state.

References:
    * R. Bristow-Johnson, "Cookbook formulae for audio EQ biquad filter
      coefficients" (RBJ cookbook).
    * ITU-R BS.1770-4, Annex 1 (K-weighting pre-filter and RLB filter).
"""

from __future__ import annotations

import math

import numpy as np
from scipy import signal


def _check_frequency(freq_hz: float, sample_rate: int) -> None:
    """Raise ValueError when the centre/corner frequency is not inside (0, fs/2)."""
    nyquist = 0.5 * sample_rate
    if not (0.0 < freq_hz < nyquist):
        raise ValueError(
            f"freq_hz={freq_hz!r} must lie strictly between 0 and the Nyquist "
            f"frequency {nyquist:.1f} Hz (sample_rate={sample_rate})"
        )


def _normalise(b0: float, b1: float, b2: float, a0: float, a1: float, a2: float) -> np.ndarray:
    """Return one SOS row with a0 normalised to 1."""
    return np.array([[b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0]], dtype=np.float64)


def bandwidth_to_q(bandwidth_oct: float, freq_hz: float, sample_rate: int) -> float:
    """Convert a bandwidth in octaves into the RBJ ``Q`` (with the w0/sin(w0) warp term).

    RBJ cookbook: ``1/Q = 2*sinh(ln(2)/2 * BW * w0/sin(w0))``.
    """
    if bandwidth_oct <= 0.0:
        raise ValueError(f"bandwidth_oct={bandwidth_oct!r} must be > 0")
    w0 = 2.0 * math.pi * freq_hz / sample_rate
    inv_q = 2.0 * math.sinh(math.log(2.0) / 2.0 * bandwidth_oct * w0 / math.sin(w0))
    return 1.0 / inv_q


def _rbj_common(freq_hz: float, q: float, sample_rate: int) -> tuple[float, float, float]:
    """Return (cos(w0), alpha, w0) shared by all RBJ designs."""
    _check_frequency(freq_hz, sample_rate)
    if q <= 0.0:
        raise ValueError(f"q={q!r} must be > 0")
    w0 = 2.0 * math.pi * freq_hz / sample_rate
    alpha = math.sin(w0) / (2.0 * q)
    return math.cos(w0), alpha, w0


def peak_sos(freq_hz: float, gain_db: float, q: float, sample_rate: int) -> np.ndarray:
    """RBJ peaking EQ: ``gain_db`` at ``freq_hz``, unity far away from it."""
    cos_w0, alpha, _ = _rbj_common(freq_hz, q, sample_rate)
    a_lin = 10.0 ** (gain_db / 40.0)
    return _normalise(
        1.0 + alpha * a_lin, -2.0 * cos_w0, 1.0 - alpha * a_lin,
        1.0 + alpha / a_lin, -2.0 * cos_w0, 1.0 - alpha / a_lin,
    )


def low_shelf_sos(freq_hz: float, gain_db: float, q: float, sample_rate: int) -> np.ndarray:
    """RBJ low shelf: ``gain_db`` below ``freq_hz``, half the gain (in dB) at ``freq_hz``."""
    cos_w0, alpha, _ = _rbj_common(freq_hz, q, sample_rate)
    a_lin = 10.0 ** (gain_db / 40.0)
    sqrt_term = 2.0 * math.sqrt(a_lin) * alpha
    return _normalise(
        a_lin * ((a_lin + 1.0) - (a_lin - 1.0) * cos_w0 + sqrt_term),
        2.0 * a_lin * ((a_lin - 1.0) - (a_lin + 1.0) * cos_w0),
        a_lin * ((a_lin + 1.0) - (a_lin - 1.0) * cos_w0 - sqrt_term),
        (a_lin + 1.0) + (a_lin - 1.0) * cos_w0 + sqrt_term,
        -2.0 * ((a_lin - 1.0) + (a_lin + 1.0) * cos_w0),
        (a_lin + 1.0) + (a_lin - 1.0) * cos_w0 - sqrt_term,
    )


def high_shelf_sos(freq_hz: float, gain_db: float, q: float, sample_rate: int) -> np.ndarray:
    """RBJ high shelf: ``gain_db`` above ``freq_hz``, half the gain (in dB) at ``freq_hz``."""
    cos_w0, alpha, _ = _rbj_common(freq_hz, q, sample_rate)
    a_lin = 10.0 ** (gain_db / 40.0)
    sqrt_term = 2.0 * math.sqrt(a_lin) * alpha
    return _normalise(
        a_lin * ((a_lin + 1.0) + (a_lin - 1.0) * cos_w0 + sqrt_term),
        -2.0 * a_lin * ((a_lin - 1.0) + (a_lin + 1.0) * cos_w0),
        a_lin * ((a_lin + 1.0) + (a_lin - 1.0) * cos_w0 - sqrt_term),
        (a_lin + 1.0) - (a_lin - 1.0) * cos_w0 + sqrt_term,
        2.0 * ((a_lin - 1.0) - (a_lin + 1.0) * cos_w0),
        (a_lin + 1.0) - (a_lin - 1.0) * cos_w0 - sqrt_term,
    )


def bandpass_sos(freq_hz: float, q: float, sample_rate: int) -> np.ndarray:
    """RBJ band-pass with constant 0 dB peak gain (``H(f0) = 1`` exactly, zero phase at f0)."""
    cos_w0, alpha, _ = _rbj_common(freq_hz, q, sample_rate)
    return _normalise(alpha, 0.0, -alpha, 1.0 + alpha, -2.0 * cos_w0, 1.0 - alpha)


def butter_highpass_sos(freq_hz: float, order: int, sample_rate: int) -> np.ndarray:
    """Butterworth high-pass (-3 dB at ``freq_hz``) as an SOS cascade."""
    _check_frequency(freq_hz, sample_rate)
    if int(order) != order or order < 1:
        raise ValueError(f"order={order!r} must be a positive integer")
    return signal.butter(int(order), freq_hz, btype="highpass", fs=sample_rate, output="sos")


# ITU-R BS.1770-4 K-weighting expressed parametrically (bilinear transform of the
# analogue prototype) so it can be designed for any sample rate. The constants are
# the ones used by libebur128 (ebur128_init_filter) and reproduce the tabulated
# 48 kHz coefficients of the standard to better than 1e-9 (tests/test_dsp_biquad.py).
K_PREFILTER_FREQ_HZ = 1681.974450955533
K_PREFILTER_GAIN_DB = 3.999843853973347
K_PREFILTER_Q = 0.7071752369554196
K_PREFILTER_VB_EXPONENT = 0.4996667741545416   # exponent of the shelf's mid-band gain Vb = Vh**e
K_RLB_FREQ_HZ = 38.13547087602444
K_RLB_Q = 0.5003270373238773


def k_weighting_sos(sample_rate: int) -> np.ndarray:
    """K-weighting (BS.1770) as two SOS rows: high-shelf pre-filter + RLB high-pass.

    Both stages are bilinear transforms with ``K = tan(pi*f0/fs)``. The RLB stage
    keeps the numerator ``[1, -2, 1]`` exactly as the standard tabulates it.
    """
    if sample_rate <= 0:
        raise ValueError(f"sample_rate={sample_rate!r} must be positive")
    k = math.tan(math.pi * K_PREFILTER_FREQ_HZ / sample_rate)
    vh = 10.0 ** (K_PREFILTER_GAIN_DB / 20.0)
    vb = vh ** K_PREFILTER_VB_EXPONENT
    a0 = 1.0 + k / K_PREFILTER_Q + k * k
    pre = np.array([[
        (vh + vb * k / K_PREFILTER_Q + k * k) / a0,
        2.0 * (k * k - vh) / a0,
        (vh - vb * k / K_PREFILTER_Q + k * k) / a0,
        1.0,
        2.0 * (k * k - 1.0) / a0,
        (1.0 - k / K_PREFILTER_Q + k * k) / a0,
    ]])
    k = math.tan(math.pi * K_RLB_FREQ_HZ / sample_rate)
    a0 = 1.0 + k / K_RLB_Q + k * k
    rlb = np.array([[1.0, -2.0, 1.0, 1.0, 2.0 * (k * k - 1.0) / a0, (1.0 - k / K_RLB_Q + k * k) / a0]])
    return np.vstack([pre, rlb])


def sos_response_db(sos: np.ndarray, freqs_hz: np.ndarray | float, sample_rate: int) -> np.ndarray:
    """Magnitude response in dB of an SOS cascade at the given frequencies (analysis helper)."""
    freqs = np.atleast_1d(np.asarray(freqs_hz, dtype=np.float64))
    _, h = signal.sosfreqz(sos, worN=freqs, fs=sample_rate)
    return 20.0 * np.log10(np.maximum(np.abs(h), 1e-12))  # 1e-12 only guards log10(0) at exact zeros


class SosFilter:
    """Stateful SOS cascade: ``process`` carries ``zi`` across blocks of any size.

    ``set_sos`` swaps coefficients at run time. When the number of sections is
    unchanged and ``fade_samples > 0``, the old filter keeps running on its own
    state for ``fade_samples`` more samples while the new filter starts from a
    copy of that state, and the output is cross-faded linearly from old to new.
    This removes the transient that a bare coefficient swap leaves in a
    transposed direct-form II state (audible as a tick for large jumps).
    """

    def __init__(self, sos: np.ndarray, fade_samples: int = 0):
        self._sos = np.ascontiguousarray(sos, dtype=np.float64)
        self._zi = np.zeros((self._sos.shape[0], 2), dtype=np.float64)
        self._fade = max(0, int(fade_samples))
        self._old_sos: np.ndarray | None = None
        self._old_zi: np.ndarray | None = None
        self._fade_done = 0

    @property
    def sos(self) -> np.ndarray:
        return self._sos

    def set_sos(self, sos: np.ndarray) -> None:
        sos = np.ascontiguousarray(sos, dtype=np.float64)
        if sos.shape[0] != self._sos.shape[0]:
            self._zi = np.zeros((sos.shape[0], 2), dtype=np.float64)
            self._old_sos = None
        elif self._fade > 0 and not np.array_equal(sos, self._sos):
            if self._old_sos is None:
                self._old_sos = self._sos
                self._old_zi = self._zi.copy()
            # a change during a running fade keeps the old filter and restarts the ramp
            self._fade_done = 0
        self._sos = sos

    def process(self, x: np.ndarray) -> np.ndarray:
        if x.size == 0:
            return np.zeros(0, dtype=np.float64)
        y, self._zi = signal.sosfilt(self._sos, x, zi=self._zi)
        if self._old_sos is None:
            return y
        y_old, self._old_zi = signal.sosfilt(self._old_sos, x, zi=self._old_zi)
        weight = np.minimum((self._fade_done + np.arange(1, x.size + 1)) / self._fade, 1.0)
        self._fade_done += x.size
        if self._fade_done >= self._fade:
            self._old_sos = None
            self._old_zi = None
        return y_old + weight * (y - y_old)

    def reset(self) -> None:
        self._zi[...] = 0.0
        self._old_sos = None
        self._old_zi = None
        self._fade_done = 0
