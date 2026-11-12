// ProductHeader - the top band of every "how it works" section: the step tracker (always shown, it is how the
// viewer follows along), one headline, and a short line under it that changes with each beat (the old line lifts
// away as the new one rises).

import React from "react";
import { Easing, interpolate, useCurrentFrame } from "remotion";
import { STEPS } from "../data/upload";
import { colors, fonts } from "../theme";
import { s } from "../timing";
import { StepTracker } from "./StepTracker";
import { Title } from "./Title";

export type Beat = { text: string; at: number; accent?: boolean };

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
const ease = Easing.bezier(0.37, 0, 0.63, 1);

export const ProductHeader: React.FC<{
  step: number; headline: string; headlineIn: number; beats: Beat[]; showText: boolean;
  /** The step the previous section showed (null: no tracker before this section); the tracker animates from it. */
  fromStep?: number | null;
  /** The section's length in seconds: the headline and line lift away over its last moments. */
  length: number;
}> = ({ step, headline, headlineIn, beats, showText, fromStep = step, length }) => {
  const frame = useCurrentFrame();
  const hand = interpolate(frame, [s(0.1), s(0.65)], [0, 1], { ...clamp, easing: ease });
  const current = fromStep === null ? step : fromStep + (step - fromStep) * hand;
  const trackerIn = fromStep === null ? hand : 1;
  const out = interpolate(frame, [s(length) - 10, s(length)], [0, 1], { ...clamp, easing: Easing.bezier(0.5, 0, 0.9, 0.55) });
  return (
    <>
      <div style={{ position: "absolute", left: 0, right: 0, top: 30, display: "flex", justifyContent: "center" }}>
        <StepTracker steps={STEPS} current={current} p={trackerIn} />
      </div>
      {showText && (
        <div style={{ opacity: 1 - out, transform: `translateY(${-24 * out}px)` }}>
          <div style={{ position: "absolute", left: 0, right: 0, top: 104, display: "flex", justifyContent: "center" }}>
            <Title text={headline} enterAt={s(headlineIn)} exit="none" align="center" style={{ fontFamily: fonts.display, fontSize: 54, fontWeight: 700, letterSpacing: "-0.02em", color: colors.ink, lineHeight: 1.1 }} />
          </div>
          {beats.map((b, i) => {
            const next = beats[i + 1];
            if (frame < s(b.at)) return null;
            const out = next ? interpolate(frame, [s(next.at) - 6, s(next.at) + 2], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }) : 0;
            if (out >= 1) return null;
            return (
              <div key={i} style={{ position: "absolute", left: 0, right: 0, top: 176, display: "flex", justifyContent: "center", opacity: 1 - out, transform: `translateY(${-14 * out}px)` }}>
                <Title text={b.text} enterAt={s(b.at)} exit="none" align="center" style={{ fontFamily: fonts.display, fontSize: 34, fontWeight: 600, color: b.accent ? colors.steel : colors.inkSoft, lineHeight: 1.2 }} />
              </div>
            );
          })}
        </div>
      )}
    </>
  );
};
