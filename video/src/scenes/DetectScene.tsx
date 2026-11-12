// Section 6 - "detect" (how it works, step 2).
//   A. The lead agent's card: its investigation plan types out, line by line.
//   B. Six specialist checks branch out under it and run across the 20 files; four turn up a finding.
//   C. The two catches: the doctored closing balance, and an address that differs between two documents.
// Rebuilt in the portal's style. Times: DETECT in timing.ts. Words: data/howitworks.ts.

import { AbsoluteFill, Easing, interpolate, useCurrentFrame } from "remotion";
import { ProductHeader } from "../components/ProductHeader";
import { shotSwipe } from "../components/swipe";
import { cameraAt } from "../components/keys";
import { CATCHES, CHECKS, DETECT_COPY, PLAN } from "../data/howitworks";
import { colors, fonts } from "../theme";
import { DETECT as T, FPS, s } from "../timing";
import { WIN, Window } from "./PortalScene";
import { CLOSE_H, CLOSE_Y, FIGURE_W, ForgedFigure, PAGE_R, Statement } from "./TamperScene";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
const land = Easing.bezier(0.2, 0.9, 0.1, 1);
const pop = Easing.bezier(0.34, 1.56, 0.64, 1); // a little overshoot

const Spinner: React.FC<{ f: number; size?: number }> = ({ f, size = 18 }) => (
  <svg width={size} height={size} viewBox="0 0 20 20" style={{ transform: `rotate(${f * 14}deg)` }}>
    <circle cx="10" cy="10" r="7.5" fill="none" stroke="#dbe3ee" strokeWidth="2.5" />
    <path d="M10 2.5 A7.5 7.5 0 0 1 17.5 10" fill="none" stroke={colors.steel} strokeWidth="2.5" strokeLinecap="round" />
  </svg>
);

const Badge: React.FC<{ tone: "high" | "ok" | "work"; children: React.ReactNode; scale?: number }> = ({ tone, children, scale = 1 }) => {
  const c = tone === "high" ? { bg: colors.red, fg: "#fff" } : tone === "ok" ? { bg: "#e1f1ec", fg: "#166d5f" } : { bg: "#e4ebf4", fg: colors.steel };
  return <span style={{ display: "inline-flex", alignItems: "center", gap: 6, font: `700 14px ${fonts.ui}`, padding: "4px 12px", borderRadius: 999, background: c.bg, color: c.fg, transform: `scale(${scale})`, transformOrigin: "0 50%" }}>{children}</span>;
};

// ---- Layout inside the window (film px) ----
const LEAD = { x: 470, y: 36, w: 660, h: 236 };
const TILE = { w: 232, h: 222, gap: 21, y: 340 };
const tileX = (i: number) => (WIN.w - (CHECKS.length * TILE.w + (CHECKS.length - 1) * TILE.gap)) / 2 + i * (TILE.w + TILE.gap);
const FINISH_ORDER = [2, 0, 4, 1, 5, 3]; // which check finishes first...
const startAt = (i: number) => T.specialistsIn + 0.12 * i;
const endAt = (i: number) => T.checksEnd - 1.7 + (1.7 * FINISH_ORDER.indexOf(i)) / (CHECKS.length - 1);

