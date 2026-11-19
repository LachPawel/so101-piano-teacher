#!/usr/bin/env python
"""Reachy Mini's head and antennas: still while the arm plays, alive the rest of the time.

The arm's camera is Reachy's head, and the policy learned from that exact view. So while the arm plays,
the head goes back to one saved pose and holds it; the teacher waits for it to get there (`lock`) before
the arm sets off. The rest of the time it sways, leans in while you sing, bobs its antennas while the
teacher talks, and wiggles them when you sing in tune. And when someone speaks, it turns to look at them:
the microphone array in its head says where speech comes from (the booth's sound following).

    uv run --with-requirements scripts/requirements-keys.txt scripts/keys_reachy.py save   # keep today's pose
    uv run --with-requirements scripts/requirements-keys.txt scripts/keys_reachy.py show   # print the pose now
    uv run --with-requirements scripts/requirements-keys.txt scripts/keys_reachy.py try    # 6 s of moves
    uv run --with-requirements scripts/requirements-keys.txt scripts/keys_reachy.py doa    # where speech comes from
    uv run --with-requirements scripts/requirements-keys.txt scripts/keys_reachy.py lock   # back to the saved pose
    uv run --with-requirements scripts/requirements-keys.txt scripts/keys_reachy.py align  # match the training view

`align --hand` does the same with the motors off: you hold the head, S saves where you hold it; then
`hold` turns the motors back on and finds the command that keeps the head there despite its weight.

`align` opens a window with Reachy's camera and the training view's outlines on top (orange), and says what
to move so the keyboard and the arm sit where they did in training. The head can fix a small turn or tilt
by itself (press A); a keyboard that is closer, farther or turned has to be moved by hand. S saves the head
pose it ends on as the new saved pose (the old one is kept as reachy_pose.before-align.json).

The stage display (keys_ui.py) moves the head while it runs; nothing else needs starting. It talks to the
Reachy Mini daemon on this computer (the Reachy Mini Control app), at http://127.0.0.1:8000.
"""

from __future__ import annotations

import argparse
import contextlib
import http.client
import json
import math
import os
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
POSE_FILE = Path(os.environ.get("KEYS_REACHY_POSE") or ROOT / "reachy_pose.json")
REFERENCE = Path(__file__).with_name("keys_front_reference.png")  # median of 48 frames of the 300 episodes
CAMERAS = Path(__file__).with_name("keys_cameras_midi.json")
HOST, PORT = "127.0.0.1", 8000
HZ = 25
# The arm counts as playing this long after it says it is done: the policy's last moves, the lift.
SETTLE_S = 0.6

