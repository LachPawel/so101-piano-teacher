// node --test web/tests
import assert from "node:assert/strict";
import { test } from "node:test";

import { bandNote, coachIdle, coachStep } from "../js/chimera.js";

test("John sings the asked note with the arm in get ready: as a key sounds, once, never in your turn", () => {
  const press = { note: 64, t: 10 };
  // the arm is still on its way: wait
  assert.deepEqual(coachStep(null, { press: null }, { phase: "get_ready", asked: "mi", press: null }), { sing: null, sungFor: null });
  // the key sounds: sing it, even while the key stays down
  assert.deepEqual(coachStep(null, { press: null }, { phase: "get_ready", asked: "mi", press, keys_down: [64] }), { sing: "mi", sungFor: "mi" });
  // once only
  assert.deepEqual(coachStep("mi", { press }, { phase: "get_ready", asked: "mi", press }), { sing: null, sungFor: "mi" });
  // the arm hit a wrong key: he still shows the note that was asked
  assert.equal(coachStep(null, { press: null }, { phase: "get_ready", asked: "mi", press: { note: 65, t: 11 } }).sing, "mi");
  // an old press from before does not count
  assert.equal(coachStep(null, { press }, { phase: "get_ready", asked: "sol", press }).sing, null);
  // your turn: quiet, and he forgets, so the same note's next get ready is sung again
  assert.deepEqual(coachStep("mi", { press }, { phase: "your_turn", asked: "mi", press: { note: 64, t: 12 } }), { sing: null, sungFor: null });
  // not one of the six keys, or the arm's own turn: nothing
  assert.equal(coachStep(null, { press: null }, { phase: "get_ready", asked: "ti", press }).sing, null);
  assert.equal(coachStep(null, { press: null }, { phase: "playing", asked: "mi", press }).sing, null);
});

test("between notes John says something", () => {
  assert.match(coachIdle({ phase: "playing" }), /robot is playing/);
  assert.match(coachIdle({ phase: "your_turn" }), /Your turn now/);
  assert.match(coachIdle({ phase: "feedback" }, "ok"), /proud/);
  assert.match(coachIdle({ phase: "feedback" }, "none"), /heard nothing/);
  assert.equal(coachIdle({ phase: "get_ready" }), null);
});

test("in band mode John sings each key the arm presses, once", () => {
  const press = { note: 64, t: 10 };
  assert.equal(bandNote({ press: null }, { mode: "band", press }).word, "mi");
  assert.equal(bandNote({ press }, { mode: "band", press }), null); // the same press again
  assert.equal(bandNote({ press: null }, { mode: "", press }), null); // not band mode
  assert.equal(bandNote({ press: null }, { mode: "band", press: { note: 61, t: 11 } }), null); // not one of the six
});
