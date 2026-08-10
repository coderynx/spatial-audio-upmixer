import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defaultManifest, type Manifest } from "@/lib/manifest";
import { useKeyCommands, type KeyCommandsOptions } from "./useKeyCommands";

// A real keydown always targets an Element (the focused one, or document.body
// when nothing is focused) — never `window` itself — so default to body and
// let it bubble, matching what the hook's window-level listener actually sees.
// Wrapped in `act` because dispatch can call `setShortcutsOpen`, a state
// update React schedules outside its own event system here.
function press(init: KeyboardEventInit & { code?: string }, target: EventTarget = document.body) {
  const event = new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init });
  act(() => { target.dispatchEvent(event); });
  return event;
}

function manifestWith(overrides: Partial<Manifest["mixing"]>): Manifest {
  return { ...defaultManifest, mixing: { ...defaultManifest.mixing, ...overrides } };
}

function makeOptions(overrides: Partial<KeyCommandsOptions> = {}): KeyCommandsOptions {
  return {
    transportEnabled: true,
    preview: {
      playing: false,
      duration: 100,
      currentTimeRef: { current: 10 },
      playPause: vi.fn(),
      stop: vi.fn(),
      seek: vi.fn(),
      toggleLoop: vi.fn(),
    },
    stems: ["Vocals", "Drums", "Bass"],
    selectedStem: "Drums",
    onSelectStem: vi.fn(),
    onToggleMute: vi.fn(),
    onToggleSolo: vi.fn(),
    manifest: manifestWith({}),
    onManifestChange: vi.fn(),
    paneView: "timeline",
    onChangePane: vi.fn(),
    ...overrides,
  };
}

beforeEach(() => { document.body.innerHTML = ""; });
afterEach(() => { document.body.innerHTML = ""; });

