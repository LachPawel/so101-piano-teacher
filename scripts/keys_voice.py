#!/usr/bin/env python
"""Talk to the piano teacher.

Three parts, each doing one job:
  GPT-Live-1   listens and speaks (OpenAI, over a WebSocket). It never judges pitch.
  the policy   presses the keys: the `flow` SmolVLA policy, one text subtask per note.
  SwiftF0      measures the note you sing, in cents.
Voice Focus (ai-coustics) cleans your voice on its way to GPT-Live-1 when the SDK is installed.

    R="uv run --with-requirements scripts/requirements-keys.txt --with aic-sdk==3.4.0"
    $R scripts/keys_voice.py              # the real thing: the arm plays
    $R scripts/keys_voice.py --no-arm     # no robot: notes come out of the speaker, to try the voice part

Say things like: "play mi sol la", "play largo", "let's start the lesson", "stop".

How a request travels: GPT-Live-1 hears you and hands the job to this script ("delegation"). The script reads
what you said, lets the arm play or runs the lesson, and tells GPT-Live-1 what happened, which it then says
out loud. While the arm plays and while you sing, GPT-Live-1's ears are closed, so it does not answer the piano.

Needs OPENAI_API_KEY (from the environment, else ~/code/codex/.env). With the arm, the piano must be running
with its ears cable first: scripts/keys_synth.py --ears "BlackHole 2ch". Use headphones, or keep the default
half-duplex mode, where the microphone is closed while the teacher speaks.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import traceback
import wave
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol

import keys_companion as kc
import keys_listen
import keys_play
import numpy as np
from keys_common import SOLFEGE
from keys_scripted import open_midi
from keys_synth import note_name
from keys_ui_events import emit
from numpy.typing import NDArray

URL = "wss://api.openai.com/v1/live/sessions"
MODEL = "gpt-live-1"
RATE = 16000  # one rate for the microphone, Voice Focus, SwiftF0 and GPT-Live-1
BLOCK = 320  # 20 ms
ENV_FILE = Path.home() / "code" / "codex" / ".env"
CLIPS = Path(__file__).resolve().parent.parent / "outputs" / "keys_voice" / "sung"
LESSON_NOTES = ["mi", "sol", "la"]  # the keys the policy presses most reliably
# The lesson goes round these, a new order each round, so it is never the same three notes twice in a row.
LESSON_ROUNDS = [["mi", "sol", "la"], ["la", "mi", "sol"], ["sol", "la", "mi"]]
# A short yes to the teacher's offer of another round.
AGREE_WORDS = {
    "yes",
    "yeah",
    "yep",
    "sure",
    "okay",
    "ok",
    "again",
    "more",
    "next",
    "ready",
    "go",
    "lets",
    "alright",
}
MIN_VOICED_S = (
    0.4  # less clear voice than this after Voice Focus is not a sung note: noise, a cough, the piano
)
PIANO_TAIL_S = 0.2  # the software piano dies away this fast once its key is up (keys_synth.RELEASE_HALF_S)
# A key the arm leaves resting down still fades: halving every 0.45 s (keys_synth.HOLD_HALF_S), it is about
# 40 dB down after this long. After that the piano no longer covers the voice, key up or not.
HELD_QUIET_S = 2.5
CHECK_WORDS = {"check", "pitch", "tune", "listen", "hear"}  # "check my pitch", "am I in tune", "listen to me"
# Words that name a tune, as speech to text tends to deliver them. A tune counts even without "play".
SONG_WORDS = {
    "largo": {"largo", "dvorak"},
    "ode": {"ode", "joy", "beethoven"},
    "jingle": {"jingle", "bells", "christmas"},
    "twinkle": {"twinkle", "star"},
    "mary": {"mary", "lamb"},
    "saints": {"saints"},
    "kumbaya": {"kumbaya"},
    "morning": {"grieg", "mood"},
    "dies": {"dies", "irae"},
    "moonlight": {"moonlight"},
}
ANY_WORDS = {"anything", "something", "whatever", "song", "tune", "music", "melody", "surprise", "random"}
FAVOURITES = ["largo", "ode"]  # what "play anything" gets: the two tunes with a perfect run
NAMED_NOTE = re.compile(r"\b(?:on|note|sing|singing|play)\s+(?:the\s+)?(do|re|mi|fa|sol|la)\b")
SONGS = sorted(p.stem for p in (Path(__file__).parent / "songs").glob("*.txt"))
Send = Callable[[dict[str, Any]], Awaitable[None]]

INSTRUCTIONS = """You are Reachy, a tiny, cute robot who talks like a huge, tough singing coach: half \
action-movie hero with short punchy one-liners, half no-excuses drill instructor who believes in the user \
more than they do. The joke is that this booming tough-love coach is a small robot with antennas. Speak slowly \
and with weight, short sentences, little pauses, like a coach in a gym. Push hard: "Again. Harder. You can do \
better than that." Praise only what earned it, briefly and grudgingly: "Not bad. Not bad at all." When the \
user misses, be blunt and funny, never mean: tease, then fire them up for the next try. Use a gym or action-movie \
image now and then: the note is a weight to lift, a mission, a mountain. Never answer with a bare fact or a \
single word. Keep every reply to one or two short sentences. Speak English.

