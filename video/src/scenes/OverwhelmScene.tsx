// Hook v2 - "Corporate loan applications require extensive documentation, and oftentimes there are
// waaaaay too many documents to review for a case." No app yet: the officer's screen gets buried.
// PDF windows pile up, browser tabs squash down to icons, and pings from other teams stack up.
// Every time is a knob in OVERWHELM (timing.ts); every string is in data/overwhelm.ts.

import { AbsoluteFill, Easing, interpolate, random, useCurrentFrame } from "remotion";
import { ChatToast } from "../components/ChatToast";
import { DocWindow } from "../components/DocWindow";
import { Stage } from "../components/Stage";
import { TabStrip } from "../components/TabStrip";
import { Title } from "../components/Title";
import { COPY2, DOCS, EXTRA_TABS, PINGS } from "../data/overwhelm";
import { colors, fonts } from "../theme";
import { FPS, OVERWHELM as T, atSeconds, s } from "../timing";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
const land = Easing.bezier(0.2, 0.9, 0.1, 1);
const rise = Easing.bezier(0.33, 1, 0.68, 1);

// The officer's screen, on the right of the frame. The headlines sit to its left.
const SCREEN = { x: 820, y: 70, w: 1040, h: 940 };
const TABS_H = 46;
const DESK_H = SCREEN.h - TABS_H;
const WIN = { w: 440, h: 560 };
const TOAST_W = 400;
const TOAST_STEP = 110;

/** n times between a and b (seconds), closer and closer together. */
const accelerating = (n: number, a: number, b: number) =>
  Array.from({ length: n }, (_, i) => a + (b - a) * Math.pow(i / Math.max(1, n - 1), T.ramp));

const DOC_TIMES = accelerating(DOCS.length, T.firstDocIn, T.lastDocIn);

// Each PDF opens a tab; the extra tabs open in between.
const TABS = (() => {
  const list: { label: string; pdf: boolean }[] = [];
  let extra = 0;
  DOCS.forEach((d, i) => {
    list.push({ label: d.file, pdf: true });
    if (i % 2 === 1 && extra < EXTRA_TABS.length) list.push({ label: EXTRA_TABS[extra++], pdf: false });
  });
  while (extra < EXTRA_TABS.length) list.push({ label: EXTRA_TABS[extra++], pdf: false });
  return list;
})();
const TAB_TIMES = accelerating(TABS.length, T.firstTabIn, T.lastTabIn);

// Where each window lands: spread over the desktop, later ones overlapping earlier ones.
const PLACES = DOCS.map((d, i) => {
  const k = 0.86 + random(`k${i}`) * 0.14;
  const col = i % 3;
  const x = -40 + col * 300 + (random(`x${i}`) - 0.5) * 160;
  const y = 10 + (random(`y${i}`) * (DESK_H - WIN.h * k + 60));
  return { x: Math.min(x, SCREEN.w - WIN.w * k + 60), y, k };
});

const StretchWay: React.FC<{ frame: number }> = ({ frame }) => {
  const p = interpolate(frame, [s(T.wayIn), s(T.wayIn) + 14], [0, 1], { ...clamp, easing: rise });
  const stretch = interpolate(frame, [s(T.stretchStart), s(T.stretchEnd)], [0, 1], { ...clamp, easing: Easing.bezier(0.5, 0, 0.5, 1) });
  const n = 1 + Math.round((COPY2.wayMaxA - 1) * stretch);
  return (
    <div style={{ clipPath: "inset(-0.1em -100vw -0.2em -100vw)", ...BIG, whiteSpace: "nowrap" }}>
      <div style={{ transform: `translateY(${(1 - p) * 110}%)` }}>
        {COPY2.wayBefore}{"a".repeat(n)}{COPY2.wayAfter}
      </div>
    </div>
  );
};

const BIG = { fontFamily: fonts.display, fontSize: 84, fontWeight: 700, letterSpacing: "-0.03em", lineHeight: 1.06, color: colors.ink } as const;
const HEAD1 = { fontFamily: fonts.display, fontSize: 64, fontWeight: 700, letterSpacing: "-0.02em", lineHeight: 1.1, color: colors.ink } as const;

