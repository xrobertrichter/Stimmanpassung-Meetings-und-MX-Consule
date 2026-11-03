"""Stage 5b: slow loudness normaliser (AGC) on K-weighted loudness, gated like BS.1770.

Measurement (per sample, vectorised): the input is K-weighted (BS.1770 pre-filter
+ RLB, designed locally in ``biquad.k_weighting_sos``), squared and averaged by a
one-pole whose time constant is half the *momentary* window (0.4 s): an
exponential window of tau is variance-equivalent to a rectangular window of
2*tau. The average is bias-corrected with the number of samples seen since
``reset`` so it is unbiased from the first sample on. Loudness is
``-0.691 + 10*log10(mean square)`` LUFS (BS.1770, one channel).

Control (every ``UPDATE_INTERVAL_MS`` on an absolute sample grid, hence
independent of the block size), using the momentary loudness ``L_m`` at the end
of the previous interval:

* noise-floor estimate ``L_floor``: evidence for the room floor is a *plateau*,
  i.e. ``PLATEAU_S`` of 10 ms interval loudness values whose range stays within
  ``PLATEAU_RANGE_LU`` (stationary noise: white < 2 LU, pink mostly < 3 LU; speech,
  even a dense lecture with 0.2 s gaps and even after the compressor, > 4 LU
  because of its syllabic modulation). The floor is the minimum of the plateau levels seen in the last
  ``FLOOR_WINDOW_S``; it follows a quieter room at once, a louder one by at most
  ``FLOOR_RISE_LU_PER_S`` (or outright once the old value is unconfirmed for a
  whole window). Without any plateau - a speaker who talks without pauses - the
  estimate simply keeps its last value, so dense speech can never masquerade as
  a loud room. The estimate is kept across a non-finite-input recovery;
* what counts as speech for the reference: an interval is learnt only when the
  signal has been *modulated* recently (range of the 10 ms interval loudness
  over the last ``MODULATION_WINDOW_S`` at least ``MODULATION_RANGE_LU``;
  stationary noise never is, speech always is) and, once a floor is known,
  when it lies at least ``gate_margin_lu + CERTAIN_SPEECH_ABOVE_GATE_LU`` above the
  floor (certainly speech, never the room). Pre-roll noise at the start of a stream therefore
  cannot become the reference, and without a reference the gain is held;
* low-SNR state: if the floor lies less than ``LOW_SNR_ENTER_LU`` under the
  speech reference, the room is too loud to be gated. The stage keeps the gate
  at floor + margin, keeps learning reference and gain from the certain-speech
  intervals (so the state can resolve itself and a quieter speaker is still
  followed), but drops the pause-headroom cap (``max_gain_db`` alone limits;
  otherwise speech in a loud room would stay too quiet for good). Pauses lie
  far below the certain-speech level and cannot lift the gain. The state ends with
  hysteresis once the floor is again ``LOW_SNR_EXIT_LU`` under the reference.
  Meters ``low_snr`` and ``snr_lu`` expose it for the controller's log and the
  status line;
* effective gate ``G = max(gate_lufs, L_floor + gate_margin_lu)`` once speech has
  been learnt (before that only ``gate_lufs``, so a signal without any dips can
  still start the regulation);
* absolute gate: ``L_m < G`` -> pause, the gain is frozen;
* relative gate (BS.1770 style): ``L_m < L_speech - 10 LU`` -> frozen as well,
  where ``L_speech`` is the exponentially smoothed loudness (time constant
  ``window_s / 2``, i.e. the 3 s short-term window) of the intervals that passed
  both gates and are not falling. While ``L_m`` lies more than 10 LU under the
  reference but above the *measured* floor plus margin, ``L_speech`` decays towards
  ``L_m`` by ``REFERENCE_DECAY_LU_PER_S`` (never below measured gate + 10 LU), so a
  speaker who becomes much quieter is re-referenced instead of being frozen out
  (the gate itself uses the floor bounded to ``L_speech - 15 LU``; the measured
  floor is what the meter ``noise_floor_lufs`` reports);
* otherwise the gain target is ``clip(target_lufs - L_speech, min_gain_db,
  min(max_gain_db, target_lufs - PAUSE_HEADROOM_LU - L_floor))``: the gain may
  never lift a gateable noise floor closer than 15 LU to the target; while ``L_m`` is
  falling faster than ``FALLING_LU_PER_S`` below the speech loudness (a decaying
  word or sentence end) the target may not lie above the current gain;
* the applied gain ramps towards the target by at most ``rate_db_per_s``
  upwards and ``release_rate_db_per_s`` downwards, evaluated per sample in
  closed form.

The normaliser is feed-forward (it measures its own input), so it cannot
oscillate; the rate limits are the only dynamic elements. Gating on the fast
momentary detector instead of on the smoothed loudness keeps the gain from
creeping up during pauses longer than the smoothing window; the internal floor
estimate keeps the protection independent of the calibrated ``gate_lufs`` when
the room gets louder during operation.
"""

