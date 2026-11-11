// Master - the hook, visuals only (no soundtrack, no voiceover: you lay those in your editor).
// HOOK_VERSION in timing.ts picks the version; every time is a knob there, in seconds.

import { AbsoluteFill, Sequence } from "remotion";
import { BuriedScene } from "./scenes/BuriedScene";
import { OverwhelmScene } from "./scenes/OverwhelmScene";
import { PackageScene } from "./scenes/PackageScene";
import { TooManyScene } from "./scenes/TooManyScene";
import { colors } from "./theme";
import { HOOK, HOOK_VERSION, OVERWHELM, OVERWHELM3, atSeconds } from "./timing";

export const Master: React.FC<{ showText?: boolean }> = ({ showText = true }) => (
  <AbsoluteFill style={{ background: colors.paper }}>
    {HOOK_VERSION === "v3" ? (
      <Sequence durationInFrames={atSeconds(OVERWHELM3.length)}>
        <BuriedScene showText={showText} />
      </Sequence>
    ) : HOOK_VERSION === "v2" ? (
      <Sequence durationInFrames={atSeconds(OVERWHELM.length)}>
        <OverwhelmScene showText={showText} />
      </Sequence>
    ) : (
      <>
        <Sequence durationInFrames={atSeconds(HOOK.package.length)}>
          <PackageScene showText={showText} />
        </Sequence>
        <Sequence from={atSeconds(HOOK.package.length)} durationInFrames={atSeconds(HOOK.tooMany.length)}>
          <TooManyScene showText={showText} />
        </Sequence>
      </>
    )}
  </AbsoluteFill>
);
