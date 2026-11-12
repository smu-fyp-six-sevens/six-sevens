// timing.ts - the film's clock. Every frame number in the film comes from here,
// and every number here comes from beats.json (written by scripts/detect-beats.py).
// Scenes are placed by beat index, so no frame is ever typed by hand and
// changing one index re-flows everything after it.

import { getRemotionEnvironment } from "remotion";
import beats from "../beats.json";
import { nearestBeat, type Scene } from "./scene-frames";

export const FPS = beats.fps;

/** Soundtrack file name under public/. null while beats.json is still the scaffold's placeholder. */
export const SOUNDTRACK: string | null = beats.audio_file;

/**
 * Frames cut off the start of the soundtrack (to skip a long intro, say).
 * Master passes this to <Audio trimBefore>, and the beat grid below shifts by
 * the same amount, so the picture and the music cannot drift apart.
 */
export const AUDIO_TRIM = 0;

/** The soundtrack's level in the master (and in test beds, so they sound the same). */
export const MUSIC_VOLUME = 0.6;

/**
 * true: the film is cut to the beat, and scene ends are written as beats.
 * false: the film is not beat-synced (the beat grid was rated "poor", or a
 * voiceover sets the pace). Scene ends are then written with atSeconds(), and
 * the brief has to say so, because the viewer will not get cuts on the music.
 */
export const BEAT_SYNCED = false;

/** Seconds to master frame. For films that are not beat-synced; never for an event that claims to be on a beat. */
export const atSeconds = (seconds: number): number => Math.round(seconds * FPS);

const onTimeline = (frames: number[]): number[] =>
  frames.map((f) => f - AUDIO_TRIM).filter((f) => f >= 0);

/** Master frames of every beat, of the backbeats (2 and 4), and of the downbeats (1 and 3). */
export const BEATS = onTimeline(beats.beat_frames);
export const BACKBEATS = onTimeline(beats.backbeat_frames);
export const DOWNBEATS = onTimeline(beats.downbeat_frames);

const pick = (frames: number[], n: number, kind: string): number => {
  const frame = frames[n];
  if (frame === undefined) {
    throw new Error(`[timing] ${kind} ${n} does not exist: the track has ${frames.length} of them.`);
  }
  return frame;
};

/** Master frame of beat n, counting from 0 at the first beat on the timeline. */
export const beatFrame = (n: number): number => pick(BEATS, n, "beat");

/** Master frame of backbeat n, counting from 0 at the first backbeat on the timeline. */
export const backbeatFrame = (n: number): number => pick(BACKBEATS, n, "backbeat");

/** The backbeat closest to a master frame. */
export const nearestBackbeat = (frame: number): number => nearestBeat(frame, BACKBEATS) ?? frame;

/** Is this master frame on a backbeat? Detected beats can sit a frame off the hit, hence the tolerance. */
export const isBackbeat = (frame: number, tolerance = 1): boolean =>
  BACKBEATS.some((b) => Math.abs(b - frame) <= tolerance);

// ---------------------------------------------------------------------------
// THE KNOBS. This film is not cut to music (BEAT_SYNCED = false): you set the
// timing. Every number below is in SECONDS. Change one, save, and Studio
// (npm run dev) re-flows the film. Scene times are each scene's length; event
// times are seconds from the start of that scene.
// ---------------------------------------------------------------------------
/**
 * Which hook the Master plays.
 *   "v3"  buried, full screen: PDFs, tabs and pings fill the frame; every "a" of "Waaaay" opens
 *         another document; ends on one big ping and a hold (for the sigh). Default.
 *   "v2"  overwhelm: the same pile on a screen at the side of the frame.
 *   "v1"  the portal's upload box filling with the case's files (first version).
 */
export const HOOK_VERSION: "v1" | "v2" | "v3" = "v3";

