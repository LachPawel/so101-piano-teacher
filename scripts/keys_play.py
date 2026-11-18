#!/usr/bin/env python
"""Let a trained policy play one note on the arm and say, from MIDI, which key it really pressed.

    scripts/keys_play.sh both mi        # camera + MIDI ears policy, 5000 steps
    scripts/keys_play.sh text sol       # camera only policy, 5000 steps
    scripts/keys_play.sh text10 do      # camera only policy, 10000 steps
    scripts/keys_play.sh both all       # all six notes in random order, Enter between them
    scripts/keys_play.sh both all --replan 25   # look again twice as often
    scripts/keys_play.sh both all --smooth      # no pauses: plans ahead while moving
    scripts/keys_play.sh replay all     # no policy: replays your own recorded presses, to check the keyboard position
    scripts/keys_play.sh both song ode  # a tune: one subtask per note, the policy stays loaded
    scripts/keys_play.sh both song "do re mi fa sol la"
    scripts/keys_play.sh flow song "mi sol la" --flow   # the note-to-note policy: stays over the keys
    scripts/keys_play.sh score          # the tally so far

Before each run hold the arm where you started the recordings (fingertip a few cm above the keys) until it
holds itself. Every run is added to outputs/keys_scores/play_log.jsonl.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shlex
import signal
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

from keys_common import read_song
from keys_scripted import open_midi
from keys_synth import note_name
from keys_teleop_check import TASK_NOTES
from keys_ui_events import emit

ROOT = Path(__file__).resolve().parent.parent
RECORDING = "PawelLach/so101_keys_teleop_20261116_212951"
RECORDING_DIR = Path.home() / ".cache" / "huggingface" / "lerobot" / RECORDING
LOG = ROOT / "outputs" / "keys_scores" / "play_log.jsonl"
FRONT = '"observation.images.front": "observation.images.camera1"'
EARS = '"observation.images.ears": "observation.images.camera2"'
MODELS = {
    # Trained from the SmolVLA base on the 300 note-to-note episodes (keys_flow_train.sh), 10,000 steps.
    # It never saw the trip back to the parked pose: use it with `song ... --flow`.
    "flow": ("outputs/keys_models/flow_10k", "keys_cameras_midi.json", True),
    # Plan B: the 5,000-step policies continued for 5,000 more steps on 268 episodes.
    "both2": ("outputs/keys_models/both_v2", "keys_cameras_midi.json", True),
    "text2": ("outputs/keys_models/text_v2", "keys_cameras_front.json", False),
    "both": (
        "outputs/keys_models/both_5k/checkpoints/005000/pretrained_model",
        "keys_cameras_midi.json",
        True,
    ),
    "text": (
        "outputs/keys_models/text_5k/checkpoints/005000/pretrained_model",
        "keys_cameras_front.json",
        False,
    ),
    "text10": (
        "outputs/keys_models/text_10k/checkpoints/010000/pretrained_model",
        "keys_cameras_front.json",
        False,
    ),
}
NOTE_LETTERS = {"do": "C", "re": "D", "mi": "E", "fa": "F", "sol": "G", "la": "A"}
HOLD_S = 0.25  # how long the key stays down after the note sounds, with --once
ORDINALS = {"do": "first", "re": "second", "mi": "third", "fa": "fourth", "sol": "fifth", "la": "sixth"}


def task_text(note: str) -> str:
    return f"play {note}, the {ORDINALS[note]} key"


def verdict(note: str, heard: list[int]) -> str:
    if not heard:
        return "no note"
    if all(n % 12 == TASK_NOTES[note] for n in heard):
        return "correct"
    return "wrong key"


def score() -> None:
    """Per policy: all notes right, first note right, and how many presses it made per run."""
    if not LOG.exists():
        sys.exit("No runs yet.")
    runs = [json.loads(line) for line in LOG.read_text().splitlines() if line.strip()]
    by_model: dict[str, list[dict]] = defaultdict(list)
    for run in runs:
        if not run.get("video"):
            by_model[run["model"]].append(run)
    filmed = sum(bool(run.get("video")) for run in runs)
    for model, group in by_model.items():
        total = len(group)
        strict = sum(run["verdict"] == "correct" for run in group)
        first = sum(bool(run["heard"]) and run["heard"][0][:-1] == NOTE_LETTERS[run["note"]] for run in group)
        once = sum(len(run["heard"]) == 1 for run in group)
        presses = sum(len(run["heard"]) for run in group) / total
        print(
            f"{model:7s} runs {total:2d} | only the right key {strict} | first press right {first} | "
            f"pressed exactly once {once} | presses per run {presses:.1f}   (chance: 1 in 6)"
        )
    if filmed:
        print(f"({filmed} runs made for video are logged but not counted.)")


def ears_cable_running() -> bool:
    out = subprocess.run(["pgrep", "-fl", "keys_synth"], capture_output=True, text=True).stdout
    return "--ears" in out


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) >= 2 and argv[1] == "song":
        parser = argparse.ArgumentParser(prog="keys_play.sh <policy> song")
        parser.add_argument("spec", nargs="?", default="ode", help='A file in scripts/songs, or "do re mi"')
        parser.add_argument("--lines", type=int, default=1, help="How many lines of a song file to play")
        parser.add_argument("--plain", action="store_true", help="Without smooth mode and 60 Hz")
        parser.add_argument(
            "--flow",
            action="store_true",
            help="Stay over the keyboard: switch to the next note when one sounds",
        )
        parser.add_argument("--speed", type=float, choices=(1.0, 1.5, 2.0), default=1.0)
        parser.add_argument("--robot-port", default="/dev/tty.usbmodem5B415331661")
        args = parser.parse_args(argv[2:])
        if argv[0] not in MODELS:
            parser.error(f"unknown policy {argv[0]!r}: {', '.join(MODELS)}")
        song(
            argv[0], song_notes(args.spec, args.lines), args.robot_port,
            smooth=not args.plain, hz=30 if args.plain else 60, speed=args.speed, flow=args.flow,
        )  # fmt: skip
        return
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("model", choices=[*MODELS, "score", "replay", "replay-noon"])
    parser.add_argument("note", nargs="?", choices=[*ORDINALS, "all"])
    parser.add_argument("seconds", nargs="?", type=float, default=9.0)
    parser.add_argument("--robot-port", default="/dev/tty.usbmodem5B415331661")
    parser.add_argument("--video", action="store_true", help="A take for filming: logged, not counted")
    parser.add_argument(
        "--replan", type=int, help="Look again after this many steps (trained default: 50, about 1.7 s)"
    )
    parser.add_argument(
        "--smooth", action="store_true", help="Plan the next piece while still moving (real-time chunking)"
    )
    parser.add_argument(
        "--hz", type=int, choices=(30, 60, 90), default=30, help="Commands per second sent to the motors"
    )
    parser.add_argument(
        "--once", action="store_true", help="End the run when MIDI hears the first note, then lift the arm"
    )
    parser.add_argument(
        "--speed", type=float, choices=(1.0, 1.5, 2.0), default=1.0,
        help="Play the policy's moves this many times faster than they were recorded",
    )  # fmt: skip
    args = parser.parse_args(argv)
    if args.model.startswith("replay"):
        session = "noon" if args.model.endswith("noon") else "night"
        notes = list(ORDINALS) if args.note in (None, "all") else [args.note]
        for note in notes:
            input(
                f"\nReplay of a recorded {note.upper()}. Put the arm in its parked pose, then press Enter. "
            )
            replay(note, session, args.robot_port)
        return
    if args.model == "score":
        score()
        return
    if args.note is None:
        parser.error("which note? do re mi fa sol la, or all")
    if args.note == "all":
        notes = list(ORDINALS)
        random.shuffle(notes)
        for index, note in enumerate(notes, start=1):
            input(
                f"\n[{index}/{len(notes)}] next: {note.upper()}. Hold the arm at the start pose, then press Enter. "
            )
            play(
                args.model,
                note,
                args.seconds,
                args.robot_port,
                args.video,
                args.replan,
                args.smooth,
                args.hz,
                args.speed,
                args.once,
            )
        return
    play(
        args.model,
        args.note,
        args.seconds,
        args.robot_port,
        args.video,
        args.replan,
        args.smooth,
        args.hz,
        args.speed,
        args.once,
    )


def replay(note: str, session: str, robot_port: str) -> None:
    """Send one recorded press back to the arm, exactly as you drove it. No policy, no camera.

    If the keyboard still sits where it was during recording, the replay sounds its own note. If it sounds
    the neighbour, the keyboard (or the arm base) has moved by a key.
    """
    episode = json.loads((ROOT / "outputs" / "keys_refs" / "replay_episodes.json").read_text())[session][note]
    command = [
        "lerobot-replay", "--robot.type=so101_follower", f"--robot.port={robot_port}",
        "--robot.id=workshop_so101", f"--dataset.repo_id={RECORDING}", f"--dataset.root={RECORDING_DIR}",
        f"--dataset.episode={episode}", "--play_sounds=false",
    ]  # fmt: skip
    midi = open_midi("auto", time.perf_counter())
    subprocess.run(command, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.3)
    heard = [n for _, n, velocity in (midi.events if midi else []) if velocity]
    if midi:
        midi.close()
    names = [note_name(n) for n in heard]
    offsets = sorted({(n % 12) - TASK_NOTES[note] for n in heard})
    if not heard:
        verdict_text = "NO NOTE: the fingertip did not reach a key"
    elif offsets == [0]:
        verdict_text = "SAME KEY as recorded: the keyboard is where it was"
    else:
        verdict_text = f"DIFFERENT KEY ({offsets} semitones off): the keyboard or the arm base has moved"
    print(
        f">>> replayed {note.upper()} (episode {episode}, {session}), heard {names or 'nothing'}: {verdict_text}"
    )


BOUNCE_S = 0.7  # a second note-on of the same key this soon is the key bouncing, not a new press
RETURN_S = 3.6  # after a note: time for the arm to get home and for the note to leave the ears' 3 s window


def song_notes(spec: str, lines: int) -> list[str]:
    """A song file in scripts/songs (key numbers 1 to 6), or note names: "do re mi"."""
    names = list(ORDINALS)
    path = ROOT / "scripts" / "songs" / f"{spec}.txt"
    if path.exists():
        return [names[key - 1] for phrase in read_song(path)[:lines] for key in phrase if key]
    notes = spec.replace(",", " ").split()
    if bad := [n for n in notes if n not in ORDINALS]:
        sys.exit(f"Not a song file and not note names: {bad}. Notes are: {' '.join(names)}")
    return notes


def song(
    model: str, notes: list[str], robot_port: str, smooth: bool, hz: int, speed: float, flow: bool = False
) -> None:
    """Play a tune as a chain of subtasks: one text per note, the policy stays loaded in between.

    Default: after each note the arm goes home, then starts the next note from there, as in the recordings.
    flow: the arm stays over the keyboard. The moment a note sounds, the next subtask text is sent and the
    policy carries on from where it is. The `flow` policy was trained this way; the older ones were not.
    """
    seconds = 0.0 if flow else 8.0  # flow is one long run; 0 means no time limit
    command, label = build_command(model, task_text(notes[0]), seconds, robot_port, None, smooth, hz, speed)
    command.append("--interactive=true")
    if flow:
        label += "+flow"
    midi = open_midi("auto", time.perf_counter())
    if midi is None:
        sys.exit("A song needs the MIDI keyboard: it tells the arm when a note has sounded.")
    print(
        f"Policy: {label}   Song: {' '.join(notes)}\nLoading, the first note takes about 20 seconds.",
        flush=True,
    )
    emit("tune", notes=notes, policy=label)
    emit("phase", name="loading", text="Loading the policy")
    process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.PIPE, text=True)

    def tell(line: str) -> None:
        assert process.stdin is not None
        process.stdin.write(line + "\n")
        process.stdin.flush()

    def presses() -> list[tuple[float, int]]:
        """Note-ons as (time, note), with a key's bounce right after its own note-on left out."""
        out: list[tuple[float, int]] = []
        for t, n, velocity in list(midi.events):
            if velocity and not (out and out[-1][1] == n and t - out[-1][0] < BOUNCE_S):
                out.append((t, n))
        return out

    played: list[tuple[str, list[int]]] = []
    try:
        for index, note in enumerate(notes):
            before = len(presses())
            tell(f"/subtask {task_text(note)}")
            if index == 0 or not flow:
                tell("/start")
            emit("ask", note=note, index=index)
            deadline = time.perf_counter() + (60.0 if index == 0 else 11.0)
            while len(presses()) == before and time.perf_counter() < deadline and process.poll() is None:
                time.sleep(0.02)
            if process.poll() is not None:
                print("The rollout stopped by itself.")
                break
            if not flow:
                time.sleep(HOLD_S)
                tell("/reset")
                time.sleep(RETURN_S)
            heard = [n for _, n in presses()[before:]][:1] if flow else [n for _, n in presses()[before:]]
            played.append((note, heard))
            names = [note_name(n) for n in heard]
            emit("result", index=index, heard=names[0] if names else None)
            mark = "ok" if verdict(note, heard[:1]) == "correct" else "MISS"
            print(f"  [{index + 1}/{len(notes)}] {note:3s} -> {names or 'no note'}  {mark}", flush=True)
    finally:
        emit("done")
        if process.poll() is None:
            if flow:
                time.sleep(HOLD_S)
                tell("/reset")
                time.sleep(RETURN_S)
            tell("/stop")
            try:
                process.wait(timeout=25)
            except subprocess.TimeoutExpired:
                process.send_signal(signal.SIGINT)
        midi.close()
    right = sum(verdict(note, heard[:1]) == "correct" for note, heard in played)
    sounded = presses()
    pace = (sounded[-1][0] - sounded[0][0]) / max(len(sounded) - 1, 1) if len(sounded) > 1 else 0.0
    print(
        f"\n>>> song: {right} of {len(played)} notes on the right key, {pace:.1f} s from one note to the next"
    )
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with (LOG.parent / "song_log.jsonl").open("a") as f:
        f.write(
            json.dumps(
                {
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "model": label,
                    "asked": [note for note, _ in played],
                    "heard": [[note_name(n) for n in heard] for _, heard in played],
                    "seconds_per_note": round(pace, 2),
                }
            )
            + "\n"
        )


