// A small piano made of sine partials that die away, and the microphone reader for the pitch meter.

import { detectPitch, midiHz } from "./pitch.js";

let context = null;
let master = null;

/** Browsers only allow sound after a click or key press, so call this from one. */
export function wakeAudio() {
  if (!context) {
    context = new (window.AudioContext || window.webkitAudioContext)();
    master = context.createGain();
    master.gain.value = 0.5;
    master.connect(context.destination);
  }
  if (context.state === "suspended") context.resume();
  return context;
}

/** Play one piano-like note. `velocity` is 1 to 127, as on a MIDI keyboard. */
export function playNote(midi, velocity = 90, seconds = 1.4) {
  const ctx = wakeAudio();
  const now = ctx.currentTime;
  const loudness = 0.25 * (velocity / 127) ** 1.5;
  const envelope = ctx.createGain();
  envelope.gain.setValueAtTime(0, now);
  envelope.gain.linearRampToValueAtTime(loudness, now + 0.005);
  envelope.gain.exponentialRampToValueAtTime(0.0008, now + seconds);
  envelope.connect(master);
  for (let partial = 1; partial <= 6; partial++) {
    const oscillator = ctx.createOscillator();
    const gain = ctx.createGain();
    oscillator.frequency.value = midiHz(midi) * partial;
    gain.gain.value = 1 / partial ** 1.5;
    oscillator.connect(gain).connect(envelope);
    oscillator.start(now);
    oscillator.stop(now + seconds + 0.05);
  }
}

/**
 * The microphone as a stream of pitch readings. Echo cancelling and noise suppression are off:
 * they are built for speech and bend a held note.
 */
export class Microphone {
  constructor() {
    this.analyser = null;
    this.buffer = null;
    this.stream = null;
  }

  get on() {
    return this.analyser !== null;
  }

  async start() {
    if (this.on) return;
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
    });
    const ctx = wakeAudio();
    this.analyser = ctx.createAnalyser();
    this.analyser.fftSize = 8192; // 0.17 s at 48 kHz: enough periods of a low voice
    this.buffer = new Float32Array(this.analyser.fftSize);
    ctx.createMediaStreamSource(this.stream).connect(this.analyser);
  }

  stop() {
    this.stream?.getTracks().forEach((track) => track.stop());
    this.analyser = this.buffer = this.stream = null;
  }

  /** The pitch right now in Hz, or null when there is no clear note. */
  read() {
    if (!this.on) return null;
    this.analyser.getFloatTimeDomainData(this.buffer);
    return detectPitch(this.buffer, this.analyser.context.sampleRate);
  }
}
