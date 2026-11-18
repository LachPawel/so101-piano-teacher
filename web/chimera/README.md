# John, the singing head (experiment)

A build of [Chimera](https://github.com/jamesondh/chimera-vocal-synth) (MIT, `LICENSE` here), a voice
grown from a simulated airway: a Rust engine in an AudioWorklet and a 3D head and throat in three.js.
`teacher.html` shows only the head, "John", in this page's colours, and sings one note when
`js/chimera.js` asks. See beginner mode below.

## Talking to it

The page sends `postMessage` to the frame (same origin only):

- `{ type: "sing", midi, seconds, name }`: sing one note on its name (`name`: do, re, mi, fa, sol, la)
- `{ type: "idle", text }`: stop, and say something else
- `{ type: "stop" }`, `{ type: "mute", on }`, `{ type: "body", body: "male" | "female" | "child" }`

It answers `{ source: "chimera-coach", type: "ready" | "started" | "singing" | "sang" | "needs-click" }`,
and `{ type: "pitch", f0 }` 20 times a second while he sings.
Browsers allow sound only after a click inside the frame, so it shows "Start John" until then.
Page URL options: `?chimera=0` turns the coach off, `?chimera_body=female` picks the voice.

## Rebuilding

Built from upstream `e780f7d` with `source/chimera-teacher-embed.patch` applied (a `teacher.html`
entry, `src/ui/teacher/`, and a light theme for the head: min blending on paper instead of max on black).

```bash
cd chimera-vocal-synth && git apply /path/to/web/chimera/source/chimera-teacher-embed.patch
cargo build -p chimera-wasm --release --target wasm32-unknown-unknown
cp target/wasm32-unknown-unknown/release/chimera_wasm.wasm src/wasm/chimera.wasm
npx vite build --config vite.teacher.config.ts
rsync -a --delete --exclude data --exclude README.md --exclude LICENSE --exclude source dist-teacher/ /path/to/web/chimera/
```

## Beginner mode: John shows first

With `keys_voice.py --beginner`, each note goes: the arm plays it; the display says "John sings mi. Watch."
and John appears half see-through over the pitch lane and sings it on its name (do, re, mi, fa, sol, la,
from exact IPA), his pitch drawn on the lane as a dashed line; then he fades away, the arm plays the
note again and it's "Your turn: sing mi." with only the lane. He never sings in your turn, so the
microphone never hears him. Without `--beginner` he stays hidden.

## Sound on another speaker

John plays through the browser, so through the Mac's sound output: pick the speaker there (for
example a JBL Flip 7). The teacher's voice goes where `keys_voice.py --speaker` says, for example
`--speaker "JBL Flip 7"` (default: Reachy Mini's speaker when plugged in, else the system output).