# Looking at whoever speaks. Angles in radians; yaw is positive to Reachy's left.
LOOK_PHASES = {"idle", "listening", "thinking", "your_turn", "feedback"}  # not while the arm or John plays
DOA_EVERY_S = 0.1  # how often the microphone array is asked where speech comes from
CONFIRM_S, CONFIRM_RAD = 0.35, math.radians(25)  # two readings this close in time and angle: a person
GAZE_HOLD_S = 3.5  # keep looking this long after the last speech heard from there
QUIET_AFTER_TALK_S = 1.5  # Reachy's own voice must not turn its head: no new bearings while it speaks
QUIET_AFTER_ARM_S = 1.5  # nor the piano still ringing after the arm played
TURN_MAX = 1.6  # the farthest Reachy turns from the keys, about 90 degrees each way
HEAD_TURN_MAX = 0.6  # the head turns this far on the body; the body turns for the rest
GAZE_PITCH = -0.1  # head pitch while looking at a person: about eye level (the keys are at +0.5)
MAX_STEP = 0.07  # the most yaw or body yaw changes per tick: about 100 degrees per second
# "Back on the saved pose" before the arm may start: told to go there, roughly there, and no longer moving.
# Roughly, because under its own weight the head settles up to about 7 degrees short of where it is told,
# the same way every time (within about 1 degree): what matters is that it was told the saved pose.
NEAR_RAD, NEAR_M = 0.25, 0.01
STILL_RAD, STILL_M = 0.004, 0.001  # change between two readings 0.1 s apart that counts as standing still


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
        if pose.get("body_yaw") is not None:
            body["body_yaw"] = pose["body_yaw"]
        self.call("POST", "/api/move/goto", {**body, "interpolation": "minjerk"})

    def target(self, head: dict[str, float], antennas: list[float], body_yaw: float | None = None) -> None:
        body: dict[str, Any] = {"target_head_pose": head, "target_antennas": antennas}
        if body_yaw is not None:
            body["target_body_yaw"] = body_yaw
        self.call("POST", "/api/move/set_target", body)

    def doa(self) -> tuple[float, bool] | None:
        """Where sound comes from, by the microphone array in the head: (angle, speech). The angle is 0 on
        Reachy's left, pi/2 straight ahead, pi on its right, relative to the head. None with no array."""
        reading = self.call("GET", "/api/state/doa")
        if not reading:
            return None
        angle = float(reading["angle"])
        if not (math.isfinite(angle) and 0.0 <= angle <= math.pi):
            return None
        return angle, bool(reading["speech_detected"])

    def aimed_at(self, pose: dict[str, Any]) -> bool:
        """Whether the head is being told to go to `pose` right now (a goto there, or holding there)."""
        state = self.call("GET", "/api/state/full?with_target_head_pose=true&with_target_body_yaw=true")
        target = state.get("target_head_pose")
        if not target:
            return False
        if any(abs(float(target[k]) - float(pose["head"][k])) > 1e-3 for k in ("x", "y", "z", "roll", "pitch", "yaw")):
            return False
        body = state.get("target_body_yaw")
        return body is None or pose.get("body_yaw") is None or abs(float(body) - float(pose["body_yaw"])) <= 1e-3


def wrap(angle: float) -> float:
    """An angle in (-pi, pi]."""
    return math.atan2(math.sin(angle), math.cos(angle))


def sound_yaw(angle: float, head: dict[str, float]) -> float:
    """The world yaw a microphone-array reading points at. The array turns with the head, so the head's
    roll, pitch and yaw (extrinsic x, y, z) carry it round. Front and back sound the same to it: front."""
    x, y, z = math.sin(angle), math.cos(angle), 0.0
    r, p, w = float(head["roll"]), float(head["pitch"]), float(head["yaw"])
    y, z = y * math.cos(r) - z * math.sin(r), y * math.sin(r) + z * math.cos(r)
    x, z = x * math.cos(p) + z * math.sin(p), -x * math.sin(p) + z * math.cos(p)
    x, y = x * math.cos(w) - y * math.sin(w), x * math.sin(w) + y * math.cos(w)
    return math.atan2(y, x)


