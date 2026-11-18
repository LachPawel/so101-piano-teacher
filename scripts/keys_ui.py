#!/usr/bin/env python
"""The stage display: what the arm is asked to play, what it pressed, what it hears, what you sang.

    uv run --with-requirements scripts/requirements-keys.txt scripts/keys_ui.py
    uv run --with-requirements scripts/requirements-keys.txt scripts/keys_ui.py --demo    # no rig: a made-up tune

Then open http://localhost:8770 and make the window full screen. Leave this running in its own tab.

It only watches, it controls nothing:
  - the MIDI keyboard, for the keys that go down (instantly);
  - the virtual cable the robot's ears listen on, drawn as the same spectrogram the policy gets;
  - the events the song player and the voice teacher write (keys_ui_events.py);
  - the microphone, only while it is your turn to sing: your pitch against the note, drawn live.

No internet and no external files are needed: the page is one HTML file next to this script.
"""

from __future__ import annotations

import argparse
import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import numpy as np
from keys_ui_events import EVENTS

ROOT = Path(__file__).resolve().parent.parent
PAGE = Path(__file__).with_name("keys_ui.html")
NOTE_OF = {"do": 60, "re": 62, "mi": 64, "fa": 65, "sol": 67, "la": 69}
NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


LIVE_WINDOW_S = 0.4  # the newest sound the live pitch is read from
EDGE_FRAMES = 3  # SwiftF0's last few frames run off the end of the sound and are never sure


def midi_name(note: int) -> str:
    return f"{NAMES[note % 12]}{note // 12 - 1}"


def fold_cents(hz: float, target_midi: int) -> float:
    """Cents above (+) or below (-) the target, in whichever octave it was sung: -600 to 600."""
    cents = 1200 * np.log2(hz / (440.0 * 2 ** ((target_midi - 69) / 12)))
    return float((cents + 600) % 1200 - 600)


def live_pitch(pitch_hz: Any, confidence: Any, target_midi: int) -> tuple[float | None, float | None]:
    """The pitch right now from the tracker's newest frames: (Hz, cents off the target), or (None, None)
    when no clear note is being sung."""
    pitch = np.asarray(pitch_hz)[:-EDGE_FRAMES][-6:]
    sure = np.asarray(confidence)[:-EDGE_FRAMES][-6:] >= 0.5
    if np.count_nonzero(sure) < 3:
        return None, None
    hz = float(np.median(pitch[sure]))
    return hz, fold_cents(hz, target_midi)


