// Section 8 - "score" (how it works, step 3).
//   A. The case's real Risk Summary rises in, angled: the gauge is ringed, then the documents to review.
//   B. The cursor opens the bank statement's real review dialog. What was flagged, where (the forged figure,
//      boxed on the page), and why are tagged in turn.
//   C. The officer ticks the metadata finding as a false alarm, picks a reason, types a note and saves.
// Times: SCORE in timing.ts. Words: data/howitworks.ts. Screens: capture/story2.spec.ts.

import { AbsoluteFill, Easing, Img, interpolate, staticFile, useCurrentFrame } from "remotion";
import { Pointer } from "../components/Pointer";
import { ProductHeader } from "../components/ProductHeader";
import boxes from "../../public/ui/story/story2-risk.json";
import { SCORE_COPY, SCREENS_STORY2 } from "../data/howitworks";
import { colors, fonts } from "../theme";
import { FPS, SCORE as T, s } from "../timing";
import { Screen, Window } from "./PortalScene";
import { CLOSE_H, CLOSE_Y, FIGURE_W, ForgedFigure, H, PAGE_R, Statement, W } from "./TamperScene";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
const land = Easing.bezier(0.2, 0.9, 0.1, 1);
const even = Easing.bezier(0.37, 0, 0.63, 1);
const lerp = (p: number, a: number, b: number) => a + (b - a) * p;
const pr = (t: number, at: number) => interpolate(t, [at - 0.05, at + 0.05, at + 0.2], [0, 1, 0], clamp);

// Measured on the captures (CSS px).
const GAUGE = { x: 222, y: 318, r: 92 };
const DOCS = boxes.risk.documents;
const ROW1 = boxes.review1.rows[0];
const TICK = boxes.review1.wrong[3];
const SAVE = boxes.review1.save[0];
const REASONS = boxes.review2.reasons[0];
const PANE = { x: 37, y: 102, w: 737, h: 715 }; // the review's blank page
const PAGE_K = Math.min(PANE.w / W, PANE.h / H);
const PAGE_X = PANE.x + (PANE.w - W * PAGE_K) / 2;
const NOTE = { x: 1094, y: 556, w: 268, h: 30 }; // the note box in the dismissed capture
const STATUS = { x: 1124, y: 158, w: 122, h: 24 }; // "Unsaved changes"

const Tag: React.FC<{ x: number; y: number; w: number; h: number; label: string; p: number; tone?: string }> = ({ x, y, w, h, label, p, tone = colors.steel }) =>
  p <= 0 ? null : (
    <div style={{ position: "absolute", left: x, top: y, width: w, height: h, border: `3px solid ${tone}`, borderRadius: 6, opacity: p, transform: `scale(${1.08 - 0.08 * p})` }}>
      <span style={{ position: "absolute", left: -3, top: -26, font: `700 15px ${fonts.ui}`, color: "#fff", background: tone, padding: "3px 10px", borderRadius: "6px 6px 6px 0" }}>{label}</span>
    </div>
  );

