import { afterEach, describe, expect, it, vi } from "vitest";
import { startSpatialCanvas } from "./spatialCanvas";

describe("spatial canvas lifecycle", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sizes at device scale, wakes on resize, and stops after settling", () => {
    const queued = new Map<number, FrameRequestCallback>();
    let nextFrame = 0;
    const requestAnimationFrame = vi.fn((callback: FrameRequestCallback) => {
      const id = ++nextFrame;
      queued.set(id, callback);
      return id;
    });
    const cancelAnimationFrame = vi.fn((id: number) => queued.delete(id));
    let resize!: ResizeObserverCallback;
    class TestResizeObserver {
      constructor(callback: ResizeObserverCallback) { resize = callback; }
      observe() {}
      disconnect() {}
    }
    vi.stubGlobal("requestAnimationFrame", requestAnimationFrame);
    vi.stubGlobal("cancelAnimationFrame", cancelAnimationFrame);
    vi.stubGlobal("ResizeObserver", TestResizeObserver);
    Object.defineProperty(window, "devicePixelRatio", { configurable: true, value: 2 });
    const canvas = document.createElement("canvas");
    const container = document.createElement("div");
    Object.defineProperties(container, {
      clientWidth: { configurable: true, value: 120 },
      clientHeight: { configurable: true, value: 80 },
    });
    const draw = vi.fn(() => true);
    const runNext = (time: number) => {
      const [id, callback] = queued.entries().next().value as [number, FrameRequestCallback];
      queued.delete(id);
      callback(time);
    };
    const lifecycle = startSpatialCanvas({
      canvas,
      container,
      active: { current: false },
      draw,
      settleFrames: 2,
    });

    expect([canvas.width, canvas.height]).toEqual([240, 160]);
    runNext(0);
    runNext(16);
    expect(draw).toHaveBeenCalledTimes(2);
    resize([] as unknown as ResizeObserverEntry[], {} as ResizeObserver);
    runNext(32);
    expect(draw).toHaveBeenCalledTimes(3);
    lifecycle.dispose();
    expect(cancelAnimationFrame).toHaveBeenCalled();
  });
});
