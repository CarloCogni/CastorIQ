// eastereggs/castor-slug/audio.js
// Inline Web Audio synth — generates 8-bit-style SFX at runtime via
// OscillatorNode + GainNode envelopes. No asset files, no external deps.
//
// Default-muted (per CLAUDE.md "respect the user"). M toggles mute, state
// persisted to localStorage.
//
// Usage:
//     import { createAudio } from "./audio.js";
//     const audio = createAudio();
//     audio.play("shoot");
//     audio.toggleMute();
//
// Adding a preset: extend PRESETS with { freq, end, type, duration, gain }.

const MUTE_KEY = "eastereggs_castor_slug_muted";
const DEFAULT_MUTED = true;

// Preset format:
//   freq:     start frequency in Hz (or array of [hz, ...] for arpeggio)
//   end:      end frequency for sweep (omit for constant)
//   type:     "sine" | "square" | "triangle" | "sawtooth"
//   duration: total duration in seconds
//   gain:     peak gain (0..1); kept low so concurrent sounds don't clip
//   attack:   attack time in seconds (default 0.005)
//   decay:    after attack, linear ramp down (default duration - attack)
const PRESETS = {
    shoot:        { freq: 880,  end: 660,  type: "square",   duration: 0.06, gain: 0.06 },
    chargedShot:  { freq: 220,  end: 1760, type: "sawtooth", duration: 0.20, gain: 0.10 },
    jump:         { freq: 440,  end: 660,  type: "square",   duration: 0.12, gain: 0.06 },
    land:         { freq: 80,              type: "triangle", duration: 0.05, gain: 0.10 },
    dash:         { freq: 660,  end: 1100, type: "triangle", duration: 0.10, gain: 0.07 },
    hit:          { freq: 110,  end: 55,   type: "square",   duration: 0.20, gain: 0.10 },
    enemyDeath:   { freq: 660,  end: 220,  type: "square",   duration: 0.15, gain: 0.07 },
    tokenPickup:  { freq: 880,  end: 1320, type: "square",   duration: 0.10, gain: 0.06 },
    powerup:      { freq: [523, 659, 784, 1047], type: "triangle", duration: 0.32, gain: 0.07 },
    combo:        { freq: 1320,            type: "triangle", duration: 0.08, gain: 0.07 },
    bossHit:      { freq: 220,  end: 110,  type: "sawtooth", duration: 0.18, gain: 0.10 },
    merged:       { freq: [523, 659, 784, 1047, 1319], type: "square", duration: 0.50, gain: 0.08 },
    gameover:     { freq: [523, 440, 349, 294], type: "sawtooth", duration: 0.60, gain: 0.10 },
    menuSelect:   { freq: 1047, end: 1568, type: "square",   duration: 0.10, gain: 0.06 },
};

export function createAudio() {

    let ctx = null;
    let muted = readMutedState();

    function readMutedState() {
        try {
            const raw = localStorage.getItem(MUTE_KEY);
            if (raw === null) return DEFAULT_MUTED;
            return raw === "1" || raw === "true";
        } catch (_e) {
            return DEFAULT_MUTED;
        }
    }

    function writeMutedState() {
        try {
            localStorage.setItem(MUTE_KEY, muted ? "1" : "0");
        } catch (_e) {
            // localStorage disabled — silently ignore.
        }
    }

    function ensureContext() {
        if (ctx) return ctx;
        const C = window.AudioContext || window.webkitAudioContext;
        if (!C) return null;
        try {
            ctx = new C();
        } catch (_e) {
            ctx = null;
        }
        return ctx;
    }

    // Play a single tone with optional sweep + envelope.
    function playTone({ freq, end, type, duration, gain, attack = 0.005, startAt = 0 }) {
        const c = ensureContext();
        if (!c) return;
        const t0 = c.currentTime + startAt;
        const osc = c.createOscillator();
        const env = c.createGain();
        osc.type = type;
        osc.frequency.setValueAtTime(freq, t0);
        if (typeof end === "number" && end !== freq) {
            osc.frequency.linearRampToValueAtTime(end, t0 + duration);
        }
        env.gain.setValueAtTime(0, t0);
        env.gain.linearRampToValueAtTime(gain, t0 + attack);
        env.gain.linearRampToValueAtTime(0, t0 + duration);
        osc.connect(env);
        env.connect(c.destination);
        osc.start(t0);
        osc.stop(t0 + duration + 0.02);
    }

    // Play an arpeggio (multiple sequential tones from an array of freqs).
    function playArpeggio(preset) {
        const freqs = preset.freq;
        const stepDuration = preset.duration / freqs.length;
        freqs.forEach((freq, i) => {
            playTone({
                freq,
                end: freq,
                type: preset.type,
                duration: stepDuration,
                gain: preset.gain,
                startAt: i * stepDuration,
            });
        });
    }

    function play(name) {
        if (muted) return;
        const preset = PRESETS[name];
        if (!preset) return;
        if (Array.isArray(preset.freq)) {
            playArpeggio(preset);
        } else {
            playTone(preset);
        }
    }

    function toggleMute() {
        muted = !muted;
        writeMutedState();
        // Browsers require a user gesture to start the AudioContext. Toggling
        // unmute is itself a gesture, so kick the context here so the next
        // play() works without a "not started" warning.
        if (!muted) {
            const c = ensureContext();
            if (c && c.state === "suspended") c.resume();
        }
        return muted;
    }

    function isMuted() { return muted; }

    return { play, toggleMute, isMuted };
}
