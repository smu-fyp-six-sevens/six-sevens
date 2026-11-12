// Section 3 - "tamper". Split screen: a bad actor (left) quietly edits the closing balance on a bank
// statement (right, zoomed in). The new figure is a near-perfect forgery with tiny tells (slightly
// lighter weight, a pixel off the line). Then the statement shrinks into a grid of the case's other
// documents and is impossible to pick out. Times: TAMPER in timing.ts. Words: data/tamper.ts.

import { AbsoluteFill, Easing, interpolate, useCurrentFrame } from "remotion";
import { DocWindow } from "../components/DocWindow";
import { Silhouette } from "../components/Silhouette";
import { Stage } from "../components/Stage";
import { Title } from "../components/Title";
import { COPY2, DOCS } from "../data/overwhelm";
import { BAD_ACTOR, CAPTION3, STATEMENT } from "../data/tamper";
import { colors, fonts } from "../theme";
import { FPS, TAMPER as T, atSeconds, s } from "../timing";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
const land = Easing.bezier(0.2, 0.9, 0.1, 1);
const even = Easing.bezier(0.37, 0, 0.63, 1);

// ---- The statement, at the same size and chrome as the hook's PDF windows (440 x 560). ----
export const W = 440;
export const H = 560;
const PAGE_X = 22 + 24; // window padding + page padding
export const PAGE_R = W - 22 - 24; // right edge of the page's content
const ROWS_TOP = 174;
const ROW_H = 22;
export const CLOSE_Y = ROWS_TOP + STATEMENT.rows.length * ROW_H + 6; // top of the closing balance row
export const CLOSE_H = 30;
export const FIGURE_W = 78; // width of the closing figure, for the selection

