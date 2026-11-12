// Section 4 v2 - "portal", with a pronounced introduction.
//   1. Plain text, centred: "So that's why we introduce..."
//   2. "GLDB Fraud Detection Portal", big and centred, a line drawing in beneath it.
//   3. The name glides up to the top band as the real Case Summary rises in, tilted in 3D.
//   4. The real Agent trace swings in from the other side; its rows light up.
//   5. The real Detection screen, angled, turns flat as the camera pushes to the finding.
// Times: PORTAL2 in timing.ts. Words: data/portal.ts. Screens: video/capture/story.spec.ts.

import { AbsoluteFill, Easing, interpolate, useCurrentFrame } from "remotion";
import { Title } from "../components/Title";
import { PORTAL_COPY, SCREENS } from "../data/portal";
import { colors, fonts } from "../theme";
import { FPS, PORTAL2 as T, s } from "../timing";
import { Detection, Screen, Trace, Window } from "./PortalScene";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
const land = Easing.bezier(0.2, 0.9, 0.1, 1);
const even = Easing.bezier(0.37, 0, 0.63, 1);
const lerp = (p: number, a: number, b: number) => a + (b - a) * p;

/** A slow sway so no angled shot ever sits dead still. */
const sway = (frame: number, deg: number, period = 6) => deg * Math.sin((frame / FPS / period) * 2 * Math.PI);

export const PortalScene2: React.FC<{ showText?: boolean }> = ({ showText = true }) => {
  const frame = useCurrentFrame();

  // 1-2. The intro line, then the name.
  const introOut = interpolate(frame, [s(T.introOut), s(T.introOut) + 10], [0, 1], { ...clamp, easing: even });
  const underline = interpolate(frame, [s(T.underline), s(T.underline) + 14], [0, 1], { ...clamp, easing: even });
  // 3. The name moves from the centre of the frame to the top band and shrinks.
  const hero = interpolate(frame, [s(T.heroStart), s(T.heroEnd)], [0, 1], { ...clamp, easing: land });
  const nameY = lerp(hero, 540 - 70, 56);
  const nameScale = lerp(hero, 1.55, 1);

  // Which screen is up.
  const shot = frame < s(T.traceAt) ? "summary" : frame < s(T.detectionAt) ? "trace" : "detection";
  const enterOf = (at: number, len = 22) => land(interpolate(frame, [s(at), s(at) + len], [0, 1], clamp));

  // The Case Summary rises from below, tilted back and to the left, then settles to a gentle angle.
  const sumP = hero;
  const summaryTilt = { rx: lerp(sumP, 38, 9) + sway(frame, 0.8), ry: lerp(sumP, -18, -8) + sway(frame, 1.5), rz: lerp(sumP, 3, 0.5), dy: lerp(sumP, 760, 0), scale: lerp(sumP, 0.9, 1) };
  // The trace swings in from the right.
  const trP = enterOf(T.traceAt);
  const traceTilt = { rx: 6 + sway(frame, 0.6), ry: lerp(trP, 34, 9) + sway(frame, 1.2), dx: lerp(trP, 520, 0), scale: lerp(trP, 0.92, 1) };
  // The detection screen arrives angled, then turns flat while the camera pushes in.
  const deP = enterOf(T.detectionAt);
  const flat = interpolate(frame, [s(T.flatten), s(T.flattenEnd)], [0, 1], { ...clamp, easing: even });
  const detectionTilt = { rx: lerp(flat, 10, 0), ry: lerp(flat, lerp(deP, -32, -12), 0), dx: lerp(deP, -480, 0), scale: lerp(deP, 0.92, 1) };
  const zoom = lerp(flat, 1, 1.45);
  const focusDet = { x: lerp(flat, 720, 944), y: lerp(flat, 353, 730) };

  return (
    <AbsoluteFill style={{ background: colors.paper }}>
      {hero > 0 && shot === "summary" && (
        <Window enter={1} tilt={summaryTilt}>
          <Screen {...SCREENS.summary} zoom={1.04} focus={{ x: 760, y: 360 }} />
        </Window>
      )}
      {shot === "trace" && (
        <Window enter={trP} tilt={traceTilt}>
          <Screen {...SCREENS.trace} zoom={1.02} focus={{ x: 420, y: 353 }}>
            <Trace frame={frame} start={T.traceStart} end={T.traceEnd} />
          </Screen>
        </Window>
      )}
      {shot === "detection" && (
        <Window enter={deP} tilt={detectionTilt}>
          <Screen {...SCREENS.detection} zoom={zoom} focus={focusDet}>
            <Detection frame={frame} boxIn={T.boxIn} findingAt={T.findingAt} />
          </Screen>
        </Window>
      )}

      {/* The lead-in line: always shown, even without captions, because it is the introduction itself. */}
      {introOut < 1 && (
        <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", opacity: 1 - introOut, transform: `translateY(${-40 * introOut}px)` }}>
          <Title text={PORTAL_COPY.intro} enterAt={s(T.introIn)} exit="none" align="center"
            style={{ fontFamily: fonts.display, fontSize: 64, fontWeight: 600, letterSpacing: "-0.02em", color: colors.inkSoft }} />
        </AbsoluteFill>
      )}

      {/* The name: centred and large, then up into the top band. */}
      {frame >= s(T.titleIn) && (
        <div style={{ position: "absolute", left: 0, right: 0, top: nameY, display: "flex", flexDirection: "column", alignItems: "center", transform: `scale(${nameScale})`, transformOrigin: "50% 0%" }}>
          <Title text={PORTAL_COPY.title} enterAt={s(T.titleIn)} exit="none" align="center"
            style={{ fontFamily: fonts.display, fontSize: 66, fontWeight: 700, letterSpacing: "-0.02em", lineHeight: 1.12, color: colors.ink }} />
          <div style={{ marginTop: 10, height: 5, width: 300, borderRadius: 3, background: colors.steel, transform: `scaleX(${underline * (1 - hero)})`, transformOrigin: "50% 50%" }} />
        </div>
      )}

      {showText && (
        <div style={{ position: "absolute", left: 0, right: 0, top: 150, display: "flex", justifyContent: "center", gap: 14 }}>
          <Title text={PORTAL_COPY.line1} enterAt={s(T.line1In)} exit="none" style={{ fontFamily: fonts.display, fontSize: 40, fontWeight: 600, color: colors.inkSoft, lineHeight: 1.2 }} />
          <Title text={PORTAL_COPY.line2} enterAt={s(T.line2In)} exit="none" style={{ fontFamily: fonts.display, fontSize: 40, fontWeight: 600, color: colors.inkSoft, lineHeight: 1.2 }} />
        </div>
      )}
    </AbsoluteFill>
  );
};