class Gaze:
    """Two consistent speech bearings make a person to look at; the look lasts a while after they stop."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.candidate: float | None = None
        self.seen_at = -math.inf
        self.target: float | None = None
        self.until = -math.inf

    def heard(self, yaw: float, now: float) -> None:
        """One speech reading from world yaw `yaw`."""
        consistent = (
            self.candidate is not None
            and now - self.seen_at < CONFIRM_S
            and abs(wrap(yaw - self.candidate)) < CONFIRM_RAD
        )
        self.candidate, self.seen_at = yaw, now
        if consistent:
            self.target, self.until = yaw, now + GAZE_HOLD_S

    def keep(self, now: float, seconds: float) -> None:
        """Keep looking at the same person a little longer (Reachy answers them)."""
        if self.current(now) is not None:
            self.until = max(self.until, now + seconds)

    def current(self, now: float) -> float | None:
        return self.target if now < self.until else None


def aim(pose: dict[str, Any], yaw: float) -> tuple[float, float, float]:
    """Head yaw, head pitch and body yaw that look toward world yaw `yaw`, as far as TURN_MAX from the
    keys. The head turns up to HEAD_TURN_MAX on the body; the body turns for the rest."""
    base_yaw, base_body = float(pose["head"]["yaw"]), float(pose.get("body_yaw") or 0.0)
    turn = max(-TURN_MAX, min(TURN_MAX, wrap(yaw - base_yaw)))
    body = math.copysign(max(0.0, abs(turn) - HEAD_TURN_MAX), turn)
    return base_yaw + turn, GAZE_PITCH, base_body + body


def near(now: dict[str, Any], pose: dict[str, Any]) -> bool:
    """Whether the head (and body) are back on the saved pose, close enough for the policy's camera. A pose
    saved by hand keeps where it was held (`held`) next to the command that settles there (`head`)."""
    a, b = now["head"], pose.get("held") or pose["head"]
    if any(abs(wrap(float(a[k]) - float(b[k]))) > NEAR_RAD for k in ("roll", "pitch", "yaw")):
        return False
    if any(abs(float(a[k]) - float(b[k])) > NEAR_M for k in ("x", "y", "z")):
        return False
    if pose.get("body_yaw") is not None and now.get("body_yaw") is not None:
        return abs(wrap(float(now["body_yaw"]) - float(pose["body_yaw"]))) <= NEAR_RAD
    return True


def travel_s(now: dict[str, Any] | None, pose: dict[str, Any]) -> float:
    """How long the trip back to the saved pose takes: quick from close by, slower from looking away."""
    if now is None:
        return 0.45
    far = max(abs(wrap(float(now["head"][k]) - float(pose["head"][k]))) for k in ("roll", "pitch", "yaw"))
    if pose.get("body_yaw") is not None and now.get("body_yaw") is not None:
        far = max(far, abs(wrap(float(now["body_yaw"]) - float(pose["body_yaw"]))))
    return min(1.4, 0.45 + 0.5 * far)


def still(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Whether two readings of the pose, taken a moment apart, show the head standing still."""
    if any(abs(wrap(float(a["head"][k]) - float(b["head"][k]))) > STILL_RAD for k in ("roll", "pitch", "yaw")):
        return False
    if any(abs(float(a["head"][k]) - float(b["head"][k])) > STILL_M for k in ("x", "y", "z")):
        return False
    if a.get("body_yaw") is not None and b.get("body_yaw") is not None:
        return abs(float(a["body_yaw"]) - float(b["body_yaw"])) <= STILL_RAD
    return True


def lock(timeout: float = 2.5, daemon: Daemon | None = None) -> bool:
    """Bring the head back to the saved pose and wait until it has stopped there: the arm's policy sees
    through it. True once it has (or when there is no Reachy or no saved pose: nothing to wait for)."""
    pose = load()
    if pose is None:
        return True
    daemon = daemon or Daemon()
    end = time.time() + timeout
    try:
        now_pose = daemon.pose()
        aimed_at = getattr(daemon, "aimed_at", None)
        sent, travel = -math.inf, 0.0
        if aimed_at is None or not aimed_at(pose):  # not already on its way there: send it
            travel = travel_s(now_pose, pose)
            daemon.goto(pose, travel)
            sent = time.time()
            time.sleep(min(travel, max(0.0, end - time.time())))
        last = None
        while True:
            now_pose = daemon.pose()
            if last is not None and still(last, now_pose) and near(now_pose, pose):
                return True
            t = time.time()
            if t >= end:
                print("Reachy: the head did not get back to the saved pose in time; the arm starts anyway.")
                return False
            if not near(now_pose, pose) and t - sent > travel + 0.5:  # something else moved it: again
                travel = travel_s(now_pose, pose)
                daemon.goto(pose, travel)
                sent = t
            last = now_pose
            time.sleep(0.1)
    except (OSError, KeyError, TypeError, ValueError):
        return True


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


