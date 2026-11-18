# SO-101 Piano Teacher

A cheap robot arm (SO-101) that presses the keys of a MIDI keyboard, hears the notes it plays, and gives
basic singing lessons together with a Reachy Mini.

It started as one question: today's robot policies learn from camera images and joint positions, so what
changes if you also let one hear? Here the sound of the keyboard is drawn as a picture (a spectrogram) and
fed to a stock [SmolVLA](https://huggingface.co/docs/lerobot/smolvla) policy as a second camera. No model
change. The MIDI keyboard labels every recording and scores every press for free.

This is a hobby experiment, built at home on one rig. Expect rough edges.

## What is in here

| Path | What it does |
| --- | --- |
| `scripts/keys_teleop.sh`, `keys_flow_record.py` | Record demonstrations with the leader arm, one key per episode, labelled by MIDI |
| `scripts/keys_scripted.py` | The arm pressing keys from saved poses: recording, testing and the backup for demos |
| `scripts/keys_flow_train.sh`, `keys_variants.py` | Build the training sets and start SmolVLA training on Hugging Face Jobs |
| `scripts/keys_play.py` | Let a trained policy play a note or a tune, and score it from MIDI |
| `scripts/keys_companion.py` | The teacher: play notes you name, teach a tune, run a singing warm-up |
| `scripts/keys_listen.py` | SwiftF0 pitch tracking: is a sung note flat or sharp, and which notes are in a riff |
| `scripts/keys_voice.py` | The voice teacher: say "play mi sol la" or "start the lesson", the policy plays, you sing back |
| `scripts/keys_ui.py` | The stage display: serves `web/` live from the rig, the 3D arm follows the real one, your pitch live |
| `scripts/keys_reachy.py` | Reachy Mini holds its saved head pose while the arm plays, and moves and wiggles otherwise |
| `scripts/keys_rollout.py` | `lerobot-rollout` with a `/pause` command, so one loaded policy can play note after note |
| `scripts/keys_pitch_judge.py` | A test of whether Gemini Decision 1 can judge pitch (it cannot) |
| `scripts/keys_synth.py` | A small software piano for the MIDI keyboard, with a clean audio feed for the "ears" |
| `plugins/lerobot_camera_spectrogram` | LeRobot camera plugin: a microphone or audio cable as a spectrogram image |
| `plugins/lerobot_camera_scaled` | LeRobot camera plugin: capture at one size, deliver a smaller one |
| `scripts/test_keys.py` | Tests that need no hardware, microphone or API key |
| `web/` | A browser page: the arm and keyboard in 3D, a pitch meter and the singing lesson. Also the stage display. See `web/README.md` |

## Models and data

Everything the experiment used is public on Hugging Face. "Camera + ears" means the policy also gets the
sound picture; "camera only" is its deaf twin, trained on the same episodes.

| What | Hugging Face | Was called |
| --- | --- | --- |
| The policy shown live | [smolvla-so101-piano-flow](https://huggingface.co/PawelLach/smolvla-so101-piano-flow) | `train_2026-11-17_22-11-36` |
| Its data, 300 episodes | [so101-piano-flow-300ep](https://huggingface.co/datasets/PawelLach/so101-piano-flow-300ep) | `so101_keys_flow_300ep` |
| Policy, 268 episodes, camera + ears | [smolvla-so101-piano-268ep-camera-ears](https://huggingface.co/PawelLach/smolvla-so101-piano-268ep-camera-ears) | `train_2026-11-17_11-41-38` |
| Policy, 268 episodes, camera only | [smolvla-so101-piano-268ep-camera-only](https://huggingface.co/PawelLach/smolvla-so101-piano-268ep-camera-only) | `train_2026-11-17_11-41-46` |
| Data, 268 episodes | [camera + ears](https://huggingface.co/datasets/PawelLach/so101-piano-268ep-camera-ears), [camera only](https://huggingface.co/datasets/PawelLach/so101-piano-268ep-camera-only) | `so101_keys_teleop_20261116_212951_v2_both`, `..._v2_text` |
| Policy, 134 episodes, 5k steps | [camera + ears](https://huggingface.co/PawelLach/smolvla-so101-piano-134ep-camera-ears-5k), [camera only](https://huggingface.co/PawelLach/smolvla-so101-piano-134ep-camera-only-5k) | `smolvla_keys_both_5k`, `smolvla_keys_text_5k` |
| Training run, 134 episodes, camera only | [smolvla-so101-piano-134ep-camera-only-run](https://huggingface.co/PawelLach/smolvla-so101-piano-134ep-camera-only-run) | `train_2026-11-16_21-24-43` |
| Data, 134 episodes | [camera + ears](https://huggingface.co/datasets/PawelLach/so101-piano-134ep-camera-ears), [camera only](https://huggingface.co/datasets/PawelLach/so101-piano-134ep-camera-only) | `so101_keys_teleop_20261116_212951_both`, `..._text` |

The scripts still use the old names: Hugging Face redirects them, and the local copies on the rig are
filed under them.

## Hardware

- SO-101 follower and leader arms
- A USB MIDI keyboard with stickers 1 to 6 on six neighbouring white keys: C D E F G A (do re mi fa sol la)
- One camera that sees the keys and the arm (a Reachy Mini here)
- A Mac. The scripts use Apple Silicon for speech to text and the `mps` device

## Quick start

```bash
git clone https://github.com/LachPawel/so101-piano-teacher
cd so101-piano-teacher
uv run --with-requirements scripts/requirements-keys.txt pytest scripts/test_keys.py -q
```

Everything runs from the repository root with the same prefix:

```bash
uv run --with-requirements scripts/requirements-keys.txt scripts/keys_listen.py check mi
```

Each script starts with a docstring that explains what it does and how to run it.

## On stage

Three terminal tabs, then open http://localhost:8770 full screen:

```bash
uv run --with-requirements scripts/requirements-keys.txt scripts/keys_synth.py --ears "BlackHole 2ch"
```

```bash
uv run --with-requirements scripts/requirements-keys.txt scripts/keys_ui.py
```

```bash
uv run --with-requirements scripts/requirements-keys.txt --with aic-sdk==3.4.0 scripts/keys_voice.py --mic "MacBook Pro Microphone"
```

Once, with Reachy looking at the keys as in training: `scripts/keys_reachy.py save`. The display then
holds that head pose while the arm plays and moves Reachy otherwise.

The teacher speaks through Reachy Mini's own speaker when it is plugged in (`--speaker` to choose another).
Name the microphone: Bluetooth headphones that connect take over the Mac's default. The display
needs no internet; `scripts/keys_ui.py --demo` shows a made-up session without the rig.

## Things you will need to change for your own rig

- Serial ports and the robot id (`/dev/tty.usbmodem...`, `workshop_so101`) in the shell scripts and defaults
- Camera index and audio device names in `scripts/keys_cameras*.json`
- Hugging Face repository ids (`PawelLach/...`) for datasets and models
- `keys_poses.json` and `keys.json`: the saved arm poses and key sounds of my keyboard position

The trained policies only know my keyboard position, camera view and lighting. They will not work on another
rig without new recordings. The datasets and models are not public yet.

## Optional services

- [ai-coustics](https://ai-coustics.com) Quail Voice Focus cleans spoken commands. Needs `AIC_SDK_LICENSE`.
- `keys_voice.py` talks through OpenAI GPT-Live-1. Needs `OPENAI_API_KEY`.
- `keys_pitch_judge.py judge` uploads audio clips to Google. Needs `GEMINI_API_KEY`.

Keys are read from the environment first, then from the `.env` file named at the top of the script.
Never commit them.

## Built on

[LeRobot](https://github.com/huggingface/lerobot), pinned in `pyproject.toml` to the commit these scripts
were written against, and [SwiftF0](https://pypi.org/project/swift-f0/).
