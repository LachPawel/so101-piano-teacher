// Wires the page together: buttons, the 3D stage, sound, the microphone, MIDI and the lesson.

import { Microphone, playNote, wakeAudio } from "./audio.js";
import { runLesson } from "./lesson.js";
import { connectMidi, midiSupported } from "./midi.js";
import { IN_TUNE_CENTS, centsFromTarget, hzToMidi, noteName } from "./pitch.js";
import { Stage } from "./scene.js";
import { KEYS, LESSON_KEYS, SONGS, keyByMidi, keyByNumber } from "./songs.js";

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const stage = new Stage($("stage"));
const mic = new Microphone();
const state = { sound: true, target: null, running: null, presses: 0, sung: 0, inTune: 0 };

// ------------------------------------------------------------------------------------- display

function log(text) {
  const item = document.createElement("li");
  item.textContent = text;
  $("log").prepend(item);
  while ($("log").children.length > 40) $("log").lastChild.remove();
  const sung = state.sung ? ` · sung in tune ${state.inTune}/${state.sung}` : "";
  $("tally").textContent = `${state.presses} presses${sung}`;
}

function setTarget(number) {
  state.target = number;
  stage.setTarget(number);
  const key = number ? keyByNumber(number) : null;
  $("target-word").textContent = key ? key.word : "Pick a note";
  $("target-note").textContent = key ? noteName(key.midi) : "";
  document.querySelectorAll("#notes button").forEach((button) => {
    button.classList.toggle("target", Number(button.dataset.key) === number);
  });
}

function say(phase, feedback, mood = "") {
  if (phase != null) $("phase").textContent = phase;
  if (feedback != null) {
    $("feedback").textContent = feedback;
    $("feedback").className = `feedback ${mood}`;
  }
}

