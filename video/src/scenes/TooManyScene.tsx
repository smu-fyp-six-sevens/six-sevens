// Scene 2 - "...and oftentimes there are waaaaay too many documents to review for a case."
// Opens on scene 1's last frame, pulls back until all 20 files of the one case fit in the frame,
// while "Way" stretches into "Waaaaaaaaay".

import { AbsoluteFill, Easing, interpolate, useCurrentFrame } from "remotion";
import { Stage } from "../components/Stage";
import { Title } from "../components/Title";
import { UploadBox } from "../components/UploadBox";
import { COPY, FILES } from "../data/hook";
import { colors, fonts } from "../theme";
import { HOOK, SCENES, s } from "../timing";
import { BOX_W, BOX_X, BOX_Y, FIT, U, scrollFor } from "./hookLayout";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
const pull = Easing.bezier(0.2, 0.9, 0.1, 1); // slowInLand: leaves the cut at speed, settles long
const rise = Easing.bezier(0.33, 1, 0.68, 1); // the Title's maskReveal curve

const BIG = {
  fontFamily: fonts.display,
  fontSize: 96,
  fontWeight: 700,
  letterSpacing: "-0.03em",
  lineHeight: 1.08,
  color: colors.ink,
} as const;

/** "Way" whose "a" stretches from one letter to wayMaxA, a letter at a time. */
const StretchWay: React.FC<{ frame: number }> = ({ frame }) => {
  const t = HOOK.tooMany;
  const p = interpolate(frame, [s(t.wayIn), s(t.wayIn) + 14], [0, 1], { ...clamp, easing: rise });
  const stretch = interpolate(frame, [s(t.stretchStart), s(t.stretchEnd)], [0, 1], {
    ...clamp,
    easing: Easing.bezier(0.5, 0, 0.5, 1),
  });
  const n = 1 + Math.round((COPY.wayMaxA - 1) * stretch);
  return (
    <div style={{ clipPath: "inset(-0.1em -100vw -0.2em -100vw)", ...BIG, whiteSpace: "nowrap" }}>
      <div style={{ transform: `translateY(${(1 - p) * 110}%)` }}>
        {COPY.wayBefore}
        {"a".repeat(n)}
        {COPY.wayAfter}
      </div>
    </div>
  );
};

export const TooManyScene: React.FC<{ showText?: boolean }> = ({ showText = true }) => {
  const frame = useCurrentFrame();
  const t = HOOK.tooMany;
  const duration = SCENES.tooMany.duration;

  // From scene 1's last frame (all rows in, scrolled) to the whole list fitted on the right.
  const q = interpolate(frame, [0, s(t.pullBackEnd)], [0, 1], { ...clamp, easing: pull });
  const startY = BOX_Y - scrollFor(FILES.length);
  const x = interpolate(q, [0, 1], [BOX_X, FIT.x]);
  const y = interpolate(q, [0, 1], [startY, FIT.y]);
  const k = interpolate(q, [0, 1], [1, FIT.k]);
  // After the pull-back, an even drift keeps the shot alive.
  const drift = interpolate(frame, [s(t.pullBackEnd), duration], [0, -14], clamp);

  const done = FILES.map(() => 1);
  const none = FILES.map(() => 0);

  return (
    <Stage duration={duration} background={colors.paper}>
      <AbsoluteFill>
        <div
          style={{
            position: "absolute",
            left: 0,
            top: 0,
            transformOrigin: "0 0",
            transform: `translate(${x}px, ${y + drift}px) scale(${k})`,
          }}
        >
          <UploadBox files={FILES} progress={done} fresh={none} u={U} width={BOX_W} />
        </div>
        {showText && (
          <div style={{ position: "absolute", left: 120, top: 300, display: "flex", flexDirection: "column", gap: 0 }}>
            <Title
              text={COPY.kicker}
              enterAt={s(t.kickerIn)}
              exit="none"
              style={{ fontFamily: fonts.ui, fontSize: 30, fontWeight: 600, color: colors.textMuted, marginBottom: 26 }}
            />
            <StretchWay frame={frame} />
            <Title text={COPY.tooManyLine2} enterAt={s(t.line2In)} exit="none" style={BIG} />
            <Title
              text={COPY.footnote}
              enterAt={s(t.footnoteIn)}
              exit="none"
              style={{ fontFamily: fonts.ui, fontSize: 36, fontWeight: 500, color: colors.inkSoft, marginTop: 40 }}
            />
          </div>
        )}
      </AbsoluteFill>
    </Stage>
  );
};