from __future__ import annotations

import math

import numpy as np

from voicechain.dsp import DB_FLOOR_DBFS, StageBase
from voicechain.dsp.biquad import SosFilter, k_weighting_sos
from voicechain.dsp.envelope import OnePole, db_to_amp, power_to_db

# BS.1770-4: loudness = -0.691 + 10*log10(sum of weighted mean squares)
LOUDNESS_OFFSET_DB = -0.691
# BS.1770-4 momentary loudness window (400 ms): fast enough to see the end of a word.
MOMENTARY_WINDOW_S = 0.4
# BS.1770-4 relative gate: 10 LU below the loudness of the programme (here: the speech).
RELATIVE_GATE_LU = 10.0
# A speech envelope decays with > 50 LU/s at the end of a word; stationary ripple and
# slow drifts (a speaker leaning back, < 10 LU/s) must still be regulated upwards.
FALLING_LU_PER_S = 10.0
# Re-evaluating the gain target every 10 ms is plenty for a controller limited to a
# few dB/s (0.3 dB per interval even at the maximum release rate of 30 dB/s); the
# applied gain ramps per sample anyway.
UPDATE_INTERVAL_MS = 10.0
# Noise-floor evidence: a plateau of 0.5 s of 10 ms interval loudness within 3 LU.
# Measured ranges over 0.5 s at the normaliser's input (after the compressor): white
# noise 1.0-1.9 LU, pink noise median 2.7 LU (some windows up to 4.7 are simply not
# counted), synthetic speech with only 6 dB syllabic modulation compressed 3:1 still
# 4.4 LU, real speech far more. 3 LU accepts most noise windows and no speech window;
# 0.5 s is short enough that a loud room is recognised within the first second of a
# pause (the gain can rise at most ~0.5 dB before the stage freezes).
PLATEAU_S = 0.5
PLATEAU_RANGE_LU = 3.0
# Sliding minimum of the plateau levels over 10 s: sentences are 2-5 s long, so a room
# that is audible in pauses shows up within the window; a longer window would follow a
# room that gets quieter only after that time.
FLOOR_WINDOW_S = 10.0
# The estimate follows a louder room at 1 LU/s (a fan that switches on is tracked in
# ~10-20 s) so that a single mis-detected plateau cannot jump it.
FLOOR_RISE_LU_PER_S = 1.0
# A room whose floor lies 15 LU under the speech meets the acceptance criterion
# (Beauftragung 6.4 a: pause <= speech - 15 LU) and must therefore never be flagged as
# "low SNR"; the state is left at 15 LU and entered 2 LU lower (hysteresis against
# flicker on a fluctuating floor). The 15 LU are also the pause headroom the gain cap
# keeps below the target.
LOW_SNR_ENTER_LU = 13.0
LOW_SNR_EXIT_LU = 15.0
PAUSE_HEADROOM_LU = 15.0
# Speech evidence for learning the reference: within 2 s (a word plus a gap, or two
# syllable groups) the 10 ms interval loudness of speech spans > 7 LU even for the
# smoothest synthetic test speech and typically 15-30 LU; stationary noise spans
# < 2 LU (white) to < 4 LU (pink) over the same time. The 400 ms momentary would
# hide the syllabic modulation (ripple ~1 LU), hence the interval loudness.
MODULATION_WINDOW_S = 2.0
MODULATION_RANGE_LU = 6.0
# Intervals this far above the effective gate (floor + gate margin) are certainly speech:
# a room plateau fluctuates by <= 3 LU (PLATEAU_RANGE_LU), so floor + 8 + 5 = 13 LU above
# the floor is never the room. A larger distance (e.g. + 10 LU = 18 LU) would stop the
# learning exactly at the operating point of a room that is 5-8 dB louder than at
# calibration (speech 15-17 LU above the floor) and leave such a room unregulated for good.
CERTAIN_SPEECH_ABOVE_GATE_LU = 5.0
# While only the relative gate blocks, the speech reference decays at 2 LU/s: a speaker
# who became 20 LU quieter is re-referenced within 10 s; five times slower than the
# fastest decay that still counts as a syllable end (FALLING_LU_PER_S), and in pauses
# the absolute (floor-based) gate stops the decay altogether.
REFERENCE_DECAY_LU_PER_S = 2.0


