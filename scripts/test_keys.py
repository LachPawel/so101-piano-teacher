"""Tests for the SO-101 narrated keyboard scripts. No hardware, microphone or extra packages needed.

uv run pytest scripts/test_keys.py -q
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cut_chunks
import key_sounds as ks
import keys_check
import keys_companion as kc
import keys_map
import keys_pitch_judge as kpj
import keys_scripted
import keys_variants
import numpy as np
import pytest
import record_session
from cut_chunks import Event, match_words, plan_episode
from keys_common import EPISODE_SCRIPTS, KEYS, read_song, words_to_keys, write_json

RATE = 16000
MIDI = {1: 56, 2: 61, 3: 63, 4: 64, 5: 68, 6: 69}  # G#3 C#4 D#4 E4 G#4 A4


def synth_note(midi: int, rng: np.random.Generator, dur: float = 1.2) -> np.ndarray:
    """Harmonic note with a 5 ms attack and a weak fundamental, like laptop speakers."""
    t = np.arange(int(dur * RATE)) / RATE
    f0 = 440 * 2 ** ((midi - 69) / 12)
    x = np.zeros_like(t)
    for h in range(1, 12):
        gain = (0.15 if h * f0 < 300 else 1.0) / h
        x += gain * np.sin(2 * np.pi * h * f0 * t + rng.uniform(0, 2 * np.pi))
    envelope = np.minimum(t / 0.005, 1) * np.exp(-t / 0.4) * np.minimum((dur - t) / 0.05, 1)
    return 0.3 * x * envelope


def synth_voice(dur: float = 0.4, f0: float = 130) -> np.ndarray:
    """Voice-like sound: a vibrato harmonic series with a formant around 700 Hz."""
    t = np.arange(int(dur * RATE)) / RATE
    phase = 2 * np.pi * np.cumsum(f0 * (1 + 0.03 * np.sin(2 * np.pi * 5 * t))) / RATE
    x = sum((1 / h) * np.sin(h * phase) * np.exp(-(((h * f0 - 700) / 600) ** 2)) for h in range(1, 30))
    return 0.25 * x * np.sin(np.pi * t / dur) ** 2


def render(events: list[tuple[float, int | str]], total: float, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    audio = rng.normal(0, 0.003, int(total * RATE))
    for t, kind in events:
        sound = synth_note(MIDI[kind], rng) if isinstance(kind, int) else synth_voice()
        i = int(t * RATE)
        n = min(len(sound), len(audio) - i)
        audio[i : i + n] += sound[:n]
    return audio.astype(np.float32)


def test_words_to_keys():
    assert words_to_keys(" Two,") == [2]
    assert words_to_keys("for") == [4]
    assert words_to_keys("won") == [1]
    assert words_to_keys("1-2") == [1, 2]
    assert words_to_keys("7") == []
    assert words_to_keys("hello") == []


def test_song_and_scripts():
    assert read_song(Path(__file__).parent.parent / "song.txt") == [[1, 2, 5, 5, 5], [4, 6, 3, 3, 3]]
    for path in keys_scripted.SONGS.glob("*.txt"):
        assert sum(len(phrase) for phrase in read_song(path)) >= 40, path
    pairs = {
        (seq[i], seq[i + 1]) for name in "ABC" for seq in [EPISODE_SCRIPTS[name]] for i in range(len(seq) - 1)
    }
    assert pairs == {(a, b) for a in KEYS for b in KEYS}


def test_presses_are_found_and_identified():
    calibration, t = [], 0.5
    for key in KEYS:
        for _ in range(3):
            calibration.append((t, key))
            t += 1.0
        t += 0.5
    cal_audio = render(calibration, t + 1)
    times = keys_map.strongest_onsets(cal_audio, 18)
    assert len(times) == 18
    keys = keys_map.build_keys({k: (cal_audio, times[i * 3 : i * 3 + 3]) for i, k in enumerate(KEYS)})
    assert [keys["keys"][str(k)]["note"] for k in KEYS] == ["G#3", "C#4", "D#4", "E4", "G#4", "A4"]
    templates = {int(k): np.asarray(v["template"]) for k, v in keys["keys"].items()}

    # The riff with its fast repeats, and a voice-like sound 0.6 s before every press.
    rng = np.random.default_rng(1)
    events: list[tuple[float, int | str]] = []
    t = 1.0
    for key in [1, 2, 5, 5, 5, 4, 6, 3, 3, 3, 1, 1, 6, 1, 5]:
        events += [(t - 0.6, "voice"), (t, key)]
        t += rng.uniform(0.45, 1.5)
    audio = render(events, t + 1, seed=2)
    onsets, _ = ks.detect_onsets(audio, RATE)
    results = ks.classify(
        np.stack([ks.note_feature(audio, RATE, x) for x in onsets]), templates, keys["min_sim"], 0.05
    )
    found = [(float(x), r[0]) for x, r in zip(onsets, results, strict=True) if r[0] is not None]
    truth = [(t, k) for t, k in events if isinstance(k, int)]
    assert [k for _, k in found] == [k for _, k in truth]
    assert max(abs(a - b) for (a, _), (b, _) in zip(found, truth, strict=True)) < 0.03


def test_match_words():
    words = [Event(1.0, 1), Event(3.0, 2), Event(6.0, 3), Event(12.0, 4)]
    presses = [Event(1.8, 1), Event(3.9, 5), Event(6.1, 3), Event(9.0, 6)]
    matches, unlabeled = match_words(words, presses, max_gap=3.0, lookback=0.3)
    assert [m.status for m in matches] == ["ok", "mislabel", "ok", "no_press"]
    assert unlabeled == [Event(3.9, 5), Event(9.0, 6)]


def test_plan_episode():
    events = [(Event(2.0, 1), True), (Event(2.3, 1), True), (Event(5.0, 4), False), (Event(30.0, 2), True)]
    kept, dropped = plan_episode(events, start=0.5, end=10.0, hold=0.3, min_len=0.5)
    assert kept == [
        (0.5, pytest.approx(2.3), 1, True),
        (pytest.approx(2.6), pytest.approx(5.3), 4, False),
    ]
    assert dropped == [(pytest.approx(2.3), pytest.approx(2.6), 1)]


def test_record_flags_parse_like_lerobot_record():
    import draccus

    from lerobot.scripts.lerobot_record import RecordConfig

    args = argparse.Namespace(
        robot_port="/dev/tty.f",
        teleop_port="/dev/tty.l",
        robot_id="workshop_so101",
        teleop_id="workshop_so101",
        repo_id="PawelLach/so101_keys_narrated",
        task="play the keyboard",
        episodes=3,
        episode_time=40,
        reset_time=10,
        fps=30,
    )
    cameras = {"front": {"type": "opencv", "index_or_path": 0, "width": 640, "height": 480, "fps": 30}}
    cfg = draccus.parse(RecordConfig, args=record_session.build_record_argv(args, cameras, []))
    assert cfg.dataset.push_to_hub is False
    assert cfg.play_sounds is False
    assert cfg.dataset.single_task == "play the keyboard"
    assert cfg.robot.id == "workshop_so101" and cfg.teleop is not None and cfg.teleop.id == "workshop_so101"
    assert cfg.robot.cameras["front"].index_or_path == 0


def test_frame_clock_and_chunks_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from lerobot.datasets.lerobot_dataset import LeRobotDataset

    fps, n_frames = 30, 90
    features = {
        "observation.state": {"dtype": "float32", "shape": (6,), "names": None},
        "action": {"dtype": "float32", "shape": (6,), "names": None},
        "observation.images.front": {
            "dtype": "video",
            "shape": (24, 32, 3),
            "names": ["height", "width", "channels"],
        },
    }
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    session = record_session.Session(session_dir, ["A", "B"], cameras={}, record_argv=[])

    # What record() does: frames, a re-recorded (cleared) attempt, then saved episodes.
    with record_session.FrameClock(session):
        dataset = LeRobotDataset.create("local/src", fps=fps, features=features, root=tmp_path / "src")

        def add(n: int, value: float) -> None:
            for i in range(n):
                image: np.ndarray = np.full((24, 32, 3), (int(value) * 40 + i) % 256, dtype=np.uint8)
                state: np.ndarray = np.full(6, value + i / 1000, dtype=np.float32)
                dataset.add_frame(
                    {
                        "observation.state": state,
                        "action": state,
                        "observation.images.front": image,
                        "task": "play the keyboard",
                    }
                )

        add(10, 9)
        dataset.clear_episode_buffer()
        add(n_frames, 0)
        dataset.save_episode()
        add(n_frames, 1)
        dataset.save_episode()
        dataset.finalize()
    assert LeRobotDataset.add_frame.__name__ == "add_frame"  # methods restored
    manifest = json.loads((session_dir / "manifest.json").read_text())
    assert [e["n_frames"] for e in manifest["episodes"]] == [n_frames, n_frames]
    assert [e["script"] for e in manifest["episodes"]] == ["A", "B"]

    # Pretend the episodes ran at 30 fps, 10 s apart, with these words and presses.
    for i, episode in enumerate(manifest["episodes"]):
        episode["start"] = 10.0 * i
        episode["frame_t"] = [10.0 * i + f / fps for f in range(n_frames)]
        episode["end"] = episode["frame_t"][-1] + 1 / fps
    write_json(session_dir / "manifest.json", manifest)
    write_json(
        session_dir / "transcript.json",
        {"words": [{"t": 0.2, "key": 1}, {"t": 1.0, "key": 2}, {"t": 10.3, "key": 5}, {"t": 11.2, "key": 5}]},
    )
    write_json(
        session_dir / "presses.json",
        {
            "presses": [
                {"t": 0.91, "key": 1},
                {"t": 1.81, "key": 2},
                {"t": 11.01, "key": 5},
                {"t": 11.91, "key": 5},
            ]
        },
    )

    out = tmp_path / "chunks"
    monkeypatch.setattr(
        sys, "argv", ["cut_chunks.py", str(session_dir), "--root", str(out), "--repo-id", "local/chunks"]
    )
    cut_chunks.main()
    chunks = LeRobotDataset("local/chunks", root=out)
    assert chunks.num_episodes == 4
    # Chunks end 0.3 s after each press: 0-1.21 s, 1.21-2.11 s, then 0-1.31 s and 1.31-2.21 s of episode 1.
    lengths = [int(chunks.meta.episodes[i]["length"]) for i in range(4)]
    assert lengths == [37, 27, 40, 27]
    tasks = [chunks[int(chunks.meta.episodes[i]["dataset_from_index"])]["task"] for i in range(4)]
    assert tasks == ["press key 1", "press key 2", "press key 5", "press key 5"]
    # The second chunk starts at frame 37 of source episode 0.
    second = chunks[int(chunks.meta.episodes[1]["dataset_from_index"])]
    assert second["observation.state"][0].item() == pytest.approx(0.037, abs=1e-6)


def test_prompter_shows_one_key_at_a_time_then_reset(capsys: pytest.CaptureFixture[str]):
    import time

    prompter = record_session.Prompter(interval=0.1)
    prompter.start(0, "S", [1, 2, 5])
    end = time.perf_counter() + 0.45
    while time.perf_counter() < end:  # the episode: frames keep coming
        prompter.frame()
        time.sleep(0.01)
    time.sleep(0.7)  # the reset phase: no frames
    prompter.stop()
    out = capsys.readouterr().out
    assert (
        out.index('say "one"') < out.index('say "two"') < out.index('say "five"') < out.index("EPISODE OVER")
    )


def test_wav_recorder_fills_missed_audio_with_silence(tmp_path: Path):
    from keys_common import read_wav

    recorder = record_session.WavRecorder(tmp_path / "x.wav", "test", t0=0.0)
    recorder._open_wav()
    block: np.ndarray = np.full((1600, 1), 1000, dtype=np.int16)  # 0.1 s
    for t in (1.0, 1.1, 1.5, 1.6):  # 0.3 s missing after the second block
        recorder._write_block(block, t)
    meta = recorder._finish()
    assert meta["missed_s"] == pytest.approx(0.3)
    assert meta["n_samples"] == 4 * 1600 + 4800
    assert meta["t_first_sample"] == pytest.approx(1.0)
    assert meta["seconds_per_sample"] == pytest.approx(1 / 16000)
    audio, _ = read_wav(tmp_path / "x.wav")
    assert np.all(audio[3200:8000] == 0) and np.all(audio[8000:] != 0)


def test_plan_words_cuts_between_spoken_numbers():
    from cut_chunks import plan_words

    # Script 1 2 5 5; Whisper heard 1 2 5 at 6 s steps, missed one 5, and heard a stray 3.
    words = [Event(1.0, 1), Event(7.0, 2), Event(13.0, 5), Event(25.0, 3), Event(29.0, 5)]
    kept, dropped = plan_words(words, [1, 2, 5, 5], 0.0, 40.0, 0.3, 6.0, 2.5, 8.5)
    assert [(k, c) for _, _, k, c in kept] == [(1, True), (2, True), (3, False), (5, True)]
    assert kept[0][:2] == (pytest.approx(0.7), pytest.approx(6.7))
    assert kept[-1][:2] == (pytest.approx(28.7), pytest.approx(35.0))
    assert dropped == [(pytest.approx(12.7), pytest.approx(24.7), 5)]  # 12 s: a word was missed


def scripted_poses() -> dict[str, dict[str, float]]:
    """Every joint at one value per pose: rest 0, home 50, key k down at 10k and above at 10k + 5."""
    joints = [f"{j}.pos" for j in (*keys_scripted.BODY, "gripper")]
    poses = {"rest": dict.fromkeys(joints, 0.0), "home": dict.fromkeys(joints, 50.0)}
    for key in KEYS:
        poses[f"down_{key}"] = dict.fromkeys(joints, 10.0 * key)
        poses[f"above_{key}"] = dict.fromkeys(joints, 10.0 * key + 5)
    return poses


class FakeArm:
    """Follower stand-in: it reaches every commanded pose at once and has one small camera."""

    name = "so_follower"

    def __init__(self) -> None:
        self.pos = {f"{j}.pos": 0.0 for j in (*keys_scripted.BODY, "gripper")}
        self.action_features = dict.fromkeys(self.pos, float)
        self.observation_features: dict = {**self.action_features, "front": (24, 32, 3)}
        self.bus = self
        self.connected = False

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def sync_read(self, data_name: str) -> dict[str, float]:
        return {k.removesuffix(".pos"): v for k, v in self.pos.items()}

    def get_observation(self) -> dict:
        return {**self.pos, "front": np.zeros((24, 32, 3), dtype=np.uint8)}

    def send_action(self, action: dict[str, float]) -> dict[str, float]:
        self.pos = dict(action)
        return action


def test_scripted_press_goes_above_presses_exactly_and_holds():
    poses = scripted_poses()
    timing = keys_scripted.Timing()
    segments = keys_scripted.press_segments(poses["home"], poses, 3, timing, np.random.default_rng(0))
    assert keys_scripted.pose_at(segments, 0.0) == poses["home"]
    above = segments[0].end
    assert all(abs(above[k] - poses["above_3"][k]) <= timing.jitter for k in above)
    assert above["gripper.pos"] == poses["above_3"]["gripper.pos"]
    t_down = segments[0].duration + segments[1].duration
    assert keys_scripted.pose_at(segments, t_down) == poses["down_3"]
    assert keys_scripted.pose_at(segments, 100.0) == above
    assert keys_scripted.min_jerk(0.5) == pytest.approx(0.5)
    script_a, script_b = EPISODE_SCRIPTS["A"], EPISODE_SCRIPTS["B"]
    assert keys_scripted.press_sequence(["A", "B"], 15) == script_a + script_b[:2]


def arm_fps(monkeypatch: pytest.MonkeyPatch, command: str) -> int:
    """The control rate a subcommand runs at when --fps is not given."""
    seen: list[int] = []
    with monkeypatch.context() as m:
        for name in ("record", "test", "song"):
            m.setattr(keys_scripted, name, lambda args: seen.append(args.fps))
        m.setattr(sys, "argv", ["keys_scripted.py", command])
        keys_scripted.main()
    return seen[0]


def dataset_root(out: Path) -> Path:
    return next(path for path in out.iterdir() if not path.name.endswith("_session"))


class FakeMidi:
    """A keyboard that sounds note 60 + key each time the fake arm arrives at a key's down pose (10 * key)."""

    name = "fake keyboard"

    def __init__(self, arm: FakeArm, deaf_key: int | None = None) -> None:
        self.deaf_key, self.notes, self.down, self.closed = deaf_key, [], False, False
        self.t0, self.events = 0.0, []
        send = arm.send_action

        def send_and_listen(action: dict[str, float]) -> dict[str, float]:
            value = action["shoulder_pan.pos"]
            down = value in {10.0 * key for key in KEYS} - {50.0}  # 50 is also home
            if down and not self.down and value // 10 != self.deaf_key:
                self.notes.append(60 + int(value // 10))
                self.events.append((time.perf_counter() - self.t0, 60 + int(value // 10), 80))
            self.down = down
            return send(action)

        arm.send_action = send_and_listen  # type: ignore[method-assign]

    def notes_between(self, start: float, end: float) -> list[int]:
        notes, self.notes = self.notes, []
        return notes

    def ons_since(self, start: float) -> list[tuple[float, int, int]]:
        return [event for event in self.events if event[0] >= start]

    def close(self) -> None:
        self.closed = True


def test_scripted_phrase_mode_logs_presses_and_checks_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    import lerobot.utils.robot_utils
    from lerobot.datasets.lerobot_dataset import LeRobotDataset

    assert keys_scripted.plan_episodes("phrase", [], [[5, 5, 0], [1], []], 4) == [[5, 5], [1], [5, 5]]
    assert keys_scripted.plan_episodes("press", ["S"], [], 3) == [[1], [2], [5]]
    assert keys_scripted.judge_press(5, None, {}) == (None, "")
    assert keys_scripted.judge_press(5, [], {5: 65}) == (False, "NO NOTE")
    assert keys_scripted.judge_press(5, [65], {5: 65}) == (True, "ok")
    assert keys_scripted.judge_press(5, [64, 65], {5: 65})[0] is False
    assert keys_scripted.judge_press(5, [65], {})[0] is None

    arm = FakeArm()
    midi = FakeMidi(arm, deaf_key=2)
    monkeypatch.setattr(keys_scripted, "make_robot", lambda args, cameras: arm)
    monkeypatch.setattr(keys_scripted, "open_midi", lambda port, t0, path=None: midi)
    monkeypatch.setattr(lerobot.utils.robot_utils, "precise_sleep", lambda seconds: None)
    write_json(tmp_path / "poses.json", {"poses": scripted_poses()})
    write_json(tmp_path / "cameras.json", {})
    write_json(tmp_path / "keys_midi.json", {"notes": {str(k): 60 + k for k in KEYS}})
    (tmp_path / "phrases.txt").write_text("3 3 3\n1 2\n")
    out = tmp_path / "out"
    argv = ["record", "--mode", "phrase", "--phrases", str(tmp_path / "phrases.txt"), "--presses", "5"]
    argv += ["--seed", "0", "--repo-id", "local/phrases", "--poses", str(tmp_path / "poses.json")]
    argv += ["--cameras", str(tmp_path / "cameras.json"), "--out", str(out)]
    argv += ["--key-notes", str(tmp_path / "keys_midi.json")]
    monkeypatch.setattr(sys, "argv", ["keys_scripted.py", *argv])
    keys_scripted.main()
    printed = capsys.readouterr().out
    assert midi.closed and "Presses that sounded their key: 4/5" in printed, printed

    dataset = LeRobotDataset("local/phrases", root=dataset_root(out))
    assert dataset.num_episodes == 2
    session = json.loads((next(out.glob("*_session")) / "session.json").read_text())
    first, second = session["episodes"]
    assert first["task"] == "play keys 3 3 3" and second["task"] == "play keys 1 2"
    assert [p["key"] for p in first["presses"]] == [3, 3, 3]
    assert [p["notes"] for p in first["presses"]] == [[63], [63], [63]]
    assert [p["ok"] for p in second["presses"]] == [True, False]
    # The presses tile the episode, every frame has a time, and the arm is down on the frame_down frame.
    assert first["presses"][0]["frame_start"] == 0
    assert all(
        a["frame_end"] == b["frame_start"]
        for a, b in zip(first["presses"], first["presses"][1:], strict=False)
    )
    n_frames = int(dataset.meta.episodes[0]["length"])
    assert first["presses"][-1]["frame_end"] == n_frames == len(first["frame_times"])
    assert first["frame_times"] == sorted(first["frame_times"])
    assert dataset[first["presses"][1]["frame_down"]]["action"][0].item() == pytest.approx(30.0)
    assert dataset[0]["task"] == "play keys 3 3 3"

    # keys_check reads the session back: the notes sit on the frame where the arm is down, and the hover
    # pose above key 3 does not tell how many times it was pressed.
    folder = next(out.glob("*_session"))
    rows = [
        {"t": ep["frame_times"][press["frame_down"]], "note": note, "velocity": 64, "channel": 0}
        for ep in session["episodes"]
        for press in ep["presses"]
        for note in press["notes"]
    ]
    (folder / "midi.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    monkeypatch.setattr(sys, "argv", ["keys_check.py", "session", str(folder)])
    keys_check.main()
    report = capsys.readouterr().out
    assert "Presses that sounded their key: 4/5" in report and "episode 1 key 2: heard []" in report
    assert "median +0 ms" in report and "Repeated notes:" in report
    assert "Key 3 (press 1: 1, press 2: 1, press 3: 1)" in report and "too few presses" in report


def test_scripted_record_writes_one_episode_per_press(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    import lerobot.utils.robot_utils
    from lerobot.datasets.lerobot_dataset import LeRobotDataset

    arm = FakeArm()
    monkeypatch.setattr(keys_scripted, "make_robot", lambda args, cameras: arm)
    monkeypatch.setattr(lerobot.utils.robot_utils, "precise_sleep", lambda seconds: None)
    write_json(tmp_path / "poses.json", {"poses": scripted_poses()})
    write_json(tmp_path / "cameras.json", {})
    files = ["--poses", str(tmp_path / "poses.json")]

    monkeypatch.setattr(sys, "argv", ["keys_scripted.py", "test", "--keys", "6", "--midi", "none", *files])
    keys_scripted.main()
    assert arm_fps(monkeypatch, "record") == arm_fps(monkeypatch, "test") == 30
    assert arm_fps(monkeypatch, "song") == 50
    assert arm.pos == scripted_poses()["rest"] and not arm.connected

    out = tmp_path / "out"
    argv = ["record", "--presses", "3", "--plan", "S", "--seed", "0", "--repo-id", "local/scripted"]
    argv += [*files, "--cameras", str(tmp_path / "cameras.json"), "--out", str(out), "--midi", "none"]
    monkeypatch.setattr(sys, "argv", ["keys_scripted.py", *argv])
    keys_scripted.main()
    assert arm.pos == scripted_poses()["rest"] and not arm.connected

    dataset = LeRobotDataset("local/scripted", root=dataset_root(out))
    assert dataset.num_episodes == 3
    # --say: the arm holds still first (frame_go), the press comes later, and every episode has one task.
    said = tmp_path / "said"
    argv = [*argv[: argv.index("--out")], "--out", str(said), "--midi", "none", "--say"]
    argv += ["--task", "play the key you hear"]
    monkeypatch.setattr(sys, "argv", ["keys_scripted.py", *argv])
    keys_scripted.main()
    press = json.loads((next(said.glob("*_session")) / "session.json").read_text())["episodes"][1]["presses"][
        0
    ]
    low, high = keys_scripted.SAY_WAIT
    assert low * 30 <= press["frame_go"] <= high * 30 + 1 and press["frame_down"] > press["frame_go"] + 15
    spoken = LeRobotDataset("local/scripted", root=dataset_root(said))
    start = int(spoken.meta.episodes[1]["dataset_from_index"])
    waiting = [spoken[start + i]["action"][0].item() for i in range(press["frame_go"])]
    assert max(waiting) - min(waiting) < 1e-6 and spoken[start]["task"] == "play the key you hear"
    # Without --task the waiting frames are "wait" and the press is its own subtask.
    labelled = tmp_path / "labelled"
    argv = [
        *argv[: argv.index("--out")],
        "--out",
        str(labelled),
        "--midi",
        "none",
        "--say",
        "--words",
        "solfege",
    ]
    monkeypatch.setattr(sys, "argv", ["keys_scripted.py", *argv])
    keys_scripted.main()
    assert ">>>  SAY:  RE  <<<" in capsys.readouterr().out
    go = json.loads((next(labelled.glob("*_session")) / "session.json").read_text())["episodes"][1][
        "presses"
    ][0]["frame_go"]
    subtasks = LeRobotDataset("local/scripted", root=dataset_root(labelled))
    start = int(subtasks.meta.episodes[1]["dataset_from_index"])
    assert [subtasks[start + i]["task"] for i in (0, go - 1, go, go + 20)] == [
        "wait",
        "wait",
        "press key 2",
        "press key 2",
    ]
    assert spoken[start + press["frame_down"]]["action"][0].item() == pytest.approx(20.0, abs=0.5)
    starts = [int(dataset.meta.episodes[i]["dataset_from_index"]) for i in range(3)]
    assert [dataset[i]["task"] for i in starts] == ["press key 1", "press key 2", "press key 5"]
    # Episode 0 starts at home, reaches key 1's down pose exactly and ends above key 1.
    first = [dataset[i]["action"][0].item() for i in range(starts[0], starts[1])]
    assert first[0] == pytest.approx(50.0)
    assert min(first) == pytest.approx(10.0)
    assert first[-1] == pytest.approx(15.0, abs=1.5)
    assert dataset[starts[0]]["observation.images.front"].shape == (3, 24, 32)


def test_song_hits_every_key_on_an_even_beat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import lerobot.utils.robot_utils

    poses = scripted_poses()
    keys = [1, 2, 5, 5, 5, 4]
    segments, beat, hits = keys_scripted.song_segments(poses["home"], poses, keys, 100.0, 0.4, 0.7, None)
    assert np.diff(hits) == pytest.approx([beat] * (len(keys) - 1))
    for key, t in zip(keys, hits, strict=True):
        assert keys_scripted.pose_at(segments, t) == pytest.approx(poses[f"down_{key}"])
    # A repeated key only lifts to 40% of the taught lift between hits.
    between = keys_scripted.pose_at(segments, hits[3] - beat / 2)
    assert between["shoulder_pan.pos"] <= 50 + 0.4 * 5 + 1e-9
    # A jump from key 1 to key 2 is one arc, 70% of the way up to the above poses halfway through.
    jump = segments[next(i for i, seg in enumerate(segments) if seg.via is not None)]
    middle = keys_scripted.pose_at([jump], jump.duration / 2)
    assert middle["shoulder_pan.pos"] == pytest.approx(15 + 0.7 * 5)

    arm = FakeArm()
    monkeypatch.setattr(keys_scripted, "make_robot", lambda args, cameras: arm)
    monkeypatch.setattr(lerobot.utils.robot_utils, "precise_sleep", lambda seconds: None)
    write_json(tmp_path / "poses.json", {"poses": poses})
    (tmp_path / "song.txt").write_text("1 2 5 5 5\n")
    argv = ["song", "--poses", str(tmp_path / "poses.json"), "--song", str(tmp_path / "song.txt")]
    monkeypatch.setattr(sys, "argv", ["keys_scripted.py", *argv])
    keys_scripted.main()
    assert arm.pos == poses["rest"] and not arm.connected


def test_adjust_moves_the_press_and_its_lift_together():
    poses = scripted_poses()
    out = keys_scripted.nudged(poses, 3, {"shoulder_pan.pos": 1.5})
    assert out["down_3"]["shoulder_pan.pos"] == 31.5 and out["above_3"]["shoulder_pan.pos"] == 36.5
    assert out["down_3"]["elbow_flex.pos"] == 30.0
    assert out["down_4"] == poses["down_4"] and poses["down_3"]["shoulder_pan.pos"] == 30.0


def test_song_holds_a_key_down_for_a_dash():
    poses = scripted_poses()
    segments, beat, hits = keys_scripted.song_segments(
        poses["home"], poses, [4, 3, 2, 0, 5], 100.0, 0.3, 0.7, None
    )
    assert np.diff(hits) == pytest.approx([beat, beat, 2 * beat])
    assert keys_scripted.pose_at(segments, hits[2] + 0.75 * beat) == poses["down_2"]


# ------------------------------------------------------------------------------------ the companion


def test_commands_are_understood():
    songs = ["moonlight", "ode", "warmup"]
    assert kc.parse_command("Play do, re, mi.", songs).keys == [1, 2, 3]
    assert kc.parse_command("please play the note C", songs).keys == [1]
    assert kc.parse_command("Play a note, sol", songs).keys == [5]
    assert kc.parse_command("play three three four five", songs).keys == [3, 3, 4, 5]
    assert kc.parse_command("Doe, ray, me", songs).keys == [1, 2, 3]  # no verb, but nothing else was said
    assert kc.parse_command("Play Ode to Joy", songs) == kc.Command("song", song="ode")
    assert kc.parse_command("Teach me the Ode to Joy", songs) == kc.Command("teach", song="ode")
    assert kc.parse_command("teach me", songs).song == "ode"
    assert kc.parse_command("Let's sing", songs).kind == "sing"
    assert kc.parse_command("Okay, stop.", songs).kind == "stop"
    assert kc.parse_command("so what do we do", songs).kind == "unknown"  # note words inside a sentence
    assert kc.parse_command("", songs).kind == "unknown"
    sharp = kc.parse_command("play C sharp", songs)
    assert sharp.keys == [1] and "sharps" in sharp.note
    assert kc.split_phrases(kc.song_keys("ode"), 4, 2) == [[3, 3, 4, 5], [5, 4, 3, 2]]
    order = kc.trial_order(3, seed=1)
    assert sorted(order) == sorted(list(KEYS) * 3) and all(
        set(order[i : i + 6]) == set(KEYS) for i in (0, 6, 12)
    )


class FakeClock(kc.Clock):
    """Time moves only when someone sleeps. Notes scheduled for a time appear once it has passed."""

    def __init__(self) -> None:
        self.t, self.t0, self.due, self.events = 100.0, 100.0, [], []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds
        for item in [d for d in self.due if d[0] <= self.t]:
            self.due.remove(item)
            self.events.append((item[0] - self.t0, item[1], 70))

    def note_in(self, seconds: float, note: int) -> None:
        self.due.append((self.t + seconds, note))

    def ons_since(self, start: float) -> list[tuple[float, int, int]]:
        return sorted(event for event in self.events if event[0] >= start)


class FakeHands:
    """Hands whose press sounds the key 0.5 s later, or the neighbour for keys in `slips`."""

    def __init__(self, clock: FakeClock, key_notes: dict[int, int], slips: tuple[int, ...] = ()) -> None:
        self.clock, self.key_notes, self.slips, self.log = clock, key_notes, slips, []

    def press(self, key: int) -> None:
        self.log.append(key)
        self.clock.note_in(0.5, self.key_notes[key + 1 if key in self.slips else key])

    def rest(self) -> None:
        self.log.append("rest")

    def away(self) -> None:
        self.log.append("away")

    def close(self) -> None:
        pass


KEY_NOTES = {1: 60, 2: 62, 3: 64, 4: 65, 5: 67, 6: 69}


def test_presses_are_scored_by_midi():
    clock = FakeClock()
    hands = FakeHands(clock, KEY_NOTES, slips=(4,))
    trials = [kc.press_and_judge(key, hands, clock, KEY_NOTES, clock, 10.0, 0.8) for key in (3, 4, 3)]
    assert [t["ok"] for t in trials] == [True, False, True]
    assert trials[0] == {
        "key": 3,
        "heard": [64],
        "ok": True,
        "seconds": pytest.approx(0.5, abs=0.03),
        "velocity": 70,
    }
    assert trials[1]["heard"] == [67] and hands.log == [3, "rest", 4, "rest", 3, "rest"]

    silent = kc.press_and_judge(2, FakeHands(FakeClock(), {2: 62}), clock, KEY_NOTES, clock, 3.0, 0.8)
    assert silent["ok"] is False and silent["heard"] == [] and silent["seconds"] is None  # timed out

    summary = kc.summarize([*trials, silent], KEY_NOTES)
    assert summary["right"] == 2 and summary["per_key"] == {"2": "0/1", "3": "2/2", "4": "0/1"}
    assert summary["asked_to_heard"] == {"3": {"3": 2}, "4": {"5": 1}, "2": {"none": 1}}


def test_lesson_shows_a_phrase_and_repeats_the_missed_note():
    clock = FakeClock()
    hands = FakeHands(clock, KEY_NOTES)
    answers = [[52, 52, 53, 57], [52, 52, 53, 55]]  # an octave down: first try ends on la, then right

    class Student(kc.Face):
        def say(self, text: str) -> None:
            said.append(text)
            if text == "Your turn.":
                for i, note in enumerate(answers.pop(0)):
                    clock.note_in(1.0 + i, note)

    said: list[str] = []
    report = kc.lesson([[3, 3, 4, 5]], hands, clock, KEY_NOTES, Student(), clock)
    assert [r["ok"] for r in report] == [False, True]
    assert "Not quite. Note 4 is this one." in said and said[-1] == "That is the tune."
    presses = [x for x in hands.log if isinstance(x, int)]
    assert presses == [3, 3, 4, 5, 5, 3, 3, 4, 5]  # the phrase, the missed sol again, the phrase again
    assert hands.log.count("away") == 2


def tone(hz: float, seconds: float = 1.5, rate: int = 16000) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    voice = sum(np.sin(2 * np.pi * hz * k * t) / k for k in (1, 2, 3, 4))
    quiet_start = np.where(t < 0.3, 0.0, 1.0)  # you start singing a moment after the note
    return (0.2 * voice * quiet_start + 0.002 * np.random.default_rng(0).standard_normal(t.size)).astype(
        np.float32
    )


def test_singing_says_higher_lower_or_well_done():
    for hz in (110.0, 196.0, 261.63, 440.0):
        assert kc.sung_pitch(tone(hz), 16000) == pytest.approx(hz, rel=0.01)
    assert kc.sung_pitch(np.zeros(24000, dtype=np.float32), 16000) is None
    assert (
        kc.sung_pitch(0.05 * np.random.default_rng(1).standard_normal(24000).astype(np.float32), 16000)
        is None
    )
    assert kc.judge_pitch(261.63, 60)[1] == "Well done."
    assert kc.judge_pitch(130.8, 60)[1] == "Well done."  # the same note an octave down
    cents, answer = kc.judge_pitch(250.0, 60)
    assert cents == pytest.approx(-78.7, abs=1) and answer == "A little higher."
    assert kc.judge_pitch(275.0, 60)[1] == "A little lower."
    assert kc.judge_pitch(None, 60)[0] is None

    clock = FakeClock()
    hands = FakeHands(clock, KEY_NOTES)

    class Mic:
        rate = 16000
        takes = [tone(250.0), tone(131.0), tone(147.0)]  # flat, then do an octave down, then re

        def record(self, seconds: float) -> np.ndarray:
            return self.takes.pop(0)

    report = kc.singing([1, 2], hands, clock, KEY_NOTES, Mic(), kc.Face(), clock)
    assert [(r["key"], r["answer"]) for r in report] == [
        (1, "A little higher."),
        (1, "Well done."),
        (2, "Well done."),
    ]
    assert [x for x in hands.log if isinstance(x, int)] == [1, 1, 2]


def test_rollout_hands_steer_the_policy_through_its_chat(capsys: pytest.CaptureFixture[str]):
    class Process:
        def __init__(self) -> None:
            self.lines, self.stdin = [], self

        def write(self, text: str) -> None:
            self.lines.append(text)

        def flush(self) -> None:
            pass

        def close(self) -> None:
            pass

        def wait(self, timeout: float) -> None:
            pass

    text = kc.RolloutHands("text", [], {}, process=Process())
    text.press(3)
    text.rest()
    text.close()
    assert text.process.lines == ["/subtask press key 3\n", "/subtask wait\n", "/stop\n"]
    sound = kc.RolloutHands("sound", [], {3: "mi"}, process=Process())
    sound.press(3)
    sound.rest()
    assert sound.process.lines == [] and ">>>  SAY:  MI  <<<" in capsys.readouterr().out


def test_companion_scores_the_saved_poses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    import lerobot.utils.robot_utils

    arm = FakeArm()
    midi = FakeMidi(arm, deaf_key=2)

    def open_midi(port: str, t0: float, path: Path | None = None) -> FakeMidi:
        midi.t0 = t0
        return midi

    monkeypatch.setattr(keys_scripted, "make_robot", lambda args, cameras: arm)
    monkeypatch.setattr(keys_scripted, "open_midi", open_midi)
    monkeypatch.setattr(lerobot.utils.robot_utils, "precise_sleep", lambda seconds: None)
    write_json(tmp_path / "poses.json", {"poses": scripted_poses()})
    write_json(tmp_path / "keys_midi.json", {"notes": {str(k): 60 + k for k in KEYS}})
    argv = [
        "score",
        "--hands",
        "scripted",
        "--per-key",
        "1",
        "--keys",
        "1",
        "2",
        "3",
        "--gap",
        "0",
        "--timeout",
        "0.3",
    ]
    argv += ["--poses", str(tmp_path / "poses.json"), "--key-notes", str(tmp_path / "keys_midi.json")]
    argv += ["--out", str(tmp_path / "scores"), "--label", "saved poses"]
    kc.main(argv)
    assert "saved poses: 2/3 right (67%, chance 33%)" in capsys.readouterr().out
    report = json.loads(next((tmp_path / "scores").glob("*_score_saved poses.json")).read_text())
    assert report["result"]["summary"]["per_key"] == {"1": "1/1", "2": "0/1", "3": "1/1"}
    assert midi.closed and arm.pos == scripted_poses()["rest"] and not arm.connected


def test_variants_split_one_recording_into_both_sound_and_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import lerobot.utils.robot_utils
    from lerobot.datasets.lerobot_dataset import LeRobotDataset

    arm = FakeArm()
    arm.observation_features["ears"] = (24, 32, 3)
    observe = arm.get_observation
    arm.get_observation = lambda: {**observe(), "ears": np.zeros((24, 32, 3), dtype=np.uint8)}  # type: ignore[method-assign]
    monkeypatch.setattr(keys_scripted, "make_robot", lambda args, cameras: arm)
    monkeypatch.setattr(lerobot.utils.robot_utils, "precise_sleep", lambda seconds: None)
    write_json(tmp_path / "poses.json", {"poses": scripted_poses()})
    write_json(tmp_path / "cameras.json", {})
    argv = ["record", "--say", "--presses", "3", "--plan", "S", "--seed", "0", "--repo-id", "local/say"]
    argv += ["--poses", str(tmp_path / "poses.json"), "--cameras", str(tmp_path / "cameras.json")]
    argv += ["--out", str(tmp_path / "out"), "--midi", "none"]
    monkeypatch.setattr(sys, "argv", ["keys_scripted.py", *argv])
    keys_scripted.main()
    root = dataset_root(tmp_path / "out")

    out = keys_variants.build(root, "local/say")
    both = LeRobotDataset("local/say_both", root=out["both"])
    sound = LeRobotDataset("local/say_sound", root=out["sound"])
    text = LeRobotDataset("local/say_text", root=out["text"])
    n = len(both)
    assert len(sound) == len(text) == n and both.num_episodes == sound.num_episodes == text.num_episodes == 3
    assert {both[i]["task"] for i in range(n)} == {"wait", "press key 1", "press key 2", "press key 5"}
    assert {sound[i]["task"] for i in range(n)} == {"play the key you hear"}
    assert [text[i]["task"] for i in range(n)] == [both[i]["task"] for i in range(n)]
    assert "observation.images.ears" in sound.features and "observation.images.ears" not in text.features
    assert "observation.images.front" in text.features
    assert keys_variants.episodes_without(root.with_name(root.name + "_session"), [1, 5]) == [1]
    printed = keys_variants.commands(out, "local/say", [1])
    assert "--dataset.repo_id=local/say_text --dataset.exclude_episodes='[1]'" in printed
    assert printed.count("observation.images.camera2") == 2  # the text variant has no ears to rename


def test_synth_plays_the_right_pitch_and_dies_away() -> None:
    import keys_synth

    piano = keys_synth.Piano(rate=48000)
    piano.note_on(69, 100)  # A4, 440 Hz
    held = np.concatenate([piano.render(256) for _ in range(94)])  # about half a second
    spectrum = np.abs(np.fft.rfft(held * np.hanning(len(held))))
    peak_hz = np.fft.rfftfreq(len(held), 1 / 48000)[int(np.argmax(spectrum))]
    assert peak_hz == pytest.approx(440.0, abs=3.0)
    assert 0.05 < float(np.max(np.abs(held))) <= 1.0
    # No click at block edges: the largest jump between samples stays small.
    assert float(np.max(np.abs(np.diff(held)))) < 0.2

    piano.note_off(69)
    for _ in range(94):
        piano.render(256)
    assert piano.voices == []


def test_pitch_judge_scores_the_model_against_the_measured_pitch(tmp_path: Path) -> None:
    clips = kpj.make_synth(tmp_path, ["mi", "sol"])
    assert len(clips) == 20
    # The pitch function measures the made-up notes where they were put, also an octave down.
    for clip, wanted in zip(clips, kpj.SYNTH_OFFSETS * 2, strict=True):
        assert clip["cents"] == pytest.approx(wanted, abs=8)
    assert [c["label"] for c in clips[:10]] == ["flat"] * 3 + ["in_tune"] * 4 + ["sharp"] * 3
    low = kpj.make_synth(tmp_path / "low", ["la"], offsets=(-70, 0, 70), octave=-1)
    assert [c["label"] for c in low] == ["flat", "in_tune", "sharp"]

    request = kpj.build_request(tmp_path / "clip_01.wav", tmp_path / "ref_01.wav", 64)
    assert [block["type"] for block in request["input"]] == ["text", "audio", "text", "audio"]
    assert request["input"][1]["mime_type"] == "audio/wav"
    assert set(request["questions"]["direction"]["criteria"]) == set(kpj.LABELS)
    assert request["questions"]["in_tune"]["type"] == "noul"
    alone = kpj.build_request(tmp_path / "clip_01.wav", None, 64)
    assert [block["type"] for block in alone["input"]] == ["text", "audio"]
    assert "E4 (329.6 Hz)" in alone["questions"]["direction"]["instructions"]

    sent: list[dict] = []

    def always_in_tune(payload: dict, key: str) -> dict:
        assert key == "test-key"
        sent.append(payload)
        return {
            "answers": {
                "direction": {
                    "type": "choice",
                    "choice": "in_tune",
                    "probabilities": {"flat": 0.2, "in_tune": 0.7, "sharp": 0.1},
                },
                "in_tune": {"type": "noul", "noul": 0.6},
            },
            "usage": {"total_input_tokens": 100},
        }

    rows = kpj.judge(tmp_path, "test-key", post=always_in_tune)
    summary = kpj.summarize(rows)
    assert len(sent) == 20 and kpj.results_file(tmp_path, True).exists()
    assert summary["labels"] == {"flat": 6, "in_tune": 8, "sharp": 6}
    assert summary["correct"] == 8 and summary["majority_guess"] == 8  # no better than one fixed answer
    assert summary["off_pitch"] == 12 and summary["direction_right"] == 6  # it always leans flat
    assert summary["in_tune_separation"] == 0.5 and summary["input_tokens"] == 2000
    kpj.print_report(summary, "test")
    assert kpj.ranks_higher([0.9, 0.8], [0.1, 0.2]) == 1.0
    assert kpj.label_for(-50.0) == "in_tune" and kpj.label_for(51.0) == "sharp"


def test_listen_names_the_notes_of_a_riff_and_checks_a_sung_note() -> None:
    pytest.importorskip("swift_f0")
    import keys_listen

    rng = np.random.default_rng(0)
    held = [kpj.sung_tone(kpj.midi_hz(m), rng, seconds=0.5)[int(0.2 * 16000) :] for m in (64, 67, 70, 69, 64)]
    notes = keys_listen.heard_notes(np.concatenate(held))
    assert [note.name for note in notes] == ["E4", "G4", "A#4", "A4", "E4"]
    assert [note.key for note in notes] == ["mi", "sol", None, "la", "mi"]  # the arm has no A sharp
    assert keys_listen.arm_keys(notes) == ["mi", "sol", "la", "mi"]
    assert 'keys_play.sh both song "mi sol la mi"' in keys_listen.describe(notes)
    low = keys_listen.heard_notes(kpj.sung_tone(kpj.midi_hz(40), rng))  # the low E string of a guitar
    assert [note.name for note in low] == ["E2"] and low[0].key == "mi"

    flat = keys_listen.check_note(kpj.sung_tone(kpj.midi_hz(51.3), rng), 64)  # an octave down, 70 cents flat
    assert flat["cents"] == pytest.approx(-70, abs=10) and flat["answer"] == "A little higher."
    assert flat["wobble_cents"] < 15
    assert keys_listen.check_note(kpj.sung_tone(kpj.midi_hz(64), rng), 64)["answer"] == "Well done."
    silent = keys_listen.check_note(np.zeros(32000, dtype=np.float32), 64)
    assert silent["cents"] is None and "did not hear" in silent["answer"]


def test_voice_teacher_routes_requests_to_the_arm_and_the_pitch_check() -> None:
    pytest.importorskip("swift_f0")
    import asyncio

    import keys_voice

    rng = np.random.default_rng(0)

    class FakeAudio:
        def __init__(self) -> None:
            self.said, self.sung = [], []

        def say(self, audio: np.ndarray) -> None:
            self.said.append(audio)

        async def finished_speaking(self, starts_within: float = 0.0) -> None:
            return None

        async def record(self, seconds: float, enough: object = None) -> np.ndarray:
            return self.sung.pop(0)

    class FakeArm:
        def __init__(self) -> None:
            self.asked: list[list[str]] = []
            self.wrong: dict[str, str] = {}
            self.held = False  # a key the arm never let go of

        def play(self, notes: list[str], on_sound: object = None) -> list[tuple[str, str | None]]:
            self.asked.append(notes)
            letters = keys_voice.keys_play.NOTE_LETTERS
            for index in range(len(notes)):
                if callable(on_sound):
                    on_sound(index)
            return [(n, self.wrong.get(n, letters[n] + "4")) for n in notes]

        def keys_down(self) -> set[int]:
            return {64} if self.held else set()

        def close(self) -> None:
            pass

    async def scenario() -> None:
        sent: list[dict] = []

        async def send(event: dict) -> None:
            sent.append(event)

        audio, arm = FakeAudio(), FakeArm()
        teacher = keys_voice.Teacher(send, arm, audio, listen_s=0.0, rounds=1)

        async def user_says(text: str) -> None:
            await teacher.on_event({"type": "session.input_transcript.delta", "delta": text})
            await teacher.on_event({"type": "session.delegation.created", "delegation": {"id": "d1"}})
            await asyncio.gather(*teacher._tasks)

        await user_says("Reachy, please play mi sol la")
        assert arm.asked == [["mi", "sol", "la"]]
        assert [e["type"] for e in sent] == [
            "session.input_audio.mute",
            "session.commentary.append",
            "session.input_audio.unmute",
        ]
        assert "every note on the right key" in sent[1]["content"] and sent[1]["delegation_id"] == "d1"
        assert not teacher.busy

        # The teacher answered, so the next words start a new request; a wrong key is reported honestly.
        await teacher.on_event({"type": "session.output_transcript.delta", "delta": "Here you go."})
        arm.wrong, sent[:] = {"mi": "F4"}, []
        await user_says("play largo")
        assert arm.asked[-1][:3] == ["mi", "sol", "sol"]
        assert "mi came out as F4" in sent[1]["content"]

        # The lesson: mi is sung 70 cents flat, then in tune; sol and la in tune first time.
        await teacher.on_event({"type": "session.output_transcript.delta", "delta": "Oops."})
        arm.wrong, arm.asked, sent[:] = {}, [], []
        hz = kpj.midi_hz
        audio.sung = [kpj.sung_tone(hz(m), rng) for m in (51.3, 52, 55, 57)]  # an octave below the keys
        await user_says("let's start the lesson")
        assert arm.asked == [["mi"], ["mi"], ["sol"], ["la"]]
        facts = [e["content"] for e in sent if e["type"] == "session.commentary.append"]
        assert "cents flat. Tell them that, then: A little higher." in facts[1]
        assert "Then cheer them on to try mi once more." in facts[1]
        assert facts[2].startswith("The user sang mi and was in tune. Tell them that, then: Well done.")
        assert "Then cheer them on for the next note, sol" in facts[2]
        assert facts[-1].startswith("The lesson is over: 3 of 3 notes in tune.")
        assert "another round" in facts[-1]

        # A plain "yes" to that offer starts the next round, in a new order.
        await teacher.on_event({"type": "session.output_transcript.delta", "delta": "Again?"})
        arm.asked, sent[:] = [], []
        audio.sung = [kpj.sung_tone(hz(m), rng) for m in (57, 52, 55)]
        await user_says("Yes!")
        assert arm.asked == [["la"], ["mi"], ["sol"]]

        # "Check my pitch" is one note: the last one played, or the one named.
        await teacher.on_event({"type": "session.output_transcript.delta", "delta": "Done."})
        arm.asked, sent[:] = [], []
        audio.sung = [kpj.sung_tone(hz(55.6), rng), kpj.sung_tone(hz(55), rng)]
        await user_says("can you check my pitch")  # the last note played: sol, from the last round
        await teacher.on_event({"type": "session.output_transcript.delta", "delta": "Ok."})
        await user_says("am I in tune on sol?")
        assert arm.asked == [["sol"], ["sol"]]
        facts = [e["content"] for e in sent if e["type"] == "session.commentary.append"]
        assert "cents sharp. Tell them that, then: A little lower." in facts[1]
        assert facts[3] == "The user sang sol and was in tune. Tell them that, then: Well done."

        # A key the arm leaves resting down does not stop the measurement: that note fades within seconds.
        await teacher.on_event({"type": "session.output_transcript.delta", "delta": "Ok."})
        arm.asked, sent[:], arm.held = [], [], True
        audio.sung = [kpj.sung_tone(hz(57), rng)]  # la, an octave down
        await user_says("check my pitch on la")
        arm.held = False
        assert arm.asked == [["la"]]
        facts = [e["content"] for e in sent if e["type"] == "session.commentary.append"]
        assert facts[1] == "The user sang la and was in tune. Tell them that, then: Well done."

        await teacher.on_event({"type": "session.output_audio.delta", "delta": keys_voice.to_pcm(np.ones(4))})
        assert audio.said[-1] == pytest.approx(np.ones(4), abs=1e-3)
        await teacher.on_event({"type": "session.output_transcript.delta", "delta": "Bye."})
        await user_says("okay stop")
        assert teacher.done.is_set()

    asyncio.run(scenario())

    # Loose requests, as speech to text really delivered them.
    first = lambda songs: songs[0]  # noqa: E731
    understand = lambda said: keys_voice.understand(said, "mi", first)  # noqa: E731
    assert (understand("Barcom. Largo").kind, understand("Barcom. Largo").song) == ("song", "largo")
    assert understand("Just play some um, some anything").song == "largo"
    assert understand("play something by Beethoven").song == "ode"
    assert understand("jingle bells please").song == "jingle"
    assert understand("play mi sol la").keys == [3, 5, 6]
    assert (
        understand("can you check my pitch on sol").note == "sol" and understand("am I in tune").note == "mi"
    )
    assert understand("let's start the lesson").kind == "teach" and understand("ok stop").kind == "stop"
    assert understand("how are you today").kind == "unknown"
    assert understand("Can you play me? Play me. Play sol").keys == [5]  # only the last try counts
    assert understand("play me sol la").keys == [5, 6] and understand("play me").keys == [3]
    assert understand("Can you play a sol").keys == [5] and understand("play the la").keys == [6]
    assert understand("can you also listen to my, help me sing, with notes and pitch").kind == "teach"
    assert understand("check my singing").kind == "check" and understand("how is my pitch").kind == "check"
    assert keys_voice.keys_listen.voiced_seconds(kpj.sung_tone(kpj.midi_hz(52), rng)) > 1.0
    assert keys_voice.keys_listen.voiced_seconds(np.zeros(32000, dtype=np.float32)) == 0.0

    # After a note the arm is paused and raised, not sent home.
    class Midi:
        events: list[tuple[float, int, int]] = []

    class Stdin:
        lines: list[str] = []

        def write(self, line: str) -> None:
            self.lines.append(line.strip())
            if line.startswith("/subtask"):
                Midi.events += [(0.0, 64, 90)]  # the key goes down at once
            if line.strip() == "/pause 3":
                Midi.events += [(0.2, 64, 0)]  # and only comes up after one extra raise

        def flush(self) -> None:
            pass

    class Process:
        stdin = Stdin()

        def poll(self) -> None:
            return None

    hands = keys_voice.PolicyHands.__new__(keys_voice.PolicyHands)
    hands.midi, hands.process, hands.first, hands.lift_deg = Midi(), Process(), True, 8.0
    assert hands.play(["mi"]) == [("mi", "E4")]
    assert Stdin.lines == ["/subtask play mi, the third key", "/start", "/pause 8", "/pause 3"]
    assert hands.keys_down() == set()

    # /pause ends the run where the arm is: the same flags as a reset, without the reset itself.
    import threading

    import keys_rollout

    class Inference:
        paused = False

        def pause(self) -> None:
            self.paused = True

    controller = argparse.Namespace(
        _control_lock=threading.Lock(),
        _stopped=threading.Event(),
        _stop_requested=threading.Event(),
        _start_requested=threading.Event(),
        _reset_requested=threading.Event(),
        _segment_stop=threading.Event(),
        _wake=threading.Event(),
        _ctx=argparse.Namespace(policy=argparse.Namespace(inference=Inference())),
    )
    controller._start_requested.set()
    assert keys_rollout.pause(controller)
    assert (
        controller._segment_stop.is_set()
        and controller._wake.is_set()
        and controller._ctx.policy.inference.paused
    )
    assert not controller._start_requested.is_set() and not controller._reset_requested.is_set()
    controller._stopped.set()
    assert not keys_rollout.pause(controller)
    keys_rollout.add_pause_command()  # the session class accepts the patch

    # The raise turns only the second servo, towards "up" (negative), and never more than 25 degrees.
    class Robot:
        sent: list[dict[str, float]] = []

        def get_observation(self) -> dict[str, float]:
            return {"shoulder_pan.pos": 3.0, "shoulder_lift.pos": -20.0, "elbow_flex.pos": 40.0, "front": 0.0}

        def send_action(self, action: dict[str, float]) -> None:
            self.sent.append(action)

    home = {"shoulder_pan.pos": 0.0, "shoulder_lift.pos": 0.0, "elbow_flex.pos": 0.0}
    idle = argparse.Namespace(
        running=False,
        _ctx=argparse.Namespace(hardware=argparse.Namespace(robot_wrapper=Robot(), initial_position=home)),
    )
    assert keys_rollout.raise_arm(idle, 8.0, seconds=0.04)
    assert Robot.sent[-1] == {"shoulder_pan.pos": 3.0, "shoulder_lift.pos": -28.0, "elbow_flex.pos": 40.0}
    lifts = [action["shoulder_lift.pos"] for action in Robot.sent]
    assert lifts == sorted(lifts, reverse=True) and len(set(lifts)) == len(lifts)
    assert keys_rollout.raise_arm(idle, 90.0, seconds=0.02) and Robot.sent[-1]["shoulder_lift.pos"] == -45.0
    assert not keys_rollout.raise_arm(idle, 0.0)

    # GPT-Live-1's voice is one endless stream with silence in it: only a short pause of it is kept.
    buffer = keys_voice.SpeechBuffer()
    silence, word = np.zeros(1600, dtype=np.float32), 0.1 * np.ones(1600, dtype=np.float32)
    for chunk in [silence] * 20 + [word] * 5 + [silence] * 30 + [word] * 2 + [silence] * 30:
        buffer.push(chunk)
    kept_s = buffer.samples.size / keys_voice.RATE
    assert kept_s == pytest.approx(0.5 + 0.4 + 0.2 + 0.4, abs=0.01)  # words, and one pause after each
    assert buffer.pull(8000).size == 8000 and buffer.pull(10**6).size == 16000 and buffer.pull(10).size == 0
    for _ in range(50):  # the stream's faint noise between sentences is silence too
        buffer.push(0.001 * np.ones(1600, dtype=np.float32))
    assert buffer.samples.size == 0

    # A soft syllable that arrives just as the speaker ran dry is still played, not dropped.
    buffer = keys_voice.SpeechBuffer()
    buffer.push(word)
    assert buffer.pull(10**6).size == 0  # under the cushion while more is still coming: wait
    for _ in range(4):
        buffer.push(word)
    assert buffer.pull(10**6).size == 8000 and buffer.gaps == 1  # it ran dry mid-reply: a gap, then a refill
    buffer.push(word)
    assert buffer.pull(10**6).size == 0  # refilling: a quarter second first
    buffer.push(word)
    buffer.push(word)
    assert buffer.pull(10**6).size == 4800
    buffer.push(0.001 * np.ones(800, dtype=np.float32))
    assert buffer.samples.size == 800

    # A take with only a flicker of clear pitch is "not heard", not a wild number of cents.
    import keys_pitch_judge as judge

    noise = np.random.default_rng(1)
    flicker = np.concatenate(
        [judge.sung_tone(judge.midi_hz(57), noise, seconds=0.3), np.zeros(16000, np.float32)]
    )
    result, _ = keys_voice.judge_singing(flicker.astype(np.float32), "la")
    assert result["cents"] is None
    result, _ = keys_voice.judge_singing(judge.sung_tone(judge.midi_hz(57), noise), "la")
    assert abs(result["cents"]) < 20

    # The cartoon voice: 15 % faster on a stream, with no clicks where the chunks meet.
    speed = keys_voice.Speedup(1.15)
    tone = (0.2 * np.sin(2 * np.pi * 200 * np.arange(16000) / 16000)).astype(np.float32)
    out = np.concatenate([speed(chunk) for chunk in np.array_split(tone, 37)])
    assert out.size == pytest.approx(16000 / 1.15, abs=3)
    assert np.max(np.abs(np.diff(out))) < 1.2 * np.max(np.abs(np.diff(tone))) * 1.15
    assert keys_voice.Speedup(1.0)(tone) is tone


def test_display_draws_the_sung_pitch_live_during_your_turn() -> None:
    import keys_listen
    import keys_ui

    # The live reading: a steady F against mi is one key (100 cents) too high, in any octave.
    rate = keys_listen.RATE
    t = np.arange(int(rate * keys_ui.LIVE_WINDOW_S)) / rate
    for hz in (349.23, 174.61):
        tone = (0.2 * np.sin(2 * np.pi * hz * t) + 0.1 * np.sin(4 * np.pi * hz * t)).astype(np.float32)
        result = keys_listen.tracker().detect(tone, rate)
        heard, cents = keys_ui.live_pitch(result.pitch_hz, result.confidence, keys_ui.NOTE_OF["mi"])
        assert heard is not None and cents is not None
        assert abs(heard - hz) < 3 and abs(cents - 100) < 15
    silence = keys_listen.tracker().detect(np.zeros(len(t), dtype=np.float32), rate)
    assert keys_ui.live_pitch(silence.pitch_hz, silence.confidence, 64) == (None, None)
    assert keys_ui.fold_cents(440.0, 69) == 0.0 and abs(keys_ui.fold_cents(220.0, 69)) < 1e-6

    # The display streams every reading, keeps the last seconds for a page opened late, and hands each
    # open page only what it has not seen.
    hub = keys_ui.Hub()
    hub.add_pitch(100.0, None, 0.001)
    hub.add_pitch(100.05, 63.58, 0.05, piano=True)
    with hub.cond:
        newest, readings = hub.pitch_since(0)
        assert readings == [[100.0, None, 0.001, 0], [100.05, 63.58, 0.05, 1]]
        assert hub.pitch_since(newest) == (newest, [])
    hub.add_pitch(100.0 + keys_ui.KEEP_PITCH_S + 1, 64.0, 0.05)
    with hub.cond:
        assert [r[0] for r in hub.pitch_since(0)[1]] == [100.0 + keys_ui.KEEP_PITCH_S + 1]

    # The conversation: each turn starts a fresh line, for you and for the teacher.
    talk = keys_ui.Hub()
    for kind, text in [("teacher", "Hi! "), ("teacher", "Boop."), ("you", "How are"), ("you", " you?")]:
        talk.event({"kind": kind, "text": text})
    assert (talk.state["teacher"], talk.state["said"]) == ("Hi! Boop.", "How are you?")
    talk.event({"kind": "teacher", "text": "Fantastic!"})
    talk.event({"kind": "you", "text": "Play mi"})
    assert (talk.state["teacher"], talk.state["said"]) == ("Fantastic!", "Play mi")

    # The microphone is the user only in their turn, once the piano is silent: never the piano or the teacher.
    assert not keys_ui.listening({"phase": "playing", "keys_down": []}, 10.0, 0.0)
    assert not keys_ui.listening({"phase": "your_turn", "keys_down": [64], "press": {"t": 9.0}}, 10.0, 0.0)
    # A key the arm leaves resting down has faded after a few seconds: then it is you again.
    assert keys_ui.listening({"phase": "your_turn", "keys_down": [64], "press": {"t": 7.0}}, 10.0, 0.0)
    assert not keys_ui.listening({"phase": "your_turn", "keys_down": []}, 10.0, 10.1)
    assert keys_ui.listening({"phase": "your_turn", "keys_down": []}, 10.2, 10.1)

    # A turn to sing, and the arm finishing while you already sing does not end it.
    hub.event({"kind": "your_turn", "note": "mi", "seconds": 6.0})
    turn = hub.state["turn"]
    assert turn["note"] == "mi" and turn["seconds"] == 6.0 and hub.state["phase"] == "your_turn"
    hub.event({"kind": "done"})
    assert hub.state["phase"] == "your_turn"
    hub.event({"kind": "sung", "note": "mi", "cents": -40.0, "answer": "Well done."})
    assert hub.state["phase"] == "feedback" and "ended" in hub.state["turn"]
    hub.event({"kind": "your_turn", "note": "sol"})
    assert hub.state["turn"]["note"] == "sol" and hub.state["sung"] is None


def test_display_serves_the_3d_page_in_live_mode(tmp_path: Path) -> None:
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    import keys_ui

    web = tmp_path / "web"
    (web / "js").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><title>page</title>")
    (web / "js" / "main.js").write_text("export {};")
    (tmp_path / "secret.txt").write_text("not for the browser")
    assert keys_ui.find_web(str(web)) == web.resolve()
    with pytest.raises(SystemExit):
        keys_ui.find_web(str(tmp_path / "nowhere"))

    hub = keys_ui.Hub()
    server = ThreadingHTTPServer(("127.0.0.1", 0), keys_ui.make_handler(hub, web.resolve()))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(f"{base}/") as page:
            assert b"<title>page</title>" in page.read()
        with urllib.request.urlopen(f"{base}/js/main.js") as script:  # modules load only as JavaScript
            assert script.headers["Content-Type"].startswith("text/javascript")
        with urllib.request.urlopen(f"{base}/state") as state:  # the page's sign that it runs live
            assert json.loads(state.read())["phase"] == "idle"
        for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/missing.js"):
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(f"{base}{path}")
            assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_reachy_holds_the_saved_pose_while_the_arm_plays(tmp_path: Path) -> None:
    import threading

    import keys_reachy
    import keys_ui

    class FakeDaemon:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def pose(self) -> dict:
            return {
                "head": {"x": 0, "y": 0, "z": 0, "roll": 0, "pitch": 0.5, "yaw": 0.15},
                "antennas": [-0.2, 0.2],
            }

        def goto(self, pose: dict, seconds: float) -> None:
            self.calls.append(("goto", pose))

        def target(self, head: dict, antennas: list) -> None:
            self.calls.append(("target", {"head": head, "antennas": antennas}))

    daemon = FakeDaemon()
    saved = daemon.pose()
    keys_reachy.POSE_FILE = tmp_path / "reachy_pose.json"
    keys_reachy.save(daemon, keys_reachy.POSE_FILE)  # type: ignore[arg-type]
    assert keys_reachy.load(keys_reachy.POSE_FILE) == saved

    # Every mood stays close to the saved pose: Reachy keeps looking at the keys.
    for mood in ("idle", "talk", "listen", "joy"):
        for t in np.linspace(0, 10, 50):
            head, antennas = keys_reachy.moved(saved, *keys_reachy.offsets(mood, float(t), 0.0))
            assert (
                abs(head["yaw"] - 0.15) <= 0.25
                and abs(head["pitch"] - 0.5) <= 0.2
                and abs(head["roll"]) <= 0.2
            )
            assert all(abs(a) <= 1.4 for a in antennas)

    hub, stop = keys_ui.Hub(), threading.Event()
    hub.event({"kind": "tune", "notes": ["mi"]})
    assert hub.state["arm_busy"]
    thread = threading.Thread(target=keys_reachy.follow, args=(hub, stop, daemon), daemon=True)
    thread.start()
    time.sleep(0.3)
    assert [kind for kind, _ in daemon.calls] == ["goto"]  # the arm plays: back to the pose, and still
    assert daemon.calls[0][1] == saved
    hub.event({"kind": "done"})
    assert not hub.state["arm_busy"]
    time.sleep(keys_reachy.SETTLE_S + 0.3)
    assert daemon.calls[-1][0] == "target"  # alive again once the arm has settled
    stop.set()
    thread.join(timeout=3)
    assert daemon.calls[-1] == ("goto", saved)  # and left where the policy expects it
