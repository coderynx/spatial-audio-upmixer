export type SpatialCanvasSize = { width: number; height: number; dpr: number };

export function startSpatialCanvas({
  canvas,
  container,
  active,
  draw,
  resize,
  settleFrames,
}: {
  canvas: HTMLCanvasElement;
  container: HTMLElement;
  active: { current: boolean };
  draw: (time: number, size: SpatialCanvasSize) => boolean;
  resize?: (size: SpatialCanvasSize) => void;
  settleFrames: number;
}) {
  let frame: number | null = null;
  let resizeFrame: number | null = null;
  let idleFrames = 0;
  let size: SpatialCanvasSize = { width: 1, height: 1, dpr: 1 };

  const wake = () => {
    if (frame !== null) window.cancelAnimationFrame(frame);
    frame = null;
    idleFrames = 0;
    render(performance.now());
  };
  const resizeCanvas = () => {
    const dpr = window.devicePixelRatio || 1;
    size = { width: container.clientWidth, height: container.clientHeight, dpr };
    canvas.width = Math.max(1, Math.round(size.width * dpr));
    canvas.height = Math.max(1, Math.round(size.height * dpr));
    resize?.(size);
  };
  const render = (time: number) => {
    const settled = draw(time, size);
    idleFrames = !active.current && settled ? idleFrames + 1 : 0;
    if (active.current || idleFrames < settleFrames) frame = window.requestAnimationFrame(render);
    else frame = null;
  };
  const observer = new ResizeObserver(() => {
    if (resizeFrame !== null) return;
    resizeFrame = window.requestAnimationFrame(() => {
      resizeFrame = null;
      resizeCanvas();
      wake();
    });
  });

  resizeCanvas();
  observer.observe(container);
  frame = window.requestAnimationFrame(render);
  return {
    wake,
    dispose() {
      observer.disconnect();
      if (resizeFrame !== null) window.cancelAnimationFrame(resizeFrame);
      if (frame !== null) window.cancelAnimationFrame(frame);
    },
  };
}