def follow(hub: Any, stop: threading.Event, daemon: Daemon | None = None, look: bool = True) -> None:
    """Move the head from the stage display's picture of the rig (keys_ui.Hub), until stopped. With `look`,
    it also turns toward whoever speaks while the arm is still."""
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
    print(
        "Reachy: holds the saved pose while the arm plays, moves otherwise"
        + (", and looks at whoever speaks." if look else ".")
    )
    holding, busy_until, last_talk, joy_until, teacher_len = False, 0.0, 0.0, 0.0, 0
    sung_seen, results_seen = None, 0
    smooth_head: dict[str, float] | None = None
    smooth_antennas: list[float] | None = None
    smooth_body: float | None = None
    base_body = float(pose.get("body_yaw") or 0.0)
    gaze, next_doa, doa_ok = Gaze(), 0.0, look
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
                    last = None if smooth_head is None else {"head": smooth_head, "body_yaw": smooth_body}
                    daemon.goto(pose, travel_s(last, pose))
                    holding, smooth_head, smooth_antennas, smooth_body = True, None, None, None
                    gaze.reset()
                continue
            holding = False
            new_mood = mood_of(state, now, last_talk, joy_until)
            if new_mood != mood:
                mood, since = new_mood, now
            head, antennas = moved(pose, *offsets(mood, now, since))
            if smooth_head is None or smooth_antennas is None or smooth_body is None:
                smooth_head, smooth_antennas = dict(pose["head"]), list(pose["antennas"])
                smooth_body = base_body
            # Who is speaking, and from where: not while Reachy itself talks, John sings or a note rings.
            if (
                doa_ok
                and now >= next_doa
                and state.get("phase", "idle") in LOOK_PHASES
                and now - last_talk > QUIET_AFTER_TALK_S
                and now > busy_until + QUIET_AFTER_ARM_S
            ):
                next_doa = now + DOA_EVERY_S
                try:
                    reading = daemon.doa()
                except OSError as error:
                    if "HTTP 404" not in str(error):
                        raise
                    reading, doa_ok = None, False  # an older daemon: no looking, the rest carries on
                    print("Reachy: this daemon gives no sound direction; it won't turn to voices.")
                if reading is not None and reading[1]:
                    gaze.heard(sound_yaw(reading[0], smooth_head), now)
            if mood == "talk":
                gaze.keep(now, 1.0)  # it answers the person it was looking at
            body_target = base_body
            looking = gaze.current(now)
            if looking is not None:
                yaw, pitch, body_target = aim(pose, looking)
                head["pitch"] = pitch + 0.5 * (head["pitch"] - float(pose["head"]["pitch"]))  # keep the nods
                head["yaw"] = yaw
            k = 0.12  # ease toward the target: mood changes glide instead of jump
            for key in head:
                step = k * (head[key] - smooth_head[key])
                if key == "yaw":
                    step = max(-MAX_STEP, min(MAX_STEP, step))
                smooth_head[key] += step
            smooth_body += max(-MAX_STEP, min(MAX_STEP, k * (body_target - smooth_body)))
            smooth_antennas = [a + 0.25 * (b - a) for a, b in zip(smooth_antennas, antennas, strict=True)]
            daemon.target(smooth_head, smooth_antennas, smooth_body)
        except OSError as error:
            print(f"Reachy: {error}. Trying again in 2 s.")
            time.sleep(2.0)
    with contextlib.suppress(OSError):
        daemon.goto(pose, 0.8)  # leave it where the policy expects it


class ViewMatch:
    """How far Reachy's view is from the training view: the picture's shift (pixels at the centre), turn
    (degrees) and size (the training keyboard's size over today's), from features both pictures share."""

    MIN_INLIERS = 25

    def __init__(self, reference: Any) -> None:
        import cv2

        self.cv2 = cv2
        self.orb = cv2.ORB_create(3000)
        self.matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
        self.clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        self.size = reference.shape[1], reference.shape[0]
        self.points, self.features = self.orb.detectAndCompute(self.gray(reference), None)

    def gray(self, image: Any) -> Any:
        return self.clahe.apply(self.cv2.cvtColor(image, self.cv2.COLOR_BGR2GRAY))

    def measure(self, live: Any) -> dict[str, float] | None:
        import numpy as np

        cv2 = self.cv2
        points, features = self.orb.detectAndCompute(self.gray(live), None)
        if features is None or self.features is None or len(points) < 10:
            return None
        pairs = [m[0] for m in self.matcher.knnMatch(features, self.features, k=2) if len(m) == 2 and m[0].distance < 0.75 * m[1].distance]
        if len(pairs) < self.MIN_INLIERS:
            return None
        src = np.float32([points[m.queryIdx].pt for m in pairs])
        dst = np.float32([self.points[m.trainIdx].pt for m in pairs])
        warp, inliers = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=3)
        if warp is None or inliers is None or int(inliers.sum()) < self.MIN_INLIERS:
            return None
        cx, cy = self.size[0] / 2, self.size[1] / 2
        mx, my = warp @ np.array([cx, cy, 1.0])
        return {
            "dx": float(mx - cx),  # move today's picture this far right to match (pixels)
            "dy": float(my - cy),
            "turn": float(np.degrees(np.arctan2(warp[1, 0], warp[0, 0]))),
            "scale": float(np.hypot(warp[0, 0], warp[1, 0])),
            "inliers": int(inliers.sum()),
        }


