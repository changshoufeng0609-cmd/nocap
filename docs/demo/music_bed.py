#!/usr/bin/env python3
"""Original ambient music bed for the NoCap demo videos, synthesized from scratch with numpy.

No samples, loops, presets, sound fonts, models or downloads: every sound is computed here from sine-wave
wavetables and seeded noise, so the output is an original work made for this project. For a given length and
seed the output is bit-for-bit deterministic.

The bed is a slow D-major pad (Dmaj9, Bm9, Gmaj9, Aadd9; one chord every 6 s with 2.5 s cross-fades), a soft
bass on the chord roots, sparse bell tones, a synthetic room reverb, and a 70 Hz to 2.4 kHz band limit that
keeps it out of the speech-presence range. The progression is aligned so the final chord is the tonic.
`duck_gains` lowers the bed under narration; the renderer sets the overall level by measurement.

Run directly to write a preview: `python music_bed.py 30 preview.flac` (seconds, output path).
"""
from __future__ import annotations

import sys

import numpy as np

SR = 48000
SEED = 20261030
CHORD_S = 6.0
FADE_S = 2.5
TABLE_N = 4096
# Pad voicings and bass roots as MIDI note numbers (D major).
CHORDS = [
    {'name': 'Dmaj9', 'pad': [50, 57, 61, 64, 66], 'bass': 38, 'bells': [73, 74, 76, 78, 81]},
    {'name': 'Bm9', 'pad': [47, 54, 57, 61, 62], 'bass': 35, 'bells': [71, 73, 74, 78, 81]},
    {'name': 'Gmaj9', 'pad': [43, 50, 54, 57, 59], 'bass': 43, 'bells': [69, 71, 74, 78, 79]},
    {'name': 'Aadd9', 'pad': [45, 52, 59, 61, 64], 'bass': 45, 'bells': [69, 71, 73, 76, 81]},
]
DETUNE_CENTS = (-7.0, 0.0, 7.0)
PANS = (-0.6, 0.0, 0.6)
BAND = (70.0, 2400.0)         # high-pass and low-pass corners, Hz
REVERB = {'tau_s': 0.42, 'length_s': 3.0, 'predelay_s': 0.025, 'wet': 0.30}
DUCK = {'depth_db': -7.0, 'pre_s': 0.25, 'post_s': 0.35, 'attack_s': 0.12, 'release_s': 0.70}
FADE_IN_S, FADE_OUT_S = 1.5, 2.5


def hz(midi):
    return 440.0 * 2.0 ** ((midi - 69) / 12.0)


def _table(harmonics, rolloff):
    phase = np.arange(TABLE_N) / TABLE_N
    table = sum(np.sin(2 * np.pi * h * phase) / h ** rolloff for h in range(1, harmonics + 1))
    table = table / np.abs(table).max()
    return np.append(table, table[0])


DARK, BRIGHT = _table(4, 2.0), _table(8, 1.45)


def _lookup(table, phase):
    position = (phase % 1.0) * TABLE_N
    return np.interp(position, np.arange(TABLE_N + 1), table)


def _pan(mono, pan):
    angle = (pan + 1) * np.pi / 4
    return np.stack([mono * np.cos(angle), mono * np.sin(angle)], axis=1)


def _voice(freq, n, rng, bright):
    """One detuned pad voice: phase accumulation with slow vibrato, dark/bright wavetable cross-fade."""
    out = np.zeros((n, 2))
    t = np.arange(n) / SR
    for cents, pan in zip(DETUNE_CENTS, PANS):
        rate = rng.uniform(0.07, 0.19)
        vibrato = 1.0 + 0.0011 * np.sin(2 * np.pi * rate * t + rng.uniform(0, 2 * np.pi))
        step = freq * 2.0 ** (cents / 1200.0) * vibrato / SR
        phase = rng.uniform(0, 1) + np.cumsum(step)
        wave = (1 - bright) * _lookup(DARK, phase) + bright * _lookup(BRIGHT, phase)
        out += _pan(wave, pan)
    return out / len(DETUNE_CENTS)


