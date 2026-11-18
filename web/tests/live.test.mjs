// node --test web/tests
import assert from "node:assert/strict";
import { test } from "node:test";

import { centsWords, foldTo, headline, livePitch, verdict } from "../js/live.js";

test("the big words say whose turn it is", () => {
  assert.equal(headline({ phase: "idle" }).html, "Make a little<br>music.");
  assert.deepEqual(headline({ phase: "playing", asked: "mi" }), { html: "The arm<br>plays <em>mi</em>.", tone: "arm" });
  assert.equal(headline({ phase: "get_ready", asked: "sol" }).tone, "ready");
  assert.equal(headline({ phase: "handover", asked: "sol" }).html, "Your turn:<br>sing <em>sol</em>.");
  assert.equal(headline({ phase: "playing", asked: "mi", mode: "band" }).html, "Band mode.<br>Play along.");
  assert.deepEqual(headline({ phase: "your_turn", asked: "la" }), { html: "Your turn:<br>sing <em>la</em>.", tone: "sing" });
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
  // In tune on average but never held for a second: not good enough.
  assert.deepEqual(verdict(10, false), { tone: "off", word: "Hold it longer.", html: "Hold it.<br>A full second." });
  assert.equal(verdict(10, true).tone, "ok");
  assert.equal(verdict(-120, false).word, "A little higher.");
});

test("only a fresh reading counts as the pitch right now", () => {
  const readings = [[1.0, 63.6, 0.05, 0], [1.05, null, 0.0, 0], [1.1, 64.12, 0.05, 1]];
  assert.deepEqual(livePitch(readings, 1.2), { midi: 64.12, piano: true });
  assert.equal(livePitch(readings, 2.0), null);
  assert.equal(livePitch([[1.0, null, 0, 0]], 1.0), null);
  assert.equal(livePitch([], 1), null);
});

test("a note sung an octave down still counts against the note asked for", () => {
  assert.equal(foldTo(52.2, 64), 64.2);
  assert.equal(foldTo(76, 64), 64);
  assert.equal(foldTo(63.7, 64), 63.7);
});