def build_command(
    model: str,
    task: str,
    seconds: float,
    robot_port: str,
    replan: int | None = None,
    smooth: bool = False,
    hz: int = 30,
    speed: float = 1.0,
) -> tuple[list[str], str]:
    """The lerobot-rollout command for one policy and its settings, and the label the tally files it under."""
    path, cameras, needs_ears = MODELS[model]
    if needs_ears and not ears_cable_running():
        sys.exit(
            "This policy needs its ears, and the piano is running without the cable. Stop the piano tab and "
            "restart it as:\n  uv run --with-requirements scripts/requirements-keys.txt scripts/keys_synth.py "
            '--ears "BlackHole 2ch"'
        )
    rename = f"{{{FRONT}, {EARS}}}" if needs_ears else f"{{{FRONT}}}"
    camera_json = json.dumps(json.loads((ROOT / "scripts" / cameras).read_text()))
    command = [
        "lerobot-rollout", "--strategy.type=base", f"--policy.path={path}",
        "--robot.type=so101_follower", f"--robot.port={robot_port}", "--robot.id=workshop_so101",
        f"--robot.cameras={camera_json}", f"--task={task}", f"--duration={seconds}",
        "--device=mps", f"--rename_map={rename}", "--play_sounds=false",
    ]  # fmt: skip

    label = model
    if replan:
        command.append(f"--policy.n_action_steps={replan}")
        label = f"{model}@{replan}"
    if smooth:
        command.append("--inference.type=rtc")
        label += "+smooth"
    if speed != 1.0:
        # The same recorded steps, sent out faster: a higher control rate, and less time needed for the press.
        command = [arg for arg in command if not arg.startswith("--duration=")]
        command += [f"--fps={round(30 * speed)}", f"--duration={seconds / speed:.1f}"]
        label += f"+{speed:g}x"
        if smooth and os.environ.get("KEYS_SPEED_TUNING", "1") != "0":
            # KEYS_SPEED_TUNING=0 leaves these three settings at the engine's defaults, to test them.
            # Faster playback uses a plan up sooner than this Mac can compute the next one. So: think in
            # half the refinement steps (about half the time), ask for the next plan earlier, and pause
            # instead of giving up if a plan is still late.
            command += [
                "--policy.num_steps=5",
                "--inference.queue_threshold=40",
                "--inference.action_starvation_grace_s=3",
            ]
    if hz != 30:
        # The policy still thinks in 30 steps per second, as recorded; the steps in between are filled in.
        command.append(f"--interpolation_multiplier={hz // 30}")
        label += f"+{hz}hz"
    # Extra lerobot-rollout arguments for experiments, e.g. KEYS_ROLLOUT_ARGS="--inference.log_level=DEBUG".
    if extra := shlex.split(os.environ.get("KEYS_ROLLOUT_ARGS", "")):
        command += extra
        label += "+extra"
    if speed != 1.0 and smooth and os.environ.get("KEYS_SPEED_TUNING", "1") == "0":
        label += "+untuned"
    return command, label


