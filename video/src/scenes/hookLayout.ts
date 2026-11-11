// Where the upload box sits and how its rows arrive: shared by both hook scenes, so the cut
// between them is a continuous shot (scene 2 opens exactly where scene 1 ends).

import { Easing, interpolate } from "remotion";
import { FILES } from "../data/hook";
import { BOX, boxHeight } from "../components/UploadBox";
import { canvas } from "../theme";
import { FPS, HOOK } from "../timing";

/** Portal px to film px: the box is the portal's own UI at 1.45x. */
export const U = 1.45;
export const BOX_X = 1010;
export const BOX_Y = 150;
export const BOX_W = 800;
/** Space kept under the newest row while the list scrolls. */
export const BOTTOM_MARGIN = 70;

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
const land = Easing.bezier(0.2, 0.9, 0.1, 1);

/** Seconds (scene-local) each file lands: slow at first, then faster and faster. */
export const arrivals = (): number[] => {
  const { firstFileIn, lastFileIn } = HOOK.package;
  const n = FILES.length;
  return FILES.map((_, i) => firstFileIn + (lastFileIn - firstFileIn) * Math.sqrt(i / (n - 1)));
};

export const rowState = (frame: number) => {
  const landFrames = Math.max(1, HOOK.package.rowLand * FPS);
  const arr = arrivals();
  const progress = arr.map((t) => land(interpolate(frame, [t * FPS, t * FPS + landFrames], [0, 1], clamp)));
  const fresh = arr.map((t) => interpolate(frame, [t * FPS, t * FPS + 0.6 * FPS], [1, 0], clamp));
  const rows = progress.reduce((sum, p) => sum + Math.min(1, p * 1.4), 0);
  return { progress, fresh, rows };
};

/** How far the box has scrolled up so the newest row stays in frame (film px). */
export const scrollFor = (rows: number) =>
  Math.max(0, BOX_Y + boxHeight(rows) * U - (canvas.height - BOTTOM_MARGIN));

/** The whole box, all rows, fitted to the right side of the frame (scene 2's end state). */
export const FIT = (() => {
  const h = boxHeight(FILES.length) * U;
  const k = (canvas.height - 140) / h;
  return { k, x: canvas.width - 130 - BOX_W * k, y: 70 };
})();

export { BOX };
