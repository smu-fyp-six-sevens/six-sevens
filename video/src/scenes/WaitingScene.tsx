// Section 2 - "waiting". The loan officer and the customer as avatar circles on either side of a
// clock that races from 9:02 to 10:14. The officer's page count barely moves; the customer's inbox
// checks keep climbing. Times: WAITING in timing.ts. Words and people: data/waiting.ts.

import { AbsoluteFill, Easing, interpolate, useCurrentFrame } from "remotion";
import { Stage } from "../components/Stage";
import { Title } from "../components/Title";
import { CLOCK, COPY_WAIT, CUSTOMER, OFFICER } from "../data/waiting";
import { colors, fonts } from "../theme";
import { FPS, WAITING as T, atSeconds, s } from "../timing";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
const land = Easing.bezier(0.2, 0.9, 0.1, 1);
const even = Easing.bezier(0.37, 0, 0.63, 1);

const ROW_Y = 570; // centre line of the avatars and the clock
const AVATAR = 230;
const CLOCK_R = 170;

const fmtTime = (mins: number) => {
  const h = Math.floor(mins / 60);
  const m = Math.floor(mins % 60);
  const h12 = ((h + 11) % 12) + 1;
  return `${h12}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
};

const Person: React.FC<{
  x: number; p: number; initials: string; name: string; role: string; tint: string; status: string;
  dot: string; pulse?: number;
}> = ({ x, p, initials, name, role, tint, status, dot, pulse }) => (
  <div
    style={{
      position: "absolute", left: x - 260, width: 520, top: ROW_Y - AVATAR / 2, display: "flex", flexDirection: "column",
      alignItems: "center", opacity: Math.min(1, p * 1.6), transform: `translateY(${(1 - p) * 40}px)`, fontFamily: fonts.ui,
    }}
  >
    <div style={{ position: "relative", width: AVATAR, height: AVATAR }}>
      {pulse !== undefined && (
        <div
          style={{
            position: "absolute", inset: 0, borderRadius: "50%", border: `4px solid ${tint}`,
            transform: `scale(${1 + 0.35 * pulse})`, opacity: 0.55 * (1 - pulse),
          }}
        />
      )}
      <div
        style={{
          position: "absolute", inset: 0, borderRadius: "50%", background: tint, color: colors.white,
          display: "flex", alignItems: "center", justifyContent: "center", fontSize: 88, fontWeight: 700,
          letterSpacing: "-0.02em", boxShadow: "0 18px 40px rgba(18,32,61,0.18)",
        }}
      >
        {initials}
      </div>
    </div>
    <div style={{ marginTop: 34, fontSize: 40, fontWeight: 700, color: colors.ink, letterSpacing: "-0.01em" }}>{name}</div>
    <div style={{ marginTop: 6, fontSize: 24, color: colors.textMuted, whiteSpace: "nowrap" }}>{role}</div>
    <div
      style={{
        marginTop: 22, display: "flex", alignItems: "center", gap: 12, padding: "10px 20px", borderRadius: 999,
        background: colors.white, border: `1px solid ${colors.line}`, fontSize: 26, fontWeight: 600, color: colors.ink,
        fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap",
      }}
    >
      <span style={{ width: 12, height: 12, borderRadius: 6, background: dot }} />
      {status}
    </div>
  </div>
);

const Clock: React.FC<{ p: number; mins: number }> = ({ p, mins }) => {
  const minuteDeg = (mins % 60) * 6;
  const hourDeg = ((mins / 60) % 12) * 30;
  const ticks = Array.from({ length: 12 }, (_, i) => i);
  return (
    <div
      style={{
        position: "absolute", left: 960 - CLOCK_R, top: ROW_Y - CLOCK_R, width: CLOCK_R * 2, display: "flex",
        flexDirection: "column", alignItems: "center", opacity: Math.min(1, p * 1.6),
        transform: `scale(${0.85 + 0.15 * p})`, transformOrigin: "50% 50%",
      }}
    >
      <svg width={CLOCK_R * 2} height={CLOCK_R * 2} viewBox="-100 -100 200 200">
        <circle r="96" fill={colors.white} stroke={colors.ink} strokeWidth="5" />
        {ticks.map((i) => (
          <line key={i} x1="0" y1={i % 3 === 0 ? -86 : -88} x2="0" y2="-78" stroke={i % 3 === 0 ? colors.ink : colors.textMuted}
            strokeWidth={i % 3 === 0 ? 4 : 2.5} strokeLinecap="round" transform={`rotate(${i * 30})`} />
        ))}
        <line x1="0" y1="8" x2="0" y2="-46" stroke={colors.ink} strokeWidth="7" strokeLinecap="round" transform={`rotate(${hourDeg})`} />
        <line x1="0" y1="12" x2="0" y2="-72" stroke={colors.steel} strokeWidth="4.5" strokeLinecap="round" transform={`rotate(${minuteDeg})`} />
        <circle r="6" fill={colors.amber} />
      </svg>
      <div style={{ marginTop: 30, fontFamily: fonts.ui, fontSize: 52, fontWeight: 700, color: colors.ink, fontVariantNumeric: "tabular-nums", letterSpacing: "-0.01em", whiteSpace: "nowrap" }}>
        {fmtTime(mins)}
      </div>
    </div>
  );
};

export const WaitingScene: React.FC<{ showText?: boolean }> = ({ showText = true }) => {
  const frame = useCurrentFrame();
  const duration = atSeconds(T.length);
  const pop = (delay: number) => land(interpolate(frame, [s(T.avatarsIn + delay), s(T.avatarsIn + delay) + 16], [0, 1], clamp));

  // One "hour" of progress, 0..1, shared by the clock and both statuses.
  const hour = interpolate(frame, [s(T.clockStart), s(T.clockEnd)], [0, 1], { ...clamp, easing: even });
  const mins = CLOCK.from + (CLOCK.to - CLOCK.from) * hour;
  const page = Math.round(OFFICER.pageFrom + (OFFICER.pageTo - OFFICER.pageFrom) * hour);
  const checks = Math.round(CUSTOMER.checksFrom + (CUSTOMER.checksTo - CUSTOMER.checksFrom) * hour);
  const pulse = ((frame / FPS) % T.pulseEvery) / T.pulseEvery;

  return (
    <Stage duration={duration} background={colors.paper} camera={[{ frame: 0, scale: 1.0 }, { frame: duration, scale: 1.03, ease: "even" }]}>
      <AbsoluteFill>
        {/* The line "on the other end": a dashed wire from officer to customer, behind the clock. */}
        <div style={{ position: "absolute", left: 420 + AVATAR / 2 + 20, right: 1920 - 1500 + AVATAR / 2 + 20, top: ROW_Y - 2, borderTop: `4px dashed ${colors.line}`, opacity: pop(0.2) }} />
        <Person x={420} p={pop(0)} {...OFFICER} status={COPY_WAIT.officerStatus(page, OFFICER.pageTotal)} dot={colors.amber} />
        <Clock p={pop(0.1)} mins={mins} />
        <Person x={1500} p={pop(0.2)} {...CUSTOMER} status={COPY_WAIT.customerStatus(checks)} dot={colors.red} pulse={pop(0.2) >= 1 ? pulse : undefined} />

        {showText && (
          <div style={{ position: "absolute", left: 0, right: 0, top: 110, display: "flex", flexDirection: "column", alignItems: "center" }}>
            <Title text={COPY_WAIT.line1} enterAt={s(T.line1In)} exit="none" align="center"
              style={{ fontFamily: fonts.display, fontSize: 64, fontWeight: 700, letterSpacing: "-0.02em", lineHeight: 1.12, color: colors.ink }} />
            <Title text={COPY_WAIT.line2} enterAt={s(T.line2In)} exit="none" align="center"
              style={{ fontFamily: fonts.display, fontSize: 64, fontWeight: 700, letterSpacing: "-0.02em", lineHeight: 1.12, color: colors.inkSoft }} />
          </div>
        )}
      </AbsoluteFill>
    </Stage>
  );
};
