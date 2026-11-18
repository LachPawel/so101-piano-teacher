"""Tell the stage display what the rig is doing. One line of JSON per event, appended to a file.

    from keys_ui_events import emit
    emit("ask", note="mi", index=2)

The display (keys_ui.py) follows the file. Nothing here can fail the caller: with no display running the
lines are simply never read, and a write error is swallowed.

Events:
  tune       notes=[...], policy="flow+smooth+60hz"   a list of notes is about to be played
  ask        note="mi", index=2                       this subtask was sent to the policy
  result     index=2, heard="E4" or None              MIDI's verdict on that note
  done                                                the list is finished
  said       text="play mi sol la", read_as="play"    what the microphone heard, and how it was read
  teacher    text="..."                               words the voice teacher is speaking (arrives in pieces)
  your_turn  note="mi", seconds=6.0                   the user should sing this note now, within this long
  sung       note="mi", cents=-23.0, answer="A little higher."
  phase      name="listening" | "loading" | "idle" | "get_ready", text="...", note="mi" (optional)
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

EVENTS = Path(__file__).resolve().parent.parent / "outputs" / "keys_ui" / "events.jsonl"


def emit(kind: str, **data: Any) -> None:
    if "PYTEST_CURRENT_TEST" in os.environ:  # the offline tests must not show up on a running display
        return
    try:
        EVENTS.parent.mkdir(parents=True, exist_ok=True)
        with EVENTS.open("a") as f:
            f.write(json.dumps({"t": time.time(), "kind": kind, **data}) + "\n")
    except Exception:  # noqa: BLE001, S110  the display is optional; the arm and the teacher are not
        pass
