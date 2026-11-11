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

const SCENE_LENGTHS = {
  package: atSeconds(HOOK.package.length),
  tooMany: atSeconds(HOOK.tooMany.length),
};

const SCENE_ENDS = (() => {
  let t = 0;
  const ends = {} as Record<keyof typeof SCENE_LENGTHS, number>;
  for (const k of Object.keys(SCENE_LENGTHS) as (keyof typeof SCENE_LENGTHS)[]) {
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
