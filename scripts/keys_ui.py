#!/usr/bin/env python
"""The stage display: the 3D page in web/, live from the rig.

    uv run --with-requirements scripts/requirements-keys.txt scripts/keys_ui.py
    uv run --with-requirements scripts/requirements-keys.txt scripts/keys_ui.py --demo    # no rig: a made-up tune

Then open http://localhost:8770 and make the window full screen. Leave this running in its own tab.

It serves the same page as the web demo (web/ in the so101-piano-teacher repository). Served from here, the
page switches to live mode: the 3D arm aims at the note the policy is asked to play and presses the key MIDI
hears, the big words say whose turn it is, and while you sing your pitch is drawn as you sing it.

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
from urllib.parse import unquote, urlsplit

import numpy as np
from keys_ui_events import EVENTS

ROOT = Path(__file__).resolve().parent.parent
# The page: web/ next to scripts/ in the public repository, else that repository checked out beside this one.
WEB_DIRS = (ROOT / "web", Path.home() / "code" / "so101-piano-teacher" / "web")
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",  # module scripts load only with a JavaScript type
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".stl": "application/octet-stream",
    ".urdf": "application/xml",
}
NOTE_OF = {"do": 60, "re": 62, "mi": 64, "fa": 65, "sol": 67, "la": 69}
NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


LIVE_WINDOW_S = 0.4  # the newest sound the live pitch is read from
EDGE_FRAMES = 3  # SwiftF0's last few frames run off the end of the sound and are never sure
PIANO_TAIL_S = 0.2  # the software piano dies away this fast once its key is up
KEEP_PITCH_S = 15.0  # how much pitch history a newly opened page gets


def midi_name(note: int) -> str:
    return f"{NAMES[note % 12]}{note // 12 - 1}"


def fold_cents(hz: float, target_midi: int) -> float:
    """Cents above (+) or below (-) the target, in whichever octave it was sung: -600 to 600."""
    cents = 1200 * np.log2(hz / (440.0 * 2 ** ((target_midi - 69) / 12)))
    return float((cents + 600) % 1200 - 600)


def hz_to_midi(hz: float) -> float:
    return float(69 + 12 * np.log2(hz / 440.0))


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
        # The pitch heard, always: [time, MIDI note as a float or None, level, 1 while the piano sounds else 0].
        # While the piano sounds the microphone hears it too, so those readings may not be the voice.
        self.pitch: list[tuple[int, list[float | None]]] = []  # (number, reading), oldest first
        self.pitch_count = 0
        self.piano_until = 0.0  # when the last key came up, plus the piano's tail
        self.speaker = ""  # who spoke last, "you" or "teacher": the other one starts a fresh line
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
            "arm_busy": False,
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

    def add_pitch(self, t: float, midi: float | None, level: float, piano: bool = False) -> None:
        """One live reading of the pitch heard at time `t`."""
        with self.cond:
            self.pitch_count += 1
            reading = [round(t, 3), None if midi is None else round(midi, 3), round(level, 4), int(piano)]
            self.pitch.append((self.pitch_count, reading))
            while self.pitch and self.pitch[0][1][0] < t - KEEP_PITCH_S:
                self.pitch.pop(0)
            self.cond.notify_all()

    def pitch_since(self, number: int) -> tuple[int, list[list[float | None]]]:
        """The readings after reading `number`, and the newest number. Call with the lock held."""
        return self.pitch_count, [reading for n, reading in self.pitch if n > number]

    def event(self, event: dict[str, Any]) -> None:
        """One line from the rig: update the picture."""
        kind, state = event.get("kind"), self.state
        if kind == "tune":
            notes = list(event.get("notes", []))
            self.set(
                queue=notes, index=-1, results=[None] * len(notes), policy=event.get("policy", ""),
                phase="playing", phase_text="The arm plays", asked=None, sung=None, press=None, arm_busy=True,
            )  # fmt: skip
        elif kind == "ask":
            self.set(
                index=event.get("index", 0),
                asked=event.get("note"),
                phase="playing",
                phase_text="The arm plays",
                arm_busy=True,
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
            if state["phase"] not in ("your_turn", "feedback"):  # the arm finishes while you already sing
                self.set(phase="idle", phase_text="Done", asked=None, arm_busy=False)
            else:
                self.set(arm_busy=False)
        elif kind == "said":  # the request as the teacher read it: the arm or the lesson will follow
            self.speaker = "you"
            self.set(said=event.get("text", ""), teacher="", phase="thinking", phase_text="Understanding")
        elif kind == "teacher":
            fresh, self.speaker = self.speaker != "teacher", "teacher"
            self.set(teacher=(("" if fresh else state["teacher"]) + event.get("text", ""))[-400:])
        elif kind == "you":
            fresh, self.speaker = self.speaker != "you", "you"
            self.set(said=(("" if fresh else state["said"]) + event.get("text", ""))[-300:])
        elif kind == "your_turn":
            started = time.time()
            self.set(
                phase="your_turn", phase_text="Your turn: sing now", asked=event.get("note"), sung=None,
                turn={"note": event.get("note"), "seconds": float(event.get("seconds", 6.0)), "t": started},
            )  # fmt: skip
        elif kind == "sung":
            sung = {k: event.get(k) for k in ("note", "cents", "answer")}
            turn = state["turn"]
            if turn is not None and "ended" not in turn:
                turn = {**turn, "ended": round(time.time() - turn["t"], 2)}
            self.set(sung=sung, phase="feedback", phase_text="Feedback", turn=turn)
        elif kind == "phase":
            name = event.get("name", "idle")
            self.set(
                phase=name,
                phase_text=event.get("text", ""),
                asked=event.get("note"),
                arm_busy=state["arm_busy"] or name == "loading",
            )


def find_web(path: str | None) -> Path:
    """The folder of the page: the one asked for, else the first of WEB_DIRS that has an index.html."""
    for folder in [Path(path).expanduser()] if path else WEB_DIRS:
        if (folder / "index.html").is_file():
            return folder.resolve()
    raise SystemExit(
        f"No page to serve: no index.html in {path or ' or '.join(str(d) for d in WEB_DIRS)}.\n"
        "Clone https://github.com/LachPawel/so101-piano-teacher next to this repository, or pass --web."
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
                if not down:
                    hub.piano_until = time.time() + PIANO_TAIL_S
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


def listening(state: dict[str, Any], now: float, piano_until: float) -> bool:
    """Whether what the microphone hears now is the user: only in their turn, once the piano is silent.
    Before that the room holds the piano and the teacher's voice, which are not the user's pitch."""
    return state["phase"] == "your_turn" and not state["keys_down"] and now >= piano_until


def follow_voice(hub: Hub, mic: str | None, stop: threading.Event) -> None:
    """In the user's turn, read the pitch at the microphone twenty times a second. Silent otherwise."""
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
            heard_last = False
            while not stop.is_set():
                time.sleep(0.05)
                now = time.time()
                if not listening(hub.state, now, hub.piano_until):
                    if heard_last:  # one empty reading ends the line
                        hub.add_pitch(now, None, 0.0)
                        heard_last = False
                    continue
                with lock:
                    audio = ring.copy()
                result = tracker.detect(audio, rate)
                hz, _cents = live_pitch(result.pitch_hz, result.confidence, 69)
                level = float(np.sqrt(np.mean(audio[-rate // 10 :] ** 2)))
                hub.add_pitch(now, None if hz is None else hz_to_midi(hz), level)
                heard_last = True
    except (Exception, SystemExit) as error:  # noqa: BLE001  the display still works without the microphone
        print(f"No live pitch for the display ({type(error).__name__}: {error}).")


def demo(hub: Hub, stop: threading.Event) -> None:
    """A made-up session, to see the display without the rig. It goes straight to this display only."""
    hub.set(mic_on=True)
    target: list[int | None] = [None]  # the note a made-up singer follows, a little late and a little flat

    def voice() -> None:
        start, sung = time.time(), 64.0
        while not stop.is_set():
            now = time.time()
            if not listening(hub.state, now, hub.piano_until):
                sung = 60.5  # starts each turn a little low, then finds the note
                time.sleep(0.05)
                continue
            if target[0] is not None:
                sung += (target[0] - 0.2 - sung) * 0.12
            singing = int((now - start) / 2.3) % 4 != 3  # breathes now and then
            wobble = 0.08 * np.sin(now * 33)
            hub.add_pitch(now, sung + wobble if singing else None, 0.05 if singing else 0.002)
            time.sleep(0.05)

    threading.Thread(target=voice, daemon=True).start()

    def show(kind: str, **data: Any) -> None:
        hub.event({"kind": kind, **data})
        if kind == "ask":
            target[0] = NOTE_OF[data["note"]]

    tune = ["mi", "mi", "fa", "sol", "sol", "fa", "mi", "re"]
    names = {"do": "C4", "re": "D4", "mi": "E4", "fa": "F4", "sol": "G4", "la": "A4"}
    while not stop.is_set():
        target[0] = None
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
            hub.piano_until = time.time() + PIANO_TAIL_S
            show("result", index=index, heard=heard)
            time.sleep(0.5)
        show("done")
        target[0] = None
        time.sleep(1.5)
        show("tune", notes=["mi"], policy="flow+smooth+60hz (demo)")
        show("ask", note="mi", index=0)
        time.sleep(1.2)
        hub.set(keys_down=[64], press={"note": 64, "name": "E4", "ok": True, "t": time.time()})
        show("result", index=0, heard="E4")
        show("your_turn", note="mi", seconds=6.0)  # the window opens as the key sounds: sing along
        time.sleep(1.0)
        hub.set(keys_down=[])
        hub.piano_until = time.time() + PIANO_TAIL_S
        show("done")
        time.sleep(2.6)
        show("sung", note="mi", cents=-20.0, answer="Well done.")
        show("teacher", text="You sang mi and were in tune. Well done.")
        target[0] = None
        time.sleep(4.0)


def make_handler(hub: Hub, web: Path) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:  # keep the terminal quiet
            return

        def do_GET(self) -> None:  # noqa: N802  the name http.server calls
            path = urlsplit(self.path).path
            if path == "/events":
                self.stream()
            elif path == "/state":  # its answer is how the page knows it runs live
                with hub.cond:
                    body = json.dumps(hub.state).encode()
                self.send(200, "application/json", body)
            else:
                file = (web / (unquote(path).lstrip("/") or "index.html")).resolve()
                if not file.is_relative_to(web) or not file.is_file():
                    self.send(404, "text/plain; charset=utf-8", b"Not found")
                else:
                    kind = TYPES.get(file.suffix.lower(), "application/octet-stream")
                    self.send(200, kind, file.read_bytes())

        def send(self, status: int, kind: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def stream(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            seen, seen_ears, seen_pitch = -1, -1, 0
            try:
                while True:
                    with hub.cond:
                        hub.cond.wait_for(
                            lambda seen=seen, seen_ears=seen_ears, seen_pitch=seen_pitch: (
                                hub.version != seen
                                or hub.ears_version != seen_ears
                                or hub.pitch_count != seen_pitch
                            ),
                            timeout=1.0,
                        )
                        state = json.dumps(hub.state) if hub.version != seen else None
                        ears = hub.ears if hub.ears_version != seen_ears else None
                        seen, seen_ears = hub.version, hub.ears_version
                        seen_pitch, points = hub.pitch_since(seen_pitch)
                    out = ": keep-alive\n\n"
                    if state:
                        out += f"event: state\ndata: {state}\n\n"
                    if points:
                        out += f"event: pitch\ndata: {json.dumps(points)}\n\n"
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
    parser.add_argument("--web", help="The folder of the page (default: web/ of so101-piano-teacher)")
    parser.add_argument(
        "--reachy",
        choices=["auto", "none"],
        default="auto",
        help="Move Reachy Mini's head between notes (keys_reachy.py). Off with --demo",
    )
    args = parser.parse_args(argv)
    web = find_web(args.web)

    hub, stop = Hub(), threading.Event()
    threading.Thread(target=follow_events, args=(hub, stop, events_end()), daemon=True).start()
    midi = None if args.midi == "none" else follow_midi(hub, args.midi)
    if args.ears != "none":
        threading.Thread(target=follow_ears, args=(hub, args.ears, stop), daemon=True).start()
    if args.mic != "none" and not args.demo:
        threading.Thread(target=follow_voice, args=(hub, args.mic, stop), daemon=True).start()
    if args.demo:
        threading.Thread(target=demo, args=(hub, stop), daemon=True).start()
    reachy = None
    if args.reachy == "auto" and not args.demo:
        import keys_reachy

        reachy = threading.Thread(target=keys_reachy.follow, args=(hub, stop), daemon=True)
        reachy.start()

    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(hub, web))
    except OSError:
        stop.set()
        raise SystemExit(
            f"The display is already running: http://localhost:{args.port}\n"
            f"Open that address, or start a second one with --port {args.port + 1}."
        ) from None
    server.daemon_threads = True
    server.block_on_close = False  # open browser tabs must not keep Ctrl+C from stopping the display
    print(f"Stage display: http://localhost:{args.port}   (Ctrl+C stops)   page: {web}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        stop.set()
        if reachy is not None:
            reachy.join(timeout=2.0)  # it puts the head back where the policy expects it
        if midi is not None:
            midi.close()
        server.server_close()


if __name__ == "__main__":
    main()
