// node --test web/tests
import assert from "node:assert/strict";
import { test } from "node:test";

import { centsWords, headline, livePitch, verdict } from "../js/live.js";

test("the big words say whose turn it is", () => {
  assert.equal(headline({ phase: "idle" }).html, "Make a little<br>music.");
  assert.deepEqual(headline({ phase: "playing", asked: "mi" }), { html: "The arm<br>plays <em>mi</em>.", tone: "arm" });
  assert.equal(headline({ phase: "get_ready", asked: "sol" }).tone, "ready");
  assert.deepEqual(headline({ phase: "your_turn", asked: "la" }), { html: "Sing <em>la</em><br>now.", tone: "sing" });
  assert.equal(headline({ phase: "feedback", asked: "la", sung: { cents: -30 } }).html, "In tune.<br>Well done.");
  assert.equal(headline({ phase: "feedback", asked: "la", sung: { cents: -130 } }).html, "A little<br>higher.");
  assert.equal(headline({ phase: "feedback", asked: "la", sung: { cents: null } }).tone, "none");
  // A note name from the rig never becomes markup.
  assert.ok(!headline({ phase: "playing", asked: "<b>x</b>" }).html.includes("<b>"));
});

test("cents in words, with the teacher's tolerance", () => {
  assert.equal(centsWords(0.2), "spot on");
  assert.equal(centsWords(-20), "20 cents under, close enough");
  assert.equal(centsWords(120), "120 cents too high");
  assert.equal(verdict(49).tone, "ok");
  assert.equal(verdict(-51).word, "A little higher.");
  assert.equal(verdict(null).word, "Not heard.");
});

test("only a fresh reading counts as the pitch right now", () => {
  const points = [[1.0, -40, 0.05], [1.05, null, 0.0], [1.1, 12, 0.05]];
  assert.equal(livePitch(points, 1.2), 12);
  assert.equal(livePitch(points, 2.0), null);
  assert.equal(livePitch([], 1), null);
});
