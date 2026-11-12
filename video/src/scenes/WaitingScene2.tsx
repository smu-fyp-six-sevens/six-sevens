// Section 2 v2 - "A loan officer may spend over an hour staring at details on the screen, while the
// loan customer anxiously waits on the other end for any news of approval."
// Silhouette avatars for the two roles (no names) either side of a clock racing 9:02 to 10:14; the
// script line is the caption, in two halves. Times: WAITING2 in timing.ts. Words: data/waiting.ts.

import { AbsoluteFill, Easing, interpolate, useCurrentFrame } from "remotion";
import { Silhouette } from "../components/Silhouette";
import { Stage } from "../components/Stage";
import { Title } from "../components/Title";
import { CAPTION, CLOCK, COPY_WAIT, CUSTOMER, OFFICER, ROLES } from "../data/waiting";
import { colors, fonts } from "../theme";
import { FPS, WAITING2 as T, atSeconds, s } from "../timing";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
const land = Easing.bezier(0.2, 0.9, 0.1, 1);
const even = Easing.bezier(0.37, 0, 0.63, 1);

export const ROW_Y = 620;
/** Where the officer and the customer stand. */
export const PEOPLE_X = { officer: 520, customer: 1400 };
export const AVATAR = 330;
const CLOCK_R = 165;

