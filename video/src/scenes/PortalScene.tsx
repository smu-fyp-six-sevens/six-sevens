// Section 4 - "portal". The product arrives:
//   A. the pile from section 3; a scan line sweeps it, checks each document and flags the doctored statement;
//   B. the camera pushes into it and cuts to the real Agent trace, whose rows light up as the agents work;
//   C. cut to the real Detection panel: the forged figure boxed in the preview, the top finding highlighted.
// The screens are the real portal, captured by video/capture/story.spec.ts. Times: PORTAL in timing.ts.

import { AbsoluteFill, Easing, Img, interpolate, staticFile, useCurrentFrame } from "remotion";
import { DocWindow } from "../components/DocWindow";
import { Title } from "../components/Title";
import boxes from "../../public/ui/story/boxes.json";
import { COPY2 } from "../data/overwhelm";
import { PORTAL_COPY, SCREENS } from "../data/portal";
import { colors, fonts } from "../theme";
import { PORTAL as T, s } from "../timing";
import {
  CLOSE_H, CLOSE_Y, COLS, FIGURE_W, ForgedFigure, H, PAGE_R, ROWS, SLOT, Statement, THUMB, W, pileDoc, slotXY,
} from "./TamperScene";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
const land = Easing.bezier(0.2, 0.9, 0.1, 1);
const even = Easing.bezier(0.37, 0, 0.63, 1);
const rampIn = Easing.bezier(0.45, 0, 0.85, 0.6);

// ---- A: the pile ----------------------------------------------------------------------------
const SECTION3_END_SCALE = 1.02; // section 3's camera ends here; holding it makes the cut seamless
const thumbW = W * THUMB;
const thumbH = H * THUMB;
const GRID_LEFT = slotXY(0).x;
const GRID_RIGHT = slotXY(COLS - 1).x + thumbW;

const Check: React.FC<{ p: number }> = ({ p }) => (
  <div style={{ position: "absolute", right: -10, top: -10, width: 30, height: 30, borderRadius: 15, background: colors.white, border: `2px solid #166d5f`, display: "flex", alignItems: "center", justifyContent: "center", transform: `scale(${p})`, boxShadow: "0 4px 10px rgba(18,32,61,0.18)" }}>
    <svg width="16" height="16" viewBox="0 0 16 16"><path d="M3 8.5 L6.5 12 L13 4.5" fill="none" stroke="#166d5f" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" /></svg>
  </div>
);

const Pile: React.FC<{ frame: number }> = ({ frame }) => {
  const scanX = interpolate(frame, [s(T.scanStart), s(T.scanEnd)], [GRID_LEFT - 60, GRID_RIGHT + 60], { ...clamp, easing: even });
  const scanOn = frame >= s(T.scanStart) && frame <= s(T.scanEnd) + 6;
  const slot = slotXY(SLOT);
  const passedAt = (x: number) => {
    // the frame the scan line crossed this x
    const u = (x - (GRID_LEFT - 60)) / (GRID_RIGHT + 60 - (GRID_LEFT - 60));
    return s(T.scanStart) + u * (s(T.scanEnd) - s(T.scanStart));
  };
  const flagP = land(interpolate(frame, [passedAt(slot.x + thumbW / 2), passedAt(slot.x + thumbW / 2) + 10], [0, 1], clamp));
  // the push into the flagged statement
  const push = interpolate(frame, [s(T.pushStart), s(T.pushEnd)], [0, 1], { ...clamp, easing: rampIn });
  const scale = SECTION3_END_SCALE * Math.exp(Math.log(4.2) * push);
  const cx = slot.x + thumbW / 2;
  const cy = slot.y + thumbH / 2;
  return (
    <AbsoluteFill style={{ transformOrigin: `${cx}px ${cy}px`, transform: `scale(${scale})`, filter: push > 0.4 ? `blur(${(push - 0.4) * 8}px)` : undefined }}>
      {Array.from({ length: COLS * ROWS }, (_, i) => {
        const { x, y } = slotXY(i);
        const checked = land(interpolate(frame, [passedAt(x + thumbW / 2), passedAt(x + thumbW / 2) + 8], [0, 1], clamp));
        const isSlot = i === SLOT;
        return (
          <div key={i} style={{ position: "absolute", left: x, top: y, width: thumbW, height: thumbH, opacity: isSlot ? 1 : 1 - 0.45 * flagP }}>
            <div style={{ transformOrigin: "0 0", transform: `scale(${THUMB})` }}>
              {isSlot ? <Statement figure={<ForgedFigure />} selection={0} caret={null} /> : <DocWindow {...pileDoc(i)} company={COPY2.company} width={W} height={H} />}
            </div>
            {!isSlot && checked > 0 && <Check p={checked} />}
            {isSlot && flagP > 0 && (
              <>
                <div style={{ position: "absolute", inset: -8, borderRadius: 12, border: `4px solid ${colors.red}`, opacity: flagP, transform: `scale(${1.12 - 0.12 * flagP})` }} />
                <div style={{ position: "absolute", left: "50%", top: -26, transform: `translateX(-50%) scale(${0.7 + 0.3 * flagP})`, opacity: flagP, background: colors.red, color: colors.white, fontFamily: fonts.ui, fontWeight: 700, fontSize: 20, padding: "6px 16px", borderRadius: 999, boxShadow: "0 8px 18px rgba(179,64,42,0.3)" }}>
                  {PORTAL_COPY.flagged}
                </div>
              </>
            )}
          </div>
        );
      })}
      {scanOn && (
        <div style={{ position: "absolute", left: scanX - 2, top: slotXY(0).y - 30, width: 4, height: thumbH * ROWS + (ROWS - 1) * (slotXY(COLS).y - slotXY(0).y - thumbH) + 60, background: colors.steel, borderRadius: 2, boxShadow: `0 0 24px 8px rgba(53,95,150,0.35)` }} />
      )}
    </AbsoluteFill>
  );
};

