/** Logic Pro-style global key commands for the project view — see
 * https://support.apple.com/guide/logicpro/global-commands-lgcp02bf31b6/mac.
 * This table is the single source of truth for both `useKeyCommands`'
 * dispatch and `KeyCommandsDialog`'s reference card, so a new binding needs
 * one entry here, not a matching edit in two places.
 *
 * Every binding uses a key physically present on macOS, Windows, and
 * Linux keyboards (Option === Alt, no Command/Meta binding ships), so
 * dispatch needs no per-platform remap — only the dialog's rendered caps
 * differ, via `keyCommandCaps`. */

export type KeyCommandGroup = "Transport" | "Mixer" | "Help";

export type KeyCommandId =
  | "playOrStop"
  | "play"
  | "pause"
  | "stop"
  | "goToBeginning"
  | "goToEnd"
  | "rewind"
  | "forward"
  | "fastRewind"
  | "fastForward"
  | "toggleCycle"
  | "toggleMasterBypass"
  | "selectPreviousStem"
  | "selectNextStem"
  | "toggleMute"
  | "toggleSolo"
  | "clearSolo"
  | "unmuteAll"
  | "toggleMixer"
  | "toggleQuickHelp"
  | "openKeyCommands";

/** `true` = modifier must be held, absent = must be up, `"ignore"` = either
 * (only `?`, whose shift state depends on the keyboard layout). */
type Modifier = true | "ignore";

export interface KeyCommand {
  id: KeyCommandId;
  group: KeyCommandGroup;
  /** Logic Pro's own command name, shown verbatim in the help dialog. */
  label: string;
  /** Physical key. Numpad keys report the same `key` as their main-row
   * twins, so they can only be told apart by `code`. */
  code?: string;
  /** Produced characters, matched case-insensitively. Layout-independent
   * for letters and for `?`, which is what the browser reports however the
   * layout produces it. */
  keys?: readonly string[];
  alt?: Modifier;
  shift?: Modifier;
  ctrl?: Modifier;
  /** Key cap shown in the dialog; modifier chips are derived from above. */
  cap: string;
  /** Cap lives on the numeric keypad — the dialog says so in words. */
  numpad?: true;
  /** Survives `event.repeat` (hold-to-scrub); everything else fires once
   * per physical keypress. */
  repeatable?: true;
}

export interface KeyChord {
  code?: string;
  key?: string;
  altKey?: boolean;
  shiftKey?: boolean;
  ctrlKey?: boolean;
}

// Numpad entries are listed first: matching is first-in-table-wins, and
// NumpadDecimal/NumpadEnter must resolve here before the bare-key Forward/
// Play entries below get a chance at their shared `key` value.
export const KEY_COMMANDS: readonly KeyCommand[] = [
  { id: "play", group: "Transport", label: "Play", code: "NumpadEnter", cap: "Enter", numpad: true },
  { id: "pause", group: "Transport", label: "Pause", code: "NumpadDecimal", cap: ".", numpad: true },
  { id: "stop", group: "Transport", label: "Stop", code: "Numpad0", cap: "0", numpad: true },
  { id: "playOrStop", group: "Transport", label: "Play or Stop", keys: [" "], cap: "Space" },
  { id: "goToBeginning", group: "Transport", label: "Go to Beginning", code: "Enter", cap: "Return" },
  { id: "goToEnd", group: "Transport", label: "Go to End of Last Region", code: "Enter", alt: true, cap: "Return" },
  { id: "rewind", group: "Transport", label: "Rewind", code: "Comma", cap: ",", repeatable: true },
  { id: "forward", group: "Transport", label: "Forward", code: "Period", cap: ".", repeatable: true },
  { id: "fastRewind", group: "Transport", label: "Fast Rewind", code: "Comma", shift: true, cap: ",", repeatable: true },
  { id: "fastForward", group: "Transport", label: "Fast Forward", code: "Period", shift: true, cap: ".", repeatable: true },
  { id: "toggleCycle", group: "Transport", label: "Toggle Cycle Mode", keys: ["c"], cap: "C" },
  { id: "toggleMasterBypass", group: "Transport", label: "Bypass Master Chain", keys: ["b"], cap: "B" },

  { id: "selectPreviousStem", group: "Mixer", label: "Select Previous Track", keys: ["arrowup"], cap: "↑" },
  { id: "selectNextStem", group: "Mixer", label: "Select Next Track", keys: ["arrowdown"], cap: "↓" },
  { id: "toggleMute", group: "Mixer", label: "Toggle Channel Strip Mute", keys: ["m"], cap: "M" },
  { id: "toggleSolo", group: "Mixer", label: "Toggle Channel Strip Solo", keys: ["s"], cap: "S" },
  { id: "clearSolo", group: "Mixer", label: "Clear/Recall Solo", code: "KeyS", alt: true, cap: "S" },
  { id: "unmuteAll", group: "Mixer", label: "Mute Off for All", keys: ["m"], ctrl: true, shift: true, cap: "M" },
  { id: "toggleMixer", group: "Mixer", label: "Show/Hide Mixer", keys: ["x"], cap: "X" },

  { id: "toggleQuickHelp", group: "Help", label: "Show/Hide Quick Help", keys: ["?"], shift: "ignore", cap: "?" },
  { id: "openKeyCommands", group: "Help", label: "Open Key Command Assignments", code: "KeyK", alt: true, cap: "K" },
];

