// The singing lesson: the arm plays a note, you sing it back, you hear higher, lower or well done.
// The same loop as `singing` in scripts/keys_companion.py. It knows nothing about the page: the
// caller passes in how to play a note, how to listen and where to show what is happening.

import { judgePitch, steadyPitch } from "./pitch.js";
import { keyByNumber } from "./songs.js";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * @param {number[]} keys          key numbers 1 to 6, in lesson order
 * @param {object}   io
 * @param {(key: number) => Promise<void>} io.play        resolves once the note has sounded
 * @param {() => number|null} io.read                     the pitch right now in Hz, or null
 * @param {(step: object) => void} io.show                called at every change of state
 * @param {() => boolean} io.stopped                      true once the user stopped the lesson
 * @param {object} [timing]        milliseconds; the tests shorten these
 * @returns {Promise<object[]>}    one record per attempt
 */
export async function runLesson(keys, io, timing = {}) {
  const { ringOut = 500, listen = 2000, every = 80, pause = 1200, tries = 3 } = timing;
  const report = [];
  for (const number of keys) {
    const key = keyByNumber(number);
    for (let attempt = 1; attempt <= tries; attempt++) {
      if (io.stopped()) return report;
      io.show({ phase: "play", key, attempt });
      await io.play(number);
      await sleep(ringOut); // let the piano note die away before listening
      if (io.stopped()) return report;

      io.show({ phase: "listen", key, attempt });
      const readings = [];
      for (let elapsed = 0; elapsed < listen && !io.stopped(); elapsed += every) {
        readings.push(io.read());
        await sleep(every);
      }
      const hz = steadyPitch(readings);
      const result = judgePitch(hz, key.midi);
      report.push({ key: number, attempt, hz, ...result });
      io.show({ phase: "result", key, attempt, hz, ...result });
      await sleep(pause);
      if (result.verdict === "in_tune") break;
    }
  }
  if (!io.stopped()) io.show({ phase: "done", report });
  return report;
}
