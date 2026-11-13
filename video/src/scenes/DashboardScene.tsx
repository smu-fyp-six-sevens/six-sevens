// Section 9 - "dashboard": the officer navigates the real portal, and the camera follows the cursor.
//   On the finalised case's summary (where the finalise beat ends), the cursor clicks "Dashboard" in the top bar,
//   The dashboard: the case pipeline; the page scrolls to the risk overview, then to "Needs attention".
//   The cursor clicks GLDB-02: its risk summary opens. It clicks "Audit" in the sidebar: the audit trail opens.
// The camera works on the browser's viewport (1440 x 900 CSS px) and pages scroll inside it, as in a browser, so a
// new page opens at its top. Times: DASHBOARD in timing.ts. Words: data/howitworks.ts.

import { AbsoluteFill, Easing, Img, interpolate, staticFile, useCurrentFrame } from "remotion";
import { cameraAt } from "../components/keys";
import { Pointer } from "../components/Pointer";
import { shotSwipe } from "../components/swipe";
import { DASHBOARD_BOXES as B, DASHBOARD_COPY, SCREENS_STORY2 } from "../data/howitworks";
import { colors } from "../theme";
import { DASHBOARD as T, FPS } from "../timing";
import { WIN } from "./PortalScene";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
const VW = 1440;
const VH = 900;
const K0 = WIN.w / VW;

/** A path of [time, a, b] keys on an even ease (pointer x, y or page scroll). */
const path = (t: number, keys: [number, number, number][]) => {
  const ts = keys.map((k) => k[0]);
  return [1, 2].map((i) => interpolate(t, ts, keys.map((k) => k[i]), clamp)) as [number, number];
};

/** The viewport, zoomed on a point (viewport px), clamped to its edges like a real crop. */
/** The browser viewport filling a box of the given size (film px), zoomed on a point (viewport px), clamped to its edges. */
const FrameViewport: React.FC<{ boxW: number; boxH: number; zoom: number; x: number; y: number; children: React.ReactNode }> = ({ boxW, boxH, zoom, x, y, children }) => {
  const k = (boxW / VW) * zoom;
  const tx = Math.min(0, Math.max(boxW - VW * k, boxW / 2 - k * x));
  const ty = Math.min(0, Math.max(boxH - VH * k, boxH / 2 - k * y));
  return <div style={{ position: "absolute", left: 0, top: 0, width: VW, height: VH, overflow: "hidden", background: colors.paper, transformOrigin: "0 0", transform: `translate(${tx}px, ${ty}px) scale(${k})` }}>{children}</div>;
};

export const Viewport: React.FC<{ zoom: number; x: number; y: number; children: React.ReactNode }> = ({ zoom, x, y, children }) => {
  const k = K0 * zoom;
  const tx = Math.min(0, Math.max(WIN.w - VW * k, WIN.w / 2 - k * x));
  const ty = Math.min(0, Math.max(WIN.h - VH * k, WIN.h / 2 - k * y));
  return <div style={{ position: "absolute", left: 0, top: 0, width: VW, height: VH, overflow: "hidden", background: colors.paper, transformOrigin: "0 0", transform: `translate(${tx}px, ${ty}px) scale(${k})` }}>{children}</div>;
};

const Ring: React.FC<{ b: { x: number; y: number; w: number; h: number }; p: number; tone?: string }> = ({ b, p, tone = colors.steel }) =>
  p <= 0 ? null : <div style={{ position: "absolute", left: b.x - 4, top: b.y - 4, width: b.w + 8, height: b.h + 8, borderRadius: 12, border: `3px solid ${tone}`, opacity: p }} />;

export const DashboardScene: React.FC<{ showText?: boolean }> = () => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const swipe = shotSwipe(t, 0, T.length, { enter: false }) ?? 0;
  const toDash = interpolate(t, [T.navClick + 0.1, T.navClick + 0.2], [0, 1], clamp);
  // The page scrolls panel by panel; the camera frames each one.
  const [scroll] = path(t, [[0, 0, 0], [T.flagsAt - 0.3, 0, 0], [T.flagsAt + 0.3, 140, 0], [T.riskAt - 0.3, 140, 0], [T.riskAt + 0.3, 500, 0], [T.attentionAt - 0.3, 500, 0], [T.attentionAt + 0.3, 940, 0]]);
  const cam = cameraAt(t, [
    // full-width panels (pipeline, flags + types, risk) are shown whole: no zoom, the page scrolls under them
    [0, 1.0, 720, 450],
    [T.attentionAt - 0.2, 1.0, 720, 450],
    // "Needs attention" is half the page wide: push in on it
    [T.attentionAt + 0.5, 1.22, 470, 330],
    [T.length, 1.28, 470, 330],
  ]);
  const [px, py] = path(t, [[0, 900, 520], [T.navClick - 0.05, 720, 36], [T.pipelineAt + 0.5, 980, 420], [T.length, 1040, 560]]);
  const press = interpolate(t, [T.navClick - 0.05, T.navClick + 0.05, T.navClick + 0.2], [0, 1, 0], clamp);
  const ring = (at: number, until: number) => interpolate(t, [at + 0.2, at + 0.5, until - 0.3, until], [0, 1, 1, 0], clamp);

  // Full screen: no step tracker or headline. The window grows to the frame over GROW seconds.
  const GROW = 0.5;
  const g = Easing.bezier(0.37, 0, 0.63, 1)(interpolate(t, [0, GROW], [0, 1], clamp));
  const box = { x: WIN.x * (1 - g), y: WIN.y * (1 - g), w: WIN.w + (1920 - WIN.w) * g, h: WIN.h + (1080 - WIN.h) * g, r: 18 * (1 - g) };
  return (
    <AbsoluteFill style={{ background: colors.paper }}>
      {/* The window grows from where the Finalise screen sits to the full frame, then the dashboard fills it. */}
      <div style={{ position: "absolute", left: box.x, top: box.y, width: box.w, height: box.h, borderRadius: box.r, overflow: "hidden", boxShadow: box.r > 0.5 ? "0 30px 70px rgba(18,32,61,0.18)" : "none", transform: `translateX(${swipe * 1920 * 0.62}px)`, opacity: 1 - 0.85 * Math.abs(swipe) }}>
        <FrameViewport boxW={box.w} boxH={box.h} zoom={cam.zoom} x={cam.x} y={cam.y}>
          <Img src={staticFile(SCREENS_STORY2.finaliseDone.src)} style={{ position: "absolute", left: 0, top: 0, width: VW, height: VH }} />
          {toDash > 0 && (
            <div style={{ position: "absolute", left: 0, top: -scroll, width: VW, height: SCREENS_STORY2.dashboard.h, opacity: toDash }}>
              <Img src={staticFile(SCREENS_STORY2.dashboard.src)} style={{ width: VW, height: SCREENS_STORY2.dashboard.h, display: "block" }} />
              <Ring b={B.pipeline} p={ring(T.pipelineAt, T.flagsAt)} />
              <Ring b={B.flags} p={ring(T.flagsAt, T.riskAt)} tone={colors.red} />
              <Ring b={B.types} p={ring(T.flagsAt + 0.4, T.riskAt)} />
              <Ring b={B.risk} p={ring(T.riskAt, T.attentionAt)} />
              <Ring b={B.attention} p={ring(T.attentionAt, T.length + 1)} tone={colors.red} />
            </div>
          )}
          {t < T.pipelineAt + 1 && <Pointer x={px} y={py} size={20} press={press} />}
        </FrameViewport>
      </div>
    </AbsoluteFill>
  );
};
