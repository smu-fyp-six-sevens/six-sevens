// Master - the hook, visuals only (no soundtrack, no voiceover: you lay those in your editor).
// Scene starts and lengths come from HOOK in timing.ts.

import { AbsoluteFill, Sequence } from "remotion";
import { PackageScene } from "./scenes/PackageScene";
import { TooManyScene } from "./scenes/TooManyScene";
import { colors } from "./theme";
import { SCENES } from "./timing";

export const Master: React.FC<{ showText?: boolean }> = ({ showText = true }) => (
  <AbsoluteFill style={{ background: colors.paper }}>
    <Sequence from={SCENES.package.start} durationInFrames={SCENES.package.duration}>
      <PackageScene showText={showText} />
    </Sequence>
    <Sequence from={SCENES.tooMany.start} durationInFrames={SCENES.tooMany.duration}>
      <TooManyScene showText={showText} />
    </Sequence>
  </AbsoluteFill>
);
