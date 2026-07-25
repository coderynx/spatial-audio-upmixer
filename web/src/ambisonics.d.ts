// The `ambisonics` package (JSAmbisonics) ships no TypeScript types, and its
// barrel `index.js` unconditionally `require()`s `serve-sofa-hrir` (for the
// SOFA/IRCAM HRIR loaders this preview doesn't use), which has a broken
// package.json `exports` map that trips up Vite/Vitest's resolver. Importing
// each class straight from its `dist/` submodule (as done throughout this
// codebase) avoids that dependency entirely, so this shim declares those
// submodule paths instead of the barrel.
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

declare module "ambisonics/dist/ambi-sceneRotator" {
  export default class sceneRotator {
    constructor(ctx: BaseAudioContext, order: number);
    in: AudioNode;
    out: AudioNode;
    yaw: number;
    pitch: number;
    roll: number;
    updateRotMtx(): void;
  }
}

declare module "ambisonics/dist/ambi-binauralDecoder" {
  export default class binDecoder {
    constructor(ctx: BaseAudioContext, order: number);
    in: AudioNode;
    out: AudioNode;
    updateFilters(buffer: AudioBuffer): void;
    resetFilters(): void;
  }
}

// No published types; only used to satisfy `ambi-sceneRotator`'s bare
// `numeric` global (see useStemPreview.ts).
declare module "numeric" {
  const numeric: unknown;
  export default numeric;
}

declare module "ambisonics/dist/hoa-loader" {
  export default class HOAloader {
    constructor(
      ctx: BaseAudioContext,
      order: number,
      url: string,
      onLoad: (buffer: AudioBuffer) => void,
    );
    load(): void;
  }
}