const fmtTime = (mins: number) => {
  const h = Math.floor(mins / 60);
  const m = Math.floor(mins % 60);
  return `${((h + 11) % 12) + 1}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
};

const Typing: React.FC<{ t: number; p: number }> = ({ t, p }) => (
  <div
    style={{
      position: "absolute", right: -36, top: -24, display: "flex", gap: 8, padding: "16px 20px", borderRadius: 24,
      borderBottomLeftRadius: 6, background: colors.white, boxShadow: "0 10px 24px rgba(18,32,61,0.14)",
      border: `1px solid ${colors.line}`, opacity: p, transform: `scale(${0.8 + 0.2 * p})`, transformOrigin: "0% 100%",
    }}
  >
    {[0, 1, 2].map((i) => {
      const b = Math.max(0, Math.sin((t * 2 * Math.PI) / 1.2 - i * 0.9));
      return <span key={i} style={{ width: 12, height: 12, borderRadius: 6, background: colors.textMuted, opacity: 0.4 + 0.6 * b, transform: `translateY(${-5 * b}px)` }} />;
    })}
  </div>
);

export const Person: React.FC<{
  x: number; p: number; role: "officer" | "customer"; t: number; status: string; dot: string; pulse?: number; typing?: number; happy?: boolean;
}> = ({ x, p, role, t, status, dot, pulse, typing, happy }) => {
  const r = ROLES[role];
  return (
    <div
      style={{
        position: "absolute", left: x - 260, width: 520, top: ROW_Y - AVATAR / 2, display: "flex", flexDirection: "column",
        alignItems: "center", opacity: Math.min(1, p * 1.6), transform: `translateY(${(1 - p) * 40}px)`, fontFamily: fonts.ui,
      }}
    >
      <div style={{ position: "relative", width: AVATAR, height: AVATAR }}>
        {pulse !== undefined && (
          <div style={{ position: "absolute", inset: 0, borderRadius: "50%", border: `5px solid ${r.tint}`, transform: `scale(${1 + 0.3 * pulse})`, opacity: 0.5 * (1 - pulse) }} />
        )}
        <div style={{ position: "absolute", inset: 0, borderRadius: "50%", boxShadow: "0 18px 40px rgba(18,32,61,0.16)", border: `5px solid ${r.tint}` }} />
        <div style={{ position: "absolute", inset: 0 }}>
          <Silhouette role={role} size={AVATAR} tint={r.tint} bg={r.bg} t={t} fidget={T.fidget} happy={happy} />
        </div>
        {typing !== undefined && typing > 0 && <Typing t={t} p={typing} />}
      </div>
      <div style={{ marginTop: 30, fontSize: 46, fontWeight: 700, color: colors.ink, letterSpacing: "-0.01em" }}>{r.label}</div>
      <div
        style={{
          marginTop: 18, display: "flex", alignItems: "center", gap: 12, padding: "12px 24px", borderRadius: 999,
          background: colors.white, border: `1px solid ${colors.line}`, fontSize: 30, fontWeight: 600, color: colors.ink,
          fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap",
        }}
      >
        <span style={{ width: 12, height: 12, borderRadius: 6, background: dot }} />
        {status}
      </div>
    </div>
  );
};

export const Clock: React.FC<{ p: number; mins: number; showTime?: boolean }> = ({ p, mins, showTime = true }) => (
  <div
    style={{
      position: "absolute", left: 960 - CLOCK_R, top: ROW_Y - CLOCK_R, width: CLOCK_R * 2, display: "flex", flexDirection: "column",
      alignItems: "center", opacity: Math.min(1, p * 1.6), transform: `scale(${0.85 + 0.15 * p})`,
    }}
  >
    <svg width={CLOCK_R * 2} height={CLOCK_R * 2} viewBox="-100 -100 200 200">
      <circle r="96" fill={colors.white} stroke={colors.ink} strokeWidth="5" />
      {Array.from({ length: 12 }, (_, i) => (
        <line key={i} x1="0" y1={i % 3 === 0 ? -86 : -88} x2="0" y2="-78" stroke={i % 3 === 0 ? colors.ink : colors.textMuted}
          strokeWidth={i % 3 === 0 ? 4 : 2.5} strokeLinecap="round" transform={`rotate(${i * 30})`} />
      ))}
      <line x1="0" y1="8" x2="0" y2="-46" stroke={colors.ink} strokeWidth="7" strokeLinecap="round" transform={`rotate(${((mins / 60) % 12) * 30})`} />
      <line x1="0" y1="12" x2="0" y2="-72" stroke={colors.steel} strokeWidth="4.5" strokeLinecap="round" transform={`rotate(${(mins % 60) * 6})`} />
      <circle r="6" fill={colors.amber} />
    </svg>
    {showTime && <div style={{ marginTop: 26, fontFamily: fonts.ui, fontSize: 48, fontWeight: 700, color: colors.ink, fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>
      {fmtTime(mins)}
    </div>}
  </div>
);

const CAPTION_STYLE = { fontFamily: fonts.display, fontSize: 50, fontWeight: 700, letterSpacing: "-0.015em", lineHeight: 1.16 } as const;

export const WaitingScene2: React.FC<{ showText?: boolean }> = ({ showText = true }) => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const duration = atSeconds(T.length);
  const pop = (delay: number) => land(interpolate(frame, [s(T.avatarsIn + delay), s(T.avatarsIn + delay) + 16], [0, 1], clamp));

  const hour = interpolate(frame, [s(T.clockStart), s(T.clockEnd)], [0, 1], { ...clamp, easing: even });
  const mins = CLOCK.from + (CLOCK.to - CLOCK.from) * hour;
  const page = Math.round(OFFICER.pageFrom + (OFFICER.pageTo - OFFICER.pageFrom) * hour);
  const checks = Math.round(CUSTOMER.checksFrom + (CUSTOMER.checksTo - CUSTOMER.checksFrom) * hour);

  // The customer's anxiety switches on with their half of the caption.
  const waitFrom = s(T.customerLineIn);
  const pulse = frame >= waitFrom ? (((frame - waitFrom) / FPS) % T.pulseEvery) / T.pulseEvery : undefined;
  const typing = land(interpolate(frame, [waitFrom + 6, waitFrom + 18], [0, 1], clamp));

  return (
    <Stage duration={duration} background={colors.paper} camera={[{ frame: 0, scale: 1 }, { frame: duration, scale: 1.03, ease: "even" }]}>
      <AbsoluteFill>
        <div style={{ position: "absolute", left: PEOPLE_X.officer + AVATAR / 2 + 20, right: 1920 - PEOPLE_X.customer + AVATAR / 2 + 20, top: ROW_Y - 2, borderTop: `4px dashed ${colors.line}`, opacity: pop(0.2) }} />
        <Person x={PEOPLE_X.officer} p={pop(0)} role="officer" t={t} status={COPY_WAIT.officerStatus(page, OFFICER.pageTotal)} dot={colors.amber} />
        <Clock p={pop(0.1)} mins={mins} />
        <Person x={PEOPLE_X.customer} p={pop(0.2)} role="customer" t={t} status={COPY_WAIT.customerStatus(checks)} dot={colors.red} pulse={pulse} typing={typing} />

        {showText && (
          <div style={{ position: "absolute", left: 0, right: 0, top: 64, display: "flex", flexDirection: "column", alignItems: "center", gap: 10 }}>
            <Title text={CAPTION.officer} enterAt={s(T.officerLineIn)} exit="none" align="center" style={{ ...CAPTION_STYLE, color: colors.ink }} />
            <Title text={CAPTION.customer} enterAt={s(T.customerLineIn)} exit="none" align="center" style={{ ...CAPTION_STYLE, color: colors.inkSoft }} />
          </div>
        )}
      </AbsoluteFill>
    </Stage>
  );
};
