// The six keys the arm plays, and tunes written for them.
// Stickers 1 to 6 sit on six neighbouring white keys: C D E F G A. In a tune, 0 holds a beat.

export const KEYS = [
  { key: 1, word: "do", midi: 60 },
  { key: 2, word: "re", midi: 62 },
  { key: 3, word: "mi", midi: 64 },
  { key: 4, word: "fa", midi: 65 },
  { key: 5, word: "sol", midi: 67 },
  { key: 6, word: "la", midi: 69 },
];

export const keyByNumber = (number) => KEYS[number - 1];
export const keyByMidi = (midi) => KEYS.find((entry) => entry.midi === midi) ?? null;

// The same files as scripts/songs in this repository.
export const SONGS = {
  scale: { title: "Up the six keys", notes: [1, 2, 3, 4, 5, 6] },
  warmup: {
    title: "Five-note warm-up",
    notes: [1, 2, 3, 4, 5, 4, 3, 2, 1, 0, 0, 2, 3, 4, 5, 6, 5, 4, 3, 2],
  },
  ode: {
    title: "Ode to Joy (Beethoven)",
    notes: [3, 3, 4, 5, 5, 4, 3, 2, 1, 1, 2, 3, 3, 0, 2, 2, 0, 3, 3, 4, 5, 5, 4, 3, 2, 1, 1, 2, 3, 2, 0, 1, 1],
  },
};

// The notes of a short singing lesson: the three keys the trained policy presses most reliably.
export const LESSON_KEYS = [3, 5, 6];