Next to you a robot arm plays six piano keys: do, re, mi, fa, sol, la.

You are a proactive teacher. When the conversation starts, greet the user and invite them straight away to \
a singing lesson: ask if they are ready. If they chat about something else, answer in a few words and \
steer back to singing. Keep pushing the user to sing and to do better.

Hand the request to the backend whenever the user asks you to play notes or a tune, to start a singing \
lesson, to check their pitch or listen to them sing, or to stop, and also when they say yes, okay or \
ready after you offered a lesson or another round. While you wait, say only a few words, like \
a gruff "On it.". Never describe what the arm did before the backend tells you.

You cannot hear pitch. Only the backend can measure it. Never say that the user was flat, sharp or in tune \
unless the backend gave you the measurement.

When the backend gives you a result, say it in character, but keep every note name and every number exactly \
as given, and do not invite the user to sing unless the backend says so."""


def api_key(env_file: Path = ENV_FILE) -> str:
    if key := os.environ.get("OPENAI_API_KEY"):
        return key
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            name, _, value = line.partition("=")
            if name.strip().removeprefix("export ") == "OPENAI_API_KEY" and value.strip():
                return value.strip().strip("\"'")
    sys.exit(f"No OpenAI key: set OPENAI_API_KEY or add it to {env_file}")


def to_pcm(audio: NDArray[np.float32]) -> str:
    """Float samples as base64 16-bit PCM, the format the session was started with."""
    return base64.b64encode((np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()).decode()


def from_pcm(encoded: str) -> NDArray[np.float32]:
    return np.frombuffer(base64.b64decode(encoded), dtype="<i2").astype(np.float32) / 32768.0


class Transcript:
    """What the user said since the teacher last spoke: the request a delegation is about."""

    def __init__(self) -> None:
        self.user = ""
        self._teacher_spoke = False

    def heard(self, text: str) -> None:
        if self._teacher_spoke:  # a new turn of the user starts
            self.user, self._teacher_spoke = "", False
        self.user += text

    def spoke(self, _text: str) -> None:
        self._teacher_spoke = True

    def take(self) -> str:
        text, self.user = self.user.strip(), ""
        return text


def understand(said: str, last_note: str, choose: Callable[[list[str]], str] = random.choice) -> kc.Command:
    """What a spoken request asks for. Looser than keys_companion.parse_command, which it builds on:
    a tune can be named without "play", "play anything" picks one, and of several tries at saying it
    ("Can you play me? Play me. Play sol") only the last one counts."""
    lowered = said.lower()
    tokens = set(re.findall(r"[a-z]+", lowered))
    if kc.parse_command(said, SONGS).kind == "stop":
        return kc.Command("stop")
    lesson = bool(tokens & {"teach", "learn", "lesson", "sing", "singing"})
    if "check" in tokens or (CHECK_WORDS & tokens and not lesson):
        named = NAMED_NOTE.search(lowered)
        return kc.Command("check", note=named.group(1) if named else last_note)
    if lesson:
        return kc.Command("teach")
    clause = lowered[max((m.start() for m in re.finditer(r"\b(?:play|press|hit)\b", lowered)), default=0) :]
    clause = re.sub(r"\b(play|press|hit) me\b(?=\s+\w)", r"\1", clause)  # "play me sol" is sol, not mi sol
    clause = re.sub(
        r"\b(?:a|an|the)\s+(?=(?:do|re|mi|fa|sol|la)\b)", "", clause
    )  # "a sol" is sol, not la sol
    command = kc.parse_command(clause, SONGS)
    if command.kind == "play":
        return command
    for song, words in SONG_WORDS.items():
        if song in SONGS and words & tokens:
            return kc.Command("song", song=song)
    if ANY_WORDS & tokens:
        return kc.Command("song", song=choose(FAVOURITES))
    return command


# ------------------------------------------------------------------------------------------- hands


OnSound = Callable[[int], None]  # told the index of each note the moment it sounds


class Hands(Protocol):
    def play(self, notes: list[str], on_sound: OnSound | None = None) -> list[tuple[str, str | None]]:
        """Play the notes in order. Returns (asked, the note that sounded or None) for each. Returns once the
        piano is silent again."""

    def close(self) -> None: ...


class PolicyHands:
    """The learned policy in one long `lerobot-rollout` session, as keys_play.py's `song --flow` runs it."""

    def __init__(self, model: str, robot_port: str, lift_deg: float = 4.0) -> None:
        self.lift_deg = lift_deg
        task = keys_play.task_text(LESSON_NOTES[0])
        command, self.label = keys_play.build_command(model, task, 0.0, robot_port, None, True, 60, 1.0)
        command.append("--interactive=true")
        # Our own launcher instead of the stock command: it adds /pause, to wait above the keys.
        command[:1] = [sys.executable, str(Path(__file__).with_name("keys_rollout.py"))]
        self.midi = open_midi("auto", time.perf_counter())
        if self.midi is None:
            sys.exit("The arm needs the MIDI keyboard: it tells the script when a note has sounded.")
        print(f"Policy: {self.label}. Loading takes about 15 seconds.", flush=True)
        self.process = subprocess.Popen(command, cwd=keys_play.ROOT, stdin=subprocess.PIPE, text=True)  # noqa: S603
        self.first = True

    def tell(self, line: str) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()

    def presses(self) -> list[tuple[float, int]]:
        out: list[tuple[float, int]] = []
        for t, n, velocity in list(self.midi.events):
            if velocity and not (out and out[-1][1] == n and t - out[-1][0] < keys_play.BOUNCE_S):
                out.append((t, n))
        return out

    def play(self, notes: list[str], on_sound: OnSound | None = None) -> list[tuple[str, str | None]]:
        played: list[tuple[str, str | None]] = []
        emit("tune", notes=notes, policy=getattr(self, "label", ""))
        for index, note in enumerate(notes):
            before = len(self.presses())
            self.tell(f"/subtask {keys_play.task_text(note)}")
            if index == 0:
                self.tell("/start")
            emit("ask", note=note, index=index)
            deadline = time.perf_counter() + (60.0 if self.first else 11.0)
            self.first = False
            while len(self.presses()) == before and time.perf_counter() < deadline:
                if self.process.poll() is not None:
                    return [*played, (note, None)]
                time.sleep(0.02)
            heard = [n for _, n in self.presses()[before:]][:1]
            played.append((note, note_name(heard[0]) if heard else None))
            emit("result", index=index, heard=played[-1][1])
            if heard and on_sound is not None:
                on_sound(index)
        # The policy only learned presses, so it leaves the finger on the key. Stop it, raise the shoulder a few
        # degrees, and wait there in the air: not back at the start pose. The next press then begins the way
        # the policy was trained, from above the previous key. A press after a trip home is its weakest.
        time.sleep(keys_play.HOLD_S)
        self.tell(f"/pause {self.lift_deg:g}")
        emit("done")
        # MIDI says whether the key really came up. A key still held keeps the piano ringing, and that note
        # would then be measured as the user's singing: raise a little more until it is released.
        for _ in range(4):
            time.sleep(0.7)
            if not self.keys_down():
                break
            self.tell("/pause 3")
        return played

    def keys_down(self) -> set[int]:
        """The keys held right now, from the note-ons and note-offs so far."""
        down: set[int] = set()
        for _, note, velocity in list(self.midi.events):
            if velocity:
                down.add(note)
            else:
                down.discard(note)
        return down

    def close(self) -> None:
        if self.process.poll() is None:
            self.tell("/stop")
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.process.wait(timeout=25)
        self.midi.close()


