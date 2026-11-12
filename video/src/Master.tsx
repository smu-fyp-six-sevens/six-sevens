// Master - the film so far (hook, then section 2), visuals only (no soundtrack, no voiceover: you lay those in your editor).
// HOOK_VERSION in timing.ts picks the version; every time is a knob there, in seconds.

import { AbsoluteFill, Sequence } from "remotion";
import { BuriedScene } from "./scenes/BuriedScene";
import { OverwhelmScene } from "./scenes/OverwhelmScene";
import { PackageScene } from "./scenes/PackageScene";
import { PortalScene } from "./scenes/PortalScene";
import { PortalScene2 } from "./scenes/PortalScene2";
import { TamperScene } from "./scenes/TamperScene";
import { TooManyScene } from "./scenes/TooManyScene";
import { UploadScene } from "./scenes/UploadScene";
import { DetectScene } from "./scenes/DetectScene";
import { TraceScene } from "./scenes/TraceScene";
import { ScoreScene } from "./scenes/ScoreScene";
import { WaitingScene } from "./scenes/WaitingScene";
import { WaitingScene2 } from "./scenes/WaitingScene2";
import { colors } from "./theme";
import { HOOK, HOOK_VERSION, OVERWHELM, OVERWHELM3, PORTAL, PORTAL2, PORTAL_VERSION, TAMPER, UPLOAD, DETECT, TRACE, SCORE, WAITING, WAITING2, WAITING_VERSION, atSeconds } from "./timing";

export const Master: React.FC<{ showText?: boolean }> = ({ showText = true }) => (
  <AbsoluteFill style={{ background: colors.paper }}>
    {HOOK_VERSION === "v3" ? (
      <>
        <Sequence durationInFrames={atSeconds(OVERWHELM3.length)}>
          <BuriedScene showText={showText} />
        </Sequence>
        {/* Section 2 follows the v3 hook. */}
        {WAITING_VERSION === "v2" ? (
          <Sequence from={atSeconds(OVERWHELM3.length)} durationInFrames={atSeconds(WAITING2.length)}>
            <WaitingScene2 showText={showText} />
          </Sequence>
        ) : (
          <Sequence from={atSeconds(OVERWHELM3.length)} durationInFrames={atSeconds(WAITING.length)}>
            <WaitingScene showText={showText} />
          </Sequence>
        )}
        {/* Section 3 */}
        <Sequence from={atSeconds(OVERWHELM3.length) + atSeconds(WAITING_VERSION === "v2" ? WAITING2.length : WAITING.length)} durationInFrames={atSeconds(TAMPER.length)}>
          <TamperScene showText={showText} />
        </Sequence>
        {/* Section 4 */}
        <Sequence from={atSeconds(OVERWHELM3.length) + atSeconds(WAITING_VERSION === "v2" ? WAITING2.length : WAITING.length) + atSeconds(TAMPER.length)} durationInFrames={atSeconds(PORTAL_VERSION === "v2" ? PORTAL2.length : PORTAL.length)}>
          {PORTAL_VERSION === "v2" ? <PortalScene2 showText={showText} /> : <PortalScene showText={showText} />}
        </Sequence>
        {/* Section 5 */}
        {(() => {
          // Sections 5-8, one after another from the end of section 4.
          let at = atSeconds(OVERWHELM3.length) + atSeconds(WAITING_VERSION === "v2" ? WAITING2.length : WAITING.length) + atSeconds(TAMPER.length) + atSeconds(PORTAL_VERSION === "v2" ? PORTAL2.length : PORTAL.length);
          const parts: [number, React.ReactNode][] = [
            [atSeconds(UPLOAD.length), <UploadScene showText={showText} />],
            [atSeconds(DETECT.length), <DetectScene showText={showText} />],
            [atSeconds(TRACE.length), <TraceScene showText={showText} />],
            [atSeconds(SCORE.length), <ScoreScene showText={showText} />],
          ];
          return parts.map(([len, node], i) => {
            const from = at;
            at += len;
            return <Sequence key={i} from={from} durationInFrames={len}>{node}</Sequence>;
          });
        })()}
      </>
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