/** Section 3, "tamper". All times in seconds from the start of the section. */
export const TAMPER = {
  length: 9.5,            // whole section
  panelsIn: -0.25,        // the two halves land. Negative = already arriving on the first frame
  line1In: 0.3,           // "Even worse, when bad actors are involved,"
  cursorIn: 1.1,          // the text cursor appears on the statement...
  cursorAt: 1.9,          // ...and reaches the closing balance
  select: 2.1,            // the figure gets selected
  erase: 2.6,             // and deleted
  typeStart: 2.8,         // the new figure is typed...
  typeEnd: 3.9,           // ...character by character
  line2In: 4.5,           // "smaller anomalies are even harder to catch."
  shrinkStart: 5.3,       // the statement shrinks into the pile...
  shrinkEnd: 6.4,         // ...and lands among the case's other documents
  pileStagger: 0.04,      // seconds between the other documents appearing
};

/**
 * Section 2 version.
 *   "v2"  silhouette avatars (Loan Officer, Customer), the script line as the caption. Default.
 *   "v1"  initials avatars with invented names (first version).
 */
export const WAITING_VERSION: "v1" | "v2" = "v2";

/** Section 2 v2. All times in seconds from the start of the section. */
export const WAITING2 = {
  length: 9,              // whole section
  avatarsIn: -0.25,       // the two avatars and the clock land, staggered. Negative = already arriving on the first frame
  officerLineIn: 0.3,     // "A loan officer may spend over an hour staring at details on the screen,"
  customerLineIn: 3.9,    // "while the loan customer anxiously waits..." (the customer starts pulsing here)
  clockStart: 0.8,        // the clock starts racing...
  clockEnd: 7.4,          // ...and reaches 10:14. Statuses tick along with it
  pulseEvery: 1.1,        // seconds between the customer's "waiting" pulses
  fidget: 1.3,            // seconds per sway of the anxious customer
};

/** Section 2, "waiting". All times in seconds from the start of the section. */
export const WAITING = {
  length: 7,              // whole section
  avatarsIn: -0.25,       // the two avatars and the clock land, staggered. Negative = already arriving on the first frame
  line1In: 0.4,           // "Over an hour on the details,"
  clockStart: 0.8,        // the clock starts racing...
  clockEnd: 5.4,          // ...and reaches 10:14. Statuses tick along with it
  line2In: 2.6,           // "while the customer waits for news."
  pulseEvery: 1.1,        // seconds between the customer's "waiting" pulses
};

/** v3 hook. All times in seconds from the start of the hook. */
export const OVERWHELM3 = {
  length: 9.6,            // whole hook, including the hold on the final ping
  title1In: 0.2,          // "Corporate loan applications need extensive documentation."
  title1Out: 3.5,         // ...lifts away
  firstDocIn: 0.3,        // first PDF window opens
  lastDocIn: 3.7,         // last window before "Waaaay" (the rest open with its letters)
  ramp: 0.75,             // how sharply those windows speed up: 1 = steady, 0.5 = slow start
  docPop: 0.2,            // how long one window takes to pop open
  firstTabIn: 0.25,       // tabs pile up across the top between these two times
  lastTabIn: 6.6,
  wayIn: 3.75,            // "Way" appears
  stretchStart: 4.0,      // the "a"s start sliding in (documents open along the way)...
  stretchEnd: 5.6,        // ...and the word reaches full length. Starts slow, speeds up, eases out at the end
  stretchEase: 0.6,       // 0 = steady pace, 1 = very slow start and end
  title2In: 4.1,          // "too many documents to review."
  footnoteIn: 5.6,        // "For one case."
  pings: [1.6, 2.7, 4.6, 5.8, 6.4], // one time per message in PINGS3
  finalPingIn: 7.1,       // everything stops and dims; the big ping lands. The rest is the hold for your sigh
  dim: 0.25,              // how long the dim takes
  pushIn: 1.04,           // the camera's slow push until the final ping (1 = none)
};

