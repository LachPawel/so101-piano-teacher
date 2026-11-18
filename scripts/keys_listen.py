#!/usr/bin/env python
"""Listen to you sing or play one note at a time, and say which notes they were.

Uses SwiftF0, a small neural pitch tracker (about 96k parameters, CPU, 16 ms steps). It follows ONE voice or
instrument: a sung line or a guitar riff played note by note. Strummed chords come out as nonsense.

    R="uv run --with-requirements scripts/requirements-keys.txt"
    $R scripts/keys_listen.py riff                 # 6 s from the microphone: the notes, and the keys the arm has
    $R scripts/keys_listen.py riff --seconds 10 --mic "MacBook Pro Microphone"
    $R scripts/keys_listen.py riff --wav take.wav  # the same from a recording
    $R scripts/keys_listen.py check mi             # sing mi: flat, in tune or sharp, in cents, and how steady

`riff` also prints the command that makes the arm play the notes back. The arm only has six white keys,
C D E F G A (do re mi fa sol la); notes it does not have are shown as "-" and left out. Octaves are ignored.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import keys_companion as kc
import numpy as np
from keys_common import read_wav
from numpy.typing import NDArray

RATE = 16000
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
SOLFEGE = {0: "do", 2: "re", 4: "mi", 5: "fa", 7: "sol", 9: "la"}  # pitch class -> the arm's six keys
MIDI = {"do": 60, "re": 62, "mi": 64, "fa": 65, "sol": 67, "la": 69}
MIN_NOTE_S = 0.08  # shorter than this is a slide or a finger noise, not a note
_tracker: Any = None


@dataclass
class Heard:
    start: float
    end: float
    hz: float

    @property
    def midi(self) -> int:
        return int(round(69 + 12 * np.log2(self.hz / 440.0)))

    @property
    def cents(self) -> float:
        """How far from the nearest piano note: negative is flat."""
        return float(100 * (69 + 12 * np.log2(self.hz / 440.0) - self.midi))

    @property
    def name(self) -> str:
        return f"{NOTE_NAMES[self.midi % 12]}{self.midi // 12 - 1}"

    @property
    def key(self) -> str | None:
        """The arm's key for this note in any octave, or None for a note it does not have."""
        return SOLFEGE.get(self.midi % 12)


def tracker() -> Any:
    """SwiftF0, loaded once."""
    global _tracker
    if _tracker is None:
        try:
            from swift_f0 import SwiftF0
        except ImportError as error:
            raise SystemExit(
                "SwiftF0 is not installed: run with "
                "`uv run --with-requirements scripts/requirements-keys.txt ...`"
            ) from error
        _tracker = SwiftF0()
    return _tracker


def heard_notes(audio: NDArray[np.float32], rate: int = RATE, min_s: float = MIN_NOTE_S) -> list[Heard]:
    """The notes in a sung or played line, in order."""
    from swift_f0 import segment_notes

    result = tracker().detect(np.ascontiguousarray(audio, dtype=np.float32), rate)
    notes = [Heard(float(n.start), float(n.end), float(n.pitch_hz)) for n in segment_notes(result)]
    return [note for note in notes if note.end - note.start >= min_s]


def arm_keys(notes: list[Heard]) -> list[str]:
    """The notes the arm can play back, in order. Notes it does not have are left out."""
    return [note.key for note in notes if note.key]


def voiced_seconds(audio: NDArray[np.float32], rate: int = RATE) -> float:
    """How much of the audio holds a clear pitch: a voice singing, as opposed to breath, speech gaps or silence."""
    if audio.size < rate // 4:
        return 0.0
    result = tracker().detect(np.ascontiguousarray(audio, dtype=np.float32), rate)
    return float(np.count_nonzero(result.confidence >= 0.5) * 0.016)


def check_note(audio: NDArray[np.float32], target_midi: int, rate: int = RATE) -> dict[str, Any]:
    """One sung note against a target: cents off (any octave), the verdict, and how much the pitch wobbled."""
    result = tracker().detect(np.ascontiguousarray(audio, dtype=np.float32), rate)
    voiced = result.pitch_hz[result.confidence >= 0.5]
    if len(voiced) < 10:  # under 0.16 s of clear pitch
        return {
            "hz": None,
            "cents": None,
            "wobble_cents": None,
            "answer": kc.judge_pitch(None, target_midi)[1],
        }
    hz = float(np.median(voiced))
    cents, answer = kc.judge_pitch(hz, target_midi)
    wobble = float(np.std(1200 * np.log2(voiced / hz)))
    return {
        "hz": round(hz, 2),
        "cents": round(float(cents or 0.0), 1),
        "wobble_cents": round(wobble, 1),
        "answer": answer,
    }


def describe(notes: list[Heard]) -> str:
    if not notes:
        return "No clear notes. One note at a time, close to the microphone."
    lines = [
        f"  {note.start:5.2f} s  {note.name:4s} {note.cents:+4.0f} cents   arm key: {note.key or '-'}"
        for note in notes
    ]
    keys = arm_keys(notes)
    if keys:
        lines.append(f'The arm can answer with: scripts/keys_play.sh both song "{" ".join(keys)}"')
    else:
        lines.append("None of these notes is on the arm's six keys (C D E F G A).")
    return "\n".join(lines)


def load_audio(wav: Path | None, seconds: float, mic: str | None) -> tuple[NDArray[np.float32], int]:
    if wav is not None:
        return read_wav(wav)
    print(f"Listening for {seconds:.0f} s...", flush=True)
    return kc.Microphone(mic, RATE).record(seconds), RATE


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, seconds, text in (("riff", 6.0, "Notes of a line"), ("check", 2.5, "One sung note")):
        p = sub.add_parser(name, help=text)
        if name == "check":
            p.add_argument("note", choices=list(MIDI))
        p.add_argument("--seconds", type=float, default=seconds)
        p.add_argument("--mic", help="Microphone (default: the system input)")
        p.add_argument("--wav", type=Path, help="Analyse this 16-bit WAV instead of the microphone")
    args = parser.parse_args(argv)

    audio, rate = load_audio(args.wav, args.seconds, args.mic)
    if args.command == "riff":
        print(describe(heard_notes(audio, rate)))
        return
    result = check_note(audio, MIDI[args.note], rate)
    if result["cents"] is None:
        print(result["answer"])
    else:
        print(
            f"{args.note}: {result['cents']:+.0f} cents. {result['answer']} "
            f"(pitch wobble {result['wobble_cents']:.0f} cents)"
        )


if __name__ == "__main__":
    main()