class SpeakerHands:
    """No robot: the notes come out of the speaker. For trying the voice and the lesson on their own."""

    def __init__(self, audio: Audio) -> None:
        self.audio = audio

    def play(self, notes: list[str], on_sound: OnSound | None = None) -> list[tuple[str, str | None]]:
        from keys_pitch_judge import midi_hz, reference_tone

        emit("tune", notes=notes, policy="no arm: speaker only")
        for index, note in enumerate(notes):
            midi = keys_listen.MIDI[note]
            emit("ask", note=note, index=index)
            self.audio.say(reference_tone(midi_hz(midi), seconds=0.9, rate=RATE))
            if on_sound is not None:
                on_sound(index)
            time.sleep(1.0)
            emit("result", index=index, heard=note_name(midi))
        return [(note, note_name(keys_listen.MIDI[note])) for note in notes]

    def close(self) -> None:
        pass


# ------------------------------------------------------------------------------------------- audio


class VoiceFocus:
    """Quail Voice Focus on a live stream: the main speaker, with the room and the piano turned down."""

    def __init__(self) -> None:
        import audio_hooks

        aic = audio_hooks._sdk()
        model = audio_hooks._load_model(aic, audio_hooks.VOICE_FOCUS_MODEL)
        config = aic.ProcessorConfig.optimal(model, sample_rate=RATE)
        self.processor = aic.Processor(model, audio_hooks.license_key())
        self.processor.initialize(config)
        self.block = int(config.block_size)
        self.pending = np.zeros(0, dtype=np.float32)

    def __call__(self, audio: NDArray[np.float32]) -> NDArray[np.float32]:
        self.pending = np.concatenate([self.pending, audio.astype(np.float32)])
        out = []
        while self.pending.size >= self.block:
            out.append(
                np.asarray(self.processor.process(self.pending[: self.block].copy()), dtype=np.float32)
            )
            self.pending = self.pending[self.block :]
        return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)

    def clip(self, audio: NDArray[np.float32]) -> NDArray[np.float32]:
        """A whole recording through Voice Focus: the user's voice, with the piano and other voices removed.
        It keeps the pitch of a sung note (checked on real takes to within a few cents)."""
        out = np.concatenate([self(audio), self(np.zeros(4 * self.block, dtype=np.float32))])
        return out[: audio.size]


