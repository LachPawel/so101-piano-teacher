# Chimera, the throat coach (experiment)

A build of [Chimera](https://github.com/jamesondh/chimera-vocal-synth) (MIT, `LICENSE` here), a voice
grown from a simulated airway: a Rust engine in an AudioWorklet and a 3D head and throat in three.js.
`teacher.html` shows only the head, in this page's colours, and sings one note on "la" when
`js/chimera.js` asks. In live mode it appears over the stage when it's your turn and sings the asked
note once, right after the arm's key stops sounding.

## Talking to it

The page sends `postMessage` to the frame (same origin only):

- `{ type: "sing", midi, word: "la", seconds, name }`: sing one note (`name`, e.g. "mi", is for the caption)
- `{ type: "idle", text }`: stop, and say something else
- `{ type: "stop" }`, `{ type: "mute", on }`, `{ type: "body", body: "male" | "female" | "child" }`

It answers `{ source: "chimera-coach", type: "ready" | "started" | "singing" | "sang" | "needs-click" }`.
Browsers allow sound only after a click inside the frame, so it shows "Start Chimera" until then.
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
