// Hook v3 - "buried", full screen. The officer's screen fills the frame: PDF windows pile up, tabs
// squash to icons, pings stack in the corner. Every extra "a" of "Waaaaaaaaay" opens one more
// document. Then everything stops and dims, one big ping lands in the middle, and the shot holds
// for the voiceover's sigh. Times: OVERWHELM3 in timing.ts. Words: data/overwhelm.ts.

import { AbsoluteFill, Easing, interpolate, random, useCurrentFrame } from "remotion";
import { ChatToast } from "../components/ChatToast";
import { DocWindow } from "../components/DocWindow";
import { Stage } from "../components/Stage";
import { TabStrip } from "../components/TabStrip";
import { Title } from "../components/Title";
import { COPY2, DOCS, EXTRA_TABS, FINAL_PING, PINGS3, WAY3_DOCS, WAY3_MAX_A } from "../data/overwhelm";
import { canvas, colors, fonts } from "../theme";
import { FPS, OVERWHELM3 as T, atSeconds, s } from "../timing";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
const land = Easing.bezier(0.2, 0.9, 0.1, 1);
const rise = Easing.bezier(0.33, 1, 0.68, 1);

const TABS_H = 50;
const DESK_H = canvas.height - TABS_H;
const WIN = { w: 460, h: 590 };
const TOAST_W = 420;
const TOAST_STEP = 112;

// ---- When things happen (seconds) ----------------------------------------------------
const EXTRA_A = WAY3_MAX_A - 1;
/** How far through the stretch (0..1) the word is at progress u of its time: an ease-in-out
 * mixed with a steady pace by stretchEase. */
const stretchCurve = (u: number) => {
  const c = Math.max(0, Math.min(1, u));
  const eased = 0.5 - 0.5 * Math.cos(Math.PI * c);
  return (1 - T.stretchEase) * c + T.stretchEase * eased;
};
/** Seconds when letter j (0-based) has fully slid in: the inverse of stretchCurve, by bisection. */
const LETTER_TIMES = Array.from({ length: EXTRA_A }, (_, j) => {
  const target = (j + 1) / EXTRA_A;
  let lo = 0;
  let hi = 1;
  for (let k = 0; k < 30; k++) {
    const mid = (lo + hi) / 2;
    if (stretchCurve(mid) < target) lo = mid;
    else hi = mid;
  }
  return T.stretchStart + (T.stretchEnd - T.stretchStart) * hi;
});

const PRE = DOCS.length - WAY3_DOCS; // windows that open before "Waaaay"; the rest open during the stretch
/** The stretch's documents open as evenly spaced letters arrive. */
const STRETCH_DOC_TIMES = Array.from({ length: WAY3_DOCS }, (_, k) =>
  LETTER_TIMES[Math.min(EXTRA_A - 1, Math.round(((k + 0.5) * EXTRA_A) / WAY3_DOCS))]);
const DOC_TIMES = DOCS.map((_, i) =>
  i < PRE ? T.firstDocIn + (T.lastDocIn - T.firstDocIn) * Math.pow(i / Math.max(1, PRE - 1), T.ramp) : STRETCH_DOC_TIMES[i - PRE]);

// Each PDF opens a tab at the same moment; the extra tabs fill in between.
const TABS = (() => {
  const list: { label: string; pdf: boolean; at: number }[] = DOCS.map((d, i) => ({ label: d.file, pdf: true, at: DOC_TIMES[i] }));
  EXTRA_TABS.forEach((label, i) => list.push({ label, pdf: false, at: T.firstTabIn + ((T.lastTabIn - T.firstTabIn) * (i + 0.5)) / EXTRA_TABS.length }));
  return list.sort((a, b) => a.at - b.at);
})();

// Where windows land. The first ones keep to the right of the headline; the letter-driven ones go anywhere.
const PLACES = DOCS.map((_, i) => {
  const k = 0.88 + random(`k${i}`) * 0.14;
  const col = i < PRE ? 2 + (i % 3) : (i * 2) % 5;
  const x = -40 + col * 370 + (random(`x${i}`) - 0.5) * 140;
  const y = 10 + random(`y${i}`) * (DESK_H - WIN.h * k + 40);
  return { x: Math.min(x, canvas.width - WIN.w * k + 40), y, k };
});

const HEAD1 = { fontFamily: fonts.display, fontSize: 66, fontWeight: 700, letterSpacing: "-0.02em", lineHeight: 1.1, color: colors.ink } as const;
const BIG = { fontFamily: fonts.display, fontSize: 92, fontWeight: 700, letterSpacing: "-0.03em", lineHeight: 1.05, color: colors.ink } as const;

/** "W" + a's + "y". The word grows smoothly: the stretch's progress is continuous, and the a that is
 * arriving opens its width (and fades in) instead of popping, so "y" glides right. */
const A_EM = 0.6; // an "a" is narrower than this in Inter Bold, so the cap never clips a finished letter
const StretchWay: React.FC<{ frame: number }> = ({ frame }) => {
  const p = interpolate(frame, [s(T.wayIn), s(T.wayIn) + 14], [0, 1], { ...clamp, easing: rise });
  const u = (frame / FPS - T.stretchStart) / (T.stretchEnd - T.stretchStart);
  const grown = stretchCurve(u) * EXTRA_A; // e.g. 7.4 = seven a's in, the eighth 40% open
  const shown = Math.ceil(grown);
  return (
    <div
      style={{
        clipPath: "inset(-0.3em -100vw -0.3em -100vw)", ...BIG, whiteSpace: "nowrap",
        textShadow: "0 0 22px rgba(249,249,248,0.95), 0 0 8px rgba(249,249,248,0.95)",
      }}
    >
      <div style={{ transform: `translateY(${(1 - p) * 110}%)` }}>
        {COPY2.wayBefore}a
        {Array.from({ length: shown }, (_, j) => {
          const open = Math.max(0, Math.min(1, grown - j));
          return (
            <span key={j} style={{ display: "inline-block", overflow: "hidden", verticalAlign: "bottom", maxWidth: `${A_EM * open}em`, opacity: Math.min(1, open * 1.5) }}>
              a
            </span>
          );
        })}
        {COPY2.wayAfter}
      </div>
    </div>
  );
};

