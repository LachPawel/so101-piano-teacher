// Live mode: this page as the stage display of the real rig.
//
// scripts/keys_ui.py serves this folder and streams what the rig does (server-sent events at /events).
// The 3D arm aims at the note the policy is asked to play and presses the key MIDI hears, the big words over
// the stage say whose turn it is, and while you sing your pitch is drawn as you sing it. Nothing here
// controls the rig, and the page makes no sound: the real piano plays.

import { IN_TUNE_CENTS, noteName } from "./pitch.js";
import { KEYS } from "./songs.js";

const BY_WORD = Object.fromEntries(KEYS.map((entry) => [entry.word, entry]));
const ORDINAL = { do: "first", re: "second", mi: "third", fa: "fourth", sol: "fifth", la: "sixth" };
const LETTERS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const RANGE = 350; // cents shown above and below the note on the pitch trace
const esc = (text) => String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const clamp = (value, lo, hi) => Math.max(lo, Math.min(hi, value));

/** True when this page was served by keys_ui.py, which answers /state. Asked only on this computer,
 * so the public page never requests a file it does not have. */
export async function liveAvailable() {
  if (!["localhost", "127.0.0.1", "[::1]"].includes(location.hostname)) return false;
  try {
    const response = await fetch("state", { cache: "no-store" });
    return response.ok && (response.headers.get("content-type") || "").includes("json");
  } catch {
    return false;
  }
}

/** How far off a sung note is, in words: "spot on", "20 cents under", "120 cents too low". */
export function centsWords(cents) {
  const size = Math.abs(Math.round(cents));
  if (size < 1) return "spot on";
  if (size <= IN_TUNE_CENTS) return `${size} cents ${cents < 0 ? "under" : "over"}, close enough`;
  return `${size} cents too ${cents < 0 ? "low" : "high"}`;
}

/** The verdict on a sung note, as the teacher judges it. */
export function verdict(cents) {
  if (cents == null) return { tone: "none", word: "Not heard.", html: "Not heard.<br>Once more." };
  if (Math.abs(cents) <= IN_TUNE_CENTS) return { tone: "ok", word: "In tune.", html: "In tune.<br>Well done." };
  const way = cents < 0 ? "higher" : "lower";
  return { tone: "off", word: `A little ${way}.`, html: `A little<br>${way}.` };
}

/** The big words over the 3D stage for one state of the rig: { html, tone }. */
export function headline(state) {
  const note = state.asked && BY_WORD[state.asked] ? `<em>${esc(state.asked)}</em>` : null;
  switch (state.phase) {
    case "loading":
      return { html: "Waking<br>the arm.", tone: "arm" };
    case "playing":
      return note ? { html: `The arm<br>plays ${note}.`, tone: "arm" } : { html: "The arm<br>plays.", tone: "arm" };
    case "get_ready":
      return { html: `Get ready<br>to sing ${note ?? ""}.`, tone: "ready" };
    case "your_turn":
      return { html: `Sing ${note ?? ""}<br>now.`, tone: "sing" };
    case "feedback": {
      const { html, tone } = verdict(state.sung?.cents ?? null);
      return { html, tone };
    }
    case "thinking":
      return { html: "Thinking…", tone: "" };
    default:
      return { html: "Make a little<br>music.", tone: "" };
  }
}

/** The newest reading of the sung pitch, if it is fresh: cents off the note, or null. */
export function livePitch(points, elapsed, fresh = 0.35) {
  const last = points.length ? points[points.length - 1] : null;
  return last && last[1] !== null && elapsed - last[0] < fresh ? last[1] : null;
}

/** Fold any note into the two octaves the 3D keyboard shows. */
const shown = (midi) => {
  let note = midi;
  while (note < 48) note += 12;
  while (note > 72) note -= 12;
  return note;
};
/** The sticker key (1 to 6) of a note in any octave, or null. */
const stickerOf = (midi) => KEYS.find((entry) => entry.midi % 12 === midi % 12)?.key ?? null;