describe("useKeyCommands dispatch", () => {
  it("Space calls playPause when transport is enabled", () => {
    const options = makeOptions();
    renderHook(() => useKeyCommands(options));
    press({ key: " " });
    expect(options.preview.playPause).toHaveBeenCalledTimes(1);
  });

  it("does not dispatch transport commands when transportEnabled is false, but M/X/? still fire", () => {
    const options = makeOptions({ transportEnabled: false });
    const { result } = renderHook(() => useKeyCommands(options));
    press({ key: " " });
    expect(options.preview.playPause).not.toHaveBeenCalled();

    press({ key: "m" });
    expect(options.onToggleMute).toHaveBeenCalledWith("Drums");

    press({ key: "x" });
    expect(options.onChangePane).toHaveBeenCalledWith("mixer");

    press({ key: "?" });
    expect(result.current.shortcutsOpen).toBe(true);
  });

  it("numpad Enter plays only when stopped; numpad Decimal pauses only when playing", () => {
    const notPlaying = makeOptions({ preview: { ...makeOptions().preview, playing: false } });
    const first = renderHook(() => useKeyCommands(notPlaying));
    press({ code: "NumpadEnter", key: "Enter" });
    expect(notPlaying.preview.playPause).toHaveBeenCalledTimes(1);
    press({ code: "NumpadDecimal", key: "." });
    expect(notPlaying.preview.playPause).toHaveBeenCalledTimes(1);
    first.unmount();

    const playing = makeOptions({ preview: { ...makeOptions().preview, playing: true } });
    renderHook(() => useKeyCommands(playing));
    press({ code: "NumpadEnter", key: "Enter" });
    expect(playing.preview.playPause).toHaveBeenCalledTimes(0);
    press({ code: "NumpadDecimal", key: "." });
    expect(playing.preview.playPause).toHaveBeenCalledTimes(1);
  });

  it("numpad 0 stops", () => {
    const options = makeOptions();
    renderHook(() => useKeyCommands(options));
    press({ code: "Numpad0", key: "0" });
    expect(options.preview.stop).toHaveBeenCalledTimes(1);
  });

  it("Return seeks to 0; Alt-Return seeks to duration", () => {
    const options = makeOptions();
    renderHook(() => useKeyCommands(options));
    press({ code: "Enter", key: "Enter" });
    expect(options.preview.seek).toHaveBeenCalledWith(0);
    press({ code: "Enter", key: "Enter", altKey: true });
    expect(options.preview.seek).toHaveBeenCalledWith(100);
  });

  it("rewind/forward read currentTimeRef, not the (possibly stale) currentTime state, and clamp", () => {
    const options = makeOptions();
    options.preview.currentTimeRef.current = 5;
    renderHook(() => useKeyCommands(options));

    press({ code: "Comma", key: "," });
    expect(options.preview.seek).toHaveBeenLastCalledWith(4);

    press({ code: "Period", key: "." });
    expect(options.preview.seek).toHaveBeenLastCalledWith(6);

    options.preview.currentTimeRef.current = 2;
    // Non-US layouts don't necessarily produce "," for Shift+Comma — this
    // dispatches on `code` alone (no `key`) to prove the match doesn't
    // depend on the produced character.
    press({ code: "Comma", shiftKey: true });
    expect(options.preview.seek).toHaveBeenLastCalledWith(0);

    options.preview.currentTimeRef.current = 95;
    press({ code: "Period", shiftKey: true });
    expect(options.preview.seek).toHaveBeenLastCalledWith(100);
  });

  it("C toggles loop", () => {
    const options = makeOptions();
    renderHook(() => useKeyCommands(options));
    press({ key: "c" });
    expect(options.preview.toggleLoop).toHaveBeenCalledTimes(1);
  });

  it("ArrowDown/ArrowUp walk stems and clamp at both ends", () => {
    const options = makeOptions({ selectedStem: "Vocals" });
    const first = renderHook(() => useKeyCommands(options));
    press({ key: "ArrowUp" });
    expect(options.onSelectStem).toHaveBeenCalledWith("Vocals");
    first.unmount();

    const atEnd = makeOptions({ selectedStem: "Bass" });
    renderHook(() => useKeyCommands(atEnd));
    press({ key: "ArrowDown" });
    expect(atEnd.onSelectStem).toHaveBeenCalledWith("Bass");
  });

  it("ArrowDown with nothing selected picks the first stem; ArrowUp picks the last", () => {
    const down = makeOptions({ selectedStem: null });
    const first = renderHook(() => useKeyCommands(down));
    press({ key: "ArrowDown" });
    expect(down.onSelectStem).toHaveBeenCalledWith("Vocals");
    first.unmount();

    const up = makeOptions({ selectedStem: null });
    renderHook(() => useKeyCommands(up));
    press({ key: "ArrowUp" });
    expect(up.onSelectStem).toHaveBeenCalledWith("Bass");
  });

  it("M/S call onToggleMute/onToggleSolo with the selected stem; no-op with no selection", () => {
    const options = makeOptions();
    const first = renderHook(() => useKeyCommands(options));
    press({ key: "m" });
    expect(options.onToggleMute).toHaveBeenCalledWith("Drums");
    press({ key: "s" });
    expect(options.onToggleSolo).toHaveBeenCalledWith("Drums");
    first.unmount();

    const none = makeOptions({ selectedStem: null });
    renderHook(() => useKeyCommands(none));
    press({ key: "m" });
    expect(none.onToggleMute).not.toHaveBeenCalled();
  });

  it("Alt-S clears solo, and no-ops when already empty", () => {
    const options = makeOptions({ manifest: manifestWith({ stem_solo: ["Vocals"] }) });
    const first = renderHook(() => useKeyCommands(options));
    press({ code: "KeyS", altKey: true });
    expect(options.onManifestChange).toHaveBeenCalledWith(
      expect.objectContaining({ mixing: expect.objectContaining({ stem_solo: [] }) }),
    );
    first.unmount();

    const empty = makeOptions({ manifest: manifestWith({ stem_solo: [] }) });
    renderHook(() => useKeyCommands(empty));
    press({ code: "KeyS", altKey: true });
    expect(empty.onManifestChange).not.toHaveBeenCalled();
  });

  it("Ctrl-Shift-M unmutes every current stem and drops keys for stems no longer prepared", () => {
    const options = makeOptions({
      stems: ["Vocals", "Drums"],
      manifest: manifestWith({ stem_enabled: { Vocals: false, Drums: false, Ghost: false } }),
    });
    renderHook(() => useKeyCommands(options));
    press({ key: "m", ctrlKey: true, shiftKey: true });
    expect(options.onManifestChange).toHaveBeenCalledWith(
      expect.objectContaining({
        mixing: expect.objectContaining({ stem_enabled: { Vocals: true, Drums: true } }),
      }),
    );
  });

  it("X toggles the bottom pane between mixer and off", () => {
    const toMixer = makeOptions({ paneView: "timeline" });
    const first = renderHook(() => useKeyCommands(toMixer));
    press({ key: "x" });
    expect(toMixer.onChangePane).toHaveBeenCalledWith("mixer");
    first.unmount();

    const toOff = makeOptions({ paneView: "mixer" });
    renderHook(() => useKeyCommands(toOff));
    press({ key: "x" });
    expect(toOff.onChangePane).toHaveBeenCalledWith(null);
  });

  it("? toggles the shortcuts dialog; Alt-K only opens it", () => {
    const { result } = renderHook(() => useKeyCommands(makeOptions()));
    expect(result.current.shortcutsOpen).toBe(false);
    press({ key: "?" });
    expect(result.current.shortcutsOpen).toBe(true);
    press({ key: "?" });
    expect(result.current.shortcutsOpen).toBe(false);
    press({ code: "KeyK", altKey: true });
    expect(result.current.shortcutsOpen).toBe(true);
    press({ code: "KeyK", altKey: true });
    expect(result.current.shortcutsOpen).toBe(true);
  });

  it("while the shortcuts dialog is open, only Help-group commands still dispatch", () => {
    const options = makeOptions();
    const { result } = renderHook(() => useKeyCommands(options));
    press({ key: "?" });
    expect(result.current.shortcutsOpen).toBe(true);

    press({ key: "m" });
    expect(options.onToggleMute).not.toHaveBeenCalled();
    press({ key: " " });
    expect(options.preview.playPause).not.toHaveBeenCalled();

    press({ key: "?" });
    expect(result.current.shortcutsOpen).toBe(false);
  });
});

