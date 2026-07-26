// The `ambisonics` package (JSAmbisonics) ships no TypeScript types, and its
// barrel `index.js` unconditionally `require()`s `serve-sofa-hrir` (for the
// SOFA/IRCAM HRIR loaders this preview doesn't use), which has a broken
// package.json `exports` map that trips up Vite/Vitest's resolver. Importing
// the class straight from its `dist/` submodule (as done throughout this
// codebase) avoids that dependency entirely, so this shim declares that
// submodule path instead of the barrel.
//
// Only the mono encoder is used — the decode/rotation stage is a plain
// ConvolverNode bank fed by our own filter files (see useStemPreview.ts and
// docs/standards/spatial_audio_engine.md), not JSAmbisonics' own decoder.
declare module "ambisonics/dist/ambi-monoEncoder" {
  export default class monoEncoder {
    constructor(ctx: BaseAudioContext, order: number);
    in: GainNode;
    out: AudioNode;
    azim: number;
    elev: number;
    updateGains(): void;
  }
}

// No published types; only used to satisfy `ambi-monoEncoder`'s bare
// `numeric` global (see useStemPreview.ts).
declare module "numeric" {
  const numeric: unknown;
  export default numeric;
}
