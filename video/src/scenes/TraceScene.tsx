// Section 7 - "trace" (how it works, step 2 continued): not a black box.
//   A. The real Agent trace; its rows light up, the cursor opens the font consistency check and the tool's
//      returned output is highlighted as the camera moves in.
//   B. The case's real audit trail, angled, its entries lighting up one by one.
// Times: TRACE in timing.ts. Words: data/howitworks.ts. Screens: capture/story2.spec.ts.

import { AbsoluteFill, Easing, Img, interpolate, staticFile, useCurrentFrame } from "remotion";
import { Pointer } from "../components/Pointer";
import { ProductHeader } from "../components/ProductHeader";
import boxes from "../../public/ui/story/story2-trace.json";
import { SCREENS_STORY2, TRACE_COPY } from "../data/howitworks";
import { colors } from "../theme";
import { FPS, TRACE as T, s } from "../timing";
import { Screen, Trace, Window } from "./PortalScene";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
const land = Easing.bezier(0.2, 0.9, 0.1, 1);
const even = Easing.bezier(0.37, 0, 0.63, 1);
const lerp = (p: number, a: number, b: number) => a + (b - a) * p;

const ROWS = boxes.trace.rows;
const TOOL_ROW = ROWS.findIndex((r) => r.text.includes("Font consistency check"));
/** The tool's returned output in the details pane (CSS px of the capture). */
const OUTPUT = { x: 722, y: 352, w: 690, h: 182 };

export const TraceScene: React.FC<{ showText?: boolean }> = ({ showText = true }) => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const inAudit = t >= T.auditAt;

  // A
  const enterA = land(interpolate(t, [0, 0.6], [0, 1], clamp));
  const tool = ROWS[TOOL_ROW];
  const toClick = interpolate(t, [T.clickAt - 0.7, T.clickAt - 0.05], [0, 1], { ...clamp, easing: even });
  const press = interpolate(t, [T.clickAt - 0.05, T.clickAt + 0.05, T.clickAt + 0.2], [0, 1, 0], clamp);
  const opened = interpolate(t, [T.clickAt, T.clickAt + 0.15], [0, 1], clamp);
  const outP = land(interpolate(t, [T.outputAt, T.outputAt + 0.4], [0, 1], clamp));
  const push = interpolate(t, [T.outputAt - 0.2, T.outputAt + 1.2], [0, 1], { ...clamp, easing: even });

  // B
  const enterB = land(interpolate(t, [T.auditAt, T.auditAt + 0.7], [0, 1], clamp));
  const entries = boxes.audit.entries;
  const u = interpolate(t, [T.entriesStart, T.entriesEnd], [0, entries.length], clamp);
  const pan = interpolate(t, [T.entriesStart, T.entriesEnd + 0.3], [0, 1], { ...clamp, easing: even });

  return (
    <AbsoluteFill style={{ background: colors.paper }}>
      {!inAudit ? (
        <Window enter={enterA} tilt={{ rx: 5, ry: lerp(enterA, 30, 8) - 6 * push, dx: lerp(enterA, 400, 0) }}>
          <Screen {...SCREENS_STORY2.trace} zoom={lerp(push, 1, 1.32)} focus={{ x: lerp(push, 600, 1060), y: lerp(push, 353, 430) }}>
            {opened > 0 && <Img src={staticFile(SCREENS_STORY2.traceTool.src)} style={{ position: "absolute", left: 0, top: 0, width: 1440, height: 900, opacity: opened }} />}
            {opened < 1 && <Trace frame={frame} start={T.rowsStart} end={T.rowsEnd} />}
            {outP > 0 && <div style={{ position: "absolute", left: OUTPUT.x, top: OUTPUT.y, width: OUTPUT.w, height: OUTPUT.h, border: `3px solid ${colors.steel}`, borderRadius: 8, background: "rgba(53,95,150,0.06)", opacity: outP, transform: `scale(${1.06 - 0.06 * outP})` }} />}
            {t < T.outputAt + 0.6 && t >= T.clickAt - 0.8 && (
              <Pointer x={lerp(toClick, tool.x + 380, tool.x + 150)} y={lerp(toClick, tool.y + 160, tool.y + 18)} size={20} press={press} />
            )}
          </Screen>
        </Window>
      ) : (
        <Window enter={enterB} tilt={{ rx: lerp(enterB, 14, 5), ry: lerp(enterB, -30, -6), dx: lerp(enterB, -400, 0) }}>
          <Screen {...SCREENS_STORY2.audit} zoom={1.08} focus={{ x: 760, y: lerp(pan, 420, 560) }}>
            {entries.map((e, i) => {
              if (i > u) return null;
              const live = i === Math.floor(u) && u < entries.length;
              return <div key={i} style={{ position: "absolute", left: e.x - 6, top: e.y - 3, width: e.w + 12, height: e.h + 6, borderRadius: 10, border: `${live ? 3 : 2}px solid rgba(53,95,150,${live ? 1 : 0.35})`, background: `rgba(53,95,150,${live ? 0.07 : 0.02})` }} />;
            })}
          </Screen>
        </Window>
      )}
      <ProductHeader step={1} headline={TRACE_COPY.headline} headlineIn={T.headlineIn} showText={showText}
        beats={TRACE_COPY.beats.map((b) => ({ ...b, at: T[b.at as keyof typeof T] }))} />
    </AbsoluteFill>
  );
};
