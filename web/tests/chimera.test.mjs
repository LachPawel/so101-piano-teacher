// node --test web/tests
import assert from "node:assert/strict";
import { test } from "node:test";

import { coachIdle, coachStep } from "../js/chimera.js";

test("John sings the asked note once in get ready, after the piano is quiet, never in your turn", () => {
  // the key is still down: wait
  assert.deepEqual(coachStep(null, { phase: "get_ready", asked: "mi", keys_down: [64] }), { sing: null, sungFor: null });
  // the key is up: sing it
  assert.deepEqual(coachStep(null, { phase: "get_ready", asked: "mi", keys_down: [] }), { sing: "mi", sungFor: "mi" });
  // once only
  assert.deepEqual(coachStep("mi", { phase: "get_ready", asked: "mi", keys_down: [] }), { sing: null, sungFor: "mi" });
  // your turn: quiet, and he forgets, so the same note's next get ready is sung again
  assert.deepEqual(coachStep("mi", { phase: "your_turn", asked: "mi", keys_down: [] }), { sing: null, sungFor: null });
  assert.equal(coachStep(null, { phase: "get_ready", asked: "mi", keys_down: [] }).sing, "mi");
  // without beginner mode there is no get ready: he never sings
  assert.equal(coachStep(null, { phase: "your_turn", asked: "sol", keys_down: [] }).sing, null);
  // not one of the six keys, or the arm's turn: nothing
  assert.equal(coachStep(null, { phase: "get_ready", asked: "ti", keys_down: [] }).sing, null);
  assert.equal(coachStep(null, { phase: "playing", asked: "mi", keys_down: [] }).sing, null);
});

test("between notes John says something", () => {
  assert.match(coachIdle({ phase: "playing" }), /robot is playing/);
  assert.match(coachIdle({ phase: "your_turn" }), /Your turn now/);
  assert.match(coachIdle({ phase: "feedback" }, "ok"), /proud/);
  assert.match(coachIdle({ phase: "feedback" }, "none"), /heard nothing/);
  assert.equal(coachIdle({ phase: "get_ready" }), null);
});
