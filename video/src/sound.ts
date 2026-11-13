// sound.ts - the film's sound, placed from the same knobs as the picture.
//   VO: one clip per on-screen caption (ElevenLabs, voice Brian), each starting as its caption rises (see VO below).
//   Music: public/music/soundtrack.mp3 (the synthwave track, extended by a 12-bar loop so its own ending lands on
//       the call to action). Quieter through the problem (sections 1-3), lifted from the portal on, and ducked under
//       the voice.
//   SFX: synthesized (public/sfx), each placed on its visual event with its measured lead.

import cues from "../public/vo/cues.json";
import { SWIPE } from "./components/swipe";
import {
  CTA, DASHBOARD, DETECT, DIFFERENCE, FINALISE, FPS, OVERWHELM3, PORTAL2, SCENES, SCORE, TAMPER, TRACE, UPLOAD, WAITING2,
} from "./timing";

export type SectionName = "buried" | "waiting" | "tamper" | "portal" | "upload" | "detect" | "trace" | "score" | "finalise" | "dashboard" | "difference" | "cta";

const start = (n: SectionName) => (SCENES as Record<string, { start: number }>)[n]?.start ?? 0;
const at = (n: SectionName, seconds: number) => start(n) + Math.round(seconds * FPS);

/** Seconds after the big ping that the sigh starts. */
export const SIGH_AFTER_PING = 0.25;

/** Seconds after its caption rises that each voice line starts. tools/fit-vo.py uses the same lead. */
export const VO_LEAD = 0.05;
const TIMINGS: Record<string, Record<string, unknown>> = {
  OVERWHELM3, WAITING2, TAMPER, PORTAL2, UPLOAD, DETECT, TRACE, SCORE, FINALISE, DASHBOARD, DIFFERENCE, CTA,
};

/** One voice line per on-screen caption (public/vo/cues.json; clips in public/vo/lines), each starting as its
 * caption rises. After replacing a clip, run `python3 tools/fit-vo.py` so the captions make room for it. */
export const VO = (cues as { section: SectionName; timing: string; knob: string; file: string; dur: number }[]).map((c) => ({
  section: c.section,
  src: `vo/lines/${c.file}.wav`,
  from: at(c.section, (TIMINGS[c.timing][c.knob] as number) + VO_LEAD),
  frames: Math.ceil((c.dur + 0.1) * FPS),
})).concat([
  // The sigh after the hook's big ping (ElevenLabs Sound Effects; sigh-alt.wav is the other take).
  { section: "buried", src: "vo/sigh.wav", from: at("buried", OVERWHELM3.finalPingIn + SIGH_AFTER_PING), frames: Math.ceil(1.9 * FPS) },
]);

/** Music level by frame: soft for the problem, a little fuller for the product, and dipped gently under the voice,
 * easing down over DUCK_IN before a line and back up over DUCK_OUT after it, so the music never jumps. */
export const MUSIC = { src: "music/soundtrack.mp3", quiet: 0.2, lifted: 0.28, ducked: 0.6, fadeFrames: 45 };
const DUCK_IN = 12; // frames
const DUCK_OUT = 20;
const smooth = (x: number) => x * x * (3 - 2 * x);
export const musicLevel = (f: number, vo = true) => {
  const base = f >= start("portal") ? MUSIC.lifted : MUSIC.quiet;
  if (!vo) return base;
  // how far into a duck this frame is (0 = full music, 1 = fully dipped)
  let d = 0;
  for (const v of VO) {
    const a = v.from, e = v.from + v.frames;
    if (f >= a && f <= e) { d = 1; break; }
    if (f < a && a - f < DUCK_IN) d = Math.max(d, smooth(1 - (a - f) / DUCK_IN));
    if (f > e && f - e < DUCK_OUT) d = Math.max(d, smooth(1 - (f - e) / DUCK_OUT));
  }
  return base * (1 - (1 - MUSIC.ducked) * d);
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
  cue("finalise", FINALISE.summaryClick, CLICK, 0.45),
  cue("finalise", FINALISE.buttonClick, CLICK, 0.45),
  cue("finalise", FINALISE.confirmClick, CLICK, 0.5),
  cue("finalise", FINALISE.doneAt + 0.15, THUMP, 0.35),
  cue("dashboard", DASHBOARD.navClick, CLICK, 0.45),
  // 10 the answer arrives
  cue("difference", DIFFERENCE.newsAt, GOOD, 0.55),
  // 11 the button
  cue("cta", CTA.pressAt, CLICK, 0.5),
  cue("cta", CTA.pressAt + 0.05, PING, 0.3),
];
