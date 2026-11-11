// theme.ts - brand tokens, taken from the portal's own frontend/DESIGN.md and the captured screens.
// Components read these and hard-code no hex values, so this file re-skins the film.

export const colors = {
  // Portal tokens (frontend/DESIGN.md)
  ink: "#12203d",
  inkSoft: "#5b6478",
  paper: "#f9f9f8",
  white: "#ffffff",
  line: "#e3e2de",
  steel: "#355f96",
  steelDark: "#284a78",
  amber: "#c6820a",
  amberBg: "#fbf0da",
  red: "#b3402a",
  redBg: "#fbe8e2",
  textMuted: "#667085",
  // Measured from the upload box (video/public/ui/upload-measure.json)
  boxBg: "#fefefd",
  boxDash: "#b3b2ad",
  pillBg: "#f1f0ed",

  // Names the scaffold's shared components read.
  accent: "#355f96",
  accentSoft: "rgba(53, 95, 150, 0.15)",
  onAccent: "#ffffff",
  background: "#f9f9f8",
  surface: "#ffffff",
  inkMuted: "#667085",
  border: "rgba(18, 32, 61, 0.08)",
  shadowTint: "rgba(18, 32, 61, 0.18)",
} as const;

// The portal uses the system UI stack; Inter is the closest web font and renders the same everywhere.
export const fonts = {
  display: "Inter, system-ui, sans-serif",
  ui: "Inter, system-ui, sans-serif",
  mono: "ui-monospace, SFMono-Regular, Menlo, monospace",
} as const;

export const canvas = { width: 1920, height: 1080 } as const;
export const canvasVertical = { width: 1080, height: 1920 } as const;

export const blendHex = (a: string, b: string, t: number): string => {
  const channels = (hex: string) =>
    [hex.slice(1, 3), hex.slice(3, 5), hex.slice(5, 7)].map((h) => parseInt(h, 16));
  const [ar, ag, ab] = channels(a);
  const [br, bg, bb] = channels(b);
  return `#${[ar + (br - ar) * t, ag + (bg - ag) * t, ab + (bb - ab) * t]
    .map((n) => Math.round(n).toString(16).padStart(2, "0"))
    .join("")}`;
};