def advice(m: dict[str, float] | None) -> list[str]:
    """What to do about a view, in plain words."""
    if m is None:
        return [
            "Too different to compare: line up the orange outlines by hand.",
            "Move the keyboard (and the arm with it) or Reachy until they roughly match.",
        ]
    lines = []
    if m["scale"] > 1.08:
        lines.append("The keyboard looks smaller than in training: move it closer to Reachy.")
    elif m["scale"] < 0.92:
        lines.append("The keyboard looks bigger than in training: move it away from Reachy.")
    if abs(m["turn"]) > 6:
        lines.append(f"The view is turned {abs(m['turn']):.0f} degrees: turn the keyboard to match the outlines.")
    if abs(m["dx"]) > 8 or abs(m["dy"]) > 8 or abs(m["turn"]) > 0.6:
        lines.append("Press A: the head turns to line it up.")
    if not lines:
        lines.append("Lined up with training. Press S to save this pose.")
    return lines


def solve3(a: list[list[float]], b: list[float]) -> list[float]:
    """x with a x = b, for a 3x3 a (Cramer's rule); zeros when a is singular."""

    def det(m: list[list[float]]) -> float:
        return (
            m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
            - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
        )

    full = det(a)
    if abs(full) < 1e-9:
        return [0.0, 0.0, 0.0]
    out = []
    for col in range(3):
        m = [row[:] for row in a]
        for r in range(3):
            m[r][col] = b[r]
        out.append(det(m) / full)
    return out


AXES = ("yaw", "pitch", "roll")
PROBE_RAD = 0.06  # how far each axis is moved to learn how the picture moves with it
STEP_MAX = 0.15  # the most one correction moves an axis


def matched(m: dict[str, float] | None) -> bool:
    """Close enough to the training view to save."""
    return m is not None and abs(m["dx"]) < 6 and abs(m["dy"]) < 6 and abs(m["turn"]) < 1 and abs(m["scale"] - 1) < 0.04


