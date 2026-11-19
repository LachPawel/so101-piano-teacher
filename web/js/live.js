// Live mode: this page as the stage display of the real rig.
//
// scripts/keys_ui.py serves this folder and streams what the rig does (server-sent events at /events).
// The 3D arm aims at the note the policy is asked to play and presses the key MIDI hears, the big words over
// the stage say whose turn it is, and a pitch lane in the middle of the stage shows, all the time, the notes
// the arm is asked for and the pitch you sing, so you can sing along and see where you are. Nothing here
// controls the rig, and the page makes no sound: the real piano plays.

import { startChimera } from "./chimera.js";
import { IN_TUNE_CENTS, noteName } from "./pitch.js";
import { KEYS } from "./songs.js";

const BY_WORD = Object.fromEntries(KEYS.map((entry) => [entry.word, entry]));
const BY_MIDI = Object.fromEntries(KEYS.map((entry) => [entry.midi, entry]));
const ORDINAL = { do: "first", re: "second", mi: "third", fa: "fourth", sol: "fifth", la: "sixth" };
const LETTERS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const LANE_S = 8; // seconds of history across the pitch lane
const LANE_NOW = 0.8; // where "now" sits across the lane; the asked note runs on to the right edge
const LOW = 58.5; // the lane's bottom and top, in MIDI notes: A#3 to B4, around the six keys
const HIGH = 70.5;
const FRESH_S = 0.35; // a reading older than this is not "now"
const HOLD_NEED_S = 1.0; // a note counts once it is held in tune this long in one go (keys_voice.py)
const LANE_PHASES = new Set(["get_ready", "handover", "your_turn", "feedback"]);
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
export function verdict(cents, ok) {
  if (cents == null) return { tone: "none", word: "Not heard.", html: "Not heard.<br>Once more." };
  // On the note only in passing: the rig wants it held for a full second in one go.
  if (Math.abs(cents) <= IN_TUNE_CENTS && ok === false) return { tone: "off", word: "Hold it longer.", html: "Hold it.<br>A full second." };
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
      if (state.mode === "band") return { html: "Band mode.<br>Play along.", tone: "sing" };
      if (state.mode === "warmup") return { html: "Warm-up.<br>Sing along.", tone: "sing" };
      return note ? { html: `The arm<br>plays ${note}.`, tone: "arm" } : { html: "The arm<br>plays.", tone: "arm" };
    case "get_ready":
      if (typeof state.slide === "number") return { html: `John slides<br>${state.slide < 0 ? "up" : "down"} to ${note ?? ""}.`, tone: "ready" };
      return { html: `John sings ${note ?? ""}.<br>Watch.`, tone: "ready" };
    case "handover": // the teacher says "Now your turn"; the arm has not started yet
    case "your_turn":
      return { html: `Your turn:<br>sing ${note ?? ""}.`, tone: "sing" };
    case "feedback": {
      const { html, tone } = verdict(state.sung?.cents ?? null, state.sung?.ok);
      return { html, tone };
    }
    case "thinking":
      return { html: "Thinking…", tone: "" };
    default:
      return { html: "Make a little<br>music.", tone: "" };
  }
}

/** A sung note moved by whole octaves to sit closest to `reference`: men often sing an octave down. */
export function foldTo(midi, reference) {
  return midi + 12 * Math.round((reference - midi) / 12);
}

/** The newest reading, if it is fresh and has a pitch: { midi, piano }, else null.
 * A reading is [time, MIDI note or null, level, 1 while the piano sounds]. */
