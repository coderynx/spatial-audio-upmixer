import * as React from "react";
import type { Manifest } from "@/lib/manifest";
import { matchKeyCommand, type KeyCommand } from "./keyCommands";
import type { PaneView } from "./projectDetailLayout";

const EDITABLE_SELECTOR = [
  "input", "textarea", "select", '[contenteditable="true"]',
  '[role="textbox"]', '[role="combobox"]', '[role="listbox"]', '[role="menu"]',
].join(", ");
const ACTIVATABLE_SELECTOR = [
  "button", "a[href]", '[role="button"]', '[role="switch"]', '[role="tab"]',
  '[role="checkbox"]', '[role="radio"]',
].join(", ");

function shouldIgnore(event: KeyboardEvent) {
  if (event.defaultPrevented || event.isComposing || event.keyCode === 229) return true;
  const target = event.target as HTMLElement | null;
  if (target?.closest) {
    if (target.isContentEditable || target.closest(EDITABLE_SELECTOR)) return true;
    // A focused button/switch/tab already owns Space and Enter as its native
    // activation key — without this, clicking Play then pressing Space fires
    // playPause twice (once from the click's activation, once from us).
    if ((event.key === " " || event.key === "Enter") && target.closest(ACTIVATABLE_SELECTOR)) return true;
  }
  // CreateProjectDialog/JobComposer render above the routes in App.tsx and
  // can be open while this page is mounted; Radix's focus trap doesn't stop
  // a window-level listener, so check the document directly, independent of
  // target. The shortcuts dialog itself carries data-key-commands so `?` can
  // still close it.
  const dialog = document.querySelector('[role="dialog"][data-state="open"]');
  return Boolean(dialog) && !dialog?.hasAttribute("data-key-commands");
}

interface KeyCommandsPreview {
  playing: boolean;
  duration: number;
  currentTimeRef: { current: number };
  playPause: () => void | Promise<void>;
  stop: () => void;
  seek: (time: number) => void;
  toggleLoop: () => void;
}

export interface KeyCommandsOptions {
  transportEnabled: boolean;
  preview: KeyCommandsPreview;
  stems: string[];
  selectedStem: string | null;
  onSelectStem: (stem: string) => void;
  onToggleMute: (stem: string) => void;
  onToggleSolo: (stem: string) => void;
  manifest: Manifest | null;
  onManifestChange: (next: Manifest) => void;
  paneView: PaneView;
  onChangePane: (next: PaneView) => void;
  onToggleMasterBypass: () => void;
  onUndo: () => void;
  onRedo: () => void;
}

/** Logic Pro-style global key commands for the project view (see
 * `keyCommands.ts` for the binding table and `docs/…` for none — the table
 * itself is the documentation). Listens on `window` in the bubble phase so
 * every existing per-widget key handler (timeline scrub, pane divider,
 * faders, pots — all of which call `preventDefault()` on the keys they
 * consume) gets first refusal; `event.defaultPrevented` is what keeps a
 * focused fader's arrow keys from also moving the stem selection. */
export function useKeyCommands(options: KeyCommandsOptions) {
  const [shortcutsOpen, setShortcutsOpen] = React.useState(false);
  const optionsRef = React.useRef(options);
  optionsRef.current = options;
  const shortcutsOpenRef = React.useRef(shortcutsOpen);
  shortcutsOpenRef.current = shortcutsOpen;

  React.useEffect(() => {
    const dispatch = (command: KeyCommand) => {
      const opts = optionsRef.current;
      switch (command.id) {
        case "playOrStop":
          if (opts.transportEnabled) void opts.preview.playPause();
          break;
        case "play":
          if (opts.transportEnabled && !opts.preview.playing) void opts.preview.playPause();
          break;
        case "pause":
          if (opts.transportEnabled && opts.preview.playing) void opts.preview.playPause();
          break;
        case "stop":
          if (opts.transportEnabled) opts.preview.stop();
          break;
        case "goToBeginning":
          if (opts.transportEnabled) opts.preview.seek(0);
          break;
        case "goToEnd":
          if (opts.transportEnabled) opts.preview.seek(opts.preview.duration);
          break;
        case "rewind":
        case "forward":
        case "fastRewind":
        case "fastForward": {
          if (!opts.transportEnabled) break;
          const delta = { rewind: -1, forward: 1, fastRewind: -10, fastForward: 10 }[command.id];
          const target = opts.preview.currentTimeRef.current + delta;
          opts.preview.seek(Math.min(opts.preview.duration, Math.max(0, target)));
          break;
        }
        case "toggleCycle":
          if (opts.transportEnabled) opts.preview.toggleLoop();
          break;
        case "toggleMasterBypass":
          opts.onToggleMasterBypass();
          break;
        case "selectPreviousStem":
        case "selectNextStem": {
          if (!opts.stems.length) break;
          const step = command.id === "selectPreviousStem" ? -1 : 1;
          const currentIndex = opts.selectedStem ? opts.stems.indexOf(opts.selectedStem) : -1;
          const nextIndex = currentIndex === -1
            ? (step === -1 ? opts.stems.length - 1 : 0)
            : Math.min(opts.stems.length - 1, Math.max(0, currentIndex + step));
          opts.onSelectStem(opts.stems[nextIndex]);
          break;
        }
        case "toggleMute":
          if (opts.selectedStem) opts.onToggleMute(opts.selectedStem);
          break;
        case "toggleSolo":
          if (opts.selectedStem) opts.onToggleSolo(opts.selectedStem);
          break;
        case "clearSolo": {
          const manifest = opts.manifest;
          if (!manifest || !manifest.mixing.stem_solo.length) break;
          opts.onManifestChange({ ...manifest, mixing: { ...manifest.mixing, stem_solo: [] } });
          break;
        }
        case "unmuteAll": {
          const manifest = opts.manifest;
          if (!manifest) break;
          const stem_enabled = Object.fromEntries(opts.stems.map((stem) => [stem, true]));
          opts.onManifestChange({ ...manifest, mixing: { ...manifest.mixing, stem_enabled } });
          break;
        }
        case "toggleMixer":
          opts.onChangePane(opts.paneView === "mixer" ? null : "mixer");
          break;
        case "undo":
          opts.onUndo();
          break;
        case "redo":
          opts.onRedo();
          break;
        case "toggleQuickHelp":
          setShortcutsOpen((open) => !open);
          break;
        case "openKeyCommands":
          setShortcutsOpen(true);
          break;
      }
    };

    const onKeyDown = (event: KeyboardEvent) => {
      if (shouldIgnore(event)) return;
      const command = matchKeyCommand(event);
      if (!command) return;
      if (event.repeat && !command.repeatable) return;
      if (shortcutsOpenRef.current && command.group !== "Help") return;
      event.preventDefault();
      dispatch(command);
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return { shortcutsOpen, setShortcutsOpen };
}
