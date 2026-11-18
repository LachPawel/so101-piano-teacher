// Notes from a USB MIDI keyboard (Web MIDI: Chrome and Edge).

export const midiSupported = () => typeof navigator !== "undefined" && "requestMIDIAccess" in navigator;

/**
 * Listen to every connected MIDI input.
 * onNote(midi, velocity) is called for each key that goes down, onDevices(names) when the list changes.
 */
export async function connectMidi(onNote, onDevices) {
  const access = await navigator.requestMIDIAccess();
  const listen = () => {
    const names = [];
    for (const input of access.inputs.values()) {
      names.push(input.name);
      input.onmidimessage = (event) => {
        const [status, note, velocity] = event.data;
        if ((status & 0xf0) === 0x90 && velocity > 0) onNote(note, velocity);
      };
    }
    onDevices(names);
  };
  access.onstatechange = listen;
  listen();
}