export const DetectScene: React.FC<{ showText?: boolean }> = ({ showText = true }) => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const leadP = land(interpolate(t, [T.planIn, T.planIn + 0.45], [0, 1], clamp));
  const planChars = PLAN.reduce((n, l) => n + l.length, 0);
  const typed = Math.round(interpolate(t, [T.planIn + 0.4, T.planEnd], [0, planChars], clamp));
  const planDone = t >= T.planEnd;
  const lineP = interpolate(t, [T.specialistsIn - 0.2, T.specialistsIn + 0.3], [0, 1], clamp);
  const away = interpolate(t, [T.catchAt - 0.15, T.catchAt + 0.35], [0, 1], { ...clamp, easing: Easing.bezier(0.5, 0, 0.9, 0.55) });
  const catchP = land(interpolate(t, [T.catchAt + 0.1, T.catchAt + 0.6], [0, 1], clamp));
  const boxP = land(interpolate(t, [T.catchAt + 0.6, T.catchAt + 0.9], [0, 1], clamp));
  const addrP = land(interpolate(t, [T.addressAt, T.addressAt + 0.5], [0, 1], clamp));
  const hlA = land(interpolate(t, [T.addressAt + 0.5, T.addressAt + 0.75], [0, 1], clamp));
  const hlB = land(interpolate(t, [T.addressAt + 0.8, T.addressAt + 1.05], [0, 1], clamp));
  const neq = land(interpolate(t, [T.addressAt + 1.1, T.addressAt + 1.35], [0, 1], { ...clamp, easing: pop }));

  // The camera: close on the plan, out as the checks branch, in on each catch, out to show both.
  const cam = cameraAt(t, [
    [0, 1.45, WIN.w / 2, LEAD.y + LEAD.h / 2],
    [T.specialistsIn - 0.1, 1.45, WIN.w / 2, LEAD.y + LEAD.h / 2],
    [T.specialistsIn + 0.9, 1.0, WIN.w / 2, WIN.h / 2],
    [T.catchAt, 1.0, WIN.w / 2, WIN.h / 2],
    [T.catchAt + 0.9, 1.18, 410, 395],
    [T.addressAt + 0.2, 1.18, 410, 395],
    [T.addressAt + 0.9, 1.18, 1190, 395],
    [T.addressAt + 2.4, 1.18, 1190, 395],
    [T.addressAt + 3.4, 1.0, WIN.w / 2, WIN.h / 2],
  ]);
  let left = typed;
  return (
    <AbsoluteFill style={{ background: colors.paper }}>
      <Window enter={1} swipe={shotSwipe(t, 0, T.length) ?? 0}>
        <AbsoluteFill style={{ background: "#f3f2ef", fontFamily: fonts.ui, transformOrigin: "0 0", transform: `translate(${WIN.w / 2 - cam.x * cam.zoom}px, ${WIN.h / 2 - cam.y * cam.zoom}px) scale(${cam.zoom})` }}>
          {/* A + B: the plan and the checks */}
          {away < 1 && (
            <AbsoluteFill style={{ opacity: 1 - away, transform: `translateY(${-50 * away}px)` }}>
              <div style={{ position: "absolute", left: LEAD.x, top: LEAD.y, width: LEAD.w, height: LEAD.h, background: colors.white, borderRadius: 16, border: `1px solid ${colors.line}`, boxShadow: "0 14px 34px rgba(18,32,61,0.10)", padding: "22px 28px", boxSizing: "border-box", opacity: leadP, transform: `translateY(${(1 - leadP) * 30}px)` }}>
                <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
                  <span style={{ width: 34, height: 34, borderRadius: 9, background: colors.ink, color: "#fff", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 16 }}>◆</span>
                  <span style={{ fontSize: 24, fontWeight: 700, color: colors.ink }}>Lead agent</span>
                  <span style={{ flex: 1 }} />
                  {planDone ? <Badge tone="ok">Plan ready</Badge> : <Badge tone="work"><Spinner f={frame} size={14} />Planning</Badge>}
                </div>
                {PLAN.map((line, i) => {
                  const show = Math.max(0, Math.min(line.length, left));
                  left -= line.length;
                  return (
                    <div key={i} style={{ display: "flex", gap: 10, fontSize: 18, lineHeight: "30px", color: colors.ink, opacity: show > 0 ? 1 : 0.0 }}>
                      <span style={{ color: colors.steel, fontWeight: 700 }}>{i + 1}.</span>
                      <span>{line.slice(0, show)}{show > 0 && show < line.length && <span style={{ color: colors.steel }}>▍</span>}</span>
                    </div>
                  );
                })}
              </div>
              {/* the branches */}
              <svg width={WIN.w} height={TILE.y} style={{ position: "absolute", left: 0, top: 0, opacity: lineP }}>
                <path d={`M ${WIN.w / 2} ${LEAD.y + LEAD.h} V ${(LEAD.y + LEAD.h + TILE.y) / 2} M ${tileX(0) + TILE.w / 2} ${(LEAD.y + LEAD.h + TILE.y) / 2} H ${tileX(CHECKS.length - 1) + TILE.w / 2}`} stroke={colors.boxDash} strokeWidth="3" fill="none" strokeDasharray={`${2000 * lineP} 2000`} />
                {CHECKS.map((_, i) => <path key={i} d={`M ${tileX(i) + TILE.w / 2} ${(LEAD.y + LEAD.h + TILE.y) / 2} V ${TILE.y}`} stroke={colors.boxDash} strokeWidth="3" fill="none" />)}
              </svg>
              {CHECKS.map((c, i) => {
                const p = land(interpolate(t, [startAt(i), startAt(i) + 0.35], [0, 1], { ...clamp, easing: pop }));
                if (p <= 0) return null;
                const run = interpolate(t, [startAt(i) + 0.2, endAt(i)], [0, 1], clamp);
                const done = t >= endAt(i);
                const hit = done && c.found > 0;
                const flash = interpolate(t, [endAt(i), endAt(i) + 0.4], [1, 0], clamp);
                const wiggle = hit ? Math.sin((t - endAt(i)) * 40) * 3 * flash : 0;
                return (
                  <div key={c.name} style={{ position: "absolute", left: tileX(i), top: TILE.y, width: TILE.w, height: TILE.h, boxSizing: "border-box", padding: "20px 20px", background: colors.white, borderRadius: 16, border: `2px solid ${hit ? colors.red : done ? "#9fd0c3" : colors.line}`, boxShadow: hit ? `0 0 0 ${8 * flash}px rgba(179,64,42,0.18), 0 12px 28px rgba(18,32,61,0.10)` : "0 12px 28px rgba(18,32,61,0.08)", transform: `scale(${0.85 + 0.15 * p}) rotate(${wiggle}deg)`, opacity: Math.min(1, p * 1.5) }}>
                    <div style={{ fontSize: 23, fontWeight: 700, color: colors.ink, lineHeight: 1.15, minHeight: 54 }}>{c.name}</div>
                    <div style={{ fontSize: 14, color: colors.textMuted, marginTop: 4, minHeight: 36 }}>{c.agent}</div>
                    <div style={{ height: 6, borderRadius: 3, background: "#eceef2", marginTop: 12, overflow: "hidden" }}>
                      <div style={{ height: 6, width: `${run * 100}%`, background: hit ? colors.red : done ? "#166d5f" : colors.steel }} />
                    </div>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: 14 }}>
                      <span style={{ fontSize: 14, color: colors.inkSoft, fontVariantNumeric: "tabular-nums" }}>{Math.round(run * 20)} / 20 files</span>
                      {!done ? <Spinner f={frame} /> : hit ? <Badge tone="high" scale={1 + 0.3 * flash}>{c.found} finding</Badge> : <Badge tone="ok">Clean</Badge>}
                    </div>
                  </div>
                );
              })}
            </AbsoluteFill>
          )}

          {/* C: the catches */}
          {catchP > 0 && (
            <>
              <div style={{ position: "absolute", left: 60, top: 50, width: 700, height: 690, background: colors.white, borderRadius: 18, border: `1px solid ${colors.line}`, boxShadow: "0 16px 40px rgba(18,32,61,0.12)", overflow: "hidden", opacity: catchP, transform: `translateY(${(1 - catchP) * 40}px)` }}>
                <div style={{ padding: "22px 26px" }}><Badge tone="high">{CATCHES.figure.label}</Badge></div>
                <div style={{ position: "absolute", left: 0, top: 84, width: 700, height: 420, overflow: "hidden", background: "#e6e4df" }}>
                  <div style={{ position: "absolute", left: 350 - 1.9 * 220, top: 250 - 1.9 * (CLOSE_Y + 4), transformOrigin: "0 0", transform: "scale(1.9)" }}>
                    <Statement figure={<ForgedFigure />} selection={0} caret={null} />
                    {boxP > 0 && <div style={{ position: "absolute", left: PAGE_R - FIGURE_W - 6, top: CLOSE_Y + 3, width: FIGURE_W + 12, height: CLOSE_H - 6, border: `2.5px solid ${colors.red}`, borderRadius: 3, background: "rgba(179,64,42,0.08)", opacity: boxP, transform: `scale(${1.4 - 0.4 * boxP})` }} />}
                  </div>
                </div>
                <div style={{ position: "absolute", left: 26, right: 26, top: 530, display: "flex", gap: 14, alignItems: "flex-start", opacity: boxP }}>
                  <span style={{ font: `700 13px ${fonts.ui}`, color: "#fff", background: colors.red, borderRadius: 4, padding: "4px 10px", marginTop: 3 }}>HIGH</span>
                  <span style={{ fontSize: 21, fontWeight: 600, color: colors.ink, lineHeight: 1.35 }}>{CATCHES.figure.finding}</span>
                </div>
              </div>
              <div style={{ position: "absolute", left: 840, top: 50, width: 700, height: 690, background: colors.white, borderRadius: 18, border: `1px solid ${colors.line}`, boxShadow: "0 16px 40px rgba(18,32,61,0.12)", opacity: addrP, transform: `translateY(${(1 - addrP) * 40}px)` }}>
                <div style={{ padding: "22px 26px" }}><Badge tone="high">{CATCHES.address.label}</Badge></div>
                {[CATCHES.address.a, CATCHES.address.b].map((d, i) => {
                  const hl = i === 0 ? hlA : hlB;
                  return (
                    <div key={i} style={{ position: "absolute", left: 30, right: 30, top: 96 + i * 196, height: 150, borderRadius: 12, border: `1px solid ${colors.line}`, background: "#fbfbfa", padding: "18px 22px", boxSizing: "border-box" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 14, color: colors.inkSoft }}>
                        <span style={{ font: `700 9px ${fonts.ui}`, color: "#fff", background: colors.red, borderRadius: 3, padding: "3px 5px" }}>PDF</span>{d.doc}
                      </div>
                      <div style={{ fontSize: 14, color: colors.textMuted, marginTop: 16 }}>{d.label}</div>
                      <div style={{ position: "relative", display: "inline-block", fontSize: 26, fontWeight: 600, color: colors.ink, marginTop: 4 }}>
                        {d.value}
                        {hl > 0 && <div style={{ position: "absolute", left: -8, top: -4, right: -8, bottom: -4, border: `2.5px solid ${colors.red}`, borderRadius: 5, background: "rgba(179,64,42,0.08)", opacity: hl, transform: `scale(${1.2 - 0.2 * hl})` }} />}
                      </div>
                    </div>
                  );
                })}
                {neq > 0 && <div style={{ position: "absolute", left: 350 - 24, top: 96 + 150 + 23 - 24, width: 48, height: 48, borderRadius: 24, background: colors.red, color: "#fff", fontSize: 30, fontWeight: 700, display: "flex", alignItems: "center", justifyContent: "center", transform: `scale(${neq})`, boxShadow: "0 6px 16px rgba(179,64,42,0.35)" }}>≠</div>}
                <div style={{ position: "absolute", left: 26, right: 26, top: 530, display: "flex", gap: 14, alignItems: "flex-start", opacity: neq }}>
                  <span style={{ font: `700 13px ${fonts.ui}`, color: "#fff", background: colors.red, borderRadius: 4, padding: "4px 10px", marginTop: 3 }}>HIGH</span>
                  <span style={{ fontSize: 21, fontWeight: 600, color: colors.ink, lineHeight: 1.35 }}>{CATCHES.address.finding}</span>
                </div>
              </div>
            </>
          )}
        </AbsoluteFill>
      </Window>
      <ProductHeader step={3} fromStep={2} length={T.length} headline={DETECT_COPY.headline} headlineIn={T.headlineIn} showText={showText}
        beats={DETECT_COPY.beats.map((b) => ({ ...b, at: T[b.at as keyof typeof T] }))} />
    </AbsoluteFill>
  );
};