class Hub:
    """The one picture of the rig that every open browser tab gets."""

    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.version = 0
        self.ears: str | None = None
        self.ears_version = 0
        self.trace: list[list[float | None]] = []  # this turn's sung pitch: [seconds, cents or None, level]
        self.trace_turn = 0.0
        self.state: dict[str, Any] = {
            "phase": "idle",
            "phase_text": "Ready",
            "asked": None,
            "queue": [],
            "index": -1,
            "results": [],
            "policy": "",
            "said": "",
            "teacher": "",
            "sung": None,
            "turn": None,
            "voiced": 0.0,
            "mic_on": False,
            "keys_down": [],
            "press": None,
            "midi": False,
            "ears_on": False,
        }

    def set(self, **changes: Any) -> None:
        with self.cond:
            self.state.update(changes)
            self.version += 1
            self.cond.notify_all()

    def set_ears(self, png: bytes) -> None:
        with self.cond:
            self.ears = base64.b64encode(png).decode()
            self.ears_version += 1
            self.cond.notify_all()

    def add_pitch(
        self, turn: float, seconds: float, cents: float | None, level: float, voiced: float
    ) -> None:
        """One live reading of the sung pitch, for the turn that started at `turn`."""
        with self.cond:
            if turn != self.trace_turn:
                return
            self.trace.append(
                [round(seconds, 3), None if cents is None else round(cents, 1), round(level, 4)]
            )
            self.state["voiced"] = round(voiced, 2)
            self.version += 1
            self.cond.notify_all()

    def event(self, event: dict[str, Any]) -> None:
        """One line from the rig: update the picture."""
        kind, state = event.get("kind"), self.state
        if kind == "tune":
            notes = list(event.get("notes", []))
            self.set(
                queue=notes, index=-1, results=[None] * len(notes), policy=event.get("policy", ""),
                phase="playing", phase_text="The arm plays", asked=None, sung=None, press=None,
            )  # fmt: skip
        elif kind == "ask":
            self.set(
                index=event.get("index", 0),
                asked=event.get("note"),
                phase="playing",
                phase_text="The arm plays",
            )
        elif kind == "result":
            results = list(state["results"])
            index = event.get("index", 0)
            if 0 <= index < len(results):
                heard, asked = event.get("heard"), state["queue"][index]
                right = bool(heard) and heard[:-1] == NAMES[NOTE_OF[asked] % 12]
                results[index] = {"heard": heard, "ok": right}
                self.set(results=results)
        elif kind == "done":
            self.set(phase="idle", phase_text="Done", asked=None)
        elif kind == "said":
            self.set(said=event.get("text", ""), teacher="", phase="thinking", phase_text="Understanding")
        elif kind == "teacher":
            self.set(teacher=(state["teacher"] + event.get("text", ""))[-400:])
        elif kind == "your_turn":
            started = time.time()
            with self.cond:
                self.trace, self.trace_turn = [], started
            self.set(
                phase="your_turn", phase_text="Your turn: sing now", asked=event.get("note"), sung=None,
                turn={"note": event.get("note"), "seconds": float(event.get("seconds", 6.0)), "t": started},
                voiced=0.0,
            )  # fmt: skip
        elif kind == "sung":
            sung = {k: event.get(k) for k in ("note", "cents", "answer")}
            turn = state["turn"]
            if turn is not None and "ended" not in turn:
                turn = {**turn, "ended": round(time.time() - turn["t"], 2)}
            self.set(sung=sung, phase="feedback", phase_text="Feedback", turn=turn)
        elif kind == "phase":
            self.set(
                phase=event.get("name", "idle"), phase_text=event.get("text", ""), asked=event.get("note")
            )


def events_end() -> int:
    """Where the events file ends right now: lines from before the display started are not replayed."""
    EVENTS.parent.mkdir(parents=True, exist_ok=True)
    EVENTS.touch()
    return EVENTS.stat().st_size


def follow_events(hub: Hub, stop: threading.Event, position: int) -> None:
    """Read new lines of the events file as they are appended."""
    while not stop.is_set():
        try:
            size = EVENTS.stat().st_size
            if size < position:  # the file was cleared
                position = 0
            if size > position:
                with EVENTS.open() as f:
                    f.seek(position)
                    chunk = f.read()
                    position = f.tell()
                for line in chunk.splitlines():
                    try:
                        hub.event(json.loads(line))
                    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                        continue
        except OSError:
            pass
        time.sleep(0.04)


def follow_midi(hub: Hub, port_name: str) -> Any:
    """Light the keys from the keyboard itself. Returns the open port, or None without a keyboard."""
    try:
        import mido

        ports = mido.get_input_names()
        port = next((p for p in ports if port_name == "auto" or port_name.lower() in p.lower()), None)
        if port is None:
            return None
        down: set[int] = set()

        def on_message(msg: Any) -> None:
            if msg.type == "note_on" and msg.velocity > 0:
                down.add(msg.note)
                asked = hub.state["asked"]
                right = None if asked is None else msg.note % 12 == NOTE_OF[asked] % 12
                hub.set(
                    keys_down=sorted(down),
                    press={"note": msg.note, "name": midi_name(msg.note), "ok": right, "t": time.time()},
                )
            elif msg.type in ("note_off", "note_on"):
                down.discard(msg.note)
                hub.set(keys_down=sorted(down))

        opened = mido.open_input(port, callback=on_message)
        hub.set(midi=True)
        return opened
    except Exception as error:  # noqa: BLE001  the display still works without the keyboard
        print(f"No MIDI keyboard for the display ({type(error).__name__}: {error}).")
        return None


