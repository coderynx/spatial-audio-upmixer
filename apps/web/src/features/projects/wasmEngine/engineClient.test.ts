import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DspEngineClient } from "./engineClient";

// `updateParams` coalesces to one post per frame while every other message
// posts immediately, so the port's order is what has to be pinned here: the
// worklet acts on the parameter block that is in it at the moment a message
// lands (a measurement forks the engine right then), and an update overtaken
// by a later message would have it act on the previous mix.

type PortMessage = { type: string; [key: string]: unknown };

class FakePort {
  messages: PortMessage[] = [];
  onmessage: ((event: MessageEvent) => void) | null = null;
  postMessage(message: PortMessage) {
    this.messages.push(message);
  }
}

class FakeAudioWorkletNode {
  port = new FakePort();
  connect(target: unknown) {
    return target;
  }
  disconnect() {}
}

const context = {
  audioWorklet: { addModule: async () => {} },
} as unknown as BaseAudioContext;

async function makeClient() {
  const client = await DspEngineClient.create(context, 2);
  return { client, port: (client.node as unknown as FakeAudioWorkletNode).port };
}

beforeEach(() => {
  vi.stubGlobal("AudioWorkletNode", FakeAudioWorkletNode);
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true })));
  vi.stubGlobal("WebAssembly", { ...WebAssembly, compileStreaming: async () => ({}) });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("DspEngineClient message ordering", () => {
  it("flushes the coalesced parameter update before starting a measurement", async () => {
    const { client, port } = await makeClient();

    client.updateParams({ marker: "current" });
    void client.measure([1, 1]);

    expect(port.messages.map((message) => message.type)).toEqual(["update", "measure"]);
  });

  it("flushes it before the transport starts, so playback never runs a frame of stale gain", async () => {
    const { client, port } = await makeClient();

    client.updateParams({ marker: "current" });
    client.setTransport({ playing: true });

    expect(port.messages.map((message) => message.type)).toEqual(["update", "transport"]);
  });

  it("flushes parameters before the worklet-side start command", async () => {
    const { client, port } = await makeClient();

    client.updateParams({ marker: "current" });
    client.start(48000, true);

    expect(port.messages).toMatchObject([
      { type: "update" },
      { type: "start", frame: 48000, loop: true },
    ]);
  });

  it("waits for the worklet to finish warming a seek", async () => {
    const { client, port } = await makeClient();

    const seeked = client.seek(48000);
    const message = port.messages.at(-1)!;

    expect(message).toMatchObject({ type: "seek", frame: 48000 });
    port.onmessage?.({ data: { type: "seeked", id: message.id } } as MessageEvent);
    await seeked;
  });

  it("still coalesces a burst of updates into one post per frame", async () => {
    const { client, port } = await makeClient();

    client.updateParams({ marker: 1 });
    client.updateParams({ marker: 2 });
    client.updateParams({ marker: 3 });
    await new Promise((resolve) => requestAnimationFrame(() => resolve(null)));

    expect(port.messages.filter((message) => message.type === "update")).toHaveLength(1);
  });
});
