// Pitch of a sung or played note, and how far it is from a target.
// A port of sung_pitch and judge_pitch in scripts/keys_companion.py, so the web page and the
// Python teacher agree. Pure functions: no browser APIs, so the tests run in Node.

export const IN_TUNE_CENTS = 50; // half a semitone either way counts as in tune
const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

export function midiHz(midi) {
  return 440 * 2 ** ((midi - 69) / 12);
}

export function hzToMidi(hz) {
  return 69 + 12 * Math.log2(hz / 440);
}

export function noteName(midi) {
  const rounded = Math.round(midi);
  return `${NOTE_NAMES[((rounded % 12) + 12) % 12]}${Math.floor(rounded / 12) - 1}`;
}

/** Average neighbouring samples so the result is close to `target` Hz. Returns [samples, rate]. */
export function downsample(samples, rate, target = 16000) {
  const factor = Math.max(1, Math.floor(rate / target));
  if (factor === 1) return [samples, rate];
  const out = new Float32Array(Math.floor(samples.length / factor));
  for (let i = 0; i < out.length; i++) {
    let sum = 0;
    for (let j = 0; j < factor; j++) sum += samples[i * factor + j];
    out[i] = sum / factor;
  }
  return [out, rate / factor];
}

/**
 * The pitch of one short window of sound in Hz, or null for silence, breath or noise.
 * Autocorrelation: the first strong peak, not the strongest, because later peaks are the same
 * note an octave or more down.
 */
export function detectPitch(input, inputRate, lo = 70, hi = 800) {
  const [samples, rate] = downsample(input, inputRate);
  const n = samples.length;
  const maxLag = Math.min(Math.ceil(rate / lo), n - 2);
  const minLag = Math.max(2, Math.floor(rate / hi));
  if (maxLag <= minLag) return null;

  let mean = 0;
  for (let i = 0; i < n; i++) mean += samples[i];
  mean /= n;
  const frame = new Float32Array(n);
  let energy = 0;
  for (let i = 0; i < n; i++) {
    const centered = samples[i] - mean;
    energy += centered * centered;
    frame[i] = centered * (0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (n - 1)));
  }
  if (Math.sqrt(energy / n) < 0.005) return null; // too quiet

  const at = (lag) => {
    let sum = 0;
    for (let i = 0; i + lag < n; i++) sum += frame[i] * frame[i + lag];
    return sum;
  };
  const zero = at(0) + 1e-12;
  const corr = new Float32Array(maxLag + 2);
  let best = -Infinity;
  for (let lag = minLag - 1; lag <= maxLag + 1; lag++) {
    corr[lag] = at(lag) / zero;
    if (lag >= minLag && lag <= maxLag && corr[lag] > best) best = corr[lag];
  }
  if (best < 0.3) return null; // no clear period

  let lag = minLag;
  while (lag <= maxLag && corr[lag] < 0.85 * best) lag++;
  while (lag < maxLag && corr[lag + 1] > corr[lag]) lag++;
  const [a, b, c] = [corr[lag - 1], corr[lag], corr[lag + 1]];
  const curve = a - 2 * b + c;
  const shift = curve ? (0.5 * (a - c)) / curve : 0;
  return rate / (lag + shift);
}

/** Cents above (+) or below (-) the target, in whichever octave the note was sung. */
export function centsFromTarget(hz, targetMidi) {
  const cents = 1200 * Math.log2(hz / midiHz(targetMidi));
  return ((((cents + 600) % 1200) + 1200) % 1200) - 600;
}

/** The verdict a teacher would give. `hz` is null when nothing was heard. */
export function judgePitch(hz, targetMidi) {
  if (hz == null) return { cents: null, verdict: "none", text: "I did not hear you. Sing la." };
  const cents = centsFromTarget(hz, targetMidi);
  if (Math.abs(cents) <= IN_TUNE_CENTS) return { cents, verdict: "in_tune", text: "Well done." };
  return cents < 0
    ? { cents, verdict: "flat", text: "A little higher." }
    : { cents, verdict: "sharp", text: "A little lower." };
}

/** The middle value of the readings that heard a note, or null when too few did. */
export function steadyPitch(readings, minimum = 3) {
  const heard = readings.filter((hz) => hz != null).sort((a, b) => a - b);
  if (heard.length < minimum) return null;
  return heard[Math.floor(heard.length / 2)];
}