def _envelope(n, fade):
    """Equal-power rise and fall over `fade` samples at each end."""
    env = np.ones(n)
    ramp = np.sin(np.linspace(0, np.pi / 2, fade)) ** 2
    env[:fade] = ramp
    env[-fade:] = ramp[::-1]
    return env


def _bell(freq, rng):
    n = int(3.0 * SR)
    t = np.arange(n) / SR
    tone = np.sin(2 * np.pi * freq * t) + 0.22 * np.sin(2 * np.pi * freq * 2.756 * t) * np.exp(-t / 0.35)
    attack = np.minimum(1.0, t / 0.012)
    return _pan(tone * attack * np.exp(-t / 0.9), rng.uniform(-0.45, 0.45))


def _band_limit(signal):
    """Zero-phase band limit (2nd-order magnitude responses) applied in the frequency domain."""
    n = len(signal)
    size = 1 << (n - 1).bit_length()
    freqs = np.fft.rfftfreq(size, 1 / SR)
    low, high = BAND
    with np.errstate(divide='ignore', invalid='ignore'):
        hp = np.where(freqs > 0, (freqs / low) ** 2 / np.sqrt(1 + (freqs / low) ** 4), 0.0)
    lp = 1 / np.sqrt(1 + (freqs / high) ** 4)
    out = np.empty_like(signal)
    for ch in range(signal.shape[1]):
        out[:, ch] = np.fft.irfft(np.fft.rfft(signal[:, ch], size) * hp * lp, size)[:n]
    return out


def _reverb(signal, rng):
    """Convolution with a synthetic, seeded room response (decaying filtered noise, decorrelated L/R)."""
    length = int(REVERB['length_s'] * SR)
    t = np.arange(length) / SR
    pre = int(REVERB['predelay_s'] * SR)
    block = 1 << 17
    size = 1 << (block + length - 1).bit_length()
    wet = np.zeros((len(signal) + length, 2))
    for ch in range(2):
        ir = rng.standard_normal(length) * np.exp(-t / REVERB['tau_s'])
        ir = np.convolve(ir, np.ones(24) / 24, mode='same')      # soften the noise
        ir[:pre] = 0
        ir /= np.sqrt(np.sum(ir ** 2))
        spectrum = np.fft.rfft(ir, size)
        for start in range(0, len(signal), block):
            chunk = signal[start:start + block, ch]
            piece = np.fft.irfft(np.fft.rfft(chunk, size) * spectrum, size)[:len(chunk) + length - 1]
            wet[start:start + len(piece), ch] += piece
    return wet[:len(signal)]


