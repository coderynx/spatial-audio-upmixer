type FrameCallback = (time: number) => void;

let nextId = 0;
let frameId: number | null = null;
const callbacks = new Map<number, FrameCallback>();

function flush(time: number) {
  frameId = null;
  const pending = [...callbacks.entries()];
  callbacks.clear();
  for (const [, callback] of pending) callback(time);
}

export function requestFrame(callback: FrameCallback): number {
  const id = ++nextId;
  callbacks.set(id, callback);
  if (frameId === null) frameId = window.requestAnimationFrame(flush);
  return id;
}

export function cancelFrame(id: number) {
  callbacks.delete(id);
}
