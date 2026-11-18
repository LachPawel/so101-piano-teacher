# Web UI

A static page: the arm, the keyboard and a Reachy Mini in 3D, a pitch meter, and the singing lesson.
No build step and no server code.

## Run it locally

```bash
python3 -m http.server 8765 --directory web
```

Then open http://localhost:8765. It must be served, not opened as a file, because it uses JavaScript modules.

## What works

- **Arm and keyboard.** Click a note, or press 1 to 6: the arm presses the key and you hear it.
- **Tunes.** The same tunes as `scripts/songs`.
- **Pitch meter.** Turn the microphone on and sing: note name, and cents flat or sharp.
- **Singing lesson.** The arm plays mi, sol, la. After each you sing, and get higher, lower or well done.
- **MIDI keyboard.** Connect a USB MIDI keyboard and its notes light the keys. The arm copies presses
  on the six sticker keys. This makes the page a live display next to the real robot.

## What it is not

- **Not a physics simulator.** The arm uses the official SO-101 meshes and joint frames, with
  scripted visual inverse kinematics. It cannot be used to train or test a policy.
- **No learned policy and no voice model.** The real robot runs SmolVLA and a voice agent.
- **Not connected to the real arm.** It only sees what the MIDI keyboard and the microphone tell it.

## Browsers

Chrome and Edge for everything. Firefox and Safari work without the MIDI keyboard.

## Tests

```bash
node --test web/tests/pitch.test.mjs
```

The pitch code in `js/pitch.js` is a port of `sung_pitch` and `judge_pitch` in
`scripts/keys_companion.py`, so the page and the Python teacher give the same verdict.

## Deploying

`.github/workflows/pages.yml` publishes this folder to GitHub Pages on every push to `main`.
Pages must be switched on once: repository Settings, Pages, Source: GitHub Actions.

three.js 0.170 is vendored in `vendor/` under the MIT license (`vendor/THREE-LICENSE`).
The official SO-101 URDF and STL meshes are vendored under Apache-2.0; see
[`vendor/so101/README.md`](vendor/so101/README.md) for pinned sources and attribution.
All scene assets, including labels and Reachy's procedural geometry, work without external
network access when served locally. No build step is required.
