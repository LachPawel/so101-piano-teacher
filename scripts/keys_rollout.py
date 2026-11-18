#!/usr/bin/env python
"""`lerobot-rollout`, with one more chat command for the interactive session: /pause.

    /pause        stop the policy and hold the arm exactly where it is. /start carries on from there.
    /pause 8      the same, then raise the arm 8 degrees at its second servo (shoulder lift)

The stock session only has /reset, which sends the arm back to the pose it had at launch. For the keyboard
that is the wrong place to wait: the policy was trained going from key to key, and its first press after a
trip home is its weakest (do and re land one key to the right). With /pause the arm waits in the air above
the key it just played, which is how almost every training episode begins.

The raise is there because the policy only learned presses: after a note it leaves the finger on the key.
Lifting the shoulder a few degrees takes the finger off, so nothing else gets played while the arm waits.

Takes the same arguments as lerobot-rollout. Nothing in LeRobot itself is changed: the command is added to
the session when it is created.
"""

from __future__ import annotations

import time
from typing import Any

from lerobot.rollout import interactive
from lerobot.scripts import lerobot_rollout


def pause(controller: Any) -> bool:
    """End the running segment without the trip home: `RolloutController.reset` minus the reset.

    The serve loop then reports "Rollout run ended. Robot is holding position", and start() runs again.
    """
    with controller._control_lock:
        if controller._stopped.is_set() or controller._stop_requested.is_set():
            return False
        controller._start_requested.clear()  # last command wins: cancel a pending start
        controller._ctx.policy.inference.pause()
        controller._segment_stop.set()
        controller._wake.set()
        return True


def raise_arm(
    controller: Any, degrees: float, joint: str = "shoulder_lift.pos", seconds: float = 0.4, fps: int = 50
) -> bool:
    """Turn one joint to lift the fingertip, once the segment has ended. Up is the negative direction of the
    shoulder lift on this arm: the taught "above" poses sit 19 to 41 degrees below their "down" poses."""
    degrees = max(0.0, min(float(degrees), 25.0))
    deadline = time.perf_counter() + 2.0
    while controller.running and time.perf_counter() < deadline:
        time.sleep(0.01)
    if controller.running or not degrees:
        return False
    hardware = controller._ctx.hardware
    robot = hardware.robot_wrapper
    observation = robot.get_observation()
    start = {key: float(observation[key]) for key in hardware.initial_position}
    steps = max(int(seconds * fps), 1)
    for step in range(1, steps + 1):
        robot.send_action({**start, joint: start[joint] - degrees * step / steps})
        time.sleep(1 / fps)
    return True


def add_pause_command() -> None:
    original = interactive.InteractiveSession.__init__

    def with_pause(self: Any, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)

        def command(cmd: Any) -> None:
            if not pause(self.controller):
                self._print("Can't pause: the session is stopping.")
                return
            try:
                degrees = float(cmd.args) if cmd.args else 0.0
            except ValueError:
                self._print(f"/pause takes a number of degrees, not {cmd.args!r}.")
                return
            if degrees and not raise_arm(self.controller, degrees):
                self._print("Paused, but the arm was not raised.")

        self._commands["pause"] = (
            command,
            " [degrees]",
            "stop movement, hold the pose, optionally raise the arm",
        )

    interactive.InteractiveSession.__init__ = with_pause  # type: ignore[method-assign]


if __name__ == "__main__":
    add_pause_command()
    lerobot_rollout.main()