export const BuriedScene: React.FC<{ showText?: boolean }> = ({ showText = true }) => {
  const frame = useCurrentFrame();
  const duration = atSeconds(T.length);
  const popFrames = Math.max(1, T.docPop * FPS);
  const final = s(T.finalPingIn);

  const tabs = TABS.map((t) => ({ ...t, p: land(interpolate(frame, [t.at * FPS, t.at * FPS + 8], [0, 1], clamp)) }));
  const pings = PINGS3.map((_, i) => land(interpolate(frame, [s(T.pings[i] ?? 99), s(T.pings[i] ?? 99) + 12], [0, 1], clamp)));
  const out1 = interpolate(frame, [s(T.title1Out), s(T.title1Out) + s(0.35)], [0, 1], clamp);

  // The ending: dim and soften the pile, then the big ping lands.
  const dim = interpolate(frame, [final - s(T.dim), final], [0, 1], { ...clamp, easing: Easing.bezier(0.4, 0, 0.2, 1) });
  const ping = land(interpolate(frame, [final, final + 16], [0, 1], clamp));

  return (
    <AbsoluteFill style={{ background: colors.paper }}>
      <AbsoluteFill style={{ filter: dim > 0 ? `blur(${5 * dim}px)` : undefined }}>
        <Stage
          duration={duration}
          background="#e6e4df"
          origin="62% 50%"
          camera={[{ frame: 0, scale: 1 }, { frame: final, scale: T.pushIn, ease: "even" }]}
        >
          <AbsoluteFill>
            <div style={{ position: "absolute", left: 0, top: TABS_H, width: canvas.width, height: DESK_H }}>
              {DOCS.map((d, i) => {
                const p = land(interpolate(frame, [DOC_TIMES[i] * FPS, DOC_TIMES[i] * FPS + popFrames], [0, 1], clamp));
                if (p <= 0) return null;
                const { x, y, k } = PLACES[i];
                return (
                  <div key={i} style={{ position: "absolute", left: x, top: y, transformOrigin: "50% 40%", transform: `translateY(${(1 - p) * 40}px) scale(${k * (0.9 + 0.1 * p)})`, opacity: Math.min(1, p * 1.5) }}>
                    <DocWindow {...d} company={COPY2.company} width={WIN.w} height={WIN.h} />
                  </div>
                );
              })}
            </div>
          </AbsoluteFill>
        </Stage>

        {/* Screen chrome, the wash and the headlines stay put while the camera pushes into the pile. */}
        <AbsoluteFill>
            <TabStrip tabs={tabs} width={canvas.width} />

            {/* A wash of the page colour behind the headlines, so they read over the pile. */}
            {showText && (
              <div
                style={{
                  position: "absolute", left: 0, top: TABS_H, bottom: 0, width: 1250,
                  background: "linear-gradient(90deg, rgba(249,249,248,0.97) 0%, rgba(249,249,248,0.93) 48%, rgba(249,249,248,0) 100%)",
                }}
              />
            )}

            {PINGS3.map((m, i) => {
              const q = pings[i];
              if (q <= 0) return null;
              const above = pings.slice(i + 1).reduce((sum, v) => sum + v, 0);
              const fade = interpolate(above, [3, 4], [1, 0], clamp);
              return (
                <div key={i} style={{ position: "absolute", left: canvas.width - TOAST_W - 40, top: canvas.height - 40 - 90 - above * TOAST_STEP, transform: `translateX(${(1 - q) * (TOAST_W + 60)}px)`, opacity: fade }}>
                  <ChatToast {...m} width={TOAST_W} />
                </div>
              );
            })}

            {showText && (
              <>
                <div style={{ position: "absolute", left: 140, top: TABS_H, bottom: 0, display: "flex", alignItems: "center", opacity: 1 - out1, transform: `translateY(${-30 * out1}px)` }}>
                  <Title text={COPY2.title1} enterAt={s(T.title1In)} exit="none" style={HEAD1} />
                </div>
                <div style={{ position: "absolute", left: 140, top: TABS_H, bottom: 0, display: "flex", flexDirection: "column", justifyContent: "center" }}>
                  {frame >= s(T.wayIn) && <StretchWay frame={frame} />}
                  <Title text={COPY2.title2} enterAt={s(T.title2In)} exit="none" style={BIG} />
                  <Title text={COPY2.footnote} enterAt={s(T.footnoteIn)} exit="none" style={{ fontFamily: fonts.ui, fontSize: 38, fontWeight: 500, color: colors.inkSoft, marginTop: 32 }} />
                </div>
              </>
            )}
        </AbsoluteFill>
      </AbsoluteFill>

      {/* The ending */}
      <AbsoluteFill style={{ background: `rgba(18, 32, 61, ${0.5 * dim})` }} />
      {ping > 0 && (
        <AbsoluteFill style={{ alignItems: "center", justifyContent: "center" }}>
          <div style={{ transform: `translateY(${(1 - ping) * 80}px) scale(${2 * (0.88 + 0.12 * ping)})`, opacity: Math.min(1, ping * 1.8) }}>
            <ChatToast {...FINAL_PING} width={440} />
          </div>
        </AbsoluteFill>
      )}
    </AbsoluteFill>
  );
};
