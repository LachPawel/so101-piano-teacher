// node --test web/tests
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  centsFromTarget,
  detectPitch,
  downsample,
  judgePitch,
  midiHz,
  noteName,
  steadyPitch,
} from "../js/pitch.js";

/** A held note with harmonics, like a voice, as one analyser window. */
function tone(hz, rate = 48000, seconds = 0.17) {
  const out = new Float32Array(Math.floor(rate * seconds));
  for (let i = 0; i < out.length; i++) {
    const t = i / rate;
    let sample = 0;
    for (let k = 1; k <= 4; k++) sample += Math.sin(2 * Math.PI * hz * k * t) / k;
    out[i] = 0.2 * sample;
  }
  return out;
}

const centsOff = (heard, wanted) => Math.abs(1200 * Math.log2(heard / wanted));

test("finds the pitch of low and high notes at browser sample rates", () => {
  for (const rate of [44100, 48000]) {
    for (const hz of [82.41, 110, 196, 261.63, 329.63, 440, 660]) {
      const heard = detectPitch(tone(hz, rate), rate);
      assert.ok(heard != null, `${hz} Hz at ${rate}: nothing heard`);
      assert.ok(centsOff(heard, hz) < 8, `${hz} Hz at ${rate}: heard ${heard.toFixed(2)}`);
    }
  }
});

test("hears nothing in silence or noise", () => {
  assert.equal(detectPitch(new Float32Array(8192), 48000), null);
  let seed = 1;
  const noise = Float32Array.from({ length: 8192 }, () => {
    seed = (seed * 16807) % 2147483647;
    return 0.1 * (seed / 2147483647 - 0.5);
  });
  assert.equal(detectPitch(noise, 48000), null);
});

test("judges flat, in tune and sharp in any octave", () => {
  assert.equal(judgePitch(261.63, 60).verdict, "in_tune");
  assert.equal(judgePitch(130.8, 60).verdict, "in_tune"); // the same note an octave down
  const flat = judgePitch(250, 60);
  assert.equal(flat.verdict, "flat");
  assert.ok(Math.abs(flat.cents + 78.7) < 1);
  assert.equal(flat.text, "A little higher.");
  assert.equal(judgePitch(275, 60).verdict, "sharp");
  assert.equal(judgePitch(null, 60).verdict, "none");
  assert.ok(Math.abs(centsFromTarget(midiHz(64 - 12 + 0.3), 64) - 30) < 0.01);
});

test("a sung note 70 cents flat reads as flat from a real window", () => {
  const rate = 48000;
  const heard = detectPitch(tone(midiHz(64 - 12 - 0.7), rate), rate);
  const result = judgePitch(heard, 64);
  assert.equal(result.verdict, "flat");
  assert.ok(Math.abs(result.cents + 70) < 8);
});

test("helpers", () => {
  assert.equal(noteName(60), "C4");
  assert.equal(noteName(69), "A4");
  assert.equal(noteName(56), "G#3");
  const [half, rate] = downsample(new Float32Array(4800).fill(1), 48000);
  assert.equal(rate, 16000);
  assert.equal(half.length, 1600);
  assert.equal(steadyPitch([null, 200, null, 220, 210]), 210);
  assert.equal(steadyPitch([null, 200, null]), null);
});
