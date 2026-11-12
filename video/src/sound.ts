// sound.ts - the film's sound, placed from the same knobs as the picture.
//   VO: one guide clip per section (public/vo, made with espeak-ng from public/vo/lines.json), starting VO_OFFSET
//       seconds into its section. Replace a clip with your own recording of the same name and everything else follows.
//   Music: public/music/soundtrack.mp3 (the synthwave track, extended by a 12-bar loop so its own ending lands on
//       the call to action). Quieter through the problem (sections 1-3), lifted from the portal on, and ducked under
//       the voice.
//   SFX: synthesized (public/sfx), each placed on its visual event with its measured lead.

import durations from "../public/vo/durations.json";
import { SWIPE } from "./components/swipe";
import {
  CTA, DASHBOARD, DETECT, DIFFERENCE, FPS, OVERWHELM3, PORTAL2, SCENES, SCORE, TAMPER, TRACE, UPLOAD, WAITING2,
} from "./timing";

export type SectionName = "buried" | "waiting" | "tamper" | "portal" | "upload" | "detect" | "trace" | "score" | "dashboard" | "difference" | "cta";
const VO_FILE: Record<SectionName, keyof typeof durations> = {
  buried: "hook", waiting: "waiting", tamper: "tamper", portal: "portal", upload: "upload", detect: "detect",
  trace: "trace", score: "score", dashboard: "dashboard", difference: "difference", cta: "cta",
};
/** Seconds into each section the voice starts. */
export const VO_OFFSET: Record<SectionName, number> = {
  buried: 0.2, waiting: 0.3, tamper: 0.3, portal: 0.15, upload: 0.25, detect: 0.2, trace: 0.3, score: 0.3,
  dashboard: 0.25, difference: 0.25, cta: 0.3,
};

const start = (n: SectionName) => (SCENES as Record<string, { start: number }>)[n]?.start ?? 0;
const at = (n: SectionName, seconds: number) => start(n) + Math.round(seconds * FPS);

export const VO = (Object.keys(VO_FILE) as SectionName[]).map((n) => ({
  section: n,
  src: `vo/${VO_FILE[n]}.wav`,
  from: at(n, VO_OFFSET[n]),
  frames: Math.ceil((durations[VO_FILE[n]] + 0.1) * FPS),
}));

/** Music level by frame: quieter for the problem, lifted for the product, ducked under the voice. */
export const MUSIC = { src: "music/soundtrack.mp3", quiet: 0.25, lifted: 0.42, ducked: 0.3, fadeFrames: 12 };
export const musicLevel = (f: number, vo = true) => {
  const lifted = f >= start("portal") ? MUSIC.lifted : MUSIC.quiet;
  const speaking = vo && VO.some((v) => f >= v.from - 6 && f <= v.from + v.frames + 6);
  return lifted * (speaking ? MUSIC.ducked : 1);
};

export type Cue = { at: number; src: string; lead: number; volume: number };
const S = (file: string, lead: number) => ({ src: `sfx/${file}.wav`, lead });
const CHAT = S("chat", 0.095), BIG = S("pingbig", 0.005), CLOCK = S("clock", 0.005), KEY = S("key", 0.005),
  CLICK = S("click", 0.005), WHOOSH = S("whoosh", 0.245), SWELL = S("swell", 1.745), THUMP = S("thump", 0.005),
  TICK = S("tick", 0.005), PING = S("ping", 0.005), GOOD = S("goodnews", 0.11);

const cue = (n: SectionName, seconds: number, s: { src: string; lead: number }, volume: number): Cue => ({ at: at(n, seconds), ...s, volume });
const range = (a: number, b: number, step: number) => Array.from({ length: Math.max(0, Math.floor((b - a) / step) + 1) }, (_, i) => a + i * step);

export const CUES: Cue[] = [
  // 1 hook: each chat ping, then the big one
  ...OVERWHELM3.pings.map((p) => cue("buried", p, CHAT, 0.35)),
  cue("buried", OVERWHELM3.finalPingIn, BIG, 0.9),
  // 2 the clock racing
  ...range(WAITING2.clockStart, WAITING2.clockEnd, 0.5).map((t) => cue("waiting", t, CLOCK, 0.22)),
  // 3 the bad actor's edit: select, then a key per character
  cue("tamper", TAMPER.select, CLICK, 0.35),
  ...range(TAMPER.typeStart, TAMPER.typeEnd, (TAMPER.typeEnd - TAMPER.typeStart) / 9).map((t) => cue("tamper", t, KEY, 0.4)),
  // 4 the portal: a swell into the name, a hit as it lands
  cue("portal", PORTAL2.titleIn, SWELL, 0.3),
  cue("portal", PORTAL2.titleIn, THUMP, 0.45),
  // 5-9: a whoosh on every swipe, a click on every press, a chime on the payoffs
  cue("upload", 0, WHOOSH, 0.3),
  cue("upload", UPLOAD.dropAt, TICK, 0.35),
  cue("upload", UPLOAD.clickAt, CLICK, 0.45),
  cue("upload", UPLOAD.scanAt + SWIPE / 2, WHOOSH, 0.3),
  cue("detect", 0, WHOOSH, 0.3),
  cue("detect", DETECT.catchAt + 0.6, PING, 0.25),
  cue("trace", 0, WHOOSH, 0.3),
  cue("trace", TRACE.clickAt, CLICK, 0.45),
  cue("trace", TRACE.auditAt + SWIPE / 2, WHOOSH, 0.3),
  cue("score", 0, WHOOSH, 0.3),
  cue("score", SCORE.openAt, CLICK, 0.45),
  cue("score", SCORE.openAt + 0.25 + SWIPE / 2, WHOOSH, 0.3),
  cue("score", SCORE.tickAt, CLICK, 0.45),
  cue("score", SCORE.noteAt + 0.3, CLICK, 0.4),
  ...range(SCORE.noteAt + 0.45, SCORE.saveAt - 0.45, 0.12).map((t) => cue("score", t, KEY, 0.25)),
  cue("score", SCORE.saveAt, CLICK, 0.45),
  cue("score", SCORE.saveAt + 0.05, PING, 0.3),
  cue("score", SCORE.addAt + SWIPE / 2, WHOOSH, 0.3),
  cue("dashboard", DASHBOARD.navClick, CLICK, 0.45),
  cue("dashboard", DASHBOARD.clickAt, CLICK, 0.45),
  cue("dashboard", DASHBOARD.auditClick, CLICK, 0.45),
  // 10 the answer arrives
  cue("difference", DIFFERENCE.newsAt, GOOD, 0.55),
  // 11 the button
  cue("cta", CTA.pressAt, CLICK, 0.5),
  cue("cta", CTA.pressAt + 0.05, PING, 0.3),
];
