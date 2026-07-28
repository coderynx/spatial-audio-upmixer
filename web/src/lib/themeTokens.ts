import * as React from "react";

/** Resolved values of the `index.css` colour tokens, for the canvas surfaces
 * that live in **chrome** rather than on the always-dark instrument field.
 *
 * `canvasTheme` exists because Haze/Elevation/ChannelMeters stay dark in both
 * app themes. The timeline and the mixer are the opposite case: they are
 * ordinary panels that happen to draw with a canvas, so they must follow the
 * active theme like every other panel. Canvas has no access to CSS variables,
 * so they are read off the document element here instead of being duplicated
 * as literals. */

const TOKENS = [
  "background",
  "card",
  "muted",
  "secondary",
  "accent",
  "border",
  "foreground",
  "muted-foreground",
] as const;

export type ThemeTokens = Record<(typeof TOKENS)[number], string>;

function readTokens(): ThemeTokens {
  const style = getComputedStyle(document.documentElement);
  const entries = TOKENS.map((token) => {
    const raw = style.getPropertyValue(`--${token}`).trim();
    return [token, raw ? `hsl(${raw})` : "transparent"];
  });
  return Object.fromEntries(entries) as ThemeTokens;
}

/** Re-reads whenever the theme class on `<html>` changes, which is how
 * `ThemeProvider` switches palettes (including the OS-driven "system" case). */
export function useThemeTokens(): ThemeTokens {
  const [tokens, setTokens] = React.useState<ThemeTokens>(readTokens);
  React.useEffect(() => {
    const observer = new MutationObserver(() => setTokens(readTokens()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return tokens;
}

/** `hsl(h s% l%)` with an alpha channel — canvas needs a colour string, and
 * Tailwind's `/ opacity` syntax is not available here. */
export function withAlpha(color: string, alpha: number): string {
  if (!color.startsWith("hsl(")) return color;
  return `hsla(${color.slice(4, -1)} / ${alpha})`;
}