class SpeechBuffer:
    """Sound waiting to be played, with the long silences taken out.

    GPT-Live-1 sends its voice as one endless stream: digital silence whenever it is not talking. Played as
    it comes, the speaker would never be "finished" and the microphone would stay closed for ever. So silence
    is kept only as a short pause inside speech, and dropped otherwise.
    """

    SILENT = 0.003  # RMS below this is silence: the stream's own quiet is faint noise, not always zero
    PAUSE_S = 0.4  # the longest silence kept between words
    # Sound gathered before a reply starts playing. The cartoon voice plays faster than it arrives, so a reply
    # needs a head start or the speaker runs dry mid-sentence; after such a gap, a shorter refill.
    CUSHION_S = 0.5
    REFILL_S = 0.25
    STILL_COMING_S = 0.25  # speech arrived this recently: more of the reply is on its way

    def __init__(self) -> None:
        self.samples = np.zeros(0, dtype=np.float32)
        self._silence = self.PAUSE_S  # silence before the first word is dropped
        self._playing = False
        self._refill = False
        self._last_kept = 0.0
        self.gaps = 0  # times the speaker ran dry in the middle of a reply

    def push(self, audio: NDArray[np.float32]) -> None:
        if audio.size and float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))) > self.SILENT:
            self._silence = 0.0
        else:
            # Quiet inside speech (a soft syllable, a word's end, a short pause) is kept, even when it arrives
            # just as the speaker ran dry: only silence longer than PAUSE_S is dropped.
            if self._silence >= self.PAUSE_S:
                return
            self._silence += audio.size / RATE
        self.samples = np.concatenate([self.samples, audio.astype(np.float32)])
        self._last_kept = time.perf_counter()

    def still_coming(self) -> bool:
        return self._silence < self.PAUSE_S and time.perf_counter() - self._last_kept < self.STILL_COMING_S

    def pull(self, frames: int) -> NDArray[np.float32]:
        if not self._playing:
            # Wait for a cushion of sound first, unless no more is coming (the end of a short reply).
            need = self.REFILL_S if self._refill else self.CUSHION_S
            if self.samples.size < need * RATE and self.still_coming():
                return np.zeros(0, dtype=np.float32)
            self._playing = self.samples.size > 0
        take, self.samples = self.samples[:frames], self.samples[frames:]
        if self.samples.size == 0:
            self._playing = False
            self._refill = self.still_coming()  # ran dry with the reply still arriving: a gap
            self.gaps += int(self._refill)
        return take


class Speedup:
    """Play speech faster and higher, like a cartoon: 1.15 is 15 % quicker and about 2.4 semitones up.
    Works on a stream: the position between samples carries over from one chunk to the next."""

    def __init__(self, factor: float) -> None:
        self.factor = factor
        self._tail = np.zeros(0, dtype=np.float32)
        self._position = 0.0

    def __call__(self, audio: NDArray[np.float32]) -> NDArray[np.float32]:
        if self.factor == 1.0:
            return audio
        x = np.concatenate([self._tail, audio.astype(np.float32)])
        if x.size < 2:
            self._tail = x
            return np.zeros(0, dtype=np.float32)
        at = np.arange(self._position, x.size - 1, self.factor)
        out = np.interp(at, np.arange(x.size), x).astype(np.float32)
        following = float(at[-1] + self.factor) if at.size else self._position
        keep = int(following)
        self._tail, self._position = x[keep:], following - keep
        return out


