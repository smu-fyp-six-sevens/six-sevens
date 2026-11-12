// Section 9 - "dashboard": every case at a glance. The real dashboard (story case GLDB-02 on top of "Needs
// attention"): the camera pushes in on the pipeline, the risk overview, then the case; the cursor drills in; swipe
// to its risk summary, then its audit trail. Flat screens, zooms only. Times: DASHBOARD in timing.ts.

import { AbsoluteFill, Easing, interpolate, useCurrentFrame } from "remotion";
import { cameraAt } from "../components/keys";
import { Pointer } from "../components/Pointer";
import { ProductHeader } from "../components/ProductHeader";
import { shotSwipe, SWIPE } from "../components/swipe";
import { DASHBOARD_BOXES as B, DASHBOARD_COPY, SCREENS_STORY2 } from "../data/howitworks";
import { colors } from "../theme";
import { DASHBOARD as T, FPS } from "../timing";
import { Screen, Window } from "./PortalScene";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
const land = Easing.bezier(0.2, 0.9, 0.1, 1);
const even = Easing.bezier(0.37, 0, 0.63, 1);

const Ring: React.FC<{ b: { x: number; y: number; w: number; h: number }; p: number; tone?: string }> = ({ b, p, tone = colors.steel }) =>
  p <= 0 ? null : <div style={{ position: "absolute", left: b.x - 4, top: b.y - 4, width: b.w + 8, height: b.h + 8, borderRadius: 12, border: `3px solid ${tone}`, opacity: p, transform: `scale(${1.03 - 0.03 * p})` }} />;

export const DashboardScene: React.FC<{ showText?: boolean }> = ({ showText = true }) => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const swipeDash = shotSwipe(t, 0, T.riskViewAt + SWIPE);
  const swipeRisk = shotSwipe(t, T.riskViewAt, T.auditAt + SWIPE);
  const swipeAudit = shotSwipe(t, T.auditAt, T.length);
  const ring = (at: number, until: number) => land(interpolate(t, [at + 0.3, at + 0.6], [0, 1], clamp)) * interpolate(t, [until, until + 0.3], [1, 0], clamp);

  const cam = cameraAt(t, [
    [0, 1.0, 720, 420],
    [T.pipelineAt, 1.0, 720, 420],
    [T.pipelineAt + 0.6, 1.25, 712, 225],
    [T.riskAt, 1.25, 712, 225],
    [T.riskAt + 0.6, 1.2, 712, 662],
    [T.attentionAt, 1.2, 712, 662],
    [T.attentionAt + 0.6, 1.55, 430, 990],
  ]);
  const toRow = interpolate(t, [T.clickAt - 0.6, T.clickAt - 0.05], [0, 1], { ...clamp, easing: even });
  const press = interpolate(t, [T.clickAt - 0.05, T.clickAt + 0.05, T.clickAt + 0.2], [0, 1, 0], clamp);
  const riskCam = cameraAt(t, [[T.riskViewAt, 1.0, 720, 420], [T.riskViewAt + 1.2, 1.25, 600, 340]]);
  const auditCam = cameraAt(t, [[T.auditAt, 1.0, 720, 420], [T.length, 1.3, 640, 560]]);

  return (
    <AbsoluteFill style={{ background: colors.paper }}>
      {swipeDash !== null && (
        <Window enter={1} swipe={swipeDash}>
          <Screen {...SCREENS_STORY2.dashboard} zoom={cam.zoom} focus={{ x: cam.x, y: cam.y }}>
            <Ring b={B.pipeline} p={ring(T.pipelineAt, T.riskAt)} />
            <Ring b={B.risk} p={ring(T.riskAt, T.attentionAt)} />
            <Ring b={B.row} p={ring(T.attentionAt, T.riskViewAt + 1)} tone={colors.red} />
            {t >= T.clickAt - 0.7 && <Pointer x={B.row.x + 120 + 200 * (1 - toRow)} y={B.row.y + 30 + 120 * (1 - toRow)} size={20} press={press} />}
          </Screen>
        </Window>
      )}
      {swipeRisk !== null && (
        <Window enter={1} swipe={swipeRisk}>
          <Screen {...SCREENS_STORY2.risk} zoom={riskCam.zoom} focus={{ x: riskCam.x, y: riskCam.y }} />
        </Window>
      )}
      {swipeAudit !== null && (
        <Window enter={1} swipe={swipeAudit}>
          <Screen {...SCREENS_STORY2.audit} zoom={auditCam.zoom} focus={{ x: auditCam.x, y: auditCam.y }} />
        </Window>
      )}
      <ProductHeader step={3} fromStep={2} length={T.length} headline={DASHBOARD_COPY.headline} headlineIn={T.headlineIn} showText={showText}
        beats={DASHBOARD_COPY.beats.map((b) => ({ ...b, at: T[b.at as keyof typeof T] }))} />
    </AbsoluteFill>
  );
};