def follow_ears(hub: Hub, device: str, stop: threading.Event) -> None:
    """Draw what the robot hears: the same spectrogram, from the same virtual cable, as its ears camera."""
    try:
        import cv2
        import sounddevice as sd
        from keys_common import load_json
        from lerobot_camera_spectrogram.config_spectrogram import SpectrogramCameraConfig
        from lerobot_camera_spectrogram.spectrogram import mel_filterbank, spectrogram_image

        ears = {
            k: v
            for k, v in load_json(ROOT / "scripts" / "keys_cameras_midi.json")["ears"].items()
            if k != "type"
        }
        config = SpectrogramCameraConfig(**{**ears, "device": device})
        rate, window = config.sample_rate, int(config.sample_rate * config.window_s)
        filters = mel_filterbank(rate, config.n_fft, config.n_mels, config.fmin, config.fmax or rate / 2)
        ring = np.zeros(window, dtype=np.float32)
        lock = threading.Lock()

        def callback(indata: np.ndarray, frames: int, _time: Any, _status: Any) -> None:
            with lock:
                ring[:] = np.roll(ring, -frames)
                ring[-frames:] = indata[:frames, 0]

        index = next(
            i
            for i, d in enumerate(sd.query_devices())
            if device.lower() in d["name"].lower() and d["max_input_channels"]
        )
        with sd.InputStream(device=index, channels=1, samplerate=rate, dtype="float32", callback=callback):
            hub.set(ears_on=True)
            while not stop.is_set():
                with lock:
                    audio = ring.copy()
                image = spectrogram_image(audio, config, filters)
                hub.set_ears(cv2.imencode(".png", image[..., 0])[1].tobytes())
                time.sleep(0.1)
    except Exception as error:  # noqa: BLE001  the display still works without the ears
        print(f"No ears picture for the display ({type(error).__name__}: {error}).")