// ---- B and C: the real portal in a window ---------------------------------------------------
export const WIN = { x: 160, y: 250, w: 1600, h: 785 };
const K0 = WIN.w / 1440; // CSS px to film px at zoom 1

/** The portal screen at a zoom, holding CSS point `focus` at the window's centre (clamped to the screen's edges). */
export const Screen: React.FC<{ src: string; w: number; h: number; zoom: number; focus: { x: number; y: number }; children?: React.ReactNode; imgH?: number }> = ({ src, w, h, zoom, focus, children, imgH }) => {
  const k = K0 * zoom;
  const tx = Math.min(0, Math.max(WIN.w - w * k, WIN.w / 2 - k * focus.x));
  const ty = Math.min(0, Math.max(WIN.h - h * k, WIN.h / 2 - k * focus.y));
  return (
    <div style={{ position: "absolute", left: 0, top: 0, width: w, height: h, background: colors.paper, transformOrigin: "0 0", transform: `translate(${tx}px, ${ty}px) scale(${k})` }}>
      <Img src={staticFile(src)} style={{ width: w, height: imgH ?? h, display: "block" }} />
      {children}
    </div>
  );
};

/** A tilt in degrees (x: top away from the viewer, y: right side away), and an offset in px. */
export type Tilt = { rx?: number; ry?: number; rz?: number; dx?: number; dy?: number; scale?: number };

/**
 * `swipe`: the one move between screens and between stages. 0 at rest; 1 = fully off to the right (entering);
 * -1 = fully off to the left (leaving). The window slides flat, easing in scale and focus as it goes.
 */
export const Window: React.FC<{ enter: number; children: React.ReactNode; tilt?: Tilt; opacity?: number; swipe?: number }> = ({ enter, children, tilt, opacity = 1, swipe = 0 }) => (
  <AbsoluteFill style={{ perspective: 2400, perspectiveOrigin: `${WIN.x + WIN.w / 2}px ${WIN.y + WIN.h / 2}px`, opacity: opacity * (1 - 0.85 * Math.abs(swipe)), filter: swipe ? `blur(${Math.abs(swipe) * 6}px)` : undefined, transform: `translateX(${swipe * WIN.w * 0.62}px) scale(${1 - 0.08 * Math.abs(swipe)})` }}>
    <div
      style={{
        position: "absolute", left: WIN.x, top: WIN.y, width: WIN.w, height: WIN.h, borderRadius: 18, overflow: "hidden",
        background: colors.white, border: "1px solid rgba(18,32,61,0.12)", boxShadow: "0 40px 90px rgba(18,32,61,0.22)",
        transform: `translate(${tilt?.dx ?? 0}px, ${tilt?.dy ?? 0}px) rotateX(${tilt?.rx ?? 0}deg) rotateY(${tilt?.ry ?? 0}deg) rotateZ(${tilt?.rz ?? 0}deg) scale(${(tilt?.scale ?? 1) * (1.05 - 0.05 * enter)})`,
        filter: enter < 1 ? `blur(${(1 - enter) * 3}px)` : undefined,
      }}
    >
      {children}
    </div>
  </AbsoluteFill>
);

const TRACE_ROWS = boxes.trace.rows;
/** Rows where the agents found something: they keep an amber tint. */
const FOUND = TRACE_ROWS.map((r) => /finding\(s\)|⚠/.test(r.text) && !/Specialist finished|Fraud detection/.test(r.text));

export const Trace: React.FC<{ frame: number; start?: number; end?: number }> = ({ frame, start = T.traceStart, end = T.traceEnd }) => {
  const u = interpolate(frame, [s(start), s(end)], [0, TRACE_ROWS.length], clamp);
  const current = Math.floor(u);
  return (
    <>
      {TRACE_ROWS.map((r, i) => {
        if (i > current || u <= 0) return null;
        const live = i === current && u < TRACE_ROWS.length;
        const tint = FOUND[i] ? "198,130,10" : "53,95,150";
        return (
          <div key={i} style={{ position: "absolute", left: r.x, top: r.y, width: r.w, height: r.h, background: `rgba(${tint}, ${live ? 0.16 : FOUND[i] ? 0.12 : 0.05})`, borderLeft: `3px solid rgba(${tint}, ${live || FOUND[i] ? 1 : 0.35})` }} />
        );
      })}
    </>
  );
};