export const Statement: React.FC<{ figure: React.ReactNode; selection: number; caret: { x: number; on: boolean } | null }> = ({ figure, selection, caret }) => (
  <div style={{ position: "relative", width: W, height: H, borderRadius: 10, overflow: "hidden", background: "#eceae6", border: "1px solid rgba(18,32,61,0.10)", boxShadow: "0 18px 40px rgba(18,32,61,0.18)", fontFamily: fonts.ui }}>
    <div style={{ height: 34, display: "flex", alignItems: "center", gap: 8, padding: "0 12px", background: "#f6f5f2", borderBottom: `1px solid ${colors.line}` }}>
      <div style={{ width: 18, height: 18, borderRadius: 3, background: colors.red, color: "#fff", fontSize: 6.5, fontWeight: 700, display: "flex", alignItems: "center", justifyContent: "center" }}>PDF</div>
      <div style={{ flex: 1, fontSize: 12, fontWeight: 600, color: colors.ink, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{STATEMENT.file}</div>
      <div style={{ fontSize: 11, color: colors.textMuted }}>1 / 6</div>
    </div>
    <div style={{ position: "absolute", left: 22, top: 50, right: 22, bottom: 0, background: "#fff", boxShadow: "0 1px 3px rgba(0,0,0,0.08)" }} />
    <div style={{ position: "absolute", left: PAGE_X, top: 72, right: W - PAGE_R }}>
      <div style={{ fontSize: 17, fontWeight: 700, color: colors.ink, marginBottom: 4 }}>{STATEMENT.title}</div>
      <div style={{ fontSize: 10.5, color: colors.textMuted }}>{STATEMENT.company}</div>
      <div style={{ fontSize: 9.5, color: colors.textMuted, marginTop: 6 }}>{STATEMENT.account} · {STATEMENT.period}</div>
    </div>
    <div style={{ position: "absolute", left: PAGE_X, right: W - PAGE_R, top: ROWS_TOP - 24, height: 20, display: "flex", justifyContent: "space-between", fontSize: 9, fontWeight: 600, color: colors.textMuted, borderBottom: `1px solid ${colors.line}` }}>
      <span style={{ width: 44 }}>DATE</span><span style={{ flex: 1 }}>DESCRIPTION</span><span>AMOUNT</span>
    </div>
    {STATEMENT.rows.map((r, i) => (
      <div key={i} style={{ position: "absolute", left: PAGE_X, right: W - PAGE_R, top: ROWS_TOP + i * ROW_H, height: ROW_H, display: "flex", alignItems: "center", fontSize: 10, color: colors.inkSoft, borderBottom: "1px solid #f0efec", fontVariantNumeric: "tabular-nums" }}>
        <span style={{ width: 44 }}>{r.date}</span><span style={{ flex: 1 }}>{r.text}</span><span>{r.amount}</span>
      </div>
    ))}
    <div style={{ position: "absolute", left: PAGE_X, right: W - PAGE_R, top: CLOSE_Y, height: CLOSE_H, display: "flex", alignItems: "center", borderTop: `1.5px solid ${colors.ink}`, fontSize: 12, fontWeight: 700, color: colors.ink }}>
      <span style={{ flex: 1 }}>{STATEMENT.closingLabel}</span>
    </div>
    {/* selection, figure and caret, right-aligned to the page's edge */}
    <div style={{ position: "absolute", right: W - PAGE_R - 2, top: CLOSE_Y + 7, height: 17, width: FIGURE_W * selection, background: "rgba(53,95,150,0.30)", borderRadius: 2 }} />
    <div style={{ position: "absolute", right: W - PAGE_R, top: CLOSE_Y, height: CLOSE_H, display: "flex", alignItems: "center", fontSize: 12, color: colors.ink, fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>
      {figure}
    </div>
    {caret && caret.on && <div style={{ position: "absolute", left: caret.x, top: CLOSE_Y + 6, width: 1.5, height: 18, background: colors.ink }} />}
  </div>
);

// ---- Layout on screen ----
const PANEL = { top: 270, bottom: 1010 };
const LEFT = { x: 80, w: 860 };
const RIGHT = { x: 980, w: 860 };
const ZOOM = 2.1; // the statement in the right panel, zoomed on the closing balance
const FOCUS = { x: W / 2, y: (72 + CLOSE_Y + CLOSE_H) / 2 }; // the statement point held at the panel centre: the page from its title to the closing balance
const PANEL_C = { x: RIGHT.x + RIGHT.w / 2, y: (PANEL.top + PANEL.bottom) / 2 };

// The pile: 6 x 3 thumbnails; the doctored statement lands in slot 8.
export const COLS = 6;
export const ROWS = 3;
export const THUMB = 0.41;
const PITCH = 250;
const GRID_X = (1920 - (COLS - 1) * PITCH - W * THUMB) / 2;
const GRID_Y = 290;
export const SLOT = 8;
/** Which of the hook's documents sits in grid slot i. */
export const pileDoc = (i: number) => DOCS[(i * 5 + 3) % DOCS.length];
/** The edited figure, with its tells: a touch lighter, a hair low, a little wider spaced. */
export const ForgedFigure: React.FC<{ chars?: number }> = ({ chars }) => (
  <span style={{ fontWeight: 600, transform: "translateY(0.6px)", letterSpacing: "0.02em", display: "inline-block" }}>
    {chars === undefined ? STATEMENT.after : STATEMENT.after.slice(STATEMENT.after.length - chars)}
  </span>
);
export const slotXY = (i: number) => ({ x: GRID_X + (i % COLS) * PITCH, y: GRID_Y + Math.floor(i / COLS) * PITCH });

export const TamperScene: React.FC<{ showText?: boolean }> = ({ showText = true }) => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const duration = atSeconds(T.length);
  const panelsP = land(interpolate(frame, [s(T.panelsIn), s(T.panelsIn) + 18], [0, 1], clamp));

  // The edit.
  const cursorP = interpolate(frame, [s(T.cursorIn), s(T.cursorAt)], [0, 1], { ...clamp, easing: even });
  const selection = interpolate(frame, [s(T.select), s(T.select) + 8], [0, 1], { ...clamp, easing: even }) * (frame < s(T.erase) ? 1 : 0);
  const typed = Math.round(interpolate(frame, [s(T.typeStart), s(T.typeEnd)], [0, STATEMENT.after.length], clamp));
  const figureX = PAGE_R - FIGURE_W;
  const caretX = frame < s(T.select)
    ? interpolate(cursorP, [0, 1], [figureX - 120, PAGE_R + 2])
    : frame < s(T.erase) ? figureX - 1 : PAGE_R + 2;
  const caretOn = frame >= s(T.cursorIn) && frame < s(T.shrinkStart) && (frame < s(T.erase) || Math.floor(t * 2.4) % 2 === 0 || (frame >= s(T.typeStart) && frame <= s(T.typeEnd)));
  // The forgery's tells: a touch lighter, a hair low, a little wider spaced.
  const figure = frame < s(T.erase)
    ? <span style={{ fontWeight: 700 }}>{STATEMENT.before}</span>
    : <ForgedFigure chars={typed} />;

  // The shrink into the pile.
  const q = interpolate(frame, [s(T.shrinkStart), s(T.shrinkEnd)], [0, 1], { ...clamp, easing: Easing.bezier(0.6, 0, 0.2, 1) });
  const zoom = Math.exp(interpolate(q, [0, 1], [Math.log(ZOOM), Math.log(THUMB)]));
  const target = slotXY(SLOT);
  const tx = interpolate(q, [0, 1], [PANEL_C.x - ZOOM * FOCUS.x, target.x]);
  const ty = interpolate(q, [0, 1], [PANEL_C.y - ZOOM * FOCUS.y, target.y]);
  // While zoomed, the statement is cropped to its panel; the crop opens as it shrinks.
  const clip = {
    top: PANEL.top * (1 - q), left: RIGHT.x * (1 - q), right: (1920 - RIGHT.x - RIGHT.w) * (1 - q), bottom: (1080 - PANEL.bottom) * (1 - q),
  };
  const panelsOut = interpolate(frame, [s(T.shrinkStart), s(T.shrinkStart) + 14], [1, 0], clamp);

  return (
    <Stage duration={duration} background={colors.paper} camera={[{ frame: 0, scale: 1 }, { frame: duration, scale: 1.02, ease: "even" }]}>
      <AbsoluteFill>
        {/* Left: the bad actor */}
        <div style={{ position: "absolute", left: LEFT.x, top: PANEL.top, width: LEFT.w, height: PANEL.bottom - PANEL.top, borderRadius: 28, background: BAD_ACTOR.bg, opacity: panelsP * panelsOut, transform: `translateX(${(1 - panelsP) * -60 - (1 - panelsOut) * 80}px)`, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
          <div style={{ width: 320, height: 320, borderRadius: "50%", border: `6px solid ${BAD_ACTOR.tint}`, boxShadow: "0 18px 40px rgba(18,32,61,0.16)", overflow: "visible" }}>
            <Silhouette role="badActor" size={308} tint={BAD_ACTOR.tint} bg={colors.white} t={t} fidget={1} />
          </div>
          <div style={{ marginTop: 34, fontFamily: fonts.ui, fontSize: 44, fontWeight: 700, color: colors.ink }}>{BAD_ACTOR.label}</div>
        </div>
        {/* Right: the desk the statement sits on */}
        <div style={{ position: "absolute", left: RIGHT.x, top: PANEL.top, width: RIGHT.w, height: PANEL.bottom - PANEL.top, borderRadius: 28, background: "#e6e4df", opacity: panelsP * panelsOut, transform: `translateX(${(1 - panelsP) * 60}px)` }} />

        {/* The pile the statement disappears into */}
        {Array.from({ length: COLS * ROWS }, (_, i) => {
          if (i === SLOT) return null;
          const d = pileDoc(i);
          const at = s(T.shrinkStart) + s(0.15) + Math.abs(i - SLOT) * s(T.pileStagger);
          const p = land(interpolate(frame, [at, at + 12], [0, 1], clamp));
          if (p <= 0) return null;
          const { x, y } = slotXY(i);
          return (
            <div key={i} style={{ position: "absolute", left: x, top: y, transformOrigin: "0 0", transform: `scale(${THUMB * (0.9 + 0.1 * p)})`, opacity: p }}>
              <DocWindow {...d} company={COPY2.company} width={W} height={H} />
            </div>
          );
        })}

        {/* The statement */}
        <div style={{ position: "absolute", inset: 0, clipPath: `inset(${clip.top}px ${clip.right}px ${clip.bottom}px ${clip.left}px round ${28 * (1 - q)}px)`, opacity: panelsP }}>
          <div style={{ position: "absolute", left: 0, top: 0, transformOrigin: "0 0", transform: `translate(${tx}px, ${ty}px) scale(${zoom})` }}>
            <Statement figure={figure} selection={selection} caret={{ x: caretX, on: caretOn }} />
          </div>
        </div>

        {showText && (
          <div style={{ position: "absolute", left: 0, right: 0, top: 70, display: "flex", flexDirection: "column", alignItems: "center", gap: 6 }}>
            <Title text={CAPTION3.line1} enterAt={s(T.line1In)} exit="none" align="center" style={{ fontFamily: fonts.display, fontSize: 56, fontWeight: 700, letterSpacing: "-0.015em", lineHeight: 1.15, color: colors.ink }} />
            <Title text={CAPTION3.line2} enterAt={s(T.line2In)} exit="none" align="center" style={{ fontFamily: fonts.display, fontSize: 56, fontWeight: 700, letterSpacing: "-0.015em", lineHeight: 1.15, color: colors.inkSoft }} />
          </div>
        )}
      </AbsoluteFill>
    </Stage>
  );
};
