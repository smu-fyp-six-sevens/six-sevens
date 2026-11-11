// Scene 1 - "Corporate loan applications need extensive documentation."
// The case's real package (20 files) drops into the portal's upload box, faster and faster,
// until the list runs off the bottom of the frame.

import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import { Stage } from "../components/Stage";
import { Title } from "../components/Title";
import { UploadBox } from "../components/UploadBox";
import { COPY, FILES } from "../data/hook";
import { colors, fonts } from "../theme";
import { HOOK, SCENES, s } from "../timing";
import { BOX_W, BOX_X, BOX_Y, U, rowState, scrollFor } from "./hookLayout";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;

export const PackageScene: React.FC<{ showText?: boolean }> = ({ showText = true }) => {
  const frame = useCurrentFrame();
  const duration = SCENES.package.duration;
  const { progress, fresh, rows } = rowState(frame);
  const scroll = scrollFor(rows);

  // The headline leaves upward over the scene's last 0.35 s.
  const out = interpolate(frame, [duration - s(0.35), duration], [0, 1], clamp);

  return (
    <Stage duration={duration} background={colors.paper}>
      <AbsoluteFill>
        <div style={{ position: "absolute", left: BOX_X, top: BOX_Y - scroll }}>
          <UploadBox files={FILES} progress={progress} fresh={fresh} u={U} width={BOX_W} />
        </div>
        {showText && (
          <div
            style={{
              position: "absolute",
              left: 120,
              top: 0,
              bottom: 0,
              display: "flex",
              alignItems: "center",
              opacity: 1 - out,
              transform: `translateY(${-30 * out}px)`,
            }}
          >
            <Title
              text={COPY.packageTitle}
              enterAt={s(HOOK.package.titleIn)}
              exit="none"
              style={{
                fontFamily: fonts.display,
                fontSize: 60,
                fontWeight: 700,
                letterSpacing: "-0.02em",
                lineHeight: 1.12,
                color: colors.ink,
              }}
            />
          </div>
        )}
      </AbsoluteFill>
    </Stage>
  );
};
