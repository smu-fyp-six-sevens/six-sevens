// Section 8b - "finalise": the officer locks the case, in the real portal. From the Additional Documents page (where
// Score ends) the cursor clicks Summary in the sidebar; on the Case Status card it clicks "Finalise Case", confirms,
// and the case is Finalised: view-only. The camera follows the cursor. Times: FINALISE in timing.ts.

import { AbsoluteFill, Easing, Img, interpolate, staticFile, useCurrentFrame } from "remotion";
import { cameraAt } from "../components/keys";
import { Pointer } from "../components/Pointer";
import { ProductHeader } from "../components/ProductHeader";
import { FINALISE_BOXES as B, FINALISE_COPY, SCREENS_STORY2 } from "../data/howitworks";
import { colors } from "../theme";
import { FINALISE as T, FPS } from "../timing";
import { Window } from "./PortalScene";
import { Viewport } from "./DashboardScene";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
const land = Easing.bezier(0.2, 0.9, 0.1, 1);
const VW = 1440;
const VH = 900;

const path = (t: number, keys: [number, number, number][]) => {
  const ts = keys.map((k) => k[0]);
  return [1, 2].map((i) => interpolate(t, ts, keys.map((k) => k[i]), clamp)) as [number, number];
};

export const FinaliseScene: React.FC<{ showText?: boolean }> = ({ showText = true }) => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const fade = (at: number) => interpolate(t, [at, at + 0.12], [0, 1], clamp);
  const cam = cameraAt(t, [
    [0, 1.0, 720, 450],
    [T.summaryClick, 1.0, 720, 450],
    [T.buttonClick - 0.2, 1.3, 1030, 300],
    [T.confirmAt, 1.3, 1030, 300],
    [T.confirmAt + 0.5, 1.2, 720, 430],
    [T.doneAt, 1.2, 720, 430],
    [T.doneAt + 0.6, 1.4, 900, 270],
  ]);
  const [px, py] = path(t, [
    [0, 700, 520],
    [T.summaryClick - 0.05, B.sidebarSummary.x, B.sidebarSummary.y],
    [T.buttonClick - 0.05, B.button.x + B.button.w / 2, B.button.y + B.button.h / 2],
    [T.confirmClick - 0.05, B.confirm.x + B.confirm.w / 2, B.confirm.y + B.confirm.h / 2],
    [T.length, B.confirm.x + 260, B.confirm.y + 120],
  ]);
  const press = (at: number) => interpolate(t, [at - 0.05, at + 0.05, at + 0.2], [0, 1, 0], clamp);
  const lock = land(interpolate(t, [T.doneAt + 0.2, T.doneAt + 0.6], [0, 1], clamp));

  return (
    <AbsoluteFill style={{ background: colors.paper }}>
      <Window enter={1}>
        <Viewport zoom={cam.zoom} x={cam.x} y={cam.y}>
          <Img src={staticFile(SCREENS_STORY2.additional.src)} style={{ position: "absolute", left: 0, top: 0, width: VW, height: VH }} />
          {fade(T.readyAt) > 0 && <Img src={staticFile(SCREENS_STORY2.finaliseReady.src)} style={{ position: "absolute", left: 0, top: 0, width: VW, height: VH, opacity: fade(T.readyAt) }} />}
          {fade(T.confirmAt) > 0 && <Img src={staticFile(SCREENS_STORY2.finaliseConfirm.src)} style={{ position: "absolute", left: 0, top: 0, width: VW, height: VH, opacity: fade(T.confirmAt) }} />}
          {fade(T.doneAt) > 0 && <Img src={staticFile(SCREENS_STORY2.finaliseDone.src)} style={{ position: "absolute", left: 0, top: 0, width: VW, height: VH, opacity: fade(T.doneAt) }} />}
          {lock > 0 && (
            <div style={{ position: "absolute", left: B.badge.x - 6, top: B.badge.y - 5, width: B.badge.w + 12, height: B.badge.h + 10, borderRadius: 999, border: `3px solid ${colors.steel}`, opacity: lock, transform: `scale(${1.5 - 0.5 * lock})`, boxShadow: `0 0 0 ${8 * (1 - lock)}px rgba(53,95,150,0.25)` }} />
          )}
          <Pointer x={px} y={py} size={20} press={Math.max(press(T.summaryClick), press(T.buttonClick), press(T.confirmClick))} />
        </Viewport>
      </Window>
      <ProductHeader step={5} fromStep={4} length={T.length} headline={FINALISE_COPY.headline} headlineIn={T.headlineIn} showText={showText}
        beats={FINALISE_COPY.beats.map((b) => ({ ...b, at: T[b.at as keyof typeof T] }))} />
    </AbsoluteFill>
  );
};