/** v2 hook. All times in seconds from the start of the hook. */
export const OVERWHELM = {
  length: 11,             // whole hook
  title1In: 0.25,         // "Corporate loan applications need extensive documentation."
  title1Out: 5.0,         // ...lifts away
  firstDocIn: 0.5,        // first PDF window opens
  lastDocIn: 9.2,         // last one; windows open faster and faster in between
  docPop: 0.25,           // how long one window takes to pop open
  ramp: 0.75,             // how sharply windows and tabs speed up: 1 = steady pace, 0.5 = slow start, frantic end
  firstTabIn: 0.4,        // browser tabs pile up across the top between these two times
  lastTabIn: 9.6,
  pings: [2.4, 3.8, 6.2, 7.2, 8.1, 8.9], // when each message lands (one per entry in PINGS)
  wayIn: 5.35,            // "Way" appears
  stretchStart: 5.7,      // the "a"s start stretching...
  stretchEnd: 6.9,        // ...and reach full length
  title2In: 5.9,          // "too many documents to review."
  footnoteIn: 7.4,        // "For one case."
  pushIn: 1.04,           // the camera's slow push toward the screen over the whole hook (1 = none)
};

/** v1 hook. Times in seconds from the start of each scene. */
export const HOOK = {
  package: {
    length: 5.5,          // scene 1 length
    titleIn: 0.25,        // headline starts rising
    firstFileIn: 0.6,     // first file row lands in the upload box
    lastFileIn: 4.6,      // twentieth file lands; rows arrive faster and faster in between
    rowLand: 0.22,        // how long one row takes to drop into place
  },
  tooMany: {
    length: 5.5,          // scene 2 length
    pullBackEnd: 1.4,     // the camera finishes pulling back to show all 20 files
    kickerIn: 0.35,       // "Case GLDB-02 · ..." line
    wayIn: 0.6,           // "Way too many" appears
    stretchStart: 0.9,    // the "a"s start stretching...
    stretchEnd: 2.0,      // ...and reach their full length
    line2In: 1.25,        // "documents to review."
    footnoteIn: 2.4,      // "20 files for a single case."
  },
};

/** Seconds to frames, for event times inside a scene. */
export const s = (seconds: number): number => Math.round(seconds * FPS);

const SCENE_LENGTHS: Record<string, number> =
  HOOK_VERSION === "v3"
    ? { buried: atSeconds(OVERWHELM3.length), waiting: atSeconds(WAITING_VERSION === "v2" ? WAITING2.length : WAITING.length), tamper: atSeconds(TAMPER.length) }
    : HOOK_VERSION === "v2"
    ? { overwhelm: atSeconds(OVERWHELM.length) }
    : { package: atSeconds(HOOK.package.length), tooMany: atSeconds(HOOK.tooMany.length) };

const SCENE_ENDS = (() => {
  let t = 0;
  const ends: Record<string, number> = {};
  for (const k of Object.keys(SCENE_LENGTHS)) {
    t += SCENE_LENGTHS[k];
    ends[k] = t;
  }
  return ends;
})();

export type SceneName = keyof typeof SCENE_ENDS;

/** Each scene's start and duration in master frames. */
export const SCENES = (() => {
  const scenes = {} as Record<SceneName, Scene>;
  let start = 0;
  for (const name of Object.keys(SCENE_ENDS) as SceneName[]) {
    scenes[name] = { start, duration: SCENE_ENDS[name] - start };
    start = SCENE_ENDS[name];
  }
  return scenes;
})();

/**
 * Scene-local frame of the track's beat n, for a scene's own useCurrentFrame().
 * Unlike an index into sceneBeats(), it still means the same beat after the
 * scene's start has been moved, which is also its trap: move the scene far
 * enough and the beat falls outside it, silently. After changing the scene
 * table, re-check every call (gotchas.md 34).
 */
export const beatInScene = (n: number, scene: Scene): number => beatFrame(n) - scene.start;

/** Length of the film: the end of the last scene. */
export const TOTAL_FRAMES = Object.values(SCENES).reduce((sum, s) => sum + s.duration, 0);

// Studio-only checks. They warn instead of throwing because a cut off the
// backbeat is allowed; it should just be a decision and not an accident.
if (getRemotionEnvironment().isStudio) {
  for (const note of beats.warnings) console.warn(`[beats] ${note}`);
  if (!BEAT_SYNCED) console.warn("[timing] this film is not beat-synced: scene ends are placed by time.");
  for (const [name, scene] of Object.entries(SCENES)) {
    if (BEAT_SYNCED && scene.start > 0 && !isBackbeat(scene.start)) {
      console.warn(`[timing] scene "${name}" starts at frame ${scene.start}, which is not a backbeat.`);
    }
  }
}
