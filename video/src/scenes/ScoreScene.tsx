// Section 8 - "score" (how it works, step 3).
//   A. The case's real Risk Summary rises in, angled: the gauge is ringed, then the documents to review.
//   B. The cursor opens the bank statement's real review dialog. What was flagged, where (the forged figure,
//      boxed on the page), and why are tagged in turn.
//   C. The officer ticks the metadata finding as a false alarm, picks a reason, types a note and saves.
// Times: SCORE in timing.ts. Words: data/howitworks.ts. Screens: capture/story2.spec.ts.

import { AbsoluteFill, Easing, Img, interpolate, staticFile, useCurrentFrame } from "remotion";
import { Pointer } from "../components/Pointer";
import { ProductHeader } from "../components/ProductHeader";
import { shotSwipe, SWIPE } from "../components/swipe";
import { cameraAt } from "../components/keys";
import boxes from "../../public/ui/story/story2-risk.json";
import { ADDITIONAL_BOXES, SCORE_COPY, SCREENS_STORY2 } from "../data/howitworks";
import { blendHex, colors, fonts } from "../theme";
import { FPS, SCORE as T, s } from "../timing";
import { Screen, Window } from "./PortalScene";
import { CLOSE_H, CLOSE_Y, FIGURE_W, ForgedFigure, H, PAGE_R, Statement, W } from "./TamperScene";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
const land = Easing.bezier(0.2, 0.9, 0.1, 1);
const even = Easing.bezier(0.37, 0, 0.63, 1);
const lerp = (p: number, a: number, b: number) => a + (b - a) * p;
const pr = (t: number, at: number) => interpolate(t, [at - 0.05, at + 0.05, at + 0.2], [0, 1, 0], clamp);

// Measured on the captures (CSS px).
const NUM = boxes.risk.gauge[0]; // the "72" on the risk page (CSS px)
const NUM_FONT = 36; // its size on the page
const DOCS = boxes.risk.documents;
const PART = (cls: string) => boxes.review1.parts.find((p) => p.cls === cls)!;
const SUMMARY = PART("risk-finding-summary"); // the finding's title: the "what"
const RATIONALE = PART("risk-finding-rationale"); // its explanation: the "why"
const TICK = boxes.review1.wrong[3];
const SAVE = boxes.review1.save[0];
const REASONS = boxes.review2.reasons[0];
const PANE = { x: 37, y: 102, w: 737, h: 715 }; // the review's blank page
const PAGE_K = Math.min(PANE.w / W, PANE.h / H);
const PAGE_X = PANE.x + (PANE.w - W * PAGE_K) / 2;
const NOTE = boxes.review2.note[0] as { x: number; y: number; w: number; h: number }; // the note box in the dismissed capture
const NOTE_TEXT_W = 200; // the typed note's width in the capture (CSS px)
const STATUS = { x: 1124, y: 158, w: 122, h: 24 }; // "Unsaved changes"

/** A box around a part of the screen, filled with a tint and outlined, its label just outside it on one side so it
 * never covers the text. Big and bold, so it reads at a glance. */
const Tag: React.FC<{ x: number; y: number; w: number; h: number; label: string; p: number; tone?: string; tint?: string; side?: "left" | "right" | "top" }> = ({ x, y, w, h, label, p, tone = colors.steel, tint = "rgba(53,95,150,0.12)", side = "right" }) =>
  p <= 0 ? null : (
    <div style={{ position: "absolute", left: x, top: y, width: w, height: h, border: `3.5px solid ${tone}`, borderRadius: 6, background: tint, boxShadow: `0 0 0 ${6 * (1 - p)}px ${tone}33, 0 6px 18px ${tone}40`, opacity: p, transform: `scale(${1.08 - 0.08 * p})` }}>
      <span style={{ position: "absolute", ...(side === "top" ? { right: -3.5, bottom: "calc(100% + 8px)", transform: `scale(${0.8 + 0.2 * p})`, transformOrigin: "100% 100%" } : { top: "50%", [side === "right" ? "left" : "right"]: "calc(100% + 10px)", transform: `translateY(-50%) scale(${0.8 + 0.2 * p})` }), font: `800 19px ${fonts.ui}`, letterSpacing: "0.02em", textTransform: "uppercase", color: "#fff", background: tone, padding: "6px 14px", borderRadius: 8, whiteSpace: "nowrap", boxShadow: `0 6px 16px ${tone}55` }}>{label}</span>
    </div>
  );