// The empty document preview in the capture (CSS px), where the edited statement is shown.
const PREVIEW = { x: 118, y: 236, w: 627, h: 745 };
const PREVIEW_K = Math.min(PREVIEW.w / W, PREVIEW.h / H);

export const Detection: React.FC<{ frame: number; boxIn?: number; findingAt?: number }> = ({ frame, boxIn = T.boxIn, findingAt = T.findingAt }) => {
  const box = land(interpolate(frame, [s(boxIn), s(boxIn) + 10], [0, 1], clamp));
  const ring = interpolate(frame, [s(boxIn) + 6, s(boxIn) + 30], [0, 1], clamp);
  const sweep = interpolate(frame, [s(findingAt), s(findingAt) + 14], [0, 1], { ...clamp, easing: even });
  const label = boxes.detection.label[0];
  const finding = boxes.detection.findings[0];
  return (
    <>
      <div style={{ position: "absolute", left: PREVIEW.x + (PREVIEW.w - W * PREVIEW_K) / 2, top: PREVIEW.y, transformOrigin: "0 0", transform: `scale(${PREVIEW_K})` }}>
        <Statement figure={<ForgedFigure />} selection={0} caret={null} />
        {box > 0 && (
          <div style={{ position: "absolute", left: PAGE_R - FIGURE_W - 6, top: CLOSE_Y + 3, width: FIGURE_W + 12, height: CLOSE_H - 6, border: `2.5px solid ${colors.red}`, borderRadius: 3, background: "rgba(179,64,42,0.08)", opacity: box, transform: `scale(${1.4 - 0.4 * box})` }} />
        )}
      </div>
      {ring > 0 && ring < 1 && (
        <div style={{ position: "absolute", left: label.x - 4, top: label.y - 4, width: label.w + 8, height: label.h + 8, borderRadius: 999, border: `3px solid ${colors.red}`, opacity: 1 - ring, transform: `scale(${1 + 0.5 * ring})` }} />
      )}
      {sweep > 0 && (
        <div style={{ position: "absolute", left: finding.x - 10, top: finding.y - 4, width: (finding.w + 20) * sweep, height: finding.h, background: "rgba(179,64,42,0.07)", borderLeft: `4px solid ${colors.red}`, borderRadius: 6 }} />
      )}
    </>
  );
};

export const PortalScene: React.FC<{ showText?: boolean }> = ({ showText = true }) => {
  const frame = useCurrentFrame();
  const inPile = frame < s(T.pushEnd);
  const inTrace = !inPile && frame < s(T.detectionAt);
  const traceEnter = land(interpolate(frame, [s(T.pushEnd), s(T.pushEnd) + 12], [0, 1], clamp));
  const detEnter = land(interpolate(frame, [s(T.detectionAt), s(T.detectionAt) + 12], [0, 1], clamp));
  const zoom = interpolate(frame, [s(T.zoomStart), s(T.zoomEnd)], [1, 1.45], { ...clamp, easing: even });
  const focusDet = {
    x: interpolate(zoom, [1, 1.45], [720, 944]),
    y: interpolate(zoom, [1, 1.45], [353, 730]),
  };
  // a slow drift on the trace so the shot never sits dead still
  const traceZoom = interpolate(frame, [s(T.pushEnd), s(T.detectionAt)], [1, 1.04], clamp);

  return (
    <AbsoluteFill style={{ background: colors.paper }}>
      {inPile && <Pile frame={frame} />}
      {inTrace && (
        <Window enter={traceEnter}>
          <Screen {...SCREENS.trace} zoom={traceZoom} focus={{ x: 420, y: 353 }}>
            <Trace frame={frame} />
          </Screen>
        </Window>
      )}
      {!inPile && !inTrace && (
        <Window enter={detEnter}>
          <Screen {...SCREENS.detection} zoom={zoom} focus={focusDet}>
            <Detection frame={frame} />
          </Screen>
        </Window>
      )}

      {showText && (
        <div style={{ position: "absolute", left: 0, right: 0, top: 56, display: "flex", flexDirection: "column", alignItems: "center", gap: 10 }}>
          <Title text={PORTAL_COPY.title} enterAt={s(T.titleIn)} exit="none" align="center" style={{ fontFamily: fonts.display, fontSize: 66, fontWeight: 700, letterSpacing: "-0.02em", lineHeight: 1.12, color: colors.ink }} />
          <div style={{ display: "flex", gap: 14 }}>
            <Title text={PORTAL_COPY.line1} enterAt={s(T.line1In)} exit="none" style={{ fontFamily: fonts.display, fontSize: 40, fontWeight: 600, color: colors.inkSoft, lineHeight: 1.2 }} />
            <Title text={PORTAL_COPY.line2} enterAt={s(T.line2In)} exit="none" style={{ fontFamily: fonts.display, fontSize: 40, fontWeight: 600, color: colors.inkSoft, lineHeight: 1.2 }} />
          </div>
        </div>
      )}
    </AbsoluteFill>
  );
};
