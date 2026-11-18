# Visual verification

Captured on 2026-11-18 from the static page served on port 8765 with
`python3 -m http.server 8765 --directory web`.

- [Desktop](desktop.png): Chromium, 1440 × 1000, device scale 1, pressing **3 / mi / E4**.
- [Phone](phone.png): Chromium mobile/touch emulation, 390 × 844 viewport,
  device scale 2, full-page capture, pressing **6 / la / A4**.

The renderer was paused at each normal note button's contact callback only to
capture the exact key-down frame, then resumed. The screenshots show the
SO-101 **in front of the keyboard**, with its gripper closed.

## Checks

- `node --test web/tests/pitch.test.mjs`: all 5 tests passed, unchanged.
- No page exceptions, console errors, HTTP errors, or external asset requests.
  The browser blocked non-local requests throughout the run.
- All six sticker keys: the rendered fingertip meets the key surface within
  0.01 mm at the callback, the intended key depresses by 6 mm at the contact
  point, and all other keys remain at rest.
- Press queue order and `busy`, MIDI-style white-key mirroring and black-key
  flashes, target highlighting/clearing, listening and celebration methods.
- Actual UI tune playback and number-key input through unchanged `main.js`.
- No horizontal overflow at widths 320, 390, 768, 1024, and 1440.
- Reduced-motion preference disables Reachy's idle movement and antenna dance.

These are browser rendering checks, not physical-robot accuracy measurements.
No real microphone or MIDI hardware was used; Safari, Firefox, and an actual
phone were not tested during this visual-only update.
