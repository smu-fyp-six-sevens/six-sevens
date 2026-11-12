// Master - the whole film: eleven sections, with the guide voiceover, music and sound effects (sound.ts).
// HOOK_VERSION in timing.ts picks the version; every time is a knob there, in seconds.

import { Audio } from "@remotion/media";
import { AbsoluteFill, Sequence, interpolate, staticFile } from "remotion";
import { Sfx } from "./components/Sfx";
import { CtaScene } from "./scenes/CtaScene";
import { DashboardScene } from "./scenes/DashboardScene";
import { DifferenceScene } from "./scenes/DifferenceScene";
import { CUES, MUSIC, VO, musicLevel, type SectionName } from "./sound";
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
import { HOOK, HOOK_VERSION, OVERWHELM, PORTAL_VERSION, SCENES, TOTAL_FRAMES, WAITING_VERSION, atSeconds } from "./timing";

/** The v3 film, in order. Each section's start and length come from SCENES in timing.ts. */
const SECTIONS: { name: SectionName; node: (showText: boolean) => React.ReactNode }[] = [
  { name: "buried", node: (t) => <BuriedScene showText={t} /> },
  { name: "waiting", node: (t) => (WAITING_VERSION === "v2" ? <WaitingScene2 showText={t} /> : <WaitingScene showText={t} />) },
  { name: "tamper", node: (t) => <TamperScene showText={t} /> },
  { name: "portal", node: (t) => (PORTAL_VERSION === "v2" ? <PortalScene2 showText={t} /> : <PortalScene showText={t} />) },
  { name: "upload", node: (t) => <UploadScene showText={t} /> },
  { name: "detect", node: (t) => <DetectScene showText={t} /> },
  { name: "trace", node: (t) => <TraceScene showText={t} /> },
  { name: "score", node: (t) => <ScoreScene showText={t} /> },
  { name: "dashboard", node: (t) => <DashboardScene showText={t} /> },
  { name: "difference", node: (t) => <DifferenceScene showText={t} /> },
  { name: "cta", node: (t) => <CtaScene showText={t} /> },
];

/** Voice, music and sound effects. Each can be switched off: `vo` false leaves music and effects for your own recording. */
const SoundLayer: React.FC<{ vo: boolean; music: boolean; sfx: boolean }> = ({ vo, music, sfx }) => (
  <>
    {music && <Audio src={staticFile(MUSIC.src)} volume={(f) => musicLevel(f) * interpolate(f, [TOTAL_FRAMES - MUSIC.fadeFrames, TOTAL_FRAMES], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" })} />}
    {vo && VO.map((v) => (
      <Sequence key={v.section} from={v.from} durationInFrames={v.frames}>
        <Audio src={staticFile(v.src)} />
      </Sequence>
    ))}
    {sfx && CUES.map((c, i) => <Sfx key={i} at={c.at} src={staticFile(c.src)} lead={c.lead} volume={c.volume} />)}
  </>
);

export const Master: React.FC<{ showText?: boolean; vo?: boolean; music?: boolean; sfx?: boolean }> = ({ showText = true, vo = true, music = true, sfx = true }) => (
  <AbsoluteFill style={{ background: colors.paper }}>
    {HOOK_VERSION === "v3" ? (
      <>
        {SECTIONS.map((sec) => {
          const sc = (SCENES as Record<string, { start: number; duration: number }>)[sec.name];
          return <Sequence key={sec.name} from={sc.start} durationInFrames={sc.duration}>{sec.node(showText)}</Sequence>;
        })}
        <SoundLayer vo={vo} music={music} sfx={sfx} />
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