def synthesize(duration_s, seed=SEED):
    """Return (samples, info): float32 stereo at 48 kHz, peak-normalized to -6 dBFS before level setting."""
    rng = np.random.default_rng(seed)
    n = int(round(duration_s * SR))
    # Slot k sounds from k * CHORD_S - FADE_S / 2 to (k + 1) * CHORD_S + FADE_S / 2. Use the fewest slots whose
    # last one still covers the final sample; one more would start after the end and never be heard.
    slots = max(1, int((duration_s - FADE_S / 2) // CHORD_S) + 1)
    assert (slots - 1) * CHORD_S - FADE_S / 2 < duration_s <= slots * CHORD_S + FADE_S / 2, (duration_s, slots)
    offset = (-(slots - 1)) % len(CHORDS)         # the last slot is the tonic (Dmaj9)
    fade = int(FADE_S * SR)
    pad = np.zeros((n + int((CHORD_S + FADE_S) * SR), 2))
    bass = np.zeros_like(pad)
    bells = np.zeros_like(pad)
    t_all = np.arange(len(pad)) / SR
    brightness = 0.35 + 0.22 * np.sin(2 * np.pi * t_all / 37.0)
    plan = []
    for slot in range(slots):
        chord = CHORDS[(slot + offset) % len(CHORDS)]
        start = int(max(0.0, slot * CHORD_S - FADE_S / 2) * SR)
        end = min(len(pad), int((slot + 1) * CHORD_S * SR + FADE_S / 2 * SR))
        length = end - start
        env = _envelope(length, min(fade, length // 2))[:, None]
        bright = brightness[start:end][:, None]
        voices = sum(_voice(hz(m), length, rng, bright[:, 0])[:, :] * (0.76 + 0.06 * i) for i, m in enumerate(chord['pad']))
        pad[start:end] += voices * env / len(chord['pad'])
        tb = np.arange(length) / SR
        root = hz(chord['bass'])
        bass[start:end] += _pan(np.sin(2 * np.pi * root * tb + rng.uniform(0, 6.28)) + 0.25 * np.sin(4 * np.pi * root * tb), 0.0) * env
        beat = 60.0 / 84.0 * 2
        for j in range(int(CHORD_S / beat)):
            if rng.uniform() < 0.62:
                at = int((slot * CHORD_S + j * beat + rng.uniform(0, 0.08)) * SR)
                tone = _bell(hz(rng.choice(chord['bells'])), rng)
                stop = min(len(bells), at + len(tone))
                if at < stop:
                    bells[at:stop] += tone[:stop - at]
        plan.append({'slot': slot, 'chord': chord['name'], 'start_s': round(slot * CHORD_S, 3)})
    dry = pad * 1.0 + bass * 0.16 + bells * 0.075
    dry = dry[:n]
    mix = (1 - REVERB['wet']) * dry + REVERB['wet'] * _reverb(dry, rng)
    mix = _band_limit(mix)
    t = np.arange(n) / SR
    fades = np.minimum(1.0, t / FADE_IN_S) * np.clip((duration_s - t) / FADE_OUT_S, 0.0, 1.0)
    mix *= (np.sin(fades * np.pi / 2) ** 2)[:, None]
    mix *= 10 ** (-6 / 20) / max(1e-9, np.abs(mix).max())
    info = {'generator': 'docs/demo/music_bed.py', 'seed': seed, 'sample_rate': SR, 'duration_s': round(duration_s, 3),
            'key': 'D major', 'chord_seconds': CHORD_S, 'crossfade_seconds': FADE_S,
            'progression': [c['name'] for c in CHORDS], 'final_chord': plan[-1]['chord'] if plan else None,
            'band_hz': list(BAND), 'reverb': REVERB, 'fade_in_s': FADE_IN_S, 'fade_out_s': FADE_OUT_S,
            'sources': 'sine-wave wavetables and seeded noise computed in this file; no samples, loops, presets or downloads'}
    return mix.astype(np.float32), info


def duck_gains(spans, duration_s, depth_db=DUCK['depth_db']):
    """Per-sample gain: `depth_db` while narration plays (with pre-roll and hang), smoothed attack and release."""
    rate = 200
    steps = int(np.ceil(duration_s * rate)) + 1
    target = np.zeros(steps)
    for start, end in spans:
        a = max(0, int((start - DUCK['pre_s']) * rate))
        b = min(steps, int(np.ceil((end + DUCK['post_s']) * rate)))
        target[a:b] = depth_db
    smoothed = np.empty(steps)
    level = 0.0
    down = 1 - np.exp(-1 / (DUCK['attack_s'] * rate))
    up = 1 - np.exp(-1 / (DUCK['release_s'] * rate))
    for i, goal in enumerate(target):
        level += (goal - level) * (down if goal < level else up)
        smoothed[i] = level
    n = int(round(duration_s * SR))
    gain_db = np.interp(np.arange(n) / SR, np.arange(steps) / rate, smoothed)
    return (10 ** (gain_db / 20)).astype(np.float32)


if __name__ == '__main__':
    import soundfile as sf
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    samples, meta = synthesize(seconds)
    sf.write(sys.argv[2] if len(sys.argv) > 2 else 'music-bed-preview.flac', samples, SR, subtype='PCM_16')
    print(meta)
