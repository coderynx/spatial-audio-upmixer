/** Palette for the canvas-rendered spatial displays (HazeView, ElevationView,
 * ChannelMeters).
 *
 * These surfaces stay dark in both app themes — the same way Logic Pro keeps
 * its instrument displays dark regardless of appearance — so the values are
 * fixed rather than read from the CSS custom properties. Colours are Apple's
 * dark system palette so they sit inside the surrounding chrome. */
export const canvasTheme = {
  /** Plot field, shared by all three displays. Logic's analysis displays
   * (Channel EQ, Quick Sampler, Beat Breaker) sit on a deep desaturated navy
   * rather than black, which is what makes their traces read as lit glass.
   * The two stops below are the ends of the field gradient. */
  plotField: "#070E17",
  plotFieldCore: "#0D1B2B",
  /** systemBlue at low alpha — the shaded wash Logic lays under an EQ curve
   * or a sampler waveform. Kept deliberately faint: the stem blobs painted
   * over it are themselves semi-transparent, and a heavier wash drags the
   * cool-coloured stems (Bass teal, Guitar green) toward the field's own hue
   * until they stop being tellable apart. */
  plotShade: "rgba(10, 132, 255, 0.06)",
  plotShadeStrong: "rgba(10, 132, 255, 0.10)",
  /** Muted channel's slot. Active channels have no track at all — Logic's
   * Level Meter paints the bar straight onto the field, so an unlit meter is
   * simply background. A muted channel keeps a visible slot precisely because
   * it should read as switched off rather than merely silent. */
  well: "#04080D",
  /** Grid lines and panel rules inside the display. Blue-tinted so they sit
   * in the same light as the plot field instead of reading as neutral soot. */
  grid: "rgba(122, 162, 208, 0.22)",
  gridSoft: "rgba(122, 162, 208, 0.11)",
  /** Speaker rings and outlines. */
  ring: "rgba(150, 186, 224, 0.42)",
  /** Speaker/label fills. */
  speaker: "#3A3A3C",
  label: "#8A97A8",
  labelStrong: "#E4ECF5",
  /** Muted speaker state. */
  mute: "#FF453A",
  muteLabel: "#FF6961",
  /** Binaural/headphone monitoring accent. */
  headphone: "#64D2FF",
  /** Lit meter zones, bottom to top. Logic's Level Meter runs blue up to the
   * threshold and yellow past it rather than the green/amber of a broadcast
   * meter, which is also what keeps the bars sitting in the same light as the
   * blue field behind them. Red stays reserved for the top zone. */
  meterSafe: "#3E9BC7",
  meterWarn: "#FFD60A",
  meterHot: "#FF453A",
  /** A mixer channel strip's meter runs green rather than the Level Meter's
   * blue — Logic uses both, one per host, and the strip is where the green
   * one belongs. systemGreen, so it sits with the rest of the Apple palette.
   * Warn/hot zones are shared with the blue meter above. */
  stripMeterSafe: "#30D158",
  /** Recessed slot a strip meter sits in, and the strip's fader track. Darker
   * than `well` so it still reads as inset against light chrome. */
  stripWell: "#0B0B0C",
  /** Fader's tick ladder — the cap itself is a theme-token control now, the
   * same plate `Pot` draws for its knob, so only the fixed-colour ticks stay
   * here. */
  faderTick: "#5A5A5E",
  /** Fallback for a stem with no assigned colour. */
  stemFallback: "#0A84FF",
} as const;

export function hexToRgb(hex: string): [number, number, number] {
  const clean = hex.replace("#", "");
  const value = clean.length === 3 ? clean.split("").map((c) => c + c).join("") : clean;
  const num = parseInt(value, 16);
  return [(num >> 16) & 255, (num >> 8) & 255, num & 255];
}

export function lerp(a: number, b: number, t: number) {
  return a + (b - a) * t;
}

/** Alpha multiplier at intensity 0 — low enough to read as "mostly off,"
 * high enough that a stem's position and level are still legible. */
export const MIN_ALPHA_SCALE = 0.22;

/** Consecutive idle frames (no audible voice) required before a draw loop
 * stops scheduling itself while inactive — long enough for the trailing
 * alpha-fade background clear and any in-flight blob fade to become visually
 * indistinguishable from a clean frame before the loop stops. */
export const SETTLE_FRAMES = 40;
