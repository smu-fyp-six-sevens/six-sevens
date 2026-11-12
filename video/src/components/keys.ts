// keys - a camera path written as keyframes in seconds: [time, zoom, focusX, focusY]. Between keys the camera
// moves on an even ease, so zooms in and out never lurch. Focus is in the units of the layer being zoomed.

import { Easing, interpolate } from "remotion";

export type Key = [t: number, zoom: number, x: number, y: number];
const even = Easing.bezier(0.37, 0, 0.63, 1);

export const cameraAt = (t: number, keys: Key[]) => {
  if (t <= keys[0][0]) return { zoom: keys[0][1], x: keys[0][2], y: keys[0][3] };
  for (let i = 1; i < keys.length; i++) {
    const [t1, z1, x1, y1] = keys[i];
    const [t0, z0, x0, y0] = keys[i - 1];
    if (t <= t1) {
      const p = interpolate(t, [t0, t1], [0, 1], { easing: even, extrapolateLeft: "clamp", extrapolateRight: "clamp" });
      // zoom in log space, so a push in and a pull out feel the same speed
      return { zoom: Math.exp(Math.log(z0) + (Math.log(z1) - Math.log(z0)) * p), x: x0 + (x1 - x0) * p, y: y0 + (y1 - y0) * p };
    }
  }
  const k = keys[keys.length - 1];
  return { zoom: k[1], x: k[2], y: k[3] };
};