function modifierMatches(required: Modifier | undefined, held: boolean) {
  if (required === "ignore") return true;
  return Boolean(required) === held;
}

export function matchKeyCommand(chord: KeyChord): KeyCommand | undefined {
  return KEY_COMMANDS.find((command) => {
    if (command.code) {
      if (chord.code !== command.code) return false;
    } else if (command.keys) {
      if (!chord.key || !command.keys.includes(chord.key.toLowerCase())) return false;
    } else {
      return false;
    }
    return (
      modifierMatches(command.alt, Boolean(chord.altKey))
      && modifierMatches(command.shift, Boolean(chord.shiftKey))
      && modifierMatches(command.ctrl, Boolean(chord.ctrlKey))
    );
  });
}

export const IS_MAC = /mac|iphone|ipad/i.test(
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- userAgentData isn't in the DOM lib yet
  (navigator as any).userAgentData?.platform ?? navigator.platform ?? navigator.userAgent,
);

export interface KeyCap {
  glyph: string;
  word: string;
}

const RETURN_CAP: Record<"mac" | "other", KeyCap> = {
  mac: { glyph: "↩", word: "Return" },
  other: { glyph: "Enter", word: "Enter" },
};
const NUMPAD_ENTER_CAP: Record<"mac" | "other", KeyCap> = {
  mac: { glyph: "⌤", word: "Enter" },
  other: { glyph: "Enter", word: "Enter" },
};

/** Renders a command's chip sequence: modifiers first (Apple's canonical
 * ⌃⌥⇧ order), then the key cap itself. `mac` defaults to the running
 * platform but is exposed as a param so the dialog can be tested in both
 * renderings without stubbing `navigator`. */
export function keyCommandCaps(command: KeyCommand, mac: boolean = IS_MAC): KeyCap[] {
  const caps: KeyCap[] = [];
  if (command.ctrl === true) caps.push(mac ? { glyph: "⌃", word: "Control" } : { glyph: "Ctrl", word: "Ctrl" });
  if (command.alt === true) caps.push(mac ? { glyph: "⌥", word: "Option" } : { glyph: "Alt", word: "Alt" });
  if (command.shift === true) caps.push(mac ? { glyph: "⇧", word: "Shift" } : { glyph: "Shift", word: "Shift" });
  if (command.code === "NumpadEnter") {
    caps.push(NUMPAD_ENTER_CAP[mac ? "mac" : "other"]);
  } else if (command.code === "Enter") {
    caps.push(RETURN_CAP[mac ? "mac" : "other"]);
  } else {
    caps.push({ glyph: command.cap, word: command.cap });
  }
  return caps;
}
