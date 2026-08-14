/** Purely decorative gerunds cycled next to the real status message. */
const GERUNDS = [
  "Reticulating",
  "Crafting",
  "Harmonizing",
  "Untangling",
  "Spatializing",
  "Sculpting",
  "Weaving",
  "Aligning",
  "Distilling",
  "Polishing",
];

export function gerundAt(tick: number): string {
  return GERUNDS[tick % GERUNDS.length];
}