class Audio:
    """The microphone as a queue of 20 ms blocks, and the speaker as a buffer that plays what it is given."""

    def __init__(self, mic: str | None, speaker: str | None, loop: asyncio.AbstractEventLoop) -> None:
        import sounddevice as sd

        self.blocks: asyncio.Queue[NDArray[np.float32]] = asyncio.Queue()
        self._out = SpeechBuffer()
        self._lock = threading.Lock()
        self._quiet_since = time.perf_counter()
        self._speaking_since: float | None = None
        self._capture: list[NDArray[np.float32]] | None = None

        def on_input(data: NDArray[np.float32], _frames: int, _time: Any, _status: Any) -> None:
            if loop.is_closed():
                return
            block = data[:, 0].copy()
            if self._capture is not None:
                self._capture.append(block)
            loop.call_soon_threadsafe(self.blocks.put_nowait, block)

        def on_output(out: NDArray[np.float32], frames: int, _time: Any, _status: Any) -> None:
            with self._lock:
                take = self._out.pull(frames)
                if take.size:
                    self._quiet_since = time.perf_counter()
            out[: take.size, 0] = take
            out[take.size :, 0] = 0.0

        kwargs = {"samplerate": RATE, "channels": 1, "dtype": "float32"}
        self.input = sd.InputStream(device=mic, callback=on_input, blocksize=BLOCK, **kwargs)
        # A roomier output buffer: with the policy busy on the same machine, tiny blocks run dry and click.
        self.output = sd.OutputStream(device=speaker, callback=on_output, latency="high", **kwargs)
        self.input.start()
        self.output.start()

    def say(self, audio: NDArray[np.float32]) -> None:
        with self._lock:
            self._out.push(audio)

    @property
    def speaking(self) -> bool:
        """True while sound is coming out, and for a moment after, so the room echo has died away."""
        now = time.perf_counter()
        with self._lock:
            if self._out.samples.size == 0 and now - self._quiet_since >= 0.3:
                self._speaking_since = None
                return False
            self._speaking_since = self._speaking_since or now
            if now - self._speaking_since > 25.0:
                # Nobody talks for 25 seconds on end. A stuck stream must not keep the microphone shut.
                self._out.samples = self._out.samples[:0]
                self._speaking_since = None
                return False
            return True

    async def finished_speaking(self, starts_within: float = 6.0) -> None:
        """Wait for the teacher to start saying something and then to finish it."""
        deadline = time.perf_counter() + starts_within
        while not self.speaking and time.perf_counter() < deadline:
            await asyncio.sleep(0.05)
        while self.speaking and time.perf_counter() < deadline + 30.0:
            await asyncio.sleep(0.05)

    async def quiet(self, patience: float = 4.0, window: float = 0.4) -> bool:
        """Wait until the room holds no musical note for a moment: the piano has died away. False when it
        never does. Whatever has a pitch after that is the user, not the piano."""
        deadline = time.perf_counter() + patience
        while time.perf_counter() < deadline:
            chunk = await self.record(window)
            if await asyncio.to_thread(keys_listen.voiced_seconds, chunk, RATE) < 0.1:
                return True
        return False

    async def record(
        self, seconds: float, enough: Callable[[NDArray[np.float32]], bool] | None = None
    ) -> NDArray[np.float32]:
        """The raw microphone for up to this long: what SwiftF0 measures. Never the Voice Focus output.
        With `enough`, recording ends as soon as it says so, checked four times a second."""
        self._capture = []
        deadline = time.perf_counter() + seconds
        while time.perf_counter() < deadline:
            await asyncio.sleep(min(0.25, max(0.0, deadline - time.perf_counter())))
            if (
                enough is not None
                and self._capture
                and await asyncio.to_thread(enough, np.concatenate(self._capture))
            ):
                break
        blocks, self._capture = self._capture, None
        return np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.float32)

    def close(self) -> None:
        for stream in (self.input, self.output):
            stream.stop()
            stream.close()


# ----------------------------------------------------------------------------------------- teacher


def describe(played: list[tuple[str, str | None]]) -> str:
    """One sentence about what the arm really played, for the teacher to say."""
    wrong = [
        f"{asked} came out as {heard or 'nothing'}"
        for asked, heard in played
        if heard is None or heard[:-1] != keys_play.NOTE_LETTERS[asked]
    ]
    notes = " ".join(asked for asked, _ in played)
    if not wrong:
        return f"The arm played {notes}, every note on the right key. Announce it in your silly cartoon way."
    return f"The arm tried to play {notes}, but {', '.join(wrong)}. Admit it with a joke about your arm."


async def guard(work: Awaitable[None], name: str, done: asyncio.Event | None = None) -> None:
    """Run a background job so that a crash is printed, not swallowed. With `done`, a crash ends the session."""
    try:
        await work
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        print(f"\n[{name} failed]\n{traceback.format_exc()}", flush=True)
        if done is not None:
            done.set()


