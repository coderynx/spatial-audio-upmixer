import { describe, expect, it } from "vitest";
import { KEY_COMMANDS, keyCommandCaps, matchKeyCommand } from "./keyCommands";

describe("matchKeyCommand", () => {
  it("tells numpad Enter apart from Return by code", () => {
    expect(matchKeyCommand({ code: "NumpadEnter", key: "Enter" })?.id).toBe("play");
    expect(matchKeyCommand({ code: "Enter", key: "Enter" })?.id).toBe("goToBeginning");
  });

  it("resolves numpad decimal to Pause, not Forward, because numpad entries come first in table order", () => {
    expect(matchKeyCommand({ code: "NumpadDecimal", key: "." })?.id).toBe("pause");
  });

  it("distinguishes Return from Alt-Return", () => {
    expect(matchKeyCommand({ code: "Enter", key: "Enter", altKey: false })?.id).toBe("goToBeginning");
    expect(matchKeyCommand({ code: "Enter", key: "Enter", altKey: true })?.id).toBe("goToEnd");
  });

  it("matches Rewind/Fast Rewind by physical key (code), independent of what the layout produces as `key`", () => {
    // Non-US layouts don't necessarily produce "," or "<" for Shift+Comma —
    // matching on `code` (the physical key) rather than `key` (the produced
    // character) is what keeps this working on those layouts.
    expect(matchKeyCommand({ code: "Comma", key: ",", shiftKey: false })?.id).toBe("rewind");
    expect(matchKeyCommand({ code: "Comma", key: "<", shiftKey: true })?.id).toBe("fastRewind");
    expect(matchKeyCommand({ code: "Comma", key: "?", shiftKey: true })?.id).toBe("fastRewind");
    expect(matchKeyCommand({ code: "Period", key: ".", shiftKey: false })?.id).toBe("forward");
    expect(matchKeyCommand({ code: "Period", key: ">", shiftKey: true })?.id).toBe("fastForward");
  });

  it("routes Ctrl-Shift-M to Mute Off for All, not Toggle Mute", () => {
    expect(matchKeyCommand({ key: "m", ctrlKey: true, shiftKey: true })?.id).toBe("unmuteAll");
    expect(matchKeyCommand({ key: "m" })?.id).toBe("toggleMute");
  });

  it("matches Alt-S for clear solo and bare S for toggle solo, by physical key", () => {
    // macOS remaps `key` for Option+letter to an alternate glyph (Option+S
    // produces "ß", not "s") — matching on `code` is what keeps this working
    // on Mac, same reasoning as the Comma/Period fix above.
    expect(matchKeyCommand({ code: "KeyS", key: "ß", altKey: true })?.id).toBe("clearSolo");
    expect(matchKeyCommand({ key: "s" })?.id).toBe("toggleSolo");
  });

  it("matches Alt-K for Open Key Command Assignments by physical key", () => {
    expect(matchKeyCommand({ code: "KeyK", key: "˚", altKey: true })?.id).toBe("openKeyCommands");
  });

  it("matches ? regardless of the layout's shift state", () => {
    expect(matchKeyCommand({ key: "?", shiftKey: true })?.id).toBe("toggleQuickHelp");
    expect(matchKeyCommand({ key: "?", shiftKey: false })?.id).toBe("toggleQuickHelp");
  });

  it("has no duplicate ids and no two commands sharing one chord signature", () => {
    const ids = new Set(KEY_COMMANDS.map((command) => command.id));
    expect(ids.size).toBe(KEY_COMMANDS.length);

    const signatures = new Set<string>();
    for (const command of KEY_COMMANDS) {
      const keySignature = command.code ?? command.keys?.join("|");
      const signature = `${keySignature}:${command.alt}:${command.shift}:${command.ctrl}`;
      expect(signatures.has(signature)).toBe(false);
      signatures.add(signature);
    }
  });
});

describe("keyCommandCaps", () => {
  it("formats caps as glyphs on macOS and words elsewhere", () => {
    const clearSolo = KEY_COMMANDS.find((command) => command.id === "clearSolo")!;
    expect(keyCommandCaps(clearSolo, true)).toEqual([
      { glyph: "⌥", word: "Option" },
      { glyph: "S", word: "S" },
    ]);
    expect(keyCommandCaps(clearSolo, false)).toEqual([
      { glyph: "Alt", word: "Alt" },
      { glyph: "S", word: "S" },
    ]);
  });

  it("renders Return distinctly from numpad Enter", () => {
    const goToBeginning = KEY_COMMANDS.find((command) => command.id === "goToBeginning")!;
    const play = KEY_COMMANDS.find((command) => command.id === "play")!;
    expect(keyCommandCaps(goToBeginning, true)).toEqual([{ glyph: "↩", word: "Return" }]);
    expect(keyCommandCaps(play, true)).toEqual([{ glyph: "⌤", word: "Enter" }]);
    expect(keyCommandCaps(goToBeginning, false)).toEqual([{ glyph: "Enter", word: "Enter" }]);
  });
});
