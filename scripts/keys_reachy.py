#!/usr/bin/env python
"""Reachy Mini's head and antennas: still while the arm plays, alive the rest of the time.

The arm's camera is Reachy's head, and the policy learned from that exact view. So while the arm plays,
the head goes back to one saved pose and holds it. The rest of the time it sways, leans in while you sing,
bobs its antennas while the teacher talks, and wiggles them when you sing in tune.

    uv run --with-requirements scripts/requirements-keys.txt scripts/keys_reachy.py save   # keep today's pose
    uv run --with-requirements scripts/requirements-keys.txt scripts/keys_reachy.py show   # print the pose now
    uv run --with-requirements scripts/requirements-keys.txt scripts/keys_reachy.py try    # 6 s of moves

The stage display (keys_ui.py) moves the head while it runs; nothing else needs starting. It talks to the
Reachy Mini daemon on this computer (the Reachy Mini Control app), at http://127.0.0.1:8000.
"""

from __future__ import annotations

import argparse
import contextlib
import http.client
import json
import math
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
POSE_FILE = ROOT / "reachy_pose.json"
HOST, PORT = "127.0.0.1", 8000
HZ = 25
# The arm counts as playing this long after it says it is done: the policy's last moves, the lift.
SETTLE_S = 0.6


class Daemon:
    """The Reachy Mini daemon's HTTP API, on one kept-open connection."""

    def __init__(self, host: str = HOST, port: int = PORT) -> None:
        self.host, self.port = host, port
        self.connection: http.client.HTTPConnection | None = None

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        for attempt in (1, 2):  # a dropped keep-alive connection is reopened once
            try:
                if self.connection is None:
                    self.connection = http.client.HTTPConnection(self.host, self.port, timeout=1.5)
                data = None if body is None else json.dumps(body)
                headers = {} if body is None else {"Content-Type": "application/json"}
                self.connection.request(method, path, body=data, headers=headers)
                response = self.connection.getresponse()
                text = response.read()
                if response.status >= 400:
                    raise OSError(f"{method} {path}: HTTP {response.status} {text[:200]!r}")
                return json.loads(text) if text else None
            except (OSError, http.client.HTTPException):
                if self.connection is not None:
                    self.connection.close()
                self.connection = None
                if attempt == 2:
                    raise
        return None

    def pose(self) -> dict[str, Any]:
        """The head pose (metres, radians), the antennas and the body yaw right now."""
        state = self.call("GET", "/api/state/full")
        return {
            "head": state["head_pose"],
            "antennas": state["antennas_position"],
            "body_yaw": state["body_yaw"],
        }

    def goto(self, pose: dict[str, Any], seconds: float) -> None:
        body = {"head_pose": pose["head"], "antennas": pose["antennas"], "duration": seconds}
        self.call("POST", "/api/move/goto", {**body, "interpolation": "minjerk"})

    def target(self, head: dict[str, float], antennas: list[float]) -> None:
        self.call("POST", "/api/move/set_target", {"target_head_pose": head, "target_antennas": antennas})


def save(daemon: Daemon, path: Path | None = None) -> dict[str, Any]:
    pose = daemon.pose()
    (path or POSE_FILE).write_text(json.dumps(pose, indent=2) + "\n")
    return pose


def load(path: Path | None = None) -> dict[str, Any] | None:
    path = path or POSE_FILE
    return json.loads(path.read_text()) if path.is_file() else None


def offsets(mood: str, t: float, since: float) -> tuple[dict[str, float], tuple[float, float]]:
    """How far the head (radians, metres) and the antennas (radians) move from the saved pose, for a mood
    at time t; `since` is when the mood began. Small on purpose: Reachy stays looking at the keys."""
    sway = math.sin(2 * math.pi * t / 7.0)
    if mood == "listen":  # you sing: lean in, head tilted, antennas up and still-ish
        head = {"roll": 0.18, "pitch": -0.12 + 0.02 * sway, "yaw": 0.05 * sway, "z": 0.006}
        flick = 0.08 * math.sin(2 * math.pi * t * 0.9)
        return head, (0.55 + flick, -0.55 - flick)
    if mood == "talk":  # the teacher speaks: small nods, antennas bobbing
        bob = math.sin(2 * math.pi * t * 2.6)
        head = {"roll": 0.03 * sway, "pitch": -0.1 + 0.035 * bob, "yaw": 0.12 * sway, "z": 0.003 * bob}
        return head, (0.35 + 0.3 * bob, -0.35 + 0.3 * bob)
    if mood == "joy":  # in tune: a happy bounce and fast antenna wiggles
        wiggle = math.sin(2 * math.pi * t * 6.0)
        bounce = abs(math.sin(2 * math.pi * (t - since) * 2.0))
        head = {
            "roll": 0.12 * math.sin(2 * math.pi * t * 2.0),
            "pitch": -0.15,
            "yaw": 0.0,
            "z": 0.01 * bounce,
        }
        return head, (0.6 + 0.5 * wiggle, -0.6 + 0.5 * wiggle)
    # idle: look up from the keys a little, sway, antennas drifting
    drift = math.sin(2 * math.pi * t / 3.3)
    head = {
        "roll": 0.05 * math.sin(2 * math.pi * t / 5.1),
        "pitch": -0.08 + 0.03 * drift,
        "yaw": 0.22 * sway,
        "z": 0.0,
    }
    return head, (0.2 + 0.25 * drift, -0.2 + 0.25 * math.sin(2 * math.pi * t / 2.9))