def play(
    model: str,
    note: str,
    seconds: float,
    robot_port: str,
    video: bool = False,
    replan: int | None = None,
    smooth: bool = False,
    hz: int = 30,
    speed: float = 1.0,
    once: bool = False,
) -> None:

    task = task_text(note)
    command, label = build_command(model, task, seconds, robot_port, replan, smooth, hz, speed)

    t0 = time.perf_counter()
    midi = open_midi("auto", t0)
    print(f"Policy: {label}   Task: {task}", flush=True)
    if once:
        label += "+once"
        print(f"Policy: {label}   (stops when the note sounds)", flush=True)
    process = subprocess.Popen(command, cwd=ROOT)
    stopped = False
    while process.poll() is None:
        time.sleep(0.02)
        if once and not stopped and midi and any(velocity for _, _, velocity in midi.events):
            # The note sounded: that is the feedback. Let the key bottom out, then end the run; the
            # rollout lifts the arm back to where it started.
            time.sleep(HOLD_S)
            process.send_signal(signal.SIGINT)
            stopped = True
    result = process
    time.sleep(0.3)
    heard = [note for _, note, velocity in (midi.events if midi else []) if velocity]
    times = [round(t, 2) for t, _, velocity in (midi.events if midi else []) if velocity]
    gaps = [round(b - a, 2) for a, b in zip(times, times[1:], strict=False)]
    if midi:
        midi.close()
    outcome = verdict(note, heard) if midi else "no midi"
    names = [note_name(n) for n in heard]
    print(f"\n>>> asked for {note.upper()}, heard {names or 'nothing'}: {outcome.upper()}", flush=True)
    if gaps:
        print(f"    seconds between the notes: {gaps}", flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(
            json.dumps(
                {
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "model": label,
                    "note": note,
                    "heard": names,
                    "times": times,
                    "verdict": outcome,
                    "rollout_exit": result.returncode,
                    "video": video,
                }
            )
            + "\n"
        )
    score()


if __name__ == "__main__":
    main()
