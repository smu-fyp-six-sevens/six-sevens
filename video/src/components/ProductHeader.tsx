// ProductHeader - the top band of every "how it works" section: the step tracker (always shown, it is how the
// viewer follows along), one headline, and a short line under it that changes with each beat (the old line lifts
// away as the new one rises).

import React from "react";
import { interpolate, useCurrentFrame } from "remotion";
import { STEPS } from "../data/upload";
import { colors, fonts } from "../theme";
import { s } from "../timing";
import { StepTracker } from "./StepTracker";
import { Title } from "./Title";

export type Beat = { text: string; at: number; accent?: boolean };

export const ProductHeader: React.FC<{ step: number; headline: string; headlineIn: number; beats: Beat[]; showText: boolean }> = ({ step, headline, headlineIn, beats, showText }) => {
  const frame = useCurrentFrame();
  return (
    <>
      <div style={{ position: "absolute", left: 0, right: 0, top: 30, display: "flex", justifyContent: "center" }}>
        <StepTracker steps={STEPS} current={step} />
      </div>
      {showText && (
        <>
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
        </>
      )}
    </>
  );
};
