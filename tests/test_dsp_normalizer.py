"""Loudness normaliser: target, rate limit, gate, gain limits, block invariance."""

import numpy as np
import pytest
from scipy.signal import sosfilt

from voicechain.dsp import Normalizer
from voicechain.dsp.biquad import k_weighting_sos

DEFAULT = dict(enabled=True, target_lufs=-18.0, window_s=3.0, max_gain_db=12.0,
               min_gain_db=-12.0, rate_db_per_s=1.0, gate_lufs=-40.0)


def loudness_lufs(x, fs):
    """Reference: BS.1770 mono loudness over the whole segment (rectangular window)."""
    weighted = sosfilt(k_weighting_sos(fs), np.asarray(x, dtype=np.float64))
    return -0.691 + 10.0 * np.log10(np.mean(weighted ** 2))


def gapped_sine(gen, freq_hz, peak_dbfs, seconds, fs, gap_s=0.6, period_s=2.0):
    """Sine with a silent gap of ``gap_s`` every ``period_s``: the normaliser learns its
    reference only from *modulated* signal (a steady tone would be taken for room noise),
    so the stationary test tones carry a 30 % gap pattern; the tone itself is unchanged."""
    x = gen.sine(freq_hz, peak_dbfs, seconds, fs)
    t = np.arange(len(x)) / fs
    return x * ((t % period_s) >= gap_s)


def _run(stage, x, block):
    out, gains = [], []
    for i in range(0, len(x), block):
        out.append(stage.process(x[i:i + block]))
        gains.append(stage.meters()["gain_db"])
    return np.concatenate(out), np.array(gains)