export const OverwhelmScene: React.FC<{ showText?: boolean }> = ({ showText = true }) => {
  const frame = useCurrentFrame();
  const duration = atSeconds(T.length);
  const popFrames = Math.max(1, T.docPop * FPS);

  const tabs = TABS.map((t, i) => ({ ...t, p: land(interpolate(frame, [TAB_TIMES[i] * FPS, TAB_TIMES[i] * FPS + 8], [0, 1], clamp)) }));
  const pings = PINGS.map((_, i) => land(interpolate(frame, [s(T.pings[i] ?? 99), s(T.pings[i] ?? 99) + 12], [0, 1], clamp)));

  const out1 = interpolate(frame, [s(T.title1Out), s(T.title1Out) + s(0.35)], [0, 1], clamp);

  return (
    <Stage
      duration={duration}
      background={colors.paper}
      origin="68% 50%"
      camera={[{ frame: 0, scale: 1 }, { frame: duration, scale: T.pushIn, ease: "even" }]}
    >
      <AbsoluteFill>
        {/* The officer's screen */}
        <div
          style={{
            position: "absolute", left: SCREEN.x, top: SCREEN.y, width: SCREEN.w, height: SCREEN.h,
            borderRadius: 16, overflow: "hidden", background: "#e6e4df",
            boxShadow: "0 30px 70px rgba(18,32,61,0.16)", border: "1px solid rgba(18,32,61,0.10)",
          }}
        >
          <TabStrip tabs={tabs} width={SCREEN.w} />
          <div style={{ position: "absolute", left: 0, top: TABS_H, width: SCREEN.w, height: DESK_H }}>
            {DOCS.map((d, i) => {
              const p = land(interpolate(frame, [DOC_TIMES[i] * FPS, DOC_TIMES[i] * FPS + popFrames], [0, 1], clamp));
              if (p <= 0) return null;
              const { x, y, k } = PLACES[i];
              return (
                <div
                  key={i}
                  style={{
                    position: "absolute", left: x, top: y, transformOrigin: "50% 40%",
                    transform: `translateY(${(1 - p) * 40}px) scale(${k * (0.92 + 0.08 * p)})`,
                    opacity: Math.min(1, p * 1.5),
                  }}
                >
                  <DocWindow {...d} company={COPY2.company} width={WIN.w} height={WIN.h} />
                </div>
              );
            })}
            {/* Pings stack in the bottom-right corner; each new one pushes the others up. */}
            {PINGS.map((m, i) => {
              const q = pings[i];
              if (q <= 0) return null;
              const above = pings.slice(i + 1).reduce((sum, v) => sum + v, 0);
              const fade = interpolate(above, [3, 4], [1, 0], clamp);
              return (
                <div
                  key={i}
                  style={{
                    position: "absolute", left: SCREEN.w - TOAST_W - 24, top: DESK_H - 24 - 86 - above * TOAST_STEP,
                    transform: `translateX(${(1 - q) * (TOAST_W + 40)}px)`, opacity: fade,
                  }}
                >
                  <ChatToast {...m} width={TOAST_W} />
                </div>
              );
            })}
          </div>
        </div>

        {showText && (
          <>
            <div style={{ position: "absolute", left: 140, top: 0, bottom: 0, display: "flex", alignItems: "center", opacity: 1 - out1, transform: `translateY(${-30 * out1}px)` }}>
              <Title text={COPY2.title1} enterAt={s(T.title1In)} exit="none" style={HEAD1} />
            </div>
            <div style={{ position: "absolute", left: 140, top: 0, bottom: 0, display: "flex", flexDirection: "column", justifyContent: "center" }}>
              {frame >= s(T.wayIn) && <StretchWay frame={frame} />}
              <Title text={COPY2.title2} enterAt={s(T.title2In)} exit="none" style={BIG} />
              <Title text={COPY2.footnote} enterAt={s(T.footnoteIn)} exit="none" style={{ fontFamily: fonts.ui, fontSize: 36, fontWeight: 500, color: colors.inkSoft, marginTop: 32 }} />
            </div>
          </>
        )}
      </AbsoluteFill>
    </Stage>
  );
};