class Normalizer(StageBase):
    PARAM_RANGES = {
        "target_lufs": (-40.0, -6.0),
        "window_s": (0.4, 10.0),        # 0.4 s = momentary, 3 s = short-term, 10 s = very slow
        "max_gain_db": (0.0, 30.0),
        "min_gain_db": (-30.0, 0.0),
        "rate_db_per_s": (0.1, 20.0),           # upward rate
        "release_rate_db_per_s": (0.1, 30.0),   # downward rate (a too-loud start is corrected faster)
        "gate_lufs": (-80.0, -20.0),
        "gate_margin_lu": (0.0, 30.0),          # effective gate = max(gate_lufs, floor + margin)
    }
    BOOL_PARAMS = ("enabled",)
    # 3 dB/s downwards: a sentence that starts 6 dB too loud is tamed within two seconds,
    # still far from audible pumping; upwards stays at the configured 1 dB/s.
    # 8 LU above the estimated floor: the fluctuation of a stationary room floor measured
    # with a 400 ms window stays within +-3 LU, so 8 LU gate the pause reliably while
    # soft speech 15 LU above the floor still passes.
    PARAM_DEFAULTS = {"release_rate_db_per_s": 3.0, "gate_margin_lu": 8.0}

    def __init__(self, sample_rate: int, **params):
        super().__init__(sample_rate)
        self._apply_params(params)
        self._kfilter = SosFilter(k_weighting_sos(self.sample_rate))
        self._momentary = OnePole(self.sample_rate, 0.5 * MOMENTARY_WINDOW_S * 1e3)
        self._momentary_tau = 0.5 * MOMENTARY_WINDOW_S * self.sample_rate
        self._update_samples = max(1, int(round(UPDATE_INTERVAL_MS * 1e-3 * self.sample_rate)))
        self._interval_s = self._update_samples / self.sample_rate
        self._up_step = 0.0
        self._down_step = 0.0
        self._speech_coef = 0.0
        self._plateau_ring = np.full(max(1, int(round(PLATEAU_S / self._interval_s))), np.nan)
        self._plateau_index = 0
        self._plateau_count = 0
        self._modulation_ring = np.full(max(1, int(round(MODULATION_WINDOW_S / self._interval_s))), np.nan)
        self._modulation_index = 0
        self._modulation_count = 0
        self._interval_index = 0                 # intervals since the last (re)start of the signal path
        self._floor_ring = np.full(max(1, int(round(FLOOR_WINDOW_S / self._interval_s))), np.inf)
        self._floor_index = 0
        self._floor_db = DB_FLOOR_DBFS           # sliding minimum of plateau levels, slow rise
        self._floor_known = False
        self._floor_stale = 0
        self._low_snr = False
        self._modulated_seen = False             # speech (modulated signal) has been learnt at least once
        self._effective_gate = self.gate_lufs
        self._interval_sum = 0.0                 # K-weighted power accumulated over the running interval
        self._interval_count = 0
        self._pos = 0                    # absolute sample index since reset (bias correction + grid)
        self._gain_db = 0.0
        self._target_db = 0.0
        self._last_momentary = DB_FLOOR_DBFS
        self._prev_momentary = DB_FLOOR_DBFS
        self._speech_power = 0.0
        self._speech_count = 0
        self._gated = True
        self._install_current()

    def _design_from(self, params: dict) -> dict:
        if params["min_gain_db"] > params["max_gain_db"]:
            raise ValueError(
                f"Normalizer: min_gain_db={params['min_gain_db']} must not exceed "
                f"max_gain_db={params['max_gain_db']}"
            )
        return {
            "up_step": params["rate_db_per_s"] / self.sample_rate,
            "down_step": params["release_rate_db_per_s"] / self.sample_rate,
            "speech_coef": math.exp(-self._interval_s / (0.5 * params["window_s"])),
        }

    def _install(self, design: dict) -> None:
        self._up_step = design["up_step"]
        self._down_step = design["down_step"]
        self._speech_coef = design["speech_coef"]

    # -- measurement -------------------------------------------------------------------
    def momentary_curve(self, x: np.ndarray) -> np.ndarray:
        """Per-sample momentary loudness in LUFS; advances the meter state and position."""
        return self._measure(np.asarray(x, dtype=np.float64))[0]

    def _measure(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Return (momentary loudness per sample, K-weighted power per sample); advances position."""
        weighted = self._kfilter.process(x)
        power = np.square(weighted)
        averaged = self._momentary.process(power)
        idx = np.arange(self._pos + 1, self._pos + x.size + 1, dtype=np.float64)
        bias = -np.expm1(-idx / self._momentary_tau)       # 1 - a**(n+1), never 0 for n >= 0
        self._pos += x.size
        return LOUDNESS_OFFSET_DB + power_to_db(averaged / bias), power

    def _interval_loudness(self, power_segment: np.ndarray) -> float:
        """Loudness of the interval that just ended (partial sums carried across blocks)."""
        total = self._interval_sum + float(np.sum(power_segment))
        count = self._interval_count + power_segment.size
        self._interval_sum = 0.0
        self._interval_count = 0
        if count == 0:
            return DB_FLOOR_DBFS
        return LOUDNESS_OFFSET_DB + float(power_to_db(total / count))

    def speech_loudness(self) -> float:
        """Smoothed loudness of the un-gated intervals (DB_FLOOR_DBFS before any speech)."""
        if self._speech_count == 0:
            return DB_FLOOR_DBFS
        bias = -math.expm1(self._speech_count * math.log(self._speech_coef))
        return LOUDNESS_OFFSET_DB + float(power_to_db(self._speech_power / bias))

    def _set_speech_loudness(self, loudness_db: float) -> None:
        """Overwrite the reference (used for the slow decay) without touching the count."""
        bias = -math.expm1(self._speech_count * math.log(self._speech_coef))
        self._speech_power = 10.0 ** ((loudness_db - LOUDNESS_OFFSET_DB) / 10.0) * bias

    def noise_floor(self) -> float:
        """Floor estimate in LUFS (DB_FLOOR_DBFS until the first plateau has been seen)."""
        return self._floor_db if self._floor_known else DB_FLOOR_DBFS

    def _floor_usable(self) -> bool:
        """The floor drives gate, cap and low-SNR state only once modulated speech has been learnt.

        A stationary test tone is itself a plateau; without this guard it would be
        taken for a loud room and gate itself. Real speech is modulated by > 7 LU
        within a second, so the guard opens with the first learnt syllables.
        """
        return self._floor_known and self._modulated_seen and self._speech_count > 0

    def snr_lu(self) -> float:
        """Speech reference minus floor estimate (inf while one of them is unknown)."""
        if not self._floor_usable():
            return math.inf
        return self.speech_loudness() - self._floor_db

    def _update_floor(self, interval_db: float) -> None:
        """Feed one 10 ms interval loudness; update the plateau detector and the floor estimate."""
        self._interval_index += 1
        self._plateau_ring[self._plateau_index] = interval_db
        self._plateau_index = (self._plateau_index + 1) % self._plateau_ring.size
        self._plateau_count = min(self._plateau_count + 1, self._plateau_ring.size)
        if self._interval_index > self._plateau_ring.size:
            # the first half second after a (re)start carries the filter start-up transient,
            # which would read as modulation
            self._modulation_ring[self._modulation_index] = interval_db
            self._modulation_index = (self._modulation_index + 1) % self._modulation_ring.size
            self._modulation_count = min(self._modulation_count + 1, self._modulation_ring.size)
        candidate = np.inf
        if self._plateau_count == self._plateau_ring.size:
            high = float(np.max(self._plateau_ring))
            if high - float(np.min(self._plateau_ring)) <= PLATEAU_RANGE_LU:
                candidate = high                     # stationary for a whole second: room floor
        self._floor_ring[self._floor_index] = candidate
        self._floor_index = (self._floor_index + 1) % self._floor_ring.size
        window_min = float(np.min(self._floor_ring))
        if not math.isfinite(window_min):
            return                                   # no plateau within the window: keep the estimate
        rise = FLOOR_RISE_LU_PER_S * self._interval_s
        if not self._floor_known:
            self._floor_db = window_min
            self._floor_known = True
            self._floor_stale = 0
        elif window_min <= self._floor_db + rise:
            self._floor_db = window_min              # follows a quieter room at once
            self._floor_stale = 0
        else:
            # every plateau in the window is louder than the estimate: rise slowly, and once
            # the estimate has been unconfirmed for a whole window it is stale (the pause it
            # came from left the window) and the window minimum is accepted outright
            self._floor_stale += 1
            if self._floor_stale >= self._floor_ring.size:
                self._floor_db = window_min
            else:
                self._floor_db += rise

    def _modulated_recently(self) -> bool:
        """True when the interval loudness spanned >= MODULATION_RANGE_LU within the last window."""
        if self._modulation_count < self._plateau_ring.size:
            return False                             # less than half a second of evidence
        values = self._modulation_ring[: self._modulation_count] if self._modulation_count < self._modulation_ring.size \
            else self._modulation_ring
        return float(np.max(values)) - float(np.min(values)) >= MODULATION_RANGE_LU

    def _certainly_speech(self, momentary_db: float) -> bool:
        """Above floor + gate margin + relative gate: cannot be the room."""
        if not self._floor_known:
            return True
        return momentary_db >= self._floor_db + self.gate_margin_lu + CERTAIN_SPEECH_ABOVE_GATE_LU

    def _update_low_snr(self) -> None:
        snr = self.snr_lu()
        if self._low_snr:
            self._low_snr = snr < LOW_SNR_EXIT_LU
        else:
            self._low_snr = snr < LOW_SNR_ENTER_LU

    def _grid_update(self, momentary_db: float, interval_db: float, gain_db: float) -> None:
        """Gate decision and new gain target at one grid point (``gain_db`` = gain right now)."""
        self._update_floor(interval_db)
        self._update_low_snr()
        gate = self.gate_lufs
        if self._floor_usable():
            gate = max(gate, self._floor_db + self.gate_margin_lu)
        self._effective_gate = gate
        absolute = momentary_db < gate
        below_reference = self._speech_count > 0 and momentary_db < self.speech_loudness() - RELATIVE_GATE_LU
        falling = momentary_db < self._prev_momentary - FALLING_LU_PER_S * self._interval_s
        self._prev_momentary = momentary_db
        if falling and self._speech_count > 0 and momentary_db >= self.speech_loudness():
            falling = False   # still above the speech loudness: a syllable, not a decay into a pause
        self._gated = absolute or below_reference
        modulated = self._modulated_recently()
        certain = self._certainly_speech(momentary_db)
        if below_reference and momentary_db >= gate and not falling and modulated and certain:
            # Steady or rising, certainly above the room, yet more than 10 LU under the
            # reference: a speaker who became quieter (decays into pauses are excluded). Let
            # the reference sink towards the signal, never below gate + 10 LU (the relative
            # gate stays above the absolute one).
            decayed = max(momentary_db, self.speech_loudness() - REFERENCE_DECAY_LU_PER_S * self._interval_s,
                          gate + RELATIVE_GATE_LU)
            self._set_speech_loudness(decayed)
        if self._gated:
            self._target_db = gain_db                # pause: freeze
            return
        if not falling and modulated and certain:
            # The reference is learnt from rising/steady, modulated intervals that lie clearly
            # above the floor: the decay phases into pauses would drag it down, stationary
            # pre-roll noise or a loud room must never become "the speaker".
            power = 10.0 ** ((momentary_db - LOUDNESS_OFFSET_DB) / 10.0)
            self._speech_power = self._speech_coef * self._speech_power + (1.0 - self._speech_coef) * power
            self._speech_count += 1
            self._modulated_seen = True
        if self._speech_count == 0:
            self._target_db = gain_db                # no reference yet: hold (pre-roll noise, start-up)
            return
        gain_cap = self.max_gain_db
        if self._floor_usable() and not self._low_snr:
            gain_cap = min(gain_cap, self.target_lufs - PAUSE_HEADROOM_LU - self._floor_db)
        gain_cap = max(gain_cap, self.min_gain_db)
        target = float(np.clip(self.target_lufs - self.speech_loudness(), self.min_gain_db, gain_cap))
        if falling or not certain:
            # never climb on a decaying envelope, nor on an interval that might be the room
            target = min(target, gain_db)
        self._target_db = target

    # -- processing --------------------------------------------------------------------
    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        n = x.size
        if n == 0:
            return x
        start = self._pos                     # absolute index of x[0]
        momentary, power = self._measure(x)
        gains = np.empty(n, dtype=np.float64)
        grid = self._update_samples
        # bypass: measuring continues (gate state, speech loudness), the gain stays where it is
        up_step = self._up_step if self.enabled else 0.0
        down_step = self._down_step if self.enabled else 0.0
        i = 0
        seg_start = 0
        gain = self._gain_db
        while i < n:
            abs_i = start + i
            if abs_i % grid == 0:
                reference = momentary[i - 1] if i > 0 else self._last_momentary
                self._grid_update(float(reference), self._interval_loudness(power[seg_start:i]), gain)
                seg_start = i
            seg_end = min(n, (abs_i // grid + 1) * grid - start)
            steps = np.arange(1, seg_end - i + 1, dtype=np.float64)
            np.clip(self._target_db, gain - down_step * steps, gain + up_step * steps, out=gains[i:seg_end])
            gain = float(gains[seg_end - 1])
            i = seg_end
        self._interval_sum += float(np.sum(power[seg_start:]))
        self._interval_count += n - seg_start
        self._last_momentary = float(momentary[-1])
        self._gain_db = gain
        if not self.enabled:
            return x
        return x * db_to_amp(gains)

    def meters(self) -> dict:
        return {
            "enabled": float(self.enabled),
            "gain_db": self._gain_db,
            "loudness_lufs": self.speech_loudness(),
            "momentary_lufs": self._last_momentary,
            "noise_floor_lufs": self.noise_floor(),
            "effective_gate_lufs": float(self._effective_gate),
            "snr_lu": self.snr_lu(),
            "low_snr": float(self._low_snr),
            "gated": float(self._gated),
        }

    def _clear_signal_path(self) -> None:
        self._kfilter.reset()
        self._momentary.reset()
        self._pos = 0
        self._interval_sum = 0.0
        self._interval_count = 0
        self._plateau_ring[...] = np.nan
        self._plateau_index = 0
        self._plateau_count = 0
        self._modulation_ring[...] = np.nan
        self._modulation_index = 0
        self._modulation_count = 0
        self._interval_index = 0
        self._last_momentary = DB_FLOOR_DBFS
        self._prev_momentary = DB_FLOOR_DBFS
        self._gated = True

    def recover(self) -> None:
        """After a non-finite block: clear filters and detector, keep gain, reference and floor."""
        self._clear_signal_path()
        self._target_db = self._gain_db

    def reset(self) -> None:
        self._clear_signal_path()
        self._gain_db = 0.0
        self._target_db = 0.0
        self._speech_power = 0.0
        self._speech_count = 0
        self._floor_ring[...] = np.inf
        self._floor_index = 0
        self._floor_db = DB_FLOOR_DBFS
        self._floor_known = False
        self._floor_stale = 0
        self._low_snr = False
        self._modulated_seen = False
        self._effective_gate = self.gate_lufs