def follow_voice(hub: Hub, mic: str | None, stop: threading.Event) -> None:
    """While it is the user's turn, read the pitch they sing about twenty times a second."""
    try:
        import keys_listen
        import sounddevice as sd

        rate = keys_listen.RATE
        tracker = keys_listen.tracker()
        ring = np.zeros(int(rate * LIVE_WINDOW_S), dtype=np.float32)
        lock = threading.Lock()

        def callback(indata: np.ndarray, frames: int, _time: Any, _status: Any) -> None:
            with lock:
                ring[:] = np.roll(ring, -frames)
                ring[-frames:] = indata[:frames, 0]

        with sd.InputStream(device=mic, channels=1, samplerate=rate, dtype="float32", callback=callback):
            hub.set(mic_on=True)
            turn, voiced, last = 0.0, 0.0, time.time()
            while not stop.is_set():
                time.sleep(0.05)
                now, state = time.time(), hub.state
                current = state["turn"]
                if state["phase"] != "your_turn" or current is None or current.get("note") not in NOTE_OF:
                    last = now
                    continue
                if current["t"] != turn:
                    turn, voiced = current["t"], 0.0
                with lock:
                    audio = ring.copy()
                result = tracker.detect(audio, rate)
                _hz, cents = live_pitch(result.pitch_hz, result.confidence, NOTE_OF[current["note"]])
                if cents is not None:
                    voiced += now - last
                level = float(np.sqrt(np.mean(audio[-rate // 10 :] ** 2)))
                hub.add_pitch(turn, now - turn, cents, level, voiced)
                last = now
    except (Exception, SystemExit) as error:  # noqa: BLE001  the display still works without the microphone
        print(f"No live pitch for the display ({type(error).__name__}: {error}).")


def demo(hub: Hub, stop: threading.Event) -> None:
    """A made-up session, to see the display without the rig. It goes straight to this display only."""

    def show(kind: str, **data: Any) -> None:
        hub.event({"kind": kind, **data})

    tune = ["mi", "mi", "fa", "sol", "sol", "fa", "mi", "re"]
    names = {"do": "C4", "re": "D4", "mi": "E4", "fa": "F4", "sol": "G4", "la": "A4"}
    while not stop.is_set():
        show("phase", name="listening", text="Listening")
        time.sleep(1.5)
        show("said", text="play the first line of Ode to Joy", read_as="song")
        time.sleep(1.0)
        show("tune", notes=tune, policy="flow+smooth+60hz (demo)")
        for index, note in enumerate(tune):
            show("ask", note=note, index=index)
            time.sleep(0.8)
            heard = "F4" if index == 4 else names[note]
            midi = NOTE_OF[note] + (-2 if index == 4 else 0)
            hub.set(keys_down=[midi], press={"note": midi, "name": heard, "ok": index != 4, "t": time.time()})
            time.sleep(0.3)
            hub.set(keys_down=[])
            show("result", index=index, heard=heard)
            time.sleep(0.3)
        show("done")
        time.sleep(1.0)
        show("phase", name="get_ready", text="Get ready", note="mi")
        time.sleep(1.5)
        show("your_turn", note="mi", seconds=6.0)
        hub.set(mic_on=True)
        turn, voiced = hub.trace_turn, 0.0
        for step in range(64):  # a made-up voice: silence, a scoop up from far below, then nearly there
            seconds = step * 0.05
            cents = (
                None
                if seconds < 0.9
                else -27 - 230 * np.exp(-(seconds - 0.9) * 2.2) + 9 * np.sin(seconds * 31)
            )
            voiced += 0.05 if cents is not None else 0.0
            hub.add_pitch(turn, seconds, cents, 0.002 if cents is None else 0.05, min(voiced, 1.0))
            time.sleep(0.05)
        show("sung", note="mi", cents=-27.0, answer="Well done.")
        show("teacher", text="You sang mi and were in tune. Well done.")
        time.sleep(4.0)


def make_handler(hub: Hub) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:  # keep the terminal quiet
            return

        def do_GET(self) -> None:  # noqa: N802  the name http.server calls
            if self.path.split("?")[0] == "/events":
                self.stream()
                return
            body = PAGE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def stream(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            seen, seen_ears, seen_turn, seen_points = -1, -1, 0.0, 0
            try:
                while True:
                    with hub.cond:
                        hub.cond.wait_for(
                            lambda seen=seen, seen_ears=seen_ears: (
                                hub.version != seen or hub.ears_version != seen_ears
                            ),
                            timeout=1.0,
                        )
                        state = json.dumps(hub.state) if hub.version != seen else None
                        ears = hub.ears if hub.ears_version != seen_ears else None
                        seen, seen_ears = hub.version, hub.ears_version
                        if hub.trace_turn != seen_turn:
                            seen_turn, seen_points = hub.trace_turn, 0
                        points = hub.trace[seen_points:]
                        seen_points += len(points)
                    out = ": keep-alive\n\n"
                    if state:
                        out += f"event: state\ndata: {state}\n\n"
                    if points:
                        out += f"event: pitch\ndata: {json.dumps({'turn': seen_turn, 'points': points})}\n\n"
                    if ears:
                        out += f"event: ears\ndata: {ears}\n\n"
                    self.wfile.write(out.encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

    return Handler


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--midi", default="auto", help='Part of the keyboard\'s name, or "none"')
    parser.add_argument("--ears", default="BlackHole 2ch", help='The cable the ears listen on, or "none"')
    parser.add_argument(
        "--mic", default="MacBook Pro Microphone", help='The microphone you sing into, or "none"'
    )
    parser.add_argument("--demo", action="store_true", help="Show a made-up session, no rig needed")
    args = parser.parse_args(argv)

    hub, stop = Hub(), threading.Event()
    threading.Thread(target=follow_events, args=(hub, stop, events_end()), daemon=True).start()
    midi = None if args.midi == "none" else follow_midi(hub, args.midi)
    if args.ears != "none":
        threading.Thread(target=follow_ears, args=(hub, args.ears, stop), daemon=True).start()
    if args.mic != "none" and not args.demo:
        threading.Thread(target=follow_voice, args=(hub, args.mic, stop), daemon=True).start()
    if args.demo:
        threading.Thread(target=demo, args=(hub, stop), daemon=True).start()

    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(hub))
    except OSError:
        stop.set()
        raise SystemExit(
            f"The display is already running: http://localhost:{args.port}\n"
            f"Open that address, or start a second one with --port {args.port + 1}."
        ) from None
    server.daemon_threads = True
    server.block_on_close = False  # open browser tabs must not keep Ctrl+C from stopping the display
    print(f"Stage display: http://localhost:{args.port}   (Ctrl+C stops)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        stop.set()
        if midi is not None:
            midi.close()
        server.server_close()


if __name__ == "__main__":
    main()