export const ScoreScene: React.FC<{ showText?: boolean }> = ({ showText = true }) => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const swipeRisk = shotSwipe(t, 0, T.openAt + 0.25 + SWIPE);
  const swipeReview = shotSwipe(t, T.openAt + 0.25, T.addAt + SWIPE);
  const swipeAdd = shotSwipe(t, T.addAt, T.length, { exit: false });
  const nowP = land(interpolate(t, [T.nowAt, T.nowAt + 0.4], [0, 1], clamp));
  // In on the gauge, out to the documents to review.
  const riskCam = cameraAt(t, [
    [0, 1.0, 720, 420],
    [T.gaugeAt - 0.3, 1.0, 720, 420],
    [T.gaugeAt + 0.5, 1.7, 330, 300],
    [T.docsAt - 0.1, 1.7, 330, 300],
    [T.docsAt + 0.7, 1.12, 740, 560],
  ]);
  // In on the score line, then out along the recommendations.
  const addCam = cameraAt(t, [
    [T.addAt, 1.0, 720, 420],
    [T.nowAt - 0.1, 1.0, 720, 420],
    [T.nowAt + 0.5, 1.8, 420, 284],
    [T.recsStart - 0.1, 1.8, 420, 284],
    [T.recsStart + 0.8, 1.15, 740, 470],
  ]);

  // A
  const ring = land(interpolate(t, [T.gaugeAt, T.gaugeAt + 0.4], [0, 1], clamp));
  // a slow pulse, about one breath every 1.2 s
  const beat = 0.5 - 0.5 * Math.cos(((t - T.gaugeAt) / 1.2) * 2 * Math.PI);
  const toRow = interpolate(t, [T.openAt - 0.7, T.openAt - 0.05], [0, 1], { ...clamp, easing: even });
  const row = DOCS[0];

  // B and C
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
  const reviewCam = cameraAt(t, [
    [T.openAt + 0.25, 1.02, 720, 380],
    [T.whatAt - 0.2, 1.02, 720, 380],
    [T.whatAt + 0.5, 1.14, 740, 330],
    [T.tickAt - 0.4, 1.14, 740, 330],
    [T.tickAt + 0.6, 1.32, 1090, 400],
  ]);
  const zoom = reviewCam.zoom;
  const focus = { x: reviewCam.x, y: reviewCam.y };

  return (
    <AbsoluteFill style={{ background: colors.paper }}>
      {swipeRisk !== null && (
        <Window enter={1} swipe={swipeRisk}>
          <Screen {...SCREENS_STORY2.risk} zoom={riskCam.zoom} focus={{ x: riskCam.x, y: riskCam.y }}>
            {ring > 0 && (
              <>
                <div style={{ position: "absolute", left: NUM.x - 2, top: NUM.y + 2, width: NUM.w + 4, height: NUM.h - 6, background: colors.white }} />
                {/* a soft red glow breathing behind the score... */}
                <div style={{ position: "absolute", left: NUM.x + NUM.w / 2 - 70, top: NUM.y + NUM.h / 2 - 70, width: 140, height: 140, borderRadius: "50%", background: "radial-gradient(circle, rgba(179,64,42,0.32) 0%, rgba(179,64,42,0.12) 45%, rgba(179,64,42,0) 70%)", opacity: ring * (0.55 + 0.45 * beat), transform: `scale(${0.85 + 0.3 * beat})` }} />
                {/* ...and the number itself, redrawn over the page's own, flashing slowly from ink to red as it swells */}
                <div style={{ position: "absolute", left: NUM.x - 20, top: NUM.y, width: NUM.w + 40, height: NUM.h, display: "flex", alignItems: "center", justifyContent: "center", font: `700 ${NUM_FONT}px ${fonts.ui}`, letterSpacing: "-0.03em", color: blendHex(colors.ink, colors.red, beat * ring), transform: `scale(${1 + 0.14 * beat * ring})`, textShadow: `0 0 ${14 * beat * ring}px rgba(179,64,42,0.55)` }}>
                  {NUM.text}
                </div>
              </>
            )}
            {DOCS.map((d, i) => {
              const p = land(interpolate(t, [T.docsAt + 0.3 * i, T.docsAt + 0.3 * i + 0.3], [0, 1], clamp));
              return p > 0 ? <div key={i} style={{ position: "absolute", left: d.x, top: d.y, width: d.w, height: d.h, background: `rgba(53,95,150,${0.07 * p})`, borderLeft: `4px solid rgba(53,95,150,${p})` }} /> : null;
            })}
            {t >= T.openAt - 0.8 && <Pointer x={lerp(toRow, 900, row.x + row.w - 40)} y={lerp(toRow, 760, row.y + 46)} size={20} press={pr(t, T.openAt)} />}
          </Screen>
        </Window>
      )}
      {swipeReview !== null && (
        <Window enter={1} swipe={swipeReview}>
          <Screen {...SCREENS_STORY2.review} zoom={zoom} focus={focus}>
            {dismissed > 0 && <Img src={staticFile(SCREENS_STORY2.dismissed.src)} style={{ position: "absolute", left: 0, top: 0, width: 1440, height: 900, opacity: dismissed }} />}
            {/* the statement on the review's page, the forged figure boxed: the "where" */}
            <div style={{ position: "absolute", left: PAGE_X, top: PANE.y, transformOrigin: "0 0", transform: `scale(${PAGE_K})` }}>
              <Statement figure={<ForgedFigure />} selection={0} caret={null} />
              <div style={{ position: "absolute", left: PAGE_R - FIGURE_W - 6, top: CLOSE_Y + 3, width: FIGURE_W + 12, height: CLOSE_H - 6, border: `2.5px solid ${colors.red}`, borderRadius: 3, background: "rgba(179,64,42,0.08)" }} />
            </div>
            {/* What, where, why */}
            <Tag x={SUMMARY.x - 6} y={SUMMARY.y - 4} w={SUMMARY.w + 12} h={SUMMARY.h + 8} label={SCORE_COPY.what} p={what * tagsOut} side="top" />
            <Tag x={PAGE_X + (PAGE_R - FIGURE_W - 14) * PAGE_K} y={PANE.y + (CLOSE_Y - 2) * PAGE_K} w={(FIGURE_W + 28) * PAGE_K} h={(CLOSE_H + 4) * PAGE_K} label={SCORE_COPY.where} p={where * tagsOut} tone={colors.red} tint="rgba(179,64,42,0.12)" side="left" />
            <Tag x={RATIONALE.x - 6} y={RATIONALE.y - 3} w={RATIONALE.w + 12} h={RATIONALE.h + 6} label={SCORE_COPY.why} p={why * tagsOut} tone={colors.amber} tint="rgba(198,130,10,0.14)" />
            {/* the note, typed */}
            {dismissed > 0 && typing < 1 && (
              <>
                <div style={{ position: "absolute", left: NOTE.x + 9 + NOTE_TEXT_W * typing, top: NOTE.y + 4, width: NOTE_TEXT_W * (1 - typing) + 2, height: NOTE.h - 8, background: colors.white }} />
                <div style={{ position: "absolute", left: NOTE.x + 9 + NOTE_TEXT_W * typing, top: NOTE.y + 6, width: 1.5, height: NOTE.h - 12, background: colors.ink, opacity: Math.floor(t * 3) % 2 === 0 || typing > 0 ? 1 : 0 }} />
              </>
            )}
            {saved > 0 && (
              <div style={{ position: "absolute", left: STATUS.x, top: STATUS.y, display: "flex", alignItems: "center", gap: 6, font: `600 12px ${fonts.ui}`, color: "#166d5f", background: "#e1f1ec", borderRadius: 999, padding: "3px 10px", transform: `scale(${1.3 - 0.3 * saved})`, transformOrigin: "100% 50%", boxShadow: `0 0 0 ${10 * (1 - saved)}px rgba(22,109,95,0.2)` }}>✓ {SCORE_COPY.saved}</div>
            )}
            {t >= T.tickAt - 0.8 && <Pointer x={cur.x} y={cur.y} size={18} press={press} />}
          </Screen>
        </Window>
      )}
      {swipeAdd !== null && (
        <Window enter={1} swipe={swipeAdd}>
          <Screen {...SCREENS_STORY2.additional} zoom={addCam.zoom} focus={{ x: addCam.x, y: addCam.y }}>
            <Tag {...ADDITIONAL_BOXES.now} label="Risk if the documents support the client" p={nowP} tone={colors.steel} />
            {ADDITIONAL_BOXES.items.map((b, i) => {
              const p = land(interpolate(t, [T.recsStart + 0.45 * i, T.recsStart + 0.45 * i + 0.3], [0, 1], clamp));
              return p > 0 ? <div key={i} style={{ position: "absolute", left: b.x, top: b.y, width: b.w, height: b.h, background: `rgba(53,95,150,${0.06 * p})`, borderLeft: `4px solid rgba(53,95,150,${p})` }} /> : null;
            })}
          </Screen>
        </Window>
      )}
      <ProductHeader step={2} fromStep={1} length={T.length} headline={SCORE_COPY.headline} headlineIn={T.headlineIn} showText={showText}
        beats={SCORE_COPY.beats.map((b) => ({ ...b, at: T[b.at as keyof typeof T] }))} />
    </AbsoluteFill>
  );
};
