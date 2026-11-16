// Section 8b - "finalise": the officer locks the case, then downloads the case report, in the real portal.
// From Additional Documents (where Score ends) the cursor clicks Summary, "Finalise Case", confirms; the case is
// Finalised. It opens the case menu and clicks "Download report": the report (the backend's own PDF renderer fed the
// story case, video/capture/story_report.py) slides up, pages fanned. It slides away again and the shot returns to
// exactly the framing the dashboard segment opens on, so that join stays seamless. Times: FINALISE in timing.ts.

import { AbsoluteFill, Easing, Img, interpolate, staticFile, useCurrentFrame } from "remotion";
import { cameraAt } from "../components/keys";
import { Pointer } from "../components/Pointer";
import { ProductHeader } from "../components/ProductHeader";
import { FINALISE_BOXES as B, FINALISE_COPY, SCREENS_STORY2 } from "../data/howitworks";
import { colors, fonts } from "../theme";
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
const centre = (b: { x: number; y: number; w: number; h: number }) => [b.x + b.w / 2, b.y + b.h / 2] as const;

export const FinaliseScene: React.FC<{ showText?: boolean }> = ({ showText = true }) => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const fade = (at: number) => interpolate(t, [at, at + 0.12], [0, 1], clamp);
  const [mx, my] = centre(B.menuButton);
  const [rx, ry] = centre(B.reportItem);
  const cam = cameraAt(t, [
    [0, 1.0, 720, 450],
    [T.summaryClick, 1.0, 720, 450],
    [T.buttonClick - 0.15, 1.3, 1030, 300],
    [T.confirmAt, 1.3, 1030, 300],
    [T.confirmAt + 0.35, 1.2, 720, 430],
    [T.doneAt + 0.1, 1.2, 720, 430],
    [T.menuClick - 0.1, 1.35, 1180, 230],
    [T.reportClick + 0.1, 1.35, 1180, 230],
    [T.reportAt + 0.5, 1.0, 720, 450],
    [T.reportOut, 1.0, 720, 450],
    // ends where the dashboard segment opens: on the status card, zoomed
    [T.length - 0.1, 1.4, 900, 270],
  ]);
  const end = { x: B.confirm.x + 260, y: B.confirm.y + 120 }; // the dashboard segment's first cursor position
  const [px, py] = path(t, [
    [0, 700, 520],
    [T.summaryClick - 0.05, B.sidebarSummary.x, B.sidebarSummary.y],
    [T.buttonClick - 0.05, B.button.x + B.button.w / 2, B.button.y + B.button.h / 2],
    [T.confirmClick - 0.05, B.confirm.x + B.confirm.w / 2, B.confirm.y + B.confirm.h / 2],
    [T.menuClick - 0.05, mx, my],
    [T.reportClick - 0.05, rx - 30, ry],
    [T.reportOut, rx - 30, ry],
    [T.length - 0.1, end.x, end.y],
  ]);
  const press = (at: number) => interpolate(t, [at - 0.05, at + 0.05, at + 0.2], [0, 1, 0], clamp);
  const pressed = Math.max(press(T.summaryClick), press(T.buttonClick), press(T.confirmClick), press(T.menuClick), press(T.reportClick));
  // The Finalised badge's ring: rings in when the case locks, steps aside for the menu and the report, and is back
  // in full on the last frame (the dashboard segment fades it from there).
  const ring = Math.min(
    land(interpolate(t, [T.doneAt + 0.2, T.doneAt + 0.6], [0, 1], clamp)),
    1 - interpolate(t, [T.menuClick - 0.3, T.menuClick], [0, 1], clamp) + interpolate(t, [T.reportOut + 0.2, T.length - 0.15], [0, 1], clamp),
  );
  const itemHi = interpolate(t, [T.reportClick - 0.3, T.reportClick - 0.1, T.reportClick + 0.1, T.reportClick + 0.2], [0, 1, 1, 0], clamp);
  // The report: up from below, settles, then away again.
  const rin = land(interpolate(t, [T.reportAt, T.reportAt + 0.55], [0, 1], clamp));
  const rout = interpolate(t, [T.reportOut, T.reportOut + 0.4], [0, 1], { ...clamp, easing: Easing.bezier(0.5, 0, 0.9, 0.55) });
  const rp = rin * (1 - rout);
  const chip = land(interpolate(t, [T.reportAt + 0.35, T.reportAt + 0.65], [0, 1], clamp)) * (1 - rout);
  const PAGE_H = 780;
  const PAGE_W = Math.round(PAGE_H * (1240 / 1754));

  return (
    <AbsoluteFill style={{ background: colors.paper }}>
      <Window enter={1}>
        <Viewport zoom={cam.zoom} x={cam.x} y={cam.y}>
          <Img src={staticFile(SCREENS_STORY2.additional.src)} style={{ position: "absolute", left: 0, top: 0, width: VW, height: VH }} />
          {fade(T.readyAt) > 0 && <Img src={staticFile(SCREENS_STORY2.finaliseReady.src)} style={{ position: "absolute", left: 0, top: 0, width: VW, height: VH, opacity: fade(T.readyAt) }} />}
          {fade(T.confirmAt) > 0 && <Img src={staticFile(SCREENS_STORY2.finaliseConfirm.src)} style={{ position: "absolute", left: 0, top: 0, width: VW, height: VH, opacity: fade(T.confirmAt) }} />}
          {fade(T.doneAt) > 0 && <Img src={staticFile(SCREENS_STORY2.finaliseDone.src)} style={{ position: "absolute", left: 0, top: 0, width: VW, height: VH, opacity: fade(T.doneAt) }} />}
          {t >= T.menuAt && t < T.reportClick + 0.15 && <Img src={staticFile(SCREENS_STORY2.finaliseMenu.src)} style={{ position: "absolute", left: 0, top: 0, width: VW, height: VH, opacity: fade(T.menuAt) }} />}
          {itemHi > 0 && <div style={{ position: "absolute", left: B.reportItem.x + 2, top: B.reportItem.y, width: B.reportItem.w - 4, height: B.reportItem.h, borderRadius: 6, background: `rgba(53,95,150,${0.14 * itemHi})`, boxShadow: `inset 0 0 0 2px rgba(53,95,150,${itemHi})` }} />}
          {ring > 0 && (
            <div style={{ position: "absolute", left: B.badge.x - 6, top: B.badge.y - 5, width: B.badge.w + 12, height: B.badge.h + 10, borderRadius: 999, border: `3px solid ${colors.steel}`, opacity: ring }} />
          )}
          <Pointer x={px} y={py} size={20} press={pressed} />
        </Viewport>
      </Window>

      {/* The downloaded report */}
      {rp > 0 && (
        <AbsoluteFill style={{ background: `rgba(18,32,61,${0.28 * rp})` }}>
          <div style={{ position: "absolute", left: 960 - PAGE_W / 2, top: 262, width: PAGE_W, height: PAGE_H, transform: `translateY(${(1 - rin) * 720 + rout * 760}px)` }}>
            {[2, 1, 0].map((i) => (
              <Img key={i} src={staticFile(SCREENS_STORY2.reportPages[i])} style={{
                position: "absolute", left: 0, top: 0, width: PAGE_W, height: PAGE_H, borderRadius: 6,
                boxShadow: "0 30px 70px rgba(18,32,61,0.35)",
                transform: `translate(${i * 70 * rin}px, ${i * 14}px) rotate(${i * 3.5 * rin}deg)`, transformOrigin: "50% 100%",
              }} />
            ))}
          </div>
          <div style={{ position: "absolute", left: 960 + PAGE_W / 2 + 120, top: 330, opacity: chip, transform: `translateY(${(1 - chip) * 20}px)`,
            display: "flex", alignItems: "center", gap: 14, padding: "16px 22px", borderRadius: 14, background: colors.white,
            boxShadow: "0 16px 40px rgba(18,32,61,0.25)", fontFamily: fonts.ui }}>
            <span style={{ width: 40, height: 48, borderRadius: 6, background: colors.red, color: "#fff", font: `700 13px ${fonts.ui}`, display: "flex", alignItems: "center", justifyContent: "center" }}>PDF</span>
            <span style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span style={{ font: `700 19px ${fonts.ui}`, color: colors.ink }}>{FINALISE_COPY.downloaded}</span>
              <span style={{ font: `600 15px ${fonts.ui}`, color: "#166d5f" }}>✓ Downloaded · 4 pages</span>
            </span>
          </div>
        </AbsoluteFill>
      )}

      <ProductHeader step={5} fromStep={4} length={T.length} headline={FINALISE_COPY.headline} headlineIn={T.headlineIn} showText={showText}
        beats={FINALISE_COPY.beats.map((b) => ({ ...b, at: T[b.at as keyof typeof T] }))} />
    </AbsoluteFill>
  );
};