function speak(text) {
  if (!$("speak").checked || !("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
}

const centsText = (cents) => {
  const size = Math.round(Math.abs(cents));
  if (size <= 5) return "right on the note";
  return `${size} cents ${cents < 0 ? "flat" : "sharp"}`;
};

// ------------------------------------------------------------------------------------- playing

/** The arm presses a sticker key. Resolves when the press is finished. */
function press(number) {
  wakeAudio();
  const { midi, word } = keyByNumber(number);
  return stage.pressKey(number, () => {
    if (state.sound) playNote(midi);
    state.presses++;
    log(`arm played ${word} (${noteName(midi)})`);
  });
}

function setRunning(what) {
  state.running = what;
  $("lesson").textContent = what === "lesson" ? "Stop lesson" : "Start singing lesson";
  $("play-tune").textContent = what === "tune" ? "Stop" : "Play tune";
  $("lesson").disabled = what === "tune";
  $("play-tune").disabled = what === "lesson";
  document.querySelectorAll("#notes button").forEach((button) => (button.disabled = what !== null));
  if (what === null) stage.listening(false);
}

async function playTune() {
  if (state.running) return setRunning(null);
  const song = SONGS[$("tune").value];
  setRunning("tune");
  say("Playing", song.title);
  for (const number of song.notes) {
    if (state.running !== "tune") break;
    if (number === 0) {
      await sleep(350);
      continue;
    }
    setTarget(number);
    await press(number);
  }
  if (state.running === "tune") say("Ready", "That was the tune.");
  setRunning(null);
}

async function lesson() {
  if (state.running) {
    setRunning(null);
    return say("Ready", "Lesson stopped.");
  }
  if (!(await microphone(true))) return;
  setRunning("lesson");
  await runLesson(LESSON_KEYS, {
    play: press,
    read: () => mic.read(),
    stopped: () => state.running !== "lesson",
    show(step) {
      if (step.phase === "play") {
        setTarget(step.key.key);
        stage.listening(false);
        say(step.attempt === 1 ? "Listen" : `Listen again (try ${step.attempt})`, "");
      } else if (step.phase === "listen") {
        stage.listening(true);
        say("Your turn", "Sing la on that note.");
      } else if (step.phase === "result") {
        stage.listening(false);
        state.sung++;
        const good = step.verdict === "in_tune";
        if (good) {
          state.inTune++;
          stage.celebrate();
        }
        const detail = step.cents == null ? "" : ` You were ${centsText(step.cents)}.`;
        say("Feedback", step.text + detail, good ? "good" : "off");
        speak(step.text);
        log(`you sang ${step.key.word}: ${step.cents == null ? "nothing heard" : centsText(step.cents)}`);
      } else if (step.phase === "done") {
        const right = step.report.filter((entry) => entry.verdict === "in_tune").length;
        say("Lesson over", `${right} of ${LESSON_KEYS.length} notes in tune.`, right ? "good" : "off");
      }
    },
  });
  setRunning(null);
}

// ---------------------------------------------------------------------------------- microphone

async function microphone(on) {
  if (!on) {
    mic.stop();
  } else if (!mic.on) {
    try {
      await mic.start();
    } catch {
      say(null, "The microphone is blocked. Allow it in the browser to sing.", "off");
    }
  }
  $("mic").textContent = mic.on ? "Microphone on" : "Microphone off";
  $("mic").classList.toggle("on", mic.on);
  $("mic").setAttribute("aria-pressed", String(mic.on));
  if (!mic.on) {
    $("meter").className = "meter";
    $("reading").textContent = "Turn the microphone on to see your pitch.";
  }
  return mic.on;
}

const recent = [];
function meter() {
  if (!mic.on) return;
  recent.push(mic.read());
  if (recent.length > 3) recent.shift();
  const heard = recent.filter((hz) => hz != null).sort((a, b) => a - b);
  if (heard.length < 2) {
    $("meter").className = "meter";
    $("reading").textContent = "Listening...";
    return;
  }
  const hz = heard[Math.floor(heard.length / 2)];
  const midi = hzToMidi(hz);
  // Against the note of the lesson when there is one, else against the nearest piano note.
  const cents = state.target ? centsFromTarget(hz, keyByNumber(state.target).midi) : 100 * (midi - Math.round(midi));
  const good = Math.abs(cents) <= IN_TUNE_CENTS;
  $("needle").style.left = `${50 + Math.max(-100, Math.min(100, cents)) / 2}%`;
  $("meter").className = `meter live ${good ? "good" : "off"}`;
  $("reading").textContent = `${noteName(midi)} · ${centsText(cents)}`;
}

// ---------------------------------------------------------------------------------------- MIDI

/** Fold any note into the two octaves the picture shows. */
function shown(midi) {
  let note = midi;
  while (note < 48) note += 12;
  while (note > 72) note -= 12;
  return note;
}

async function midi() {
  if (!midiSupported()) {
    $("midi").textContent = "MIDI needs Chrome or Edge";
    return;
  }
  try {
    await connectMidi(
      (note, velocity) => {
        stage.flash(shown(note));
        const key = keyByMidi(60 + (((note - 60) % 12) + 12) % 12);
        if (key) stage.mirrorPress(key.key);
        if ($("midi-sound").checked) playNote(note, velocity);
        log(`keyboard played ${noteName(note)}${key ? ` (${key.word})` : ""}`);
      },
      (names) => {
        $("midi").textContent = names.length ? `MIDI: ${names[0]}` : "No MIDI keyboard found";
        $("midi").classList.toggle("on", names.length > 0);
      },
    );
  } catch {
    $("midi").textContent = "MIDI was blocked";
  }
}

// --------------------------------------------------------------------------------------- setup

for (const { key, word } of KEYS) {
  const button = document.createElement("button");
  button.type = "button";
  button.dataset.key = key;
  button.innerHTML = `<strong>${key}</strong><small>${word}</small>`;
  button.addEventListener("click", () => {
    setTarget(key);
    say("Playing", "");
    press(key).then(() => say("Ready", null));
  });
  $("notes").append(button);
}

for (const [id, song] of Object.entries(SONGS)) $("tune").add(new Option(song.title, id));

$("lesson").addEventListener("click", lesson);
$("play-tune").addEventListener("click", playTune);
$("mic").addEventListener("click", () => microphone(!mic.on));
$("midi").addEventListener("click", midi);
$("sound").addEventListener("click", () => {
  state.sound = !state.sound;
  $("sound").textContent = state.sound ? "Sound on" : "Sound off";
  $("sound").classList.toggle("on", state.sound);
  $("sound").setAttribute("aria-pressed", String(state.sound));
});

window.addEventListener("keydown", (event) => {
  const number = Number(event.key);
  const typing = ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName);
  if (typing || state.running || event.metaKey || event.ctrlKey || !(number >= 1 && number <= 6)) return;
  setTarget(number);
  press(number);
});

setInterval(meter, 80);
log("ready");

// For poking at the page from the browser console.
window.pianoTeacher = { stage, mic, state, press, setTarget };
