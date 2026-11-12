// Section 10 - "difference": the bookend to section 2. The same Loan Officer and Customer, after the portal: the
// clock barely moves, the officer's review completes, an answer travels down the line, and the customer, calm now,
// gets the good-news ping (answering the hook's last ping). Times: DIFFERENCE in timing.ts. Words: data/howitworks.ts.

import { AbsoluteFill, Easing, interpolate, useCurrentFrame } from "remotion";
import { ChatToast } from "../components/ChatToast";
import { Stage } from "../components/Stage";
import { Title } from "../components/Title";
import { DIFFERENCE_COPY as C } from "../data/howitworks";
import { CLOCK } from "../data/waiting";
import { colors, fonts } from "../theme";
import { DIFFERENCE as T, FPS, atSeconds, s } from "../timing";
import { AVATAR, Clock, Person, ROW_Y } from "./WaitingScene2";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
const land = Easing.bezier(0.2, 0.9, 0.1, 1);
const even = Easing.bezier(0.37, 0, 0.63, 1);
const pop = Easing.bezier(0.34, 1.56, 0.64, 1);

const LINE = { from: 400 + AVATAR / 2 + 20, to: 1520 - AVATAR / 2 - 20 };
const CAPTION = { fontFamily: fonts.display, fontSize: 50, fontWeight: 700, letterSpacing: "-0.015em", lineHeight: 1.16 } as const;

export const DifferenceScene: React.FC<{ showText?: boolean }> = ({ showText = true }) => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const duration = atSeconds(T.length);
  const popIn = (delay: number) => land(interpolate(frame, [s(T.avatarsIn + delay), s(T.avatarsIn + delay) + 16], [0, 1], clamp));

  // The clock barely moves: a few minutes, not an hour.
  const mins = CLOCK.from + 6 * interpolate(t, [T.clockStart, T.clockEnd], [0, 1], { ...clamp, easing: even });
  const done = t >= T.doneAt;
  const send = interpolate(t, [T.sendAt, T.newsAt], [0, 1], { ...clamp, easing: even });
  const news = land(interpolate(t, [T.newsAt, T.newsAt + 0.35], [0, 1], { ...clamp, easing: pop }));
  const happy = t >= T.newsAt;

  return (
    <Stage duration={duration} background={colors.paper} camera={[{ frame: 0, scale: 1 }, { frame: duration, scale: 1.04, ease: "even" }]}>
      <AbsoluteFill>
        {/* the line between them; solid once the answer is sent, with the answer travelling along it */}
        <div style={{ position: "absolute", left: LINE.from, width: LINE.to - LINE.from, top: ROW_Y - 2, borderTop: `4px ${t >= T.sendAt ? "solid" : "dashed"} ${t >= T.sendAt ? colors.steel : colors.line}`, opacity: popIn(0.2) }} />
        {t >= T.sendAt && t < T.newsAt + 0.1 && (
          <div style={{ position: "absolute", left: LINE.from + (LINE.to - LINE.from) * send - 14, top: ROW_Y - 14, width: 28, height: 28, borderRadius: 14, background: colors.steel, boxShadow: "0 0 18px 6px rgba(53,95,150,0.35)" }} />
        )}
        <Person x={400} p={popIn(0)} role="officer" t={t} status={done ? `✓ ${C.officerDone}` : C.officerBusy} dot={done ? "#166d5f" : colors.steel} />
        <Clock p={popIn(0.1)} mins={mins} showTime={false} />
        <Person x={1520} p={popIn(0.2)} role="customer" t={t} status={happy ? `✓ ${C.customerDone}` : C.customerWaiting} dot={happy ? "#166d5f" : colors.amber} happy={happy} />
        {news > 0 && (
          <div style={{ position: "absolute", left: 1520 - 250, top: ROW_Y - AVATAR / 2 - 150, transform: `scale(${0.6 + 0.4 * news})`, transformOrigin: "50% 100%", opacity: Math.min(1, news * 1.5) }}>
            <ChatToast {...C.news} width={500} />
          </div>
        )}
        {showText && (
          <div style={{ position: "absolute", left: 0, right: 0, top: 64, display: "flex", flexDirection: "column", alignItems: "center", gap: 10 }}>
            <Title text={C.line1} enterAt={s(T.line1In)} exit="none" align="center" style={{ ...CAPTION, color: colors.ink }} />
            <Title text={C.line2} enterAt={s(T.line2In)} exit="none" align="center" style={{ ...CAPTION, fontSize: 40, color: colors.inkSoft }} />
            <Title text={C.line3} enterAt={s(T.line3In)} exit="none" align="center" style={{ ...CAPTION, fontSize: 40, color: colors.steel }} />
          </div>
        )}
      </AbsoluteFill>
    </Stage>
  );
};
