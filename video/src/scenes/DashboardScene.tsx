// Section 9 - "dashboard": the officer navigates the real portal, and the camera follows the cursor.
//   On the Additional Documents page (where section 8 ends), the cursor clicks "Dashboard" in the top bar.
//   The dashboard: the case pipeline; the page scrolls to the risk overview, then to "Needs attention".
//   The cursor clicks GLDB-02: its risk summary opens. It clicks "Audit" in the sidebar: the audit trail opens.
// The camera works on the browser's viewport (1440 x 900 CSS px) and pages scroll inside it, as in a browser, so a
// new page opens at its top. Times: DASHBOARD in timing.ts. Words: data/howitworks.ts.

import { AbsoluteFill, Img, interpolate, staticFile, useCurrentFrame } from "remotion";
import { cameraAt } from "../components/keys";
import { Pointer } from "../components/Pointer";
import { ProductHeader } from "../components/ProductHeader";
import { shotSwipe } from "../components/swipe";
import { DASHBOARD_BOXES as B, DASHBOARD_COPY, SCREENS_STORY2 } from "../data/howitworks";
import { colors } from "../theme";
import { DASHBOARD as T, FPS } from "../timing";
import { WIN, Window } from "./PortalScene";

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
const Viewport: React.FC<{ zoom: number; x: number; y: number; children: React.ReactNode }> = ({ zoom, x, y, children }) => {
  const k = K0 * zoom;
  const tx = Math.min(0, Math.max(WIN.w - VW * k, WIN.w / 2 - k * x));
  const ty = Math.min(0, Math.max(WIN.h - VH * k, WIN.h / 2 - k * y));
  return <div style={{ position: "absolute", left: 0, top: 0, width: VW, height: VH, overflow: "hidden", background: colors.paper, transformOrigin: "0 0", transform: `translate(${tx}px, ${ty}px) scale(${k})` }}>{children}</div>;
};

const Ring: React.FC<{ b: { x: number; y: number; w: number; h: number }; p: number; tone?: string }> = ({ b, p, tone = colors.steel }) =>
  p <= 0 ? null : <div style={{ position: "absolute", left: b.x - 4, top: b.y - 4, width: b.w + 8, height: b.h + 8, borderRadius: 12, border: `3px solid ${tone}`, opacity: p }} />;

export const DashboardScene: React.FC<{ showText?: boolean }> = ({ showText = true }) => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const swipe = shotSwipe(t, 0, T.length, { enter: false }) ?? 0;

  // Which page is showing (a few frames' cross-fade, like a page load).
  const fade = (at: number) => interpolate(t, [at, at + 0.1], [0, 1], clamp);
  const toDash = fade(T.navClick + 0.1);
  const toRisk = fade(T.riskViewAt);
  const toAudit = fade(T.auditAt);

  // The dashboard's own scroll.
  const [scroll] = path(t, [[0, 0, 0], [T.riskAt, 0, 0], [T.riskAt + 0.6, 400, 0], [T.attentionAt, 400, 0], [T.attentionAt + 0.6, 760, 0]]);

  // The camera, in viewport px: it only ever moves toward what the cursor is doing.
  const cam = cameraAt(t, [
    [0, 1.15, 740, 470],
    [0.5, 1.0, 720, 450],
    [T.navClick, 1.0, 720, 450],
    [T.pipelineAt + 0.5, 1.12, 712, 300],
    [T.riskAt, 1.12, 712, 300],
    [T.riskAt + 0.6, 1.08, 712, 280],
    [T.attentionAt, 1.08, 712, 280],
    [T.attentionAt + 0.6, 1.2, 500, 300],
    [T.clickAt, 1.2, 500, 300],
    [T.riskViewAt + 0.6, 1.08, 650, 360],
    [T.auditClick - 0.7, 1.0, 600, 450],
    [T.auditAt + 0.6, 1.1, 640, 420],
    [T.length, 1.18, 640, 500],
  ]);
  const [px, py] = path(t, [
    [0, 900, 520],
    [T.navClick - 0.05, 720, 36],
    [T.pipelineAt + 0.4, 760, 360],
    [T.attentionAt + 0.3, 700, 420],
    [T.clickAt - 0.05, 150, 182],
    [T.riskViewAt + 0.4, 420, 420],
    [T.auditClick - 0.05, 47, 574],
    [T.length, 320, 620],
  ]);
  const press = (at: number) => interpolate(t, [at - 0.05, at + 0.05, at + 0.2], [0, 1, 0], clamp);
  const ring = (at: number, until: number) => interpolate(t, [at + 0.3, at + 0.6, until, until + 0.3], [0, 1, 1, 0], clamp);

  return (
    <AbsoluteFill style={{ background: colors.paper }}>
      <Window enter={1} swipe={swipe}>
        <Viewport zoom={cam.zoom} x={cam.x} y={cam.y}>
          <Img src={staticFile(SCREENS_STORY2.additional.src)} style={{ position: "absolute", left: 0, top: 0, width: VW, height: VH }} />
          {toDash > 0 && (
            <div style={{ position: "absolute", left: 0, top: -scroll, width: VW, height: SCREENS_STORY2.dashboard.h, opacity: toDash }}>
              <Img src={staticFile(SCREENS_STORY2.dashboard.src)} style={{ width: VW, height: SCREENS_STORY2.dashboard.h, display: "block" }} />
              <Ring b={B.pipeline} p={ring(T.pipelineAt, T.riskAt)} />
              <Ring b={B.risk} p={ring(T.riskAt + 0.3, T.attentionAt)} />
              <Ring b={B.row} p={ring(T.attentionAt + 0.3, T.riskViewAt)} tone={colors.red} />
            </div>
          )}
          {toRisk > 0 && <Img src={staticFile(SCREENS_STORY2.risk.src)} style={{ position: "absolute", left: 0, top: 0, width: VW, height: VH, opacity: toRisk }} />}
          {toAudit > 0 && <Img src={staticFile(SCREENS_STORY2.audit.src)} style={{ position: "absolute", left: 0, top: 0, width: VW, height: VH, opacity: toAudit }} />}
          <Pointer x={px} y={py} size={20} press={Math.max(press(T.navClick), press(T.clickAt), press(T.auditClick))} />
        </Viewport>
      </Window>
      <ProductHeader step={5} fromStep={5} length={T.length} headline={DASHBOARD_COPY.headline} headlineIn={T.headlineIn} showText={showText}
        beats={DASHBOARD_COPY.beats.map((b) => ({ ...b, at: T[b.at as keyof typeof T] }))} />
    </AbsoluteFill>
  );
};