describe("useKeyCommands guard", () => {
  it("ignores keydown targeted at editable elements", () => {
    const options = makeOptions();
    renderHook(() => useKeyCommands(options));
    for (const tag of ["input", "textarea", "select"]) {
      const element = document.createElement(tag);
      document.body.appendChild(element);
      press({ key: "m" }, element);
    }
    const editable = document.createElement("div");
    editable.setAttribute("contenteditable", "true");
    document.body.appendChild(editable);
    press({ key: "m" }, editable);

    expect(options.onToggleMute).not.toHaveBeenCalled();
  });

  it("ignores an event another handler already called preventDefault on", () => {
    const options = makeOptions();
    renderHook(() => useKeyCommands(options));
    const div = document.createElement("div");
    document.body.appendChild(div);
    div.addEventListener("keydown", (event) => event.preventDefault());
    press({ key: "m" }, div);
    expect(options.onToggleMute).not.toHaveBeenCalled();
  });

  it("ignores Space targeted at a focused button (native activation already owns it)", () => {
    const options = makeOptions();
    renderHook(() => useKeyCommands(options));
    const button = document.createElement("button");
    document.body.appendChild(button);
    press({ key: " " }, button);
    expect(options.preview.playPause).not.toHaveBeenCalled();
  });

  it("ignores keydown while a foreign dialog is open, but not the shortcuts dialog itself", () => {
    const options = makeOptions();
    const first = renderHook(() => useKeyCommands(options));

    const foreign = document.createElement("div");
    foreign.setAttribute("role", "dialog");
    foreign.setAttribute("data-state", "open");
    document.body.appendChild(foreign);
    press({ key: "m" });
    expect(options.onToggleMute).not.toHaveBeenCalled();
    press({ key: "?" });
    expect(first.result.current.shortcutsOpen).toBe(false);
    document.body.removeChild(foreign);
    first.unmount();

    const shortcuts = document.createElement("div");
    shortcuts.setAttribute("role", "dialog");
    shortcuts.setAttribute("data-state", "open");
    shortcuts.setAttribute("data-key-commands", "");
    document.body.appendChild(shortcuts);
    const { result } = renderHook(() => useKeyCommands(makeOptions()));
    press({ key: "?" });
    expect(result.current.shortcutsOpen).toBe(true);
  });

  it("ignores repeat for M but honours repeat for . (hold-to-scrub)", () => {
    const options = makeOptions();
    renderHook(() => useKeyCommands(options));
    press({ key: "m", repeat: true });
    expect(options.onToggleMute).not.toHaveBeenCalled();
    press({ code: "Period", key: ".", repeat: true });
    expect(options.preview.seek).toHaveBeenCalledTimes(1);
  });

  it("ignores composing keydowns (IME)", () => {
    const options = makeOptions();
    renderHook(() => useKeyCommands(options));
    press({ key: "m", isComposing: true });
    expect(options.onToggleMute).not.toHaveBeenCalled();
  });

  it("removes the listener on unmount", () => {
    const options = makeOptions();
    const { unmount } = renderHook(() => useKeyCommands(options));
    unmount();
    press({ key: "m" });
    expect(options.onToggleMute).not.toHaveBeenCalled();
  });
});