def align(daemon: Daemon | None = None, by_hand: bool = False) -> None:
    """The interactive window: Reachy's view with the training outlines, advice, and head fine-tuning.
    `by_hand`: the motors are off and you hold the head; nothing is moved, S saves where you hold it."""
    import cv2
    import numpy as np

    daemon = daemon or Daemon()
    pose = load()
    if pose is None:
        raise SystemExit(f"Save a pose first: keys_reachy.py save ({POSE_FILE})")
    reference = cv2.imread(str(REFERENCE))
    if reference is None:
        raise SystemExit(f"No training view at {REFERENCE}")
    match = ViewMatch(reference)
    edges = cv2.dilate(cv2.Canny(cv2.GaussianBlur(cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY), (5, 5), 0), 40, 110), None)
    camera = json.loads(CAMERAS.read_text())["front"]
    size = int(camera["width"]), int(camera["height"])
    cap = cv2.VideoCapture(int(camera["index_or_path"]), cv2.CAP_AVFOUNDATION)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(camera.get("capture_width", size[0])))
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(camera.get("capture_height", size[1])))
    cap.set(cv2.CAP_PROP_FPS, int(camera.get("capture_fps", camera.get("fps", 30))))
    if not cap.isOpened():
        raise SystemExit("Reachy's camera did not open (is the teacher running? It uses the camera too).")
    command = {**pose, "head": {k: float(v) for k, v in pose["head"].items()}}
    if by_hand:
        status = "Motors off: hold the head where the outlines match. S saves where you hold it, Q quits."
    else:
        daemon.goto(command, travel_s(daemon.pose(), command))
        status = "J/L turn, I/K tilt, U/O roll, A auto, B blend, S save, Q quit"
    blend, was_matched = False, False

    def grab() -> Any:
        frame = None
        for _ in range(4):  # the newest picture, not one from the buffer
            ok, got = cap.read()
            if ok:
                frame = got
        return None if frame is None else cv2.resize(frame, size, interpolation=cv2.INTER_AREA)

    def settle_and_measure(target: dict[str, Any]) -> dict[str, float] | None:
        daemon.goto(target, 0.6)
        time.sleep(1.1)
        readings = [m for m in (match.measure(f) for f in (grab(), grab(), grab()) if f is not None) if m]
        if not readings:
            return None
        return {k: float(np.median([r[k] for r in readings])) for k in readings[0]}

    def auto() -> str:
        nonlocal command
        base = settle_and_measure(command)
        if base is None:
            return "Auto: too different to compare. Line up the outlines by hand first."
        columns = []
        for axis in AXES:  # how the picture moves when each axis moves a little
            probe = {**command, "head": {**command["head"], axis: command["head"][axis] + PROBE_RAD}}
            moved_to = settle_and_measure(probe)
            if moved_to is None:
                return "Auto: lost the match while probing; line up by hand."
            columns.append([(moved_to[k] - base[k]) / PROBE_RAD for k in ("dx", "dy", "turn")])
        jacobian = [[columns[c][r] for c in range(3)] for r in range(3)]
        error = base
        for _ in range(3):
            step = solve3(jacobian, [-error["dx"], -error["dy"], -error["turn"]])
            step = [max(-STEP_MAX, min(STEP_MAX, s)) for s in step]
            command = {**command, "head": {**command["head"], **{a: command["head"][a] + s for a, s in zip(AXES, step)}}}
            now = settle_and_measure(command)
            if now is None:
                return "Auto: lost the match; line up by hand."
            error = now
            if abs(error["dx"]) < 4 and abs(error["dy"]) < 4 and abs(error["turn"]) < 0.5:
                break
        return f"Auto: off by {error['dx']:+.0f}, {error['dy']:+.0f} px and {error['turn']:+.1f} deg. S saves."

    keys = {ord("j"): ("yaw", 0.01), ord("l"): ("yaw", -0.01), ord("i"): ("pitch", -0.01), ord("k"): ("pitch", 0.01),
            ord("u"): ("roll", -0.01), ord("o"): ("roll", 0.01)}  # fmt: skip
    last_measure, measured = 0.0, None
    try:
        while True:
            live = grab()
            if live is None:
                status = "No picture from the camera."
                time.sleep(0.2)
                continue
            if time.time() - last_measure > 0.5:
                measured, last_measure = match.measure(live), time.time()
            view = cv2.addWeighted(reference, 0.5, live, 0.5, 0) if blend else live.copy()
            view[edges > 0] = (39, 120, 216)  # the training outlines, in the deck's orange
            view = cv2.resize(view, (size[0] * 2, size[1] * 2))
            lines = advice(measured) + [status]
            if measured is not None:
                lines.insert(0, f"off by {measured['dx']:+.0f}, {measured['dy']:+.0f} px, turned {measured['turn']:+.1f} deg, "
                                f"size x{measured['scale']:.2f}  ({measured['inliers']} matches)")  # fmt: skip
            if matched(measured) != was_matched:
                was_matched = matched(measured)
                print("MATCHED: hold still" if was_matched else "not matched", flush=True)
            if was_matched:
                cv2.rectangle(view, (0, 0), (view.shape[1] - 1, view.shape[0] - 1), (80, 200, 80), 14)
                cv2.putText(view, "MATCHED", (view.shape[1] // 2 - 150, view.shape[0] - 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 2.0, (80, 200, 80), 5, cv2.LINE_AA)  # fmt: skip
            for n, text in enumerate(lines):
                y = 34 + 32 * n
                cv2.putText(view, text, (16, y), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 0, 0), 4, cv2.LINE_AA)
                cv2.putText(view, text, (16, y), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 1, cv2.LINE_AA)
            cv2.imshow("Reachy align: orange = training view", view)
            key = cv2.waitKey(30) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("b"):
                blend = not blend
            elif by_hand and key in (*keys, ord("a")):
                status = "Motors off: move the head by hand."
            elif key in keys:
                axis, delta = keys[key]
                command = {**command, "head": {**command["head"], axis: command["head"][axis] + delta}}
                daemon.goto(command, 0.3)
            elif key == ord("a"):
                status = "Auto: lining up (about 10 s)..."
                status = auto()
                last_measure = 0.0
            elif key == ord("s"):
                if by_hand:  # exactly where it is held; run `keys_reachy.py hold` after, for the sag
                    command = daemon.pose()
                    print("Saved the held pose:", json.dumps(command["head"]), flush=True)
                backup = POSE_FILE.with_name(POSE_FILE.stem + ".before-align.json")
                if POSE_FILE.is_file() and not backup.exists():
                    backup.write_text(POSE_FILE.read_text())
                POSE_FILE.write_text(json.dumps(command, indent=2) + "\n")
                pose = command
                status = f"Saved to {POSE_FILE.name} (the old one: {backup.name})."
    finally:
        cap.release()
        cv2.destroyAllWindows()
        if not by_hand:
            with contextlib.suppress(OSError):
                daemon.goto(pose, 0.8)


def hold(daemon: Daemon | None = None, tries: int = 5) -> float:
    """Make the head stay where it was held. Under its own weight it droops a few degrees from wherever it
    is told to go, so find the command that settles on the held pose and save it as `head`, keeping the
    held pose as `held`. Turns the motors on first (gently, from where the head is). Returns how far off
    (radians, the worst of roll, pitch and yaw) the head settles in the end."""
    daemon = daemon or Daemon()
    pose = load()
    if pose is None:
        raise SystemExit(f"Save a pose first ({POSE_FILE})")
    held = dict(pose.get("held") or pose["head"])
    now = daemon.pose()
    daemon.target(now["head"], now["antennas"], now.get("body_yaw"))
    daemon.call("POST", "/api/motors/set_mode/enabled")
    command = {**pose, "head": dict(held)}
    axes = ("roll", "pitch", "yaw")
    best: tuple[float, dict[str, Any]] | None = None
    for attempt in range(tries + 1):
        daemon.goto(command, 1.0 if attempt == 0 else 0.6)
        time.sleep(2.0 if attempt == 0 else 1.4)
        settled = daemon.pose()["head"]
        error = {a: float(held[a]) - float(settled[a]) for a in axes}
        worst = max(abs(v) for v in error.values())
        print(f"  off by {math.degrees(worst):.1f} degrees")
        if best is None or worst < best[0]:
            best = (worst, {**command, "head": dict(command["head"])})
        if worst < 0.015:
            break
        command = {**command, "head": {**command["head"], **{a: command["head"][a] + 0.7 * error[a] for a in axes}}}
    assert best is not None
    saved = {**best[1], "held": held}
    POSE_FILE.write_text(json.dumps(saved, indent=2) + "\n")
    daemon.goto(saved, 0.6)
    return best[0]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=["save", "show", "try", "doa", "lock", "align", "hold"])
    parser.add_argument("--hand", action="store_true", help="align with the motors off: you hold the head")
    args = parser.parse_args(argv)
    daemon = Daemon()
    if args.command == "align":
        align(daemon, by_hand=args.hand)
        return
    if args.command == "hold":
        print("Motors on; the head settles on the held pose. Hands off.")
        worst = hold(daemon)
        print(f"Saved. The head settles within {math.degrees(worst):.1f} degrees of where you held it.")
        return
    if args.command == "doa":
        print("Speak from different sides. Ctrl+C stops. (0 = straight ahead, + = Reachy's left)")
        with contextlib.suppress(KeyboardInterrupt):
            while True:
                reading = daemon.doa()
                if reading is None:
                    print("no reading (no microphone array, or none yet)")
                else:
                    world = math.degrees(sound_yaw(reading[0], daemon.pose()["head"]))
                    print(f"{world:+6.0f} deg  {'speech' if reading[1] else '-'}")
                time.sleep(0.2)
        return
    if args.command == "lock":
        print("At the saved pose." if lock(daemon=daemon) else "Not there yet.")
        return
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
