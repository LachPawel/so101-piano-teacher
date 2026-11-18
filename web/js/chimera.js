// Chimera, the throat coach (experiment): a 3D head and throat that sings each note on "la".
//
// When it's your turn to sing, Chimera's head (chimera/teacher.html, a build of
// github.com/jamesondh/chimera-vocal-synth, MIT) appears over the stage and sings the asked note once,
// right after the arm's key stops sounding, so you see where the tongue, the jaw and the voice box go.
// It runs in an iframe and is told what to do with postMessage. Browsers only let it make a sound after
// a click inside it, so it shows a Start button until then. Off with ?chimera=0.

import { KEYS } from "./songs.js";

const BY_WORD = Object.fromEntries(KEYS.map((entry) => [entry.word, entry]));
const SING = new Set(["get_ready", "your_turn"]);
const SHOWN = new Set(["get_ready", "your_turn", "feedback"]);
export const SING_SECONDS = 1.6;

/**
 * What the coach does for one state of the rig. `sungFor` is the note it already sang this turn
 * (null after the turn). Returns { sing: the word to sing now or null, sungFor }.
 * It sings once per turn, after the piano is quiet, and forgets the note when the turn is over.
 */
export function coachStep(sungFor, state) {
  if (!SING.has(state.phase) || !state.asked || !BY_WORD[state.asked]) return { sing: null, sungFor: SING.has(state.phase) ? sungFor : null };
  if (sungFor === state.asked || (state.keys_down ?? []).length) return { sing: null, sungFor };
  return { sing: state.asked, sungFor: state.asked };
}

/** What the coach says between notes, for one phase (null: say nothing new). */
export function coachIdle(state, tone) {
  switch (state.phase) {
    case "playing":
      return "Shh. The robot is playing.";
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

export function startChimera() {
  if (new URLSearchParams(location.search).get("chimera") === "0") return null;
  const box = document.getElementById("coach");
  const frame = document.getElementById("coach-frame");
  if (!box || !frame) return null;
  const body = new URLSearchParams(location.search).get("chimera_body");
  frame.src = `chimera/teacher.html${body ? `?body=${encodeURIComponent(body)}` : ""}`;
  box.hidden = false;

  let ready = false;
  let started = false;
  let showing = false;
  let sungFor = null;
  const queue = []; // messages sent before the head loaded
  const post = (message) => {
    if (ready) frame.contentWindow?.postMessage(message, location.origin);
    else queue.push(message);
  };
  const show = () => {
    box.dataset.show = String(showing || !started);
    box.dataset.started = String(started);
  };

  window.addEventListener("message", (event) => {
    if (event.origin !== location.origin || event.source !== frame.contentWindow) return;
    const data = event.data;
    if (!data || data.source !== "chimera-coach") return;
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
    showing = SHOWN.has(now.phase);
    show();
    const step = coachStep(sungFor, now);
    sungFor = step.sungFor;
    if (step.sing) {
      post({ type: "sing", midi: BY_WORD[step.sing].midi, word: "la", seconds: SING_SECONDS, name: step.sing });
    } else if (now.phase !== was?.phase) {
      const text = coachIdle(now, tone);
      if (text) post({ type: "idle", text });
    }
  };
}
