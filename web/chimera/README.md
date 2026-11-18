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
Also `{ type: "sleep" }` and `{ type: "wake" }`. Browsers allow sound only once the page has been clicked or
a key pressed: he tries to start by himself, and otherwise the first click or key anywhere on the page
starts him (the page shows "Click anywhere for John's voice" until then). To never need it, allow sound
or autoplay for `localhost:8770` in the browser's site settings.
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

## The lesson: John shows first

Reachy asks if it is your first time. For beginners (the default) each note goes: the display says
"John sings mi. Watch." and John appears at the bottom left, half see-through over the pitch lane; the
arm plays the note and, the moment the key sounds, John sings it on its name (do, re, mi, fa, sol, la,
from exact IPA), his pitch drawn on the lane as a dashed line; he fades away; Reachy says "Now your
turn" out loud; and only then does the arm play the note again, which opens your window. He never sings in
your turn, so the microphone never hears him. Say "I've sung before" or "skip John" to go straight to
the notes, "I'm a beginner" to get him back; `keys_voice.py --direct` starts without him.

A note counts only when it is right and held in tune for one full second in one go: crossing it on the way
somewhere else gets "Hold it longer", with how long you were on it and off it. The lane counts the hold
live ("Hold it… 0.6 s", then "Held ✓"). The arm sets off while Reachy still says "Now your turn".

The turn opens on the display as the arm starts your note, and your pitch is drawn from then on (grey
while the piano also sounds). The pitch lane is on screen only for John's note, your turn and the
verdict; a turn nobody closes (a teacher stopped with Ctrl+C) expires by itself.

## Warm-up, songs and band mode

- "Let's warm up": the arm goes up and down the six notes, John sings each key it presses, you sing
  along. Nothing is judged. Reachy proposes it by itself, then the lesson.
- `keys_voice.py --songs`: just chat and play. Reachy is a relaxed jukebox: "play largo", "play
  anything", "play stomp". No lessons unless you ask.
- Band mode, in any mode: say "band mode" or "jam with me" (or "jam on largo"). The arm loops the
  riff's first line four times as a backing track, John sings each key the arm presses, and you play
  over it. Nothing is judged, and the teacher's microphone is closed meanwhile.

If the arm's policy stops by itself (its engine gives up when actions come late), the teacher starts it
again (about 15 seconds, "Waking the arm" on the display) and plays the note again; after two failed
restarts in one request it says the arm is down instead of crashing. The policy now also waits 8 seconds
for late actions, not 1, holding the arm still meanwhile. A note the arm never sounds is not judged.

Hidden, John sleeps: he is not drawn and his voice engine is suspended, so he costs the rig's computer
nothing between notes. Awake, he draws at 30 frames a second and a lower pixel ratio: the policy runs on the
same GPU.

## Sound on another speaker

John plays through the browser, so through the Mac's sound output: pick the speaker there (for
example a JBL Flip 7). The teacher's voice goes where `keys_voice.py --speaker` says, for example
`--speaker "JBL Flip 7"` (default: Reachy Mini's speaker when plugged in, else the system output).
