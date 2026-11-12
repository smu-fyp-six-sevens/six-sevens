// swipe - the shared transition for the "how it works" sections: one shot hands over to the next by the outgoing
// window swiping off to the left as the incoming one swipes in from the right, overlapping for SWIPE seconds. A
// section's first shot swipes in at its start and its last shot swipes out at its end, so the stages hand over
// the same way. The exit gathers speed into the cut and the entrance leaves it at speed (no dead stop either side).

import { Easing, interpolate } from "remotion";

export const SWIPE = 0.55; // seconds

const ENTER = Easing.bezier(0.12, 0.6, 0.3, 1);
const EXIT = Easing.bezier(0.5, 0, 0.9, 0.55);
const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;

/** The window's swipe for a shot on screen from `start` to `end` (seconds), or null when it is off screen. */
export const shotSwipe = (t: number, start: number, end: number, opts: { enter?: boolean; exit?: boolean } = {}): number | null => {
  if (t < start || t > end) return null;
  const { enter = true, exit = true } = opts;
  if (enter && t < start + SWIPE) return interpolate(t, [start, start + SWIPE], [1, 0], { ...clamp, easing: ENTER });
  if (exit && t > end - SWIPE) return interpolate(t, [end - SWIPE, end], [0, -1], { ...clamp, easing: EXIT });
  return 0;
};