def test_reference_loudness_of_sine(fs, gen):
    x = gen.sine(1000.0, -27.0, 5.0, fs)          # -30 dBFS RMS -> -30 LUFS at 1 kHz
    assert abs(loudness_lufs(x, fs) + 30.0) < 0.05
    stage = Normalizer(fs, **DEFAULT)
    estimate = np.concatenate([stage.momentary_curve(x[i:i + 1024]) for i in range(0, len(x), 1024)])
    assert abs(estimate[-1] + 30.0) < 0.1
    assert abs(estimate[fs // 20] + 30.0) < 0.1    # bias correction: valid long before the 400 ms window


def test_quiet_signal_is_pulled_to_target_within_rate_limit(fs, gen):
    x = gapped_sine(gen, 1000.0, -27.0, 30.0, fs)                      # tone at -30 LUFS
    stage = Normalizer(fs, **DEFAULT)
    y, gains = _run(stage, x, 1024)
    tone = np.abs(x) > 0
    assert abs(loudness_lufs(y[-6 * fs:][tone[-6 * fs:]], fs) - DEFAULT["target_lufs"]) < 1.0
    block_s = 1024 / fs
    assert np.max(np.diff(gains)) <= DEFAULT["rate_db_per_s"] * block_s * (1 + 1e-9)
    assert abs(np.max(np.diff(gains)) / block_s - DEFAULT["rate_db_per_s"]) < 0.01   # exactly 1 dB/s while rising
    assert np.all(np.diff(gains) >= -1e-12)         # monotonic ramp towards the target
    assert np.argmax(gains > 0.01) * block_s < 1.5  # starts as soon as the first gap proves modulation
    assert abs(gains[-1] - 12.0) < 0.05             # exactly the 12 dB that are needed
    assert abs(stage.meters()["loudness_lufs"] + 30.0) < 0.5


def test_gate_prevents_pumping_up_silence(fs, gen):
    x = gen.white_noise(-60.0, 10.0, fs, seed=1)
    y, gains = _run(Normalizer(fs, **DEFAULT), x, 1024)
    assert np.all(gains == 0.0)
    assert np.array_equal(y, x)
    z, gains = _run(Normalizer(fs, **DEFAULT), np.zeros(5 * fs), 256)
    assert np.max(np.abs(z)) == 0.0 and np.all(gains == 0.0)


def test_max_and_min_gain_are_respected(fs, gen):
    x = gapped_sine(gen, 1000.0, -32.0, 30.0, fs)   # -35 LUFS needs +17 dB -> capped at +12
    tone = np.abs(x) > 0
    y, gains = _run(Normalizer(fs, **DEFAULT), x, 1024)
    assert np.max(gains) <= DEFAULT["max_gain_db"] + 1e-12
    assert abs(loudness_lufs(y[-6 * fs:][tone[-6 * fs:]], fs) + 23.0) < 0.5
    loud = gapped_sine(gen, 1000.0, 3.0, 25.0, fs)  # 0 LUFS needs -18 dB -> capped at -12
    loud_tone = np.abs(loud) > 0
    y, gains = _run(Normalizer(fs, **DEFAULT), loud, 1024)
    assert np.min(gains) >= DEFAULT["min_gain_db"] - 1e-12
    assert abs(loudness_lufs(y[-6 * fs:][loud_tone[-6 * fs:]], fs) + 12.0) < 0.5


def test_gain_is_held_in_pauses_and_resumes(fs, gen):
    x, _ = gen.speech_like(-30.0, 20.0, fs, seed=2, noise_rms_dbfs=-70.0, talk_s=1.5, pause_s=1.5)
    y, gains = _run(Normalizer(fs, **DEFAULT), x, 1024)
    assert np.all(np.diff(gains) >= -1e-12)         # never pulled down by a pause
    assert gains[-1] > 6.0


def test_block_invariance(fs, gen):
    x, _ = gen.speech_like(-28.0, 6.0, fs, seed=3)
    a, _ = _run(Normalizer(fs, **DEFAULT), x, 64)
    b, _ = _run(Normalizer(fs, **DEFAULT), x, 4096)
    assert np.max(np.abs(a - b)) < 1e-9


def test_disabled_keeps_measuring(fs, gen):
    x = gapped_sine(gen, 1000.0, -27.0, 8.0, fs)
    stage = Normalizer(fs, **{**DEFAULT, "enabled": False})
    y, gains = _run(stage, x, 1024)
    assert np.array_equal(y, x) and np.all(gains == 0.0)
    assert abs(stage.meters()["loudness_lufs"] + 30.0) < 0.5


def test_max_gain_can_be_changed_at_runtime(fs, gen):
    """The adaptive controller lowers/raises max_gain_db live; the cap must apply at once."""
    x = gapped_sine(gen, 1000.0, -32.0, 30.0, fs)   # -35 LUFS wants +17 dB
    stage = Normalizer(fs, **DEFAULT)
    _, gains = _run(stage, x[:15 * fs], 1024)
    assert 8.0 <= stage.meters()["gain_db"] <= 12.0            # ~1 dB/s during the tone parts
    stage.set_param("max_gain_db", 6.0)
    assert stage.get_param("max_gain_db") == 6.0
    _, gains = _run(stage, x[15 * fs:], 1024)
    assert np.all(gains <= 12.0 + 1e-12) and abs(gains[-1] - 6.0) < 0.05   # ramps down to the new cap
    release_rate = stage.get_param("release_rate_db_per_s")                  # default 3 dB/s downwards
    assert release_rate == 3.0
    assert np.min(np.diff(gains)) >= -release_rate * 1024 / fs * (1 + 1e-9)
    assert abs(np.min(np.diff(gains)) + release_rate * 1024 / fs) < 1e-9     # and it does use the full rate


def test_gain_does_not_rise_on_a_decaying_envelope(fs, gen):
    """Tone at -30 LUFS that fades out at -30 dB/s: the gain climbs during the tone, not during
    the fade (a word end). Without the rule it would keep climbing at 1 dB/s for ~0.7 s until
    the gate closes; the 400 ms detector needs ~0.1 s to register the fall, so a residual rise
    of at most 0.25 dB is tolerated."""
    plateau = gapped_sine(gen, 1000.0, -27.0, 8.0, fs)
    t = np.arange(2 * fs) / fs
    fade = gen.sine(1000.0, -27.0, 2.0, fs) * 10 ** (-30.0 * t / 20.0)
    stage = Normalizer(fs, **DEFAULT)
    _, g_plateau = _run(stage, plateau, 256)
    _, g_fade = _run(stage, fade, 256)
    assert g_plateau[-1] > 3.5
    assert np.max(g_fade) <= g_plateau[-1] + 0.25
    assert g_fade[-1] <= g_plateau[-1] + 0.25


def test_asymmetric_rates(fs, gen):
    loud = gapped_sine(gen, 1000.0, 3.0, 10.0, fs)              # 0 LUFS: 12 dB too loud -> cap -12
    y, gains = _run(Normalizer(fs, **DEFAULT), loud, 1024)
    per_s = fs / 1024
    assert abs(np.min(np.diff(gains)) * per_s + 3.0) < 0.03     # 3 dB/s downwards
    quiet = gapped_sine(gen, 1000.0, -27.0, 10.0, fs)
    y, gains = _run(Normalizer(fs, **DEFAULT), quiet, 1024)
    assert abs(np.max(np.diff(gains)) * per_s - 1.0) < 0.01     # 1 dB/s upwards
    fast = Normalizer(fs, **{**DEFAULT, "release_rate_db_per_s": 6.0})
    y, gains = _run(fast, loud, 1024)
    assert abs(np.min(np.diff(gains)) * per_s + 6.0) < 0.06


def test_validation(fs):
    with pytest.raises(ValueError):
        Normalizer(fs, **{**DEFAULT, "min_gain_db": 5.0, "max_gain_db": 2.0})
    stage = Normalizer(fs, **DEFAULT)
    with pytest.raises(ValueError):
        stage.set_param("target_lufs", 0.0)
    stage.set_param("rate_db_per_s", 2.0)
    assert stage.get_param("rate_db_per_s") == 2.0


def _speech_pause_speech(gen, fs, noise_dbfs, seed=11):
    """Long-pause scenario: 30 s speech, 20 s pause (noise only), 20 s speech; speech at -20 dBFS RMS."""
    first, _ = gen.speech_like(-20.0, 30.0, fs, seed=seed, noise_rms_dbfs=noise_dbfs)
    pause = gen.white_noise(noise_dbfs, 20.0, fs, seed=seed + 1)
    second, _ = gen.speech_like(-20.0, 20.0, fs, seed=seed + 2, noise_rms_dbfs=noise_dbfs)
    return np.concatenate([first, pause, second])


@pytest.mark.parametrize("noise_dbfs, gate_lufs", [
    (-50.0, DEFAULT["gate_lufs"]),   # quiet room: the default absolute gate (-40 LUFS) sees the pause
    (-42.0, -30.0),                  # fan: calibrated gate (floor + 8 LU) as the calibration sets it
])
def test_long_pause_freezes_gain_and_next_sentence_is_not_louder(fs, gen, noise_dbfs, gate_lufs):
    """The gain must not creep up during a 20 s pause; the first sentence afterwards
    is at most 1 dB louder than the last one before."""
    x = _speech_pause_speech(gen, fs, noise_dbfs)
    y, gains = _run(Normalizer(fs, **{**DEFAULT, "gate_lufs": gate_lufs}), x, 256)
    per_s = fs / 256
    in_pause = gains[int(30 * per_s):int(50 * per_s)]
    assert np.max(in_pause) - np.min(in_pause) <= 1.0
    assert abs(gains[int(50 * per_s)] - gains[int(30 * per_s)]) <= 1.0
    before = loudness_lufs(y[29 * fs:30 * fs], fs)
    after = loudness_lufs(y[int(50.5 * fs):int(51.5 * fs)], fs)
    assert after <= before + 1.0
    assert gains[int(30 * per_s)] > 1.0          # it did regulate the quiet speech upwards before the pause


def test_internal_floor_gate_freezes_pause_above_configured_gate(fs, gen):
    """A pause 13 LU below the speech but 20 LU above the configured gate is still frozen:
    the effective gate is max(gate_lufs, estimated floor + gate_margin_lu)."""
    x = _speech_pause_speech(gen, fs, -45.0, seed=21)                    # pause ~ -41.5 LUFS (K-weighted)
    stage = Normalizer(fs, **{**DEFAULT, "gate_lufs": -60.0})
    y, gains = _run(stage, x, 256)
    per_s = fs / 256
    in_pause = gains[int(30 * per_s):int(50 * per_s)]
    assert np.max(in_pause) - np.min(in_pause) <= 0.5
    assert loudness_lufs(y[int(50.5 * fs):int(51.5 * fs)], fs) <= loudness_lufs(y[29 * fs:30 * fs], fs) + 1.0
    m = stage.meters()
    # effective gate = max(gate_lufs, bounded floor + margin); the bounded floor is at most the
    # measured one, so the gate lies at most 8 LU above the measured floor and far above -60
    assert -40.0 <= m["effective_gate_lufs"] <= m["noise_floor_lufs"] + 8.0 + 1e-9
    assert stage.get_param("gate_margin_lu") == 8.0


def test_noise_floor_estimate_tracks_a_room_that_gets_louder(fs, gen):
    """Floor estimate: follows the pauses (within 3 LU), rises to a louder room within ~20 s
    (1 LU/s plus the 10 s window), the effective gate follows at floor + margin."""
    quiet, _ = gen.speech_like(-20.0, 30.0, fs, seed=61, noise_rms_dbfs=-55.0, talk_s=0.6, pause_s=1.5)
    loud, _ = gen.speech_like(-20.0, 30.0, fs, seed=62, noise_rms_dbfs=-45.0, talk_s=0.6, pause_s=1.5)
    floor_quiet = loudness_lufs(gen.white_noise(-55.0, 5.0, fs, seed=1), fs)     # ~ -51.9 LUFS
    floor_loud = loudness_lufs(gen.white_noise(-45.0, 5.0, fs, seed=1), fs)      # ~ -41.9 LUFS
    stage = Normalizer(fs, **{**DEFAULT, "gate_lufs": -60.0})
    x = np.concatenate([quiet, loud])
    floors, gates = [], []
    for i in range(0, len(x), 256):
        stage.process(x[i:i + 256])
        m = stage.meters()
        floors.append(m["noise_floor_lufs"])
        gates.append(m["effective_gate_lufs"])
    per_s = fs / 256
    assert abs(floors[int(20 * per_s)] - floor_quiet) <= 3.0
    assert abs(floors[int(29 * per_s)] - floor_quiet) <= 3.0
    assert floors[int(35 * per_s)] <= floor_quiet + 6.0            # rises at most 1 LU/s at first
    assert abs(floors[int(55 * per_s)] - floor_loud) <= 3.0         # tracked within 25 s
    assert gates[int(55 * per_s)] == pytest.approx(floors[int(55 * per_s)] + 8.0)


def test_quieter_speaker_is_re_referenced(fs, gen):
    """Speaker becomes 20 dB quieter (still above the gate): the relative-gate reference decays
    (2 LU/s in intervals above the measured floor) instead of freezing the gain for good. The
    recovery time is then set by the upward gain rate, not by the reference: with 2 dB/s the
    active speech is back at -18 +-2 LU within 40 s (measured ~35 s; 1 dB/s needs ~55 s,
    3 dB/s ~30 s)."""
    loud, loud_active = gen.speech_like(-4.0, 30.0, fs, seed=31, noise_rms_dbfs=-55.0, talk_s=0.6, pause_s=0.8)   # ~ -6 LUFS
    quiet, quiet_active = gen.speech_like(-24.0, 50.0, fs, seed=32, noise_rms_dbfs=-55.0, talk_s=0.6, pause_s=0.8)  # ~ -26 LUFS
    x = np.concatenate([loud, quiet])
    active = np.concatenate([loud_active, quiet_active])
    stage = Normalizer(fs, **{**DEFAULT, "rate_db_per_s": 2.0})
    out, refs = [], []
    for i in range(0, len(x), 256):
        out.append(stage.process(x[i:i + 256]))
        refs.append(stage.meters()["loudness_lufs"])
    y = np.concatenate(out)
    per_s = fs / 256

    def active_loudness(a, b):
        seg = slice(a * fs, b * fs)
        return loudness_lufs(y[seg][active[seg]], fs)
    # the reference sits ~1.5 LU under the loudness of the whole active part (it excludes the
    # decays into pauses but includes syllable dips), hence 2.5 LU tolerance on that measure
    assert abs(active_loudness(20, 30) + 18.0) <= 2.5            # loud speaker regulated down (gain ~ -12)
    assert refs[int(29 * per_s)] > -9.0                           # reference = loud speaker
    assert refs[int(50 * per_s)] < -20.0                          # decayed by > 11 LU within 20 s of the drop
    assert abs(active_loudness(60, 70) + 18.0) <= 2.5            # back on target within 40 s of the drop


def test_gain_cap_keeps_pause_15_lu_below_target(fs, gen):
    """Invariant of the dynamic cap: estimated floor + gain <= target - 15 LU at every block,
    also when max_gain_db would allow more; the regulation itself still works."""
    x, _ = gen.speech_like(-20.0, 30.0, fs, seed=71, noise_rms_dbfs=-45.0, talk_s=0.6, pause_s=1.5)
    stage = Normalizer(fs, **{**DEFAULT, "target_lufs": -10.0, "max_gain_db": 30.0, "gate_lufs": -80.0})
    gains, floors = [], []
    for i in range(0, len(x), 256):
        stage.process(x[i:i + 256])
        m = stage.meters()
        gains.append(m["gain_db"])
        floors.append(m["noise_floor_lufs"])
    gains, floors = np.array(gains), np.array(floors)
    known = floors > -150.0
    assert np.all(gains[known] <= -10.0 - 15.0 - floors[known] + 1e-9)
    assert gains[-1] > 6.0                                     # it does regulate the -22 LUFS speech upwards
    assert abs(floors[-1] - loudness_lufs(gen.white_noise(-45.0, 5.0, fs, seed=1), fs)) <= 3.0


def test_low_snr_state_with_hysteresis(fs, gen):
    """Room floor steps -50 -> -36 -> -39 -> -41 -> -50 dBFS under speech with 1.5 s pauses:
    low_snr enters below 13 LU, stays while 13 <= snr < 15 (hysteresis), leaves at 15 LU (a
    room with 15 LU distance meets Beauftragung 6.4 a and must not be flagged).
    While it is set the reference is not learnt from the room and pauses do not lift the
    gain (learning continues only from intervals >= floor + margin + 5 LU)."""
    parts = []
    for k, floor in enumerate((-50.0, -36.0, -39.0, -41.0, -50.0)):
        part, _ = gen.speech_like(-20.0, 30.0, fs, seed=80 + k, noise_rms_dbfs=floor, talk_s=0.6, pause_s=1.5)
        parts.append(part)
    x = np.concatenate(parts)
    stage = Normalizer(fs, **{**DEFAULT, "gate_lufs": -60.0})
    log = []
    for i in range(0, len(x), 256):
        stage.process(x[i:i + 256])
        m = stage.meters()
        log.append((m["low_snr"], m["snr_lu"], m["gain_db"], m["loudness_lufs"]))
    per_s = fs / 256

    def at(t):
        return log[int(t * per_s)]
    assert at(25)[0] == 0.0 and at(25)[1] > 20.0                   # quiet room: normal operation
    assert at(55)[0] == 1.0 and at(55)[1] < 13.0                   # -36 dBFS: entered (measured ~10 LU)
    assert at(85)[0] == 1.0 and 13.0 <= at(85)[1] < 15.0           # -39 dBFS: inside the hysteresis band (~14 LU)
    assert at(115)[0] == 0.0 and at(115)[1] >= 15.0                # -41 dBFS: left again (~16 LU)
    gains = np.array([row[2] for row in log])
    refs = np.array([row[3] for row in log])
    low = slice(int(50 * per_s), int(90 * per_s))
    # the gain may still follow the speech reference (learning continues from certain-speech
    # intervals), but it never exceeds what that reference asks for: pauses cannot lift it
    assert np.max(gains[low]) <= -18.0 - np.min(refs[low]) + 0.5
    assert np.max(gains[low]) - gains[int(50 * per_s)] <= 1.0
    # reference not learnt from the room: it may drift up (only the loud, certain syllables are
    # learnt now) but never sinks towards the room floor
    assert refs[int(45 * per_s)] - 1.0 <= refs[int(89 * per_s)] <= refs[int(45 * per_s)] + 2.5
    assert set(stage.meters()) >= {"low_snr", "snr_lu", "noise_floor_lufs", "effective_gate_lufs"}


def test_preroll_noise_is_not_learnt_as_reference(fs, gen):
    """Stationary noise above gate_lufs before the first word (pre-roll) must not become the
    speech reference and must not run the gain up; the first words start the regulation."""
    noise = gen.white_noise(-38.0, 10.0, fs, seed=5)                 # ~ -35 LUFS, well above the -40 gate
    speech, _ = gen.speech_like(-20.0, 30.0, fs, seed=6, noise_rms_dbfs=-38.0)
    stage = Normalizer(fs, **DEFAULT)
    _, g_pre = _run(stage, noise, 256)
    assert np.max(np.abs(g_pre)) <= 0.5                                # no regulation on noise
    assert stage.meters()["loudness_lufs"] == -150.0                   # no reference learnt
    _, g_speech = _run(stage, speech, 256)
    assert g_speech[-1] > 1.0                                          # speech is learnt and regulated upwards
    assert stage.meters()["loudness_lufs"] > -26.0                     # the reference is the speaker, not the noise
