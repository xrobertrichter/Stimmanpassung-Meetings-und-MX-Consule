"""Control-signal utilities shared by the dynamics stages.

Everything here is vectorised: no Python loop runs over individual samples.

* :class:`OnePole` - linear one-pole smoother (``scipy.signal.lfilter`` with state).
* :class:`ReleaseDecay` - the non-linear half of a decoupled peak detector
  ``y[n] = max(x[n], a*y[n-1] + (1-a)*x[n])``. It rises instantly and decays
  exponentially towards the input. The recursion contains a ``max`` and is
  therefore not an LTI filter, but after the substitution ``u = y - x`` it becomes
  the Lindley recursion ``u[n] = max(0, a*(u[n-1] + x[n-1] - x[n]))`` whose closed
  form is a cumulative sum minus its running minimum. That closed form is
  evaluated with ``numpy.cumsum`` / ``numpy.minimum.accumulate`` on chunks of at
  most :data:`DECAY_CHUNK` samples (the chunking keeps the factor ``a**-n`` inside
  the float64 range); the loop over chunks is 1 iteration for a 256-sample block.
* :class:`AttackReleaseSmoother` - release decay followed by a one-pole attack,
  the "smooth decoupled peak detector" of Giannoulis, Massberg & Reiss,
  "Digital Dynamic Range Compressor Design - A Tutorial and Analysis",
  JAES 60(6), 2012.
* :class:`HoldMax` - causal running maximum over a window (gate hold), computed
  with ``scipy.ndimage.maximum_filter1d`` on a short tail buffer.
* :class:`GainRamp` - linear amplitude ramp between two static gain values so
  that a run-time change of a gain parameter never produces a step (click).
* :class:`MovingAverage` - causal rectangular moving average (box-car), computed
  with a cumulative sum over the block plus a tail buffer of the previous
  ``window - 1`` inputs. Used as short-time RMS detector and as the limiter's
  linear attack ramp.

Level helpers convert between linear amplitude / power and dB with a floor so
that digital silence yields a finite number instead of ``-inf``.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import signal
from scipy.ndimage import maximum_filter1d

from voicechain.dsp import DB_FLOOR_DBFS

# Floor for level measurements (one definition for the whole package, see voicechain.dsp).
DB_FLOOR = DB_FLOOR_DBFS
AMP_FLOOR = 10.0 ** (DB_FLOOR / 20.0)
POWER_FLOOR = 10.0 ** (DB_FLOOR / 10.0)

# Chunk length for the vectorised release decay. With the shortest allowed release
# time of 1 ms at 192 kHz (tau = 192 samples) the scaling factor a**-n reaches at
# most exp(256/192) ~ 3.8; even a 1-sample time constant stays at exp(256) ~ 1e111,
# well inside float64. 256 also equals the default block size, so the loop over
# chunks runs exactly once per block in live operation.
DECAY_CHUNK = 256

# Run-time parameter changes are smoothed over 10 ms (gain ramps, filter cross-fades):
# shorter than a syllable, so the new setting is effective practically at once, but
# long enough that even a large jump produces no click.
PARAM_RAMP_MS = 10.0


def amp_to_db(a: np.ndarray | float) -> np.ndarray | float:
    """20*log10(|a|) with the floor applied."""
    return 20.0 * np.log10(np.maximum(np.abs(a), AMP_FLOOR))


def power_to_db(p: np.ndarray | float) -> np.ndarray | float:
    """10*log10(p) with the floor applied (p is a mean-square value)."""
    return 10.0 * np.log10(np.maximum(p, POWER_FLOOR))


def db_to_amp(db: np.ndarray | float) -> np.ndarray | float:
    return 10.0 ** (np.asarray(db, dtype=np.float64) / 20.0)


def peak_dbfs(x: np.ndarray) -> float:
    """Sample peak of a block in dBFS (DB_FLOOR for an empty or silent block)."""
    if x.size == 0:
        return DB_FLOOR
    return float(amp_to_db(np.max(np.abs(x))))


def rms_dbfs(x: np.ndarray) -> float:
    """RMS level of a block in dBFS (DB_FLOOR for an empty or silent block)."""
    if x.size == 0:
        return DB_FLOOR
    return float(power_to_db(np.mean(np.square(x, dtype=np.float64))))


def smoothing_coefficient(time_ms: float, sample_rate: int) -> float:
    """One-pole coefficient ``a = exp(-1/(tau*fs))`` so that a step reaches 63 % after ``time_ms``.

    ``time_ms <= 0`` returns 0, i.e. no smoothing.
    """
    tau_samples = time_ms * 1e-3 * sample_rate
    if tau_samples <= 0.0:
        return 0.0
    return math.exp(-1.0 / tau_samples)


class OnePole:
    """``y[n] = a*y[n-1] + (1-a)*x[n]`` with carried state (lfilter)."""

    def __init__(self, sample_rate: int, time_ms: float):
        self._sample_rate = int(sample_rate)
        self._zi = np.zeros(1, dtype=np.float64)
        self._b = np.ones(1)
        self._a = np.ones(2)
        self.set_time(time_ms)

    def set_time(self, time_ms: float) -> None:
        self.coefficient = smoothing_coefficient(time_ms, self._sample_rate)
        self._b = np.array([1.0 - self.coefficient])
        self._a = np.array([1.0, -self.coefficient])

    @property
    def value(self) -> float:
        """Current state (equals the last output sample)."""
        # For this filter the lfilter state zi equals a*y[n-1]; divide to recover y[n-1].
        if self.coefficient == 0.0:
            return 0.0
        return float(self._zi[0] / self.coefficient)

    def set_value(self, value: float) -> None:
        self._zi[0] = self.coefficient * value

    def process(self, x: np.ndarray) -> np.ndarray:
        if x.size == 0:
            return np.zeros(0, dtype=np.float64)
        if self.coefficient == 0.0:
            return np.asarray(x, dtype=np.float64)
        y, self._zi = signal.lfilter(self._b, self._a, x, zi=self._zi)
        return y

    def reset(self) -> None:
        self._zi[...] = 0.0


class ReleaseDecay:
    """``y[n] = max(x[n], a*y[n-1] + (1-a)*x[n])``: instant rise, exponential fall towards x."""

    def __init__(self, sample_rate: int, time_ms: float):
        self._sample_rate = int(sample_rate)
        self._u_prev = 0.0  # y[n-1] - x[n-1]  (always >= 0)
        self._x_prev = 0.0
        self.set_time(time_ms)

    def set_time(self, time_ms: float) -> None:
        self.coefficient = smoothing_coefficient(time_ms, self._sample_rate)

    @property
    def value(self) -> float:
        return self._u_prev + self._x_prev

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        n = x.size
        if n == 0:
            return np.zeros(0, dtype=np.float64)
        a = self.coefficient
        if a == 0.0:
            self._u_prev = 0.0
            self._x_prev = float(x[-1])
            return x.copy()
        out = np.empty(n, dtype=np.float64)
        for start in range(0, n, DECAY_CHUNK):
            xc = x[start:start + DECAY_CHUNK]
            m = xc.size
            # d[n] = x[n-1] - x[n];  e[n] = a**-n * d[n];  w[n] = max(0, w[n-1] + e[n])
            d = np.empty(m)
            d[0] = self._x_prev - xc[0]
            np.subtract(xc[:-1], xc[1:], out=d[1:])
            scale = np.exp(np.arange(m, dtype=np.float64) * (-math.log(a)))  # a**-n
            s = np.empty(m + 1)
            s[0] = self._u_prev
            np.cumsum(d * scale, out=s[1:])
            s[1:] += self._u_prev
            running_min = np.minimum.accumulate(s)
            np.minimum(running_min, 0.0, out=running_min)
            # u[n] = a**(n+1) * w[n]
            u = (s[1:] - running_min[1:]) * (a / scale)
            np.add(u, xc, out=out[start:start + m])
            self._u_prev = float(u[-1])
            self._x_prev = float(xc[-1])
        return out

    def reset(self) -> None:
        self._u_prev = 0.0
        self._x_prev = 0.0


class AttackReleaseSmoother:
    """Smooth decoupled peak detector: upward moves take ``attack_ms``, downward ``release_ms``.

    Feed it the *target* control value; it returns the smoothed one. The release
    stage decays towards the current target, the one-pole stage then rounds off
    the instant rise with the attack time constant. Because the one-pole also lags
    the release, the measured 63 % release point sits at about ``release + attack``.
    """

    def __init__(self, sample_rate: int, attack_ms: float, release_ms: float):
        self._release = ReleaseDecay(sample_rate, release_ms)
        self._attack = OnePole(sample_rate, attack_ms)

    def set_times(self, attack_ms: float, release_ms: float) -> None:
        self._attack.set_time(attack_ms)
        self._release.set_time(release_ms)

    @property
    def value(self) -> float:
        return self._attack.value

    def process(self, target: np.ndarray) -> np.ndarray:
        return self._attack.process(self._release.process(target))

    def reset(self) -> None:
        self._release.reset()
        self._attack.reset()


class HoldMax:
    """Causal running maximum over the last ``hold_samples`` samples (gate hold).

    The previous ``hold_samples - 1`` inputs are kept in a tail buffer, so the
    output is independent of the block size.
    """

    def __init__(self, hold_samples: int, fill_value: float = 0.0):
        self._fill = float(fill_value)
        self._tail = np.zeros(0)
        self.set_hold(hold_samples)

    @property
    def hold_samples(self) -> int:
        return self._hold

    def set_hold(self, hold_samples: int) -> None:
        hold = max(1, int(round(hold_samples)))
        new_tail = np.full(hold - 1, self._fill)
        keep = min(self._tail.size, new_tail.size)
        if keep:
            new_tail[new_tail.size - keep:] = self._tail[self._tail.size - keep:]
        self._tail = new_tail
        self._hold = hold

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        if x.size == 0:
            return np.zeros(0, dtype=np.float64)
        if self._hold == 1:
            return x.copy()
        buf = np.concatenate([self._tail, x])
        # origin=(size-1)//2 shifts the window so that output n covers buf[n-size+1 .. n]
        # (verified against a naive implementation in tests/test_dsp_expander.py).
        full = maximum_filter1d(buf, size=self._hold, origin=(self._hold - 1) // 2, mode="nearest")
        self._tail = buf[buf.size - (self._hold - 1):]
        return full[self._hold - 1:]

    def reset(self) -> None:
        self._tail[...] = self._fill


class MovingAverage:
    """Causal box-car mean over the last ``window_samples`` inputs, block-size independent."""

    def __init__(self, window_samples: int):
        self._tail = np.zeros(0)
        self.set_window(window_samples)

    @property
    def window_samples(self) -> int:
        return self._window

    def set_window(self, window_samples: int) -> None:
        window = max(1, int(round(window_samples)))
        new_tail = np.zeros(window - 1)
        keep = min(self._tail.size, new_tail.size)
        if keep:
            new_tail[new_tail.size - keep:] = self._tail[self._tail.size - keep:]
        self._tail = new_tail
        self._window = window

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        n = x.size
        if n == 0:
            return np.zeros(0, dtype=np.float64)
        if self._window == 1:
            return x.copy()
        buf = np.concatenate([self._tail, x])
        csum = np.empty(buf.size + 1)
        csum[0] = 0.0
        np.cumsum(buf, out=csum[1:])
        w = self._window
        out = (csum[w:w + n] - csum[:n]) / w
        self._tail = buf[buf.size - (w - 1):]
        return out

    def reset(self) -> None:
        self._tail[...] = 0.0


class GainRamp:
    """Static gain with a linear ramp of ``ramp_samples`` whenever the target changes."""

    def __init__(self, ramp_samples: int, gain_db: float = 0.0):
        self._ramp = max(1, int(round(ramp_samples)))
        self._current = float(db_to_amp(gain_db))
        self._target = self._current
        self._remaining = 0

    @property
    def gain(self) -> float:
        """Current linear gain (end of the last processed block)."""
        return self._current

    def set_gain_db(self, gain_db: float) -> None:
        target = float(db_to_amp(gain_db))
        if target != self._target:
            self._target = target
            self._remaining = self._ramp

    def snap(self) -> None:
        """Jump to the target immediately (used after ``reset``)."""
        self._current = self._target
        self._remaining = 0

    def process(self, n: int) -> np.ndarray | float:
        """Gain for the next ``n`` samples: a scalar when settled, else a per-sample array."""
        if self._remaining == 0 or n == 0:
            return self._current
        k = min(n, self._remaining)
        increment = (self._target - self._current) / self._remaining
        out = np.full(n, self._target)
        out[:k] = self._current + increment * np.arange(1, k + 1)
        self._current = float(out[k - 1])
        self._remaining -= k
        if self._remaining == 0:
            self._current = self._target
        return out


class ReductionAccumulator:
    """Gain-reduction statistics: last block mean, plus mean and max since the last ``read``.

    The adaptive controller polls the meters every few seconds; a snapshot of the
    last 5 ms block would miss short events, so the stages accumulate between reads.
    """

    def __init__(self):
        self.last_mean = 0.0
        self._sum = 0.0
        self._count = 0
        self._max = 0.0

    def add(self, reduction: np.ndarray) -> None:
        if reduction.size == 0:
            return
        self.last_mean = float(np.mean(reduction))
        self._sum += float(np.sum(reduction))
        self._count += reduction.size
        self._max = max(self._max, float(np.max(reduction)))

    def add_zeros(self, n: int) -> None:
        """Account for a block in which the stage did not reduce (bypass)."""
        self.last_mean = 0.0
        self._count += int(n)

    def peek(self) -> tuple[float, float]:
        """Return (mean, max) since the previous read without restarting the accumulation."""
        return (self._sum / self._count if self._count else 0.0), self._max

    def read(self) -> tuple[float, float]:
        """Return (mean, max) since the previous read and restart the accumulation."""
        mean, peak = self.peek()
        self._sum = 0.0
        self._count = 0
        self._max = 0.0
        return mean, peak

    def reset(self) -> None:
        self.last_mean = 0.0
        self.read()