def moved(
    pose: dict[str, Any], head: dict[str, float], antennas: tuple[float, float]
) -> tuple[dict[str, float], list[float]]:
    """The saved pose plus the offsets."""
    base = pose["head"]
    target = {key: float(base[key]) + head.get(key, 0.0) for key in ("x", "y", "z", "roll", "pitch", "yaw")}
    left, right = pose["antennas"]
    return target, [float(left) + antennas[0], float(right) + antennas[1]]


def mood_of(state: dict[str, Any], now: float, last_talk: float, joy_until: float) -> str:
    if now < joy_until:
        return "joy"
    if state.get("phase") == "your_turn":
        return "listen"
    if now - last_talk < 0.8:
        return "talk"
    return "idle"


def follow(hub: Any, stop: threading.Event, daemon: Daemon | None = None) -> None:
    """Move the head from the stage display's picture of the rig (keys_ui.Hub), until stopped."""
    daemon = daemon or Daemon()
    pose = load()
    if pose is None:
        print(f"Reachy stays still: no saved pose. Run `scripts/keys_reachy.py save` first ({POSE_FILE}).")
        return
    try:
        daemon.pose()
    except OSError as error:
        print(f"Reachy stays still: no Reachy Mini daemon at {HOST}:{PORT} ({error}).")
        return
    print("Reachy: holds the saved pose while the arm plays, moves otherwise.")
    holding, busy_until, last_talk, joy_until, teacher_len = False, 0.0, 0.0, 0.0, 0
    sung_seen, results_seen = None, 0
    smooth_head: dict[str, float] | None = None
    smooth_antennas: list[float] | None = None
    mood, since = "idle", time.time()
    while not stop.is_set():
        time.sleep(1 / HZ)
        now, state = time.time(), dict(hub.state)
        if state.get("arm_busy"):
            busy_until = now + SETTLE_S
        teacher = state.get("teacher") or ""
        if len(teacher) != teacher_len:
            last_talk, teacher_len = now, len(teacher)
        sung = state.get("sung")
        if sung is not None and sung is not sung_seen and state.get("phase") == "feedback":
            sung_seen = sung
            if sung.get("cents") is not None and abs(sung["cents"]) <= 50:
                joy_until = now + 1.8
        done = [r for r in state.get("results") or [] if r]
        if len(done) != results_seen:
            results_seen = len(done)
            if (
                done
                and len(done) == len(state.get("results") or [])
                and all(r["ok"] for r in done)
                and len(done) > 1
            ):
                joy_until = now + 1.8
        try:
            if now < busy_until:  # the arm plays: the camera must see what the policy learned from
                if not holding:
                    daemon.goto(pose, 0.45)
                    holding, smooth_head, smooth_antennas = True, None, None
                continue
            holding = False
            new_mood = mood_of(state, now, last_talk, joy_until)
            if new_mood != mood:
                mood, since = new_mood, now
            head, antennas = moved(pose, *offsets(mood, now, since))
            if smooth_head is None or smooth_antennas is None:
                smooth_head, smooth_antennas = dict(pose["head"]), list(pose["antennas"])
            k = 0.12  # ease toward the target: mood changes glide instead of jump
            for key in head:
                smooth_head[key] += k * (head[key] - smooth_head[key])
            smooth_antennas = [a + 0.25 * (b - a) for a, b in zip(smooth_antennas, antennas, strict=True)]
            daemon.target(smooth_head, smooth_antennas)
        except OSError as error:
            print(f"Reachy: {error}. Trying again in 2 s.")
            time.sleep(2.0)
    with contextlib.suppress(OSError):
        daemon.goto(pose, 0.8)  # leave it where the policy expects it


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=["save", "show", "try"])
    args = parser.parse_args(argv)
    daemon = Daemon()
    if args.command == "save":
        pose = save(daemon)
        print(f"Saved to {POSE_FILE}: {json.dumps(pose)}")
    elif args.command == "show":
        print(json.dumps(daemon.pose(), indent=2))
    else:
        pose = load()
        if pose is None:
            raise SystemExit("Save a pose first: keys_reachy.py save")
        start = time.time()
        for mood in ("idle", "talk", "listen", "joy"):
            print(mood)
            until = time.time() + 1.5
            while time.time() < until:
                head, antennas = moved(pose, *offsets(mood, time.time() - start, until - 1.5))
                daemon.target(head, antennas)
                time.sleep(1 / HZ)
        daemon.goto(pose, 0.8)
        print("Back at the saved pose.")


if __name__ == "__main__":
    main()