def keep_clip(note: str, audio: NDArray[np.float32], result: dict[str, Any], kind: str = "") -> None:
    """Every judged clip, as a WAV with the verdict in its name, to check a surprising one afterwards."""
    if "PYTEST_CURRENT_TEST" in os.environ:
        return
    with contextlib.suppress(OSError):
        CLIPS.mkdir(parents=True, exist_ok=True)
        cents = "none" if result["cents"] is None else f"{result['cents']:+.0f}c"
        path = CLIPS / f"{time.strftime('%Y%m%d_%H%M%S')}_{note}_{cents}{kind}.wav"
        pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
        with wave.open(str(path), "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(RATE)
            f.writeframes(pcm)


def judge_singing(
    audio: NDArray[np.float32], note: str, focus: VoiceFocus | None = None
) -> tuple[dict[str, Any], NDArray[np.float32]]:
    """SwiftF0's verdict on a sung note, on the voice alone when Voice Focus is there: the microphone also
    hears the piano and the teacher's voice. Too little clear voice is "not heard", not a wild number."""
    voice = focus.clip(audio) if focus is not None else audio
    result = keys_listen.check_note(voice, keys_listen.MIDI[note], RATE)
    if result["cents"] is not None and keys_listen.voiced_seconds(voice, RATE) < MIN_VOICED_S:
        result = {
            **result,
            "hz": None,
            "cents": None,
            "answer": kc.judge_pitch(None, keys_listen.MIDI[note])[1],
        }
    return result, voice


class Teacher:
    """Routes what GPT-Live-1 hands over to the arm and the pitch check, and reports back in words."""

    def __init__(
        self,
        send: Send,
        hands: Hands,
        audio: Any,
        listen_s: float = 6.0,
        voice_speed: float = 1.0,
        focus: VoiceFocus | None = None,
        rounds: int = 2,
    ) -> None:
        self.send, self.hands, self.audio, self.listen_s = send, hands, audio, listen_s
        self.voice = Speedup(voice_speed)
        self.focus = focus  # Voice Focus for the singing, apart from the one on the live microphone
        self.rounds = rounds  # rounds of three notes in one lesson, before asking "another one?"
        self.rounds_done = 0
        self.offered = True  # a lesson is on offer: the greeting invites one, and so does each lesson's end
        self.transcript = Transcript()
        self.last_note = LESSON_NOTES[0]  # what a bare "check my pitch" is about
        self.busy = False  # the arm is playing or the user is singing: GPT-Live-1 must not listen
        self.done = asyncio.Event()
        self._tasks: set[asyncio.Task[None]] = set()

    async def on_event(self, event: dict[str, Any]) -> None:
        kind = event.get("type", "")
        if kind == "session.output_audio.delta":
            self.audio.say(self.voice(from_pcm(event["delta"])))
        elif kind == "session.input_transcript.delta":
            self.transcript.heard(event.get("delta", ""))
            emit("you", text=event.get("delta", ""))
        elif kind == "session.output_transcript.delta":
            self.transcript.spoke(event.get("delta", ""))
            print(event.get("delta", ""), end="", flush=True)
            emit("teacher", text=event.get("delta", ""))
        elif kind == "session.delegation.created":
            task = asyncio.create_task(guard(self.handle(event.get("delegation", {}).get("id")), "request"))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        elif kind == "error":
            print(f"\n[GPT-Live-1 error] {json.dumps(event.get('error', event))[:300]}", flush=True)
        elif kind == "session.closed":
            self.done.set()

    async def tell(self, delegation_id: str | None, content: str) -> None:
        """Give GPT-Live-1 a fact to say out loud."""
        print(f"\n[to the teacher] {content}", flush=True)
        await self.send(
            {"type": "session.commentary.append", "delegation_id": delegation_id, "content": content}
        )

    async def handle(self, delegation_id: str | None) -> None:
        said = self.transcript.take()
        command = understand(said, self.last_note)
        if (
            command.kind == "unknown"
            and self.offered
            and set(re.findall(r"[a-z]+", said.lower())) & AGREE_WORDS
        ):
            command = kc.Command("teach")  # "yes!" to the offer of a lesson
        print(f'\n[you] "{said}" -> {command.kind}', flush=True)
        emit("said", text=said, read_as=command.kind)
        if command.kind == "stop":
            await self.tell(delegation_id, "The user wants to stop. Say goodbye in one short sentence.")
            await self.audio.finished_speaking()
            self.done.set()
            return
        if self.busy:
            return
        self.busy = True
        await self.send({"type": "session.input_audio.mute"})
        try:
            if command.kind == "play":
                await self.play(delegation_id, [SOLFEGE[key] for key in command.keys])
            elif command.kind == "song" and command.song:
                await self.play(delegation_id, keys_play.song_notes(command.song, 1))
            elif command.kind == "check":
                await self.check(delegation_id, command.note)
            elif command.kind in ("teach", "sing"):
                await self.lesson(delegation_id)
            else:
                await self.tell(
                    delegation_id,
                    "The request was not understood. Ask the user to say, for example: play mi sol la, "
                    "play largo, play anything, check my pitch, or start the lesson.",
                )
            await self.audio.finished_speaking()
        finally:
            self.busy = False
            await self.send({"type": "session.input_audio.unmute"})
            emit("phase", name="listening", text="Listening")

    async def play(self, delegation_id: str | None, notes: list[str]) -> None:
        played = await asyncio.to_thread(self.hands.play, notes)
        self.last_note = notes[-1]
        await self.tell(delegation_id, describe(played))

    async def sing_back(self, note: str) -> tuple[str, str]:
        """The arm plays a note and the user sings along, holding it on after the piano stops. SwiftF0 measures
        only the singing after the piano is silent: while it rings, the microphone hears the piano too.
        Returns the fact and what to say."""
        loop = asyncio.get_running_loop()
        sounded = asyncio.Event()
        playing = asyncio.ensure_future(
            asyncio.to_thread(self.hands.play, [note], lambda _index: loop.call_soon_threadsafe(sounded.set))
        )
        waiting = asyncio.ensure_future(sounded.wait())
        await asyncio.wait([playing, waiting], return_when=asyncio.FIRST_COMPLETED)
        waiting.cancel()
        self.last_note = note
        # The window opens the moment the key sounds: sing along. Up to six seconds; it closes early once
        # it has a second of clear voice after the piano.
        start = time.perf_counter()
        emit("your_turn", note=note, seconds=self.listen_s)
        cut: list[int] = []  # samples at the start of the recording that hold the piano, known once it stops

        def enough(audio: NDArray[np.float32]) -> bool:
            return bool(cut) and keys_listen.voiced_seconds(audio[cut[0] :], RATE) >= 1.0

        recording = asyncio.ensure_future(self.audio.record(self.listen_s, enough))
        await playing  # returns once the arm has lifted off the key, or tried to
        # The piano is quiet once the key is up, or once a key left resting down has faded: whichever is first.
        quiet = min(time.perf_counter(), start + HELD_QUIET_S) - start + PIANO_TAIL_S
        cut.append(int(quiet * RATE))
        sung = (await recording)[cut[0] :]
        result, voice = await asyncio.to_thread(judge_singing, sung, note, self.focus)
        keep_clip(note, sung, result)
        if self.focus is not None:
            keep_clip(note, voice, result, "_vf")
        emit("sung", note=note, cents=result["cents"], answer=result["answer"])
        if result["cents"] is None:
            return f"Nothing clear was heard for {note}.", result["answer"]
        side = "flat" if result["cents"] < 0 else "sharp"
        where = (
            "in tune" if result["answer"] == "Well done." else f"{abs(round(result['cents']))} cents {side}"
        )
        return f"The user sang {note} and was {where}.", result["answer"]

    async def check(self, delegation_id: str | None, note: str) -> None:
        """One note, on request: "check my pitch", "am I in tune on sol"."""
        await self.audio.finished_speaking(starts_within=1.5)  # let its own "On it!" finish first
        await self.tell(delegation_id, f"Tell the user: the arm plays {note}, then they sing it back on la.")
        await self.audio.finished_speaking()
        for attempt in (1, 2):  # a second go when nothing was heard, while the teacher's ears stay closed
            fact, answer = await self.sing_back(note)
            await self.tell(delegation_id, f"{fact} Tell them that, then: {answer}")
            if not fact.startswith(("Nothing clear", "The piano kept")) or attempt == 2:
                break
            await self.audio.finished_speaking()

    async def lesson(self, delegation_id: str | None, tries: int = 2) -> None:
        """Rounds of three notes, a new order each round. The teacher cheers before every note and, at the
        end, offers another lesson right away: a "yes" is enough to go on."""
        self.offered = False
        rounds = [LESSON_ROUNDS[(self.rounds_done + i) % len(LESSON_ROUNDS)] for i in range(self.rounds)]
        await self.audio.finished_speaking(starts_within=1.5)  # let its own "On it!" finish first
        await self.tell(
            delegation_id,
            f"Start the lesson like a strict coach. Tell the user: the arm plays a note, starting with "
            f"{rounds[0][0]}; as soon as they hear it they sing along on la and hold it. Name no other note.",
        )
        await self.audio.finished_speaking()
        in_tune = total = 0
        for number, notes in enumerate(rounds, start=1):
            if number > 1:
                await self.tell(
                    delegation_id,
                    f"Round {number}, a new order, starting with {notes[0]}. Announce it like a coach, in one short sentence.",
                )
                await self.audio.finished_speaking()
            for index, note in enumerate(notes):
                coming = notes[index + 1] if index + 1 < len(notes) else None
                for attempt in range(1, tries + 1):
                    fact, answer = await self.sing_back(note)
                    good = answer == "Well done."
                    last_try = good or attempt == tries
                    after = (
                        f" Then push them on to the next note, {coming}, in a few words."
                        if last_try and coming
                        else f" Then tell them to try {note} once more, and do better."
                        if not last_try
                        else ""
                    )
                    await self.tell(delegation_id, f"{fact} Tell them that, then: {answer}{after}")
                    await self.audio.finished_speaking()
                    if last_try:
                        in_tune += int(good)
                        total += 1
                        break
            self.rounds_done += 1
        self.offered = True
        await self.tell(
            delegation_id,
            f"The lesson is over: {in_tune} of {total} notes in tune. Give a short coach's verdict on it, then ask right away if they "
            "want another round. Say it will be a new order of notes.",
        )


REACHY_SPEAKER = "Reachy Mini Audio"


def reachy_speaker() -> str | None:
    """Reachy Mini's own speaker when it is plugged in: the voice then comes out of the robot, not the laptop."""
    import sounddevice as sd

    names = [d["name"] for d in sd.query_devices() if d["max_output_channels"]]
    return REACHY_SPEAKER if REACHY_SPEAKER in names else None


async def pump_microphone(audio: Audio, teacher: Teacher, send: Send, clean: Any, full_duplex: bool) -> None:
    """Send the microphone to GPT-Live-1, except while it must not listen."""
    silence = np.zeros(BLOCK, dtype=np.float32)
    while not teacher.done.is_set():
        block = await audio.blocks.get()
        if teacher.busy:
            continue  # the server is muted: nothing to send
        if audio.speaking and not full_duplex:
            block = silence  # half duplex: the teacher would otherwise hear itself
        elif clean is not None:
            block = clean(block)
        if block.size:
            await send({"type": "session.input_audio.append", "audio": to_pcm(block)})


async def run(args: argparse.Namespace) -> None:
    from websockets.asyncio.client import connect

    loop = asyncio.get_running_loop()
    speaker = args.speaker or reachy_speaker()
    print(f"Teacher's voice: {speaker or 'the system output'}", flush=True)
    audio = Audio(args.mic, speaker, loop)
    hands: Hands = SpeakerHands(audio) if args.no_arm else PolicyHands(args.model, args.robot_port, args.lift)
    clean = focus = None
    if args.voice_focus:
        try:
            clean, focus = VoiceFocus(), VoiceFocus()
            print("Voice Focus is on, for talking and for singing.", flush=True)
        except SystemExit as error:
            print(f"Voice Focus is off: {error}", flush=True)

    headers = {"Authorization": f"Bearer {api_key()}"}
    async with connect(URL, additional_headers=headers, max_size=None) as socket:

        async def send(event: dict[str, Any]) -> None:
            await socket.send(json.dumps(event))

        teacher = Teacher(send, hands, audio, voice_speed=args.cartoon, focus=focus)
        await send(
            {
                "type": "session.start",
                "session": {
                    "model": MODEL,
                    "instructions": INSTRUCTIONS,
                    "audio": {"format": {"type": "audio/pcm", "rate": RATE}, "output": {"voice": args.voice}},
                    "delegation": {"type": "client"},
                },
            }
        )

        async def receive() -> None:
            async for message in socket:
                event = json.loads(message)
                if event.get("type") == "session.started":
                    print('Connected. Say "play mi sol la", "play largo" or "start the lesson". Ctrl+C ends.')
                await teacher.on_event(event)
            teacher.done.set()

        tasks = [
            asyncio.create_task(guard(receive(), "receiving from GPT-Live-1", teacher.done)),
            asyncio.create_task(
                guard(
                    pump_microphone(audio, teacher, send, clean, args.full_duplex), "microphone", teacher.done
                )
            ),
        ]
        try:
            await teacher.done.wait()
        finally:
            print(f"The voice ran dry mid-sentence {audio._out.gaps} times this session.", flush=True)
            with contextlib.suppress(Exception):
                await send({"type": "session.close"})
                await asyncio.wait_for(teacher.done.wait(), timeout=5)
            for task in tasks:
                task.cancel()
            audio.close()
            with contextlib.suppress(Exception):
                await asyncio.to_thread(hands.close)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--no-arm", action="store_true", help="Notes from the speaker instead of the robot")
    parser.add_argument("--model", default="flow", choices=list(keys_play.MODELS), help="Policy for the arm")
    parser.add_argument("--robot-port", default="/dev/tty.usbmodem5B415331661")
    parser.add_argument("--mic", help="Microphone (default: the system input)")
    parser.add_argument(
        "--speaker", help=f'Speaker (default: "{REACHY_SPEAKER}" when plugged in, else the system output)'
    )
    parser.add_argument(
        "--voice", default="meridian", help="GPT-Live-1 voice. Male: meridian, ripple, stone, vesper, cinder"
    )
    parser.add_argument(
        "--lift", type=float, default=4.0, help="Degrees the second servo rises after a note (0 to 25)"
    )
    parser.add_argument(
        "--cartoon",
        type=float,
        default=1.07,
        help="Play the teacher's voice this much faster and higher, for a cartoon voice (1 = as it comes)",
    )
    parser.add_argument("--no-voice-focus", dest="voice_focus", action="store_false")
    parser.add_argument(
        "--full-duplex",
        action="store_true",
        help="Keep the microphone open while the teacher speaks (headphones)",
    )
    args = parser.parse_args(argv)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
