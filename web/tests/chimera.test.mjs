// node --test web/tests
import assert from "node:assert/strict";
import { test } from "node:test";

import { coachIdle, coachStep } from "../js/chimera.js";

test("the coach sings the asked note once a turn, after the piano is quiet", () => {
  // the key is still down: wait
  assert.deepEqual(coachStep(null, { phase: "your_turn", asked: "mi", keys_down: [64] }), { sing: null, sungFor: null });
  // the key is up: sing it
  assert.deepEqual(coachStep(null, { phase: "your_turn", asked: "mi", keys_down: [] }), { sing: "mi", sungFor: "mi" });
  // get ready, then your turn, on the same note: once only
  assert.deepEqual(coachStep(null, { phase: "get_ready", asked: "sol", keys_down: [] }), { sing: "sol", sungFor: "sol" });
  assert.deepEqual(coachStep("sol", { phase: "your_turn", asked: "sol", keys_down: [] }), { sing: null, sungFor: "sol" });
  // a new note in the same turn phase: sing that one
  assert.equal(coachStep("sol", { phase: "your_turn", asked: "la", keys_down: [] }).sing, "la");
  // the turn is over: it forgets, so the same note next time is sung again
  assert.deepEqual(coachStep("mi", { phase: "feedback", asked: "mi", keys_down: [] }), { sing: null, sungFor: null });
  assert.equal(coachStep(null, { phase: "your_turn", asked: "mi", keys_down: [] }).sing, "mi");
  // not one of the six keys, or the arm's turn: nothing
  assert.equal(coachStep(null, { phase: "your_turn", asked: "ti", keys_down: [] }).sing, null);
  assert.equal(coachStep(null, { phase: "playing", asked: "mi", keys_down: [] }).sing, null);
});

test("between notes the coach says something, and keeps quiet in your turn", () => {
  assert.match(coachIdle({ phase: "playing" }), /robot is playing/);
  assert.match(coachIdle({ phase: "feedback" }, "ok"), /proud/);
  assert.match(coachIdle({ phase: "feedback" }, "none"), /heard nothing/);
  assert.equal(coachIdle({ phase: "your_turn" }), null);
});
