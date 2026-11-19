// John, the singing head (experiment, beginner mode): a 3D head and throat that sings each note first.
//
// For a beginner each note goes: the rig says "get_ready", John (chimera/teacher.html, a build of
// github.com/jamesondh/chimera-vocal-synth, MIT) appears half see-through over the pitch lane, the arm
// plays the note and he sings it on its name, do re mi fa sol la, as the key sounds, his pitch drawn on
// the lane. Then he fades away, the arm plays the note again and it's your turn, with only the lane. He runs in an iframe
// and is told what to do with postMessage. Off with ?chimera=0.
// In band mode (state.mode "band": the arm loops a riff, you play over it) he stays on screen and sings
// each note the arm presses, as the backing vocal.

import { KEYS } from "./songs.js";

const BY_WORD = Object.fromEntries(KEYS.map((entry) => [entry.word, entry]));
const SING = new Set(["get_ready"]);
const SHOWN = new Set(["get_ready"]);
export const SING_SECONDS = 1.6;
export const SLIDE_SECONDS = 0.7; // a slide from where you were to the note takes this much longer
export const BAND_SECONDS = 0.5; // band mode: John's backing note for each key the arm presses

const ALONG = new Set(["band", "warmup"]); // John sings along with the arm, key by key

/** Band mode and the warm-up: the note John sings for a key press the page has not seen yet, or null. */
export function bandNote(was, now) {
  if (!ALONG.has(now.mode) || !now.press || now.press.t === was?.press?.t) return null;
  return KEYS.find((entry) => entry.midi % 12 === now.press.note % 12) ?? null;
}

/**
 * What John does for one state of the rig. `sungFor` is the note he already sang in this "get ready"
 * (null after it). Returns { sing: the note to sing now or null, sungFor }. In get_ready (a beginner's
 * note) the arm plays the note and he sings it with the arm: the moment a key sounds, once. Whatever key
 * the arm hit, he sings the note that was asked. Never in your turn, where the microphone would hear him.
 */
export function coachStep(sungFor, was, now) {
  if (!SING.has(now.phase) || !now.asked || !BY_WORD[now.asked]) return { sing: null, sungFor: null };
  const struck = !!now.press && now.press.t !== was?.press?.t;
  if (sungFor === now.asked || !struck) return { sing: null, sungFor };
  return { sing: now.asked, sungFor: now.asked };
}

/** What the coach says between notes, for one phase (null: say nothing new). */
export function coachIdle(state, tone) {
  switch (state.phase) {
    case "playing":
      return "Shh. The robot is playing.";
    case "your_turn":
      return "Your turn now. I'll be quiet. I'm good at that, I have no lungs.";
    case "feedback":
      if (tone === "ok") return "Nailed it. My larynx is proud of yours.";
      if (tone === "none") return "I heard nothing. Mouth closed? I tried that once. Nothing.";
      return "Close. My throat has seen worse.";
    case "idle":
    case "listening":
      return "Resting my vocal folds. They're made of maths.";
    default:
      return null;
  }
}

/** `onPitch(f0)`: John's pitch in Hz, about 20 times a second while he sings. */
export function startChimera(onPitch = () => {}) {
  if (new URLSearchParams(location.search).get("chimera") === "0") return null;
  const box = document.getElementById("coach");
  const frame = document.getElementById("coach-frame");
  if (!box || !frame) return null;
  const body = new URLSearchParams(location.search).get("chimera_body");
  frame.src = `chimera/teacher.html${body ? `?body=${encodeURIComponent(body)}` : ""}`;
  box.hidden = false;

  // Browsers play no sound until the page has been clicked or a key pressed. John tries to start by himself;
  // if that is refused, the first click or key anywhere on this page starts him, and a small note says so.
  const hint = document.getElementById("live-john");
  const nudge = () => {
    if (!started) frame.contentWindow?.__coachNudge?.();
  };
  window.addEventListener("pointerdown", nudge, true);
  window.addEventListener("keydown", nudge, true);

  let ready = false;
  let started = false;
  setTimeout(() => {
    if (hint && !started) hint.hidden = false;
  }, 1500);
  let showing = false;
  let sungFor = null;
  const queue = []; // messages sent before the head loaded
  const post = (message) => {
    if (ready) frame.contentWindow?.postMessage(message, location.origin);
    else queue.push(message);
  };
  let awake = true;
  let nap = 0;
  const show = () => {
    const on = showing;
    box.dataset.show = String(on);
    box.dataset.started = String(started);
    if (hint && started) hint.hidden = true;
    // Hidden, he sleeps: no drawing, no voice engine. He costs the rig's computer nothing between notes.
    clearTimeout(nap);
    if (on && !awake) post({ type: "wake" });
    if (on) awake = true;
    else if (awake) {
      nap = setTimeout(() => {
        awake = false;
        post({ type: "sleep" });
      }, 800);
    }
  };

  window.addEventListener("message", (event) => {
    if (event.origin !== location.origin || event.source !== frame.contentWindow) return;
    const data = event.data;
    if (!data || data.source !== "chimera-coach") return;
    if (data.type === "pitch") {
      if (typeof data.f0 === "number" && data.f0 > 0) onPitch(data.f0);
      return;
    }
    if (data.type === "ready") {
      ready = true;
      while (queue.length) frame.contentWindow.postMessage(queue.shift(), location.origin);
    } else if (data.type === "started") started = true;
    box.dataset.singing = String(data.type === "singing");
    show();
  });
  show();

  /** Follow the rig: called with each new state, and the verdict's tone in feedback. */
  return function follow(was, now, tone) {
    showing = SHOWN.has(now.phase) || ALONG.has(now.mode);
    const veil = document.getElementById("coach-veil");
    if (veil) veil.dataset.on = String(ALONG.has(now.mode));
    show();
    const step = coachStep(sungFor, was, now);
    sungFor = step.sungFor;
    const backing = bandNote(was, now);
    if (backing) {
      post({ type: "sing", midi: backing.midi, seconds: BAND_SECONDS, name: backing.word, backing: true });
    } else if (ALONG.has(now.mode)) {
      if (was?.mode !== now.mode) {
        post({ type: "idle", text: now.mode === "band" ? "Band mode. I do backing vocals. I've always been a background throat." : "Warm-up. Sing along with me. Nobody is judging. I can't even hear you." });
      }
    } else if (step.sing) {
      // On a retry he starts where you were (so many cents off) and slides to the note.
      const off = typeof now.slide === "number" ? Math.max(-400, Math.min(400, now.slide)) : null;
      const midi = BY_WORD[step.sing].midi;
      post({ type: "sing", midi, seconds: SING_SECONDS + (off === null ? 0 : SLIDE_SECONDS), name: step.sing, ...(off === null ? {} : { from: midi + off / 100 }) });
    } else if (now.phase !== was?.phase) {
      const text = coachIdle(now, tone);
      if (text) post({ type: "idle", text });
    }
  };
}