export const ScoreScene: React.FC<{ showText?: boolean }> = ({ showText = true }) => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const inReview = t >= T.openAt + 0.25;

  // A
  const enterA = land(interpolate(t, [0, 0.8], [0, 1], clamp));
  const ring = land(interpolate(t, [T.gaugeAt, T.gaugeAt + 0.4], [0, 1], clamp));
  const toRow = interpolate(t, [T.openAt - 0.7, T.openAt - 0.05], [0, 1], { ...clamp, easing: even });
  const row = DOCS[0];

  // B and C
  const enterB = land(interpolate(t, [T.openAt + 0.25, T.openAt + 0.75], [0, 1], clamp));
  const what = land(interpolate(t, [T.whatAt, T.whatAt + 0.35], [0, 1], clamp));
  const where = land(interpolate(t, [T.whereAt, T.whereAt + 0.35], [0, 1], clamp));
  const why = land(interpolate(t, [T.whyAt, T.whyAt + 0.35], [0, 1], clamp));
  const tagsOut = interpolate(t, [T.tickAt - 0.5, T.tickAt - 0.2], [1, 0], clamp);
  const dismissed = interpolate(t, [T.tickAt + 0.1, T.tickAt + 0.25], [0, 1], clamp);
  const typing = interpolate(t, [T.noteAt + 0.4, T.saveAt - 0.4], [0, 1], clamp);
  const saved = land(interpolate(t, [T.saveAt + 0.05, T.saveAt + 0.3], [0, 1], clamp));
  // the cursor: to the tick, to the reasons, then to Save
  const cur = t < T.noteAt
    ? { x: lerp(interpolate(t, [T.tickAt - 0.7, T.tickAt], [0, 1], { ...clamp, easing: even }), 700, TICK.x + 8), y: lerp(interpolate(t, [T.tickAt - 0.7, T.tickAt], [0, 1], { ...clamp, easing: even }), 560, TICK.y + 8) }
    : t < T.saveAt - 0.7
      ? { x: lerp(interpolate(t, [T.noteAt - 0.1, T.noteAt + 0.3], [0, 1], { ...clamp, easing: even }), TICK.x + 8, REASONS.x + 300), y: lerp(interpolate(t, [T.noteAt - 0.1, T.noteAt + 0.3], [0, 1], { ...clamp, easing: even }), TICK.y + 8, REASONS.y + 70) }
      : { x: lerp(interpolate(t, [T.saveAt - 0.7, T.saveAt], [0, 1], { ...clamp, easing: even }), REASONS.x + 300, SAVE.x + SAVE.w / 2), y: lerp(interpolate(t, [T.saveAt - 0.7, T.saveAt], [0, 1], { ...clamp, easing: even }), REASONS.y + 70, SAVE.y + SAVE.h / 2) };
  const press = Math.max(pr(t, T.tickAt), pr(t, T.noteAt + 0.3), pr(t, T.saveAt));
  const zoom = lerp(interpolate(t, [T.tickAt - 0.3, T.tickAt + 0.6], [0, 1], { ...clamp, easing: even }), 1.02, 1.32);
  const focus = { x: lerp(zoom > 1.03 ? (zoom - 1.02) / 0.3 : 0, 720, 1090), y: lerp(zoom > 1.03 ? (zoom - 1.02) / 0.3 : 0, 380, 400) };

  return (
    <AbsoluteFill style={{ background: colors.paper }}>
      {!inReview ? (
        <Window enter={enterA} tilt={{ rx: lerp(enterA, 30, 7), ry: lerp(enterA, 12, 5), dy: lerp(enterA, 500, 0) }}>
          <Screen {...SCREENS_STORY2.risk} zoom={1.05} focus={{ x: 720, y: 400 }}>
            {ring > 0 && <div style={{ position: "absolute", left: GAUGE.x - GAUGE.r, top: GAUGE.y - GAUGE.r, width: GAUGE.r * 2, height: GAUGE.r * 2, borderRadius: "50%", border: `4px solid ${colors.red}`, opacity: ring, transform: `scale(${1.3 - 0.3 * ring})` }} />}
            {DOCS.map((d, i) => {
              const p = land(interpolate(t, [T.docsAt + 0.3 * i, T.docsAt + 0.3 * i + 0.3], [0, 1], clamp));
              return p > 0 ? <div key={i} style={{ position: "absolute", left: d.x, top: d.y, width: d.w, height: d.h, background: `rgba(53,95,150,${0.07 * p})`, borderLeft: `4px solid rgba(53,95,150,${p})` }} /> : null;
            })}
            {t >= T.openAt - 0.8 && <Pointer x={lerp(toRow, 900, row.x + row.w - 40)} y={lerp(toRow, 760, row.y + 46)} size={20} press={pr(t, T.openAt)} />}
          </Screen>
        </Window>
      ) : (
        <Window enter={enterB} tilt={{ rx: lerp(enterB, 8, 2), ry: lerp(enterB, -10, -2) }}>
          <Screen {...SCREENS_STORY2.review} zoom={zoom} focus={focus}>
            {dismissed > 0 && <Img src={staticFile(SCREENS_STORY2.dismissed.src)} style={{ position: "absolute", left: 0, top: 0, width: 1440, height: 900, opacity: dismissed }} />}
            {/* the statement on the review's page, the forged figure boxed: the "where" */}
            <div style={{ position: "absolute", left: PAGE_X, top: PANE.y, transformOrigin: "0 0", transform: `scale(${PAGE_K})` }}>
              <Statement figure={<ForgedFigure />} selection={0} caret={null} />
              <div style={{ position: "absolute", left: PAGE_R - FIGURE_W - 6, top: CLOSE_Y + 3, width: FIGURE_W + 12, height: CLOSE_H - 6, border: `2.5px solid ${colors.red}`, borderRadius: 3, background: "rgba(179,64,42,0.08)" }} />
            </div>
            {/* What, where, why */}
            <Tag x={ROW1.x + 80} y={ROW1.y + 24} w={ROW1.w - 76} h={44} label={SCORE_COPY.what} p={what * tagsOut} />
            <Tag x={PAGE_X + (PAGE_R - FIGURE_W - 14) * PAGE_K} y={PANE.y + (CLOSE_Y - 6) * PAGE_K} w={(FIGURE_W + 28) * PAGE_K} h={(CLOSE_H + 12) * PAGE_K} label={SCORE_COPY.where} p={where * tagsOut} tone={colors.red} />
            <Tag x={ROW1.x + 80} y={ROW1.y + 70} w={ROW1.w - 76} h={22} label={SCORE_COPY.why} p={why * tagsOut} tone={colors.amber} />
            {/* the note, typed */}
            {dismissed > 0 && typing < 1 && <div style={{ position: "absolute", left: NOTE.x + NOTE.w * typing, top: NOTE.y, width: NOTE.w * (1 - typing), height: NOTE.h, background: colors.white }} />}
            {saved > 0 && (
              <div style={{ position: "absolute", left: STATUS.x, top: STATUS.y, display: "flex", alignItems: "center", gap: 6, font: `600 12px ${fonts.ui}`, color: "#166d5f", background: "#e1f1ec", borderRadius: 999, padding: "3px 10px", transform: `scale(${1.3 - 0.3 * saved})`, transformOrigin: "100% 50%", boxShadow: `0 0 0 ${10 * (1 - saved)}px rgba(22,109,95,0.2)` }}>✓ {SCORE_COPY.saved}</div>
            )}
            {t >= T.tickAt - 0.8 && <Pointer x={cur.x} y={cur.y} size={18} press={press} />}
          </Screen>
        </Window>
      )}
      <ProductHeader step={2} headline={SCORE_COPY.headline} headlineIn={T.headlineIn} showText={showText}
        beats={SCORE_COPY.beats.map((b) => ({ ...b, at: T[b.at as keyof typeof T] }))} />
    </AbsoluteFill>
  );
};