export function startLive(stage) {
  const $ = (id) => document.getElementById(id);
  document.body.classList.add("live");
  const caption = document.querySelector(".stage-caption p");
  const eyebrow = document.querySelector(".stage-eyebrow");
  if (eyebrow) eyebrow.textContent = "LIVE FROM THE RIG";
  $("tagline").textContent = "The real SO-101 plays. You sing it back. The teacher listens.";
  $("footer-note").textContent = "Live: the 3D arm follows the real one, which runs a SmolVLA policy. MIDI hears every key.";
  stage.ready.then(() => {
    stage.status.textContent = "Live";
  });

  let state = null;
  let trace = { turn: 0, points: [] };

  function log(text) {
    const item = document.createElement("li");
    item.textContent = text;
    $("log").prepend(item);
    while ($("log").children.length > 40) $("log").lastChild.remove();
  }

  /** The 3D stage and the log follow what changed. */
  function react(before, now) {
    const was = before ?? { phase: "", asked: null, index: -1, results: [], press: null, sung: null, said: "" };
    if (now.asked !== was.asked) stage.setTarget(now.asked ? BY_WORD[now.asked]?.key ?? null : null);
    if (now.phase === "playing" && now.asked && (now.index !== was.index || now.asked !== was.asked)) {
      stage.aim(BY_WORD[now.asked]?.key);
    }
    if (now.press && now.press.t !== was.press?.t) {
      stage.flash(shown(now.press.note), 320);
      const key = stickerOf(now.press.note);
      if (key) stage.follow(key);
    }
    stage.listening(now.phase === "your_turn");
    now.results.forEach((result, index) => {
      if (result && !was.results[index]) {
        const asked = now.queue[index];
        log(`arm asked ${asked}, MIDI heard ${result.heard ?? "nothing"} ${result.ok ? "✓" : "✗"}`);
      }
    });
    if (now.phase === "idle" && was.phase === "playing" && now.results.length && now.results.every((r) => r?.ok)) {
      stage.celebrate();
    }
    if (now.sung && now.sung !== was.sung && now.phase === "feedback" && was.phase !== "feedback") {
      const cents = now.sung.cents;
      log(`you sang ${now.sung.note}: ${cents == null ? "nothing heard" : centsWords(cents)}`);
      if (verdict(cents).tone === "ok") stage.celebrate();
    }
    if (now.said && now.said !== was.said) log(`you said “${now.said}”`);
  }

  function render() {
    const s = state;
    for (const [id, on] of [["live-midi", s.midi], ["live-ears", s.ears_on], ["live-mic", s.mic_on]]) $(id).classList.toggle("on", !!on);

    const { html, tone } = headline(s);
    if (caption && caption.innerHTML !== html) caption.innerHTML = html;
    if (caption) caption.dataset.tone = tone;

    const singing = ["get_ready", "your_turn", "feedback"].includes(s.phase) && !!s.asked;
    $("phase").textContent = s.phase_text || "Ready";
    const entry = s.asked ? BY_WORD[s.asked] : null;
    $("target-word").textContent = entry ? entry.word : "Waiting";
    $("target-note").textContent = entry ? noteName(entry.midi) : "";
    document.querySelector(".now").classList.toggle("singing", s.phase === "your_turn");
    document.querySelector(".now").classList.toggle("ready", s.phase === "get_ready");
    $("countdown").hidden = !singing;
    if (!singing) {
      const press = s.press && Date.now() / 1000 - s.press.t < 4 ? s.press : null;
      const text =
        s.phase === "playing" && entry ? `Subtask: “play ${entry.word}, the ${ORDINAL[entry.word]} key”` + (press ? ` · MIDI heard ${press.name}` : "")
        : s.phase === "listening" ? "Say “play mi sol la” or “start the lesson”."
        : "";
      setFeedback(text, press && s.phase === "playing" ? (press.ok === false ? "off" : "good") : "");
    } else if (s.phase === "get_ready") {
      setFeedback("Wait for the piano to fade.", "");
    } else if (s.phase === "feedback") {
      const cents = s.sung?.cents ?? null;
      const { tone } = verdict(cents);
      setFeedback(cents == null ? "No clear note was heard." : centsWords(cents), tone === "ok" ? "good live" : "off live");
    }

    const chips = $("tune-chips");
    if (!s.queue.length) chips.innerHTML = '<span class="muted">No tune yet.</span>';
    else {
      chips.innerHTML = s.queue
        .map((word, index) => {
          const result = s.results[index];
          const cls = result ? (result.ok ? "ok" : "miss") : index === s.index ? "now" : "";
          const title = result ? `heard ${result.heard ?? "nothing"}` : "";
          return `<span class="${cls}" title="${esc(title)}">${esc(word)}</span>`;
        })
        .join("");
    }
    const done = s.results.filter(Boolean);
    $("tune-tally").textContent = done.length ? `${done.filter((r) => r.ok).length} of ${done.length} right` : "";
    if (s.policy) $("tune-policy").textContent = `policy: ${s.policy}`;

    setText("said", s.said);
    setText("teacher", s.teacher);
  }

  function setFeedback(text, mood) {
    $("feedback").textContent = text;
    $("feedback").className = `feedback ${mood}`;
  }
  function setText(id, text) {
    $(id).textContent = text || "–";
    $(id).classList.toggle("muted", !text);
  }

  /** Every frame while you sing: the countdown, the needle, the words and the trace. */
  function tick() {
    requestAnimationFrame(tick);
    const s = state;
    if (!s) return;
    const turn = s.turn && s.turn.note === s.asked ? s.turn : null;
    const singing = turn && (s.phase === "your_turn" || s.phase === "feedback");
    const elapsed = !turn ? 0
      : s.phase === "your_turn" ? clamp(Date.now() / 1000 - turn.t, 0, turn.seconds)
      : Math.min(turn.ended ?? turn.seconds, turn.seconds);
    const points = turn && trace.turn === turn.t ? trace.points : [];
    if (s.phase === "your_turn" && turn) {
      const left = turn.seconds - elapsed;
      $("countdown-fill").style.width = `${(100 * left) / turn.seconds}%`;
      $("countdown-text").textContent = `listening, ${left.toFixed(1)} s left`;
      const cents = livePitch(points, elapsed);
      if (cents == null) setFeedback(s.mic_on ? "Sing a steady “laaa”." : "The display has no microphone.", "");
      else if (Math.abs(cents) <= IN_TUNE_CENTS) setFeedback("In tune, keep holding", "good live");
      else setFeedback(cents < 0 ? "↑ Higher" : "↓ Lower", "off live");
      needle(cents);
    } else if (s.phase === "get_ready") {
      $("countdown-fill").style.width = "100%";
      $("countdown-text").textContent = "the window opens soon";
      needle(null);
    } else if (s.phase === "feedback") {
      $("countdown-fill").style.width = "0%";
      $("countdown-text").textContent = "window closed";
      needle(s.sung?.cents ?? null);
    } else {
      needle(null);
    }
    drawTrace(singing ? { turn, elapsed, points } : null, s);
  }

  function needle(cents) {
    const meter = $("meter");
    if (cents == null) {
      meter.className = "meter";
      $("reading").textContent = state.phase === "your_turn" ? "Listening…" : "Your pitch shows here when it is your turn.";
      return;
    }
    const good = Math.abs(cents) <= IN_TUNE_CENTS;
    $("needle").style.left = `${50 + clamp(cents, -100, 100) / 2}%`;
    meter.className = `meter live ${good ? "good" : "off"}`;
    $("reading").textContent = centsWords(cents);
  }

  function drawTrace(now, s) {
    const canvas = $("trace");
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (!width || !height) return;
    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(width * dpr)) canvas.width = Math.round(width * dpr);
    if (canvas.height !== Math.round(height * dpr)) canvas.height = Math.round(height * dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    const left = 34;
    const seconds = now ? now.turn.seconds : 6;
    const y = (cents) => 6 + (height - 12) * (1 - (clamp(cents, -RANGE, RANGE) + RANGE) / (2 * RANGE));
    const x = (t) => left + (width - left - 4) * clamp(t / seconds, 0, 1);
    const midi = s.asked && BY_WORD[s.asked] ? BY_WORD[s.asked].midi : 64;

    ctx.fillStyle = "#f0f0e4";
    ctx.fillRect(left, 0, width - left, height);
    ctx.fillStyle = "#b8c99760";
    ctx.fillRect(left, y(IN_TUNE_CENTS), width - left, y(-IN_TUNE_CENTS) - y(IN_TUNE_CENTS));
    ctx.font = "500 9px ui-monospace, SFMono-Regular, monospace";
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (let k = -3; k <= 3; k++) {
      ctx.strokeStyle = k === 0 ? "#637747" : "#e3dfd1";
      ctx.lineWidth = k === 0 ? 1.5 : 1;
      ctx.beginPath();
      ctx.moveTo(left, y(100 * k));
      ctx.lineTo(width, y(100 * k));
      ctx.stroke();
      if (k % 3 === 0 || k === 0) {
        ctx.fillStyle = k === 0 ? "#637747" : "#9a9e8b";
        ctx.fillText(k === 0 ? (s.asked ?? "") : LETTERS[(midi + k + 120) % 12], left - 5, y(100 * k));
      }
    }
    if (!now) return;
    ctx.fillStyle = "rgba(40, 43, 35, 0.05)"; // the part of the window still to come
    ctx.fillRect(x(now.elapsed), 0, width - x(now.elapsed), height);
    ctx.lineWidth = 3.5;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    let previous = null;
    for (const point of now.points) {
      if (point[1] === null) {
        previous = null;
        continue;
      }
      if (previous && point[0] - previous[0] < 0.25) {
        ctx.strokeStyle = Math.abs(point[1]) <= IN_TUNE_CENTS ? "#637747" : "#d87827";
        ctx.beginPath();
        ctx.moveTo(x(previous[0]), y(previous[1]));
        ctx.lineTo(x(point[0]), y(point[1]));
        ctx.stroke();
      }
      previous = point;
    }
    if (s.phase === "feedback" && s.sung?.cents != null) {
      ctx.setLineDash([5, 4]);
      ctx.strokeStyle = "#282b23";
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(left, y(s.sung.cents));
      ctx.lineTo(width, y(s.sung.cents));
      ctx.stroke();
      ctx.setLineDash([]);
    } else if (previous && s.phase === "your_turn" && now.elapsed - previous[0] < 0.35) {
      ctx.fillStyle = Math.abs(previous[1]) <= IN_TUNE_CENTS ? "#637747" : "#d87827";
      ctx.beginPath();
      ctx.arc(x(previous[0]), y(previous[1]), 6, 0, 2 * Math.PI);
      ctx.fill();
    }
  }

  const source = new EventSource("events");
  source.onopen = () => $("live-link").classList.add("on");
  source.onerror = () => $("live-link").classList.remove("on");
  source.addEventListener("state", (event) => {
    const next = JSON.parse(event.data);
    react(state, next);
    state = next;
    render();
  });
  source.addEventListener("pitch", (event) => {
    const data = JSON.parse(event.data);
    if (data.turn !== trace.turn) trace = { turn: data.turn, points: [] };
    trace.points.push(...data.points);
  });
  source.addEventListener("ears", (event) => {
    $("ears").src = `data:image/png;base64,${event.data}`;
    $("ears").hidden = false;
  });
  setInterval(() => state && render(), 500); // lets "MIDI heard" fade
  requestAnimationFrame(tick);
}