export function livePitch(readings, now, fresh = FRESH_S) {
  const last = readings.length ? readings[readings.length - 1] : null;
  return last && last[1] !== null && now - last[0] < fresh ? { midi: last[1], piano: last[3] === 1 } : null;
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
  $("tagline").textContent = "The real SO-101 plays. Sing along. The teacher listens.";
  $("footer-note").textContent = "Live: the 3D arm follows the real one, which runs a SmolVLA policy. MIDI hears every key.";
  stage.ready.then(() => {
    stage.status.textContent = "Live";
  });

  // One screen, any size: the header and the panel grow with the window, like the 3D stage and the lane.
  const fit = () => {
    const scale = clamp(Math.min(innerWidth / 1440, innerHeight / 860), 0.7, 2.4);
    document.body.style.setProperty("--live-scale", scale.toFixed(3));
  };
  fit();
  window.addEventListener("resize", fit);

  const johnPitch = []; // John's pitch while he sings: [t, midi]
  const coach = startChimera((f0) => {
    const midi = 69 + 12 * Math.log2(f0 / 440);
    const goal = target();
    if (goal !== null && Math.abs(midi - goal) > 5) return; // his voice starting up, not a note he is singing
    johnPitch.push([Date.now() / 1000, midi]);
    while (johnPitch.length && johnPitch[0][0] < Date.now() / 1000 - LANE_S - 2) johnPitch.shift();
  }); // John, the singing head of beginner mode: null with ?chimera=0
  let onNoteSince = null; // when your live pitch came onto the note and stayed, or null
  let state = null;
  let readings = []; // the pitch heard, oldest first
  const targets = []; // the notes asked for over time: { from, to (null while asked), midi }

  function log(text) {
    const item = document.createElement("li");
    item.textContent = text;
    $("log").prepend(item);
    while ($("log").children.length > 40) $("log").lastChild.remove();
  }

  /** The note asked for right now, as MIDI, or null. */
  const target = () => (state?.asked && BY_WORD[state.asked] ? BY_WORD[state.asked].midi : null);

  /** The 3D stage, the target bars and the log follow what changed. */
  function react(before, now) {
    const was = before ?? { phase: "", asked: null, index: -1, results: [], press: null, sung: null, said: "" };
    const t = Date.now() / 1000;
    if (now.asked !== was.asked) {
      stage.setTarget(now.asked ? BY_WORD[now.asked]?.key ?? null : null);
      const open = targets[targets.length - 1];
      if (open && open.to === null) open.to = t;
      if (now.asked && BY_WORD[now.asked]) targets.push({ from: t, to: null, midi: BY_WORD[now.asked].midi });
      while (targets.length && targets[0].to !== null && targets[0].to < t - LANE_S) targets.shift();
    }
    if (["playing", "get_ready", "your_turn"].includes(now.phase) && now.asked && (now.index !== was.index || now.asked !== was.asked)) {
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
        log(`arm asked ${now.queue[index]}, MIDI heard ${result.heard ?? "nothing"} ${result.ok ? "✓" : "✗"}`);
      }
    });
    if (now.phase === "idle" && was.phase === "playing" && now.results.length && now.results.every((r) => r?.ok)) {
      stage.celebrate();
    }
    if (now.sung && now.sung !== was.sung && now.phase === "feedback" && was.phase !== "feedback") {
      const cents = now.sung.cents;
      log(`you sang ${now.sung.note}: ${cents == null ? "nothing heard" : centsWords(cents)}`);
      if (verdict(cents, now.sung.ok).tone === "ok") stage.celebrate();
    }
    // Your words arrive in pieces: the log gets your line once the teacher answers it.
    if (now.teacher && !was.teacher && was.said) log(`you said “${was.said.trim()}”`);
  }

  function render() {
    const s = state;
    for (const [id, on] of [["live-midi", s.midi], ["live-ears", s.ears_on], ["live-mic", s.mic_on]]) $(id).classList.toggle("on", !!on);

    const { html, tone } = headline(s);
    if (caption && caption.innerHTML !== html) caption.innerHTML = html;
    if (caption) caption.dataset.tone = tone;

    $("phase").textContent = s.phase_text || "Ready";
    const entry = s.asked ? BY_WORD[s.asked] : null;
    $("target-word").textContent = entry ? entry.word : "–";
    $("target-note").textContent = entry ? noteName(entry.midi) : "";
    document.querySelector(".now").classList.toggle("singing", s.phase === "your_turn");
    $("countdown").hidden = !(s.phase === "your_turn" || s.phase === "feedback") || !entry;
    if (s.phase === "playing" && entry) {
      const press = s.press && Date.now() / 1000 - s.press.t < 4 ? s.press : null;
      setFeedback(press && press.ok === false ? `The arm hit ${press.name}` : "", press && press.ok === false ? "off" : "");
    } else if (s.phase === "feedback") {
      const cents = s.sung?.cents ?? null;
      const judged = verdict(cents, s.sung?.ok);
      const held = s.sung?.held != null && cents != null ? `, held ${s.sung.held.toFixed(1)} s` : "";
      setFeedback(cents == null ? "No clear note was heard." : centsWords(cents) + held, judged.tone === "ok" ? "good live" : "off live");
    } else if (s.phase === "listening") {
      setFeedback("", "");
    } else if (s.phase !== "your_turn") {
      setFeedback("", "");
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
    if ($("feedback").textContent !== text) $("feedback").textContent = text;
    $("feedback").className = `feedback ${mood}`;
  }
  function setText(id, text) {
    $(id).textContent = text || "–";
    $(id).classList.toggle("muted", !text);
  }

  /** Every frame: the pitch lane, the words beside it, the needle and the countdown. */
  function tick() {
    requestAnimationFrame(tick);
    const s = state;
    if (!s) return;
    const now = Date.now() / 1000;
    const goal = target();
    const heard = livePitch(readings, now);
    const cents = heard && goal !== null ? 100 * (foldTo(heard.midi, goal) - goal) : null;

    // The words over the lane: what to do right now. It listens only in your turn, after the piano.
    let word = "";
    let mood = "";
    if (s.phase === "your_turn") {
      if (s.keys_down.length || heard?.piano) word = `Listen to ${s.asked}…`;
      else if (cents !== null) {
        const good = Math.abs(cents) <= IN_TUNE_CENTS;
        // In tune counts once it is held: the count runs while you stay on the note, and starts over when you leave it.
        if (good) onNoteSince ??= now;
        else onNoteSince = null;
        const heldFor = good ? now - onNoteSince : 0;
        word = good ? (heldFor >= HOLD_NEED_S ? "Held ✓" : `Hold it… ${heldFor.toFixed(1)} s`) : cents < 0 ? "↑ Higher" : "↓ Lower";
        mood = good ? "good" : "off";
      } else {
        if (!heard) onNoteSince = null; // you stopped: the next try counts from zero
        word = s.mic_on ? `Sing ${s.asked} now` : "No microphone";
      }
    } else if (s.phase === "playing" && goal !== null) {
      word = `Listen to ${s.asked}`;
    } else if (s.phase === "get_ready" && goal !== null) {
      word = "";
    } else if (s.phase === "feedback") {
      word = verdict(s.sung?.cents ?? null, s.sung?.ok).word;
      mood = verdict(s.sung?.cents ?? null, s.sung?.ok).tone === "ok" ? "good" : "off";
    }
    if (s.phase !== "your_turn") onNoteSince = null;
    const laneWord = $("lane-word");
    if (laneWord.textContent !== word) laneWord.textContent = word;
    laneWord.dataset.mood = mood;
    $("lane").dataset.active = String(s.phase === "your_turn" || s.phase === "feedback");
    // The lane is on screen only around a turn: John's note, your turn, the verdict. Never while you talk.
    $("lane").dataset.shown = String(LANE_PHASES.has(s.phase));

    // The listening window of a lesson turn.
    const turn = s.turn && s.turn.note === s.asked ? s.turn : null;
    let count = "";
    if (s.phase === "your_turn" && turn) {
      const left = clamp(turn.seconds - (now - turn.t), 0, turn.seconds);
      count = `listening, ${left.toFixed(1)} s left`;
      $("countdown-fill").style.width = `${(100 * left) / turn.seconds}%`;
      $("countdown-text").textContent = count;
      if (cents !== null && !heard.piano) setFeedback(word, mood ? `${mood} live` : "");
      else setFeedback("", "");
    } else if (s.phase === "feedback") {
      $("countdown-fill").style.width = "0%";
      $("countdown-text").textContent = "window closed";
    }
    $("lane-count").textContent = count;

    needle(heard && !heard.piano ? cents : null, heard);
    drawLane(now, goal);
  }

  function needle(cents, heard) {
    const meter = $("meter");
    if (cents == null) {
      meter.className = "meter";
      const idle = state?.phase === "your_turn" ? "Sing, and your pitch shows here." : "Your pitch shows in your turn.";
      $("reading").textContent = heard ? (heard.piano ? "The piano is still sounding." : noteName(Math.round(heard.midi))) : idle;
      return;
    }
    $("needle").style.left = `${50 + clamp(cents, -100, 100) / 2}%`;
    meter.className = `meter live ${Math.abs(cents) <= IN_TUNE_CENTS ? "good" : "off"}`;
    $("reading").textContent = centsWords(cents);
  }

  /** The pitch lane: the six keys as rows, the asked notes as bars, your pitch as a line. */
  function drawLane(now, goal) {
    const canvas = $("lane-canvas");
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (!width || !height) return;
    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(width * dpr)) canvas.width = Math.round(width * dpr);
    if (canvas.height !== Math.round(height * dpr)) canvas.height = Math.round(height * dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const left = 58;
    const plot = width - left - 12; // a margin on the right too
    const x = (t) => left + plot * (LANE_NOW + (t - now) / LANE_S);
    const y = (midi) => height * (1 - (midi - LOW) / (HIGH - LOW));
    const row = height / (HIGH - LOW);
    const reference = goal ?? 64.5;

    // Rows: the six keys named, the others faint.
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (let midi = Math.ceil(LOW); midi <= Math.floor(HIGH); midi++) {
      const key = BY_MIDI[midi];
      if (key) {
        ctx.fillStyle = midi === goal ? "#dbe4be" : "#f3f1e6";
        ctx.beginPath();
        ctx.roundRect(left, y(midi) - row / 2 + 1, plot, row - 2, 4);
        ctx.fill();
      }
      ctx.fillStyle = midi === goal ? "#44572a" : key ? "#6f745f" : "#b4b6a6";
      ctx.font = `${key ? 650 : 500} ${Math.round(clamp(row * (key ? 0.62 : 0.45), 9, 22))}px -apple-system, BlinkMacSystemFont, sans-serif`;
      ctx.fillText(key ? key.word : LETTERS[midi % 12], left - 10, y(midi));
    }

    // The notes asked for: bars, the current one running on to the right edge.
    for (const bar of targets) {
      const from = Math.max(x(bar.from), left);
      const to = bar.to === null ? left + plot : x(bar.to);
      if (to <= left) continue;
      ctx.fillStyle = bar.to === null ? "#f6d985" : "#efe2b8";
      ctx.beginPath();
      ctx.roundRect(from, y(bar.midi) - row * 0.32, Math.max(4, to - from), row * 0.64, row * 0.3);
      ctx.fill();
    }

    // Now.
    ctx.strokeStyle = "#c9c6b5";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x(now), 0);
    ctx.lineTo(x(now), height);
    ctx.stroke();

    // Your pitch: thick where it is your voice, thin and grey while the piano also sounds.
    ctx.save();
    ctx.beginPath();
    ctx.rect(left, 0, plot, height);
    ctx.clip();
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    let previous = null;
    for (const reading of readings) {
      if (reading[1] === null || reading[0] < now - LANE_S * LANE_NOW - 0.5) {
        previous = null;
        continue;
      }
      const midi = foldTo(reading[1], aimAt(reading[0]) ?? reference);
      const point = { x: x(reading[0]), y: y(clamp(midi, LOW - 0.3, HIGH + 0.3)), t: reading[0], piano: reading[3] === 1, midi };
      if (previous && point.t - previous.t < 0.25) {
        const asked = aimAt(point.t);
        const good = asked !== null && Math.abs(midi - asked) * 100 <= IN_TUNE_CENTS;
        ctx.strokeStyle = point.piano ? "#c4c3b4" : asked === null ? "#7c8d64" : good ? "#637747" : "#d87827";
        ctx.lineWidth = point.piano ? 2 : Math.max(4, row * 0.22);
        ctx.beginPath();
        ctx.moveTo(previous.x, previous.y);
        ctx.lineTo(point.x, point.y);
        ctx.stroke();
      }
      previous = point;
    }
    // John's pitch, while he shows the note: a dashed dark line with his name at its end.
    ctx.setLineDash([row * 0.3, row * 0.22]);
    ctx.strokeStyle = "#4b5039";
    ctx.lineWidth = Math.max(3, row * 0.14);
    ctx.beginPath();
    let john = null;
    for (const [t, midi] of johnPitch) {
      if (t < now - LANE_S * LANE_NOW - 0.5) continue;
      const point = { x: x(t), y: y(clamp(foldTo(midi, aimAt(t) ?? reference), LOW - 0.3, HIGH + 0.3)), t };
      if (john && point.t - john.t < 0.25) ctx.lineTo(point.x, point.y);
      else ctx.moveTo(point.x, point.y);
      john = point;
    }
    ctx.stroke();
    ctx.setLineDash([]);
    if (john && now - john.t < 3) {
      ctx.fillStyle = "#4b5039";
      ctx.textAlign = "left";
      ctx.font = `650 ${Math.round(clamp(row * 0.55, 10, 18))}px -apple-system, BlinkMacSystemFont, sans-serif`;
      ctx.fillText("John", john.x + 8, john.y - row * 0.45);
    }

    const last = livePitch(readings, now);
    if (last && previous) {
      const asked = goal;
      const good = asked !== null && Math.abs(previous.midi - asked) * 100 <= IN_TUNE_CENTS;
      ctx.fillStyle = last.piano ? "#c4c3b4" : asked === null ? "#7c8d64" : good ? "#637747" : "#d87827";
      ctx.beginPath();
      ctx.arc(previous.x, previous.y, Math.max(7, row * 0.32), 0, 2 * Math.PI);
      ctx.fill();
    }
    ctx.restore();
  }

  /** The note that was asked for at time t, as MIDI, or null. */
  function aimAt(t) {
    for (let i = targets.length - 1; i >= 0; i--) {
      const bar = targets[i];
      if (bar.from <= t && (bar.to === null || t < bar.to)) return bar.midi;
    }
    return null;
  }

  const source = new EventSource("events");
  source.onopen = () => $("live-link").classList.add("on");
  source.onerror = () => $("live-link").classList.remove("on");
  source.addEventListener("state", (event) => {
    const next = JSON.parse(event.data);
    react(state, next);
    coach?.(state, next, next.phase === "feedback" ? verdict(next.sung?.cents ?? null, next.sung?.ok).tone : "");
    state = next;
    render();
  });
  source.addEventListener("pitch", (event) => {
    readings.push(...JSON.parse(event.data));
    const oldest = Date.now() / 1000 - LANE_S - 2;
    if (readings.length && readings[0][0] < oldest) readings = readings.filter((reading) => reading[0] >= oldest);
  });
  source.addEventListener("ears", (event) => {
    $("ears").src = `data:image/png;base64,${event.data}`;
    $("ears").hidden = false;
  });
  setInterval(() => state && render(), 500); // lets "MIDI heard" fade
  requestAnimationFrame(tick);
}
