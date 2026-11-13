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

/** Section 8b, "finalise": the officer locks the case. All times in seconds from the start of the section. */
export const FINALISE = {
  length: 5.23,
  headlineIn: 0.2,        // "Then lock the case."
  summaryClick: 0.6,      // on the Additional Documents page, the cursor clicks "Summary" in the sidebar
  readyAt: 1.92,          // the case summary: "Ready to Finalise"
  buttonClick: 2.87,       // the cursor clicks "Finalise Case"
  confirmAt: 3.02,        // the confirmation: view-only, can't be undone
  confirmClick: 4.07,      // it confirms
  doneAt: 4.22,           // "Finalised": the case is locked, view-only
};

/** Section 9, "dashboard": a tour of the dashboard over a month of cases; the camera follows the page. */
export const DASHBOARD = {
  length: 10.92,
  headlineIn: 0.2,        // "Every case, at a glance."
  navClick: 0.6,          // on the finalised case, the cursor clicks "Dashboard" in the top bar
  pipelineAt: 1.48,        // the case pipeline
  flagsAt: 3.89,           // scroll: the most common flags and documents by type
  riskAt: 7.04,            // scroll: the risk overview
  attentionAt: 9.13,       // scroll: needs attention, riskiest first
};

/** Section 10, "difference": the same two people as section 2, after the portal. */
export const DIFFERENCE = {
  length: 7.65,
  avatarsIn: -0.25,       // already arriving on the first frame
  line1In: 0.2,           // "What took hours of screening now takes minutes."
  clockStart: 0.4,        // the clock barely moves...
  clockEnd: 2.1,
  doneAt: 2.1,            // the officer's status flips to "Review complete"
  line2In: 2.68,           // "The AI does the reading. Your loan officers make the call."
  sendAt: 3.28,            // the answer travels down the line...
  newsAt: 3.98,            // ...and the good-news ping lands with the customer
  line3In: 5.74,           // "And customers hear back sooner."
};

/** Section 11, "cta". */
export const CTA = {
  length: 5.93,
  leadIn: 0.2,            // "Bring your next loan case."
  nameIn: 1.99,            // "GLDB Anomaly Detection Portal"
  subIn: 2.39,             // "Upload the package, and see what the portal catches."
  buttonIn: 4.03,          // the button pops in...
  hoverAt: 4.93,           // ...the cursor arrives...
  pressAt: 5.33,           // ...and presses it
};

/** Section 6, "detect" (how it works, step 2). All times in seconds from the start of the section. */
export const DETECT = {
  length: 11.17,
  headlineIn: 0.2,        // "Agents investigate every document."
  planIn: 0.15,           // the lead agent's card; its plan types out...
  planEnd: 2.4,           // ...line by line
  specialistsIn: 2.7,     // the six checks branch out under it and start running...
  checksEnd: 5.6,         // ...finishing one after another by here (four turn up a finding)
  catchAt: 7.36,           // the two catches: the doctored figure...
  addressAt: 8.66,         // ...and the mismatched address
};

/** Section 7, "trace" (how it works, step 2 continued). */
export const TRACE = {
  length: 11.29,
  headlineIn: 0.2,        // "And it's not a black box."
  line1In: 1.69,           // "The agent trace shows every step..."
  rowsStart: 1.99,         // the trace's rows light up...
  rowsEnd: 4.09,
  clickAt: 4.69,           // the cursor opens the font consistency check...
  outputAt: 5.19,          // ...and its returned output is highlighted
  auditAt: 7.39,           // cut to the case's audit trail: "Every finding can be audited."
  entriesStart: 7.79,      // its entries light up one by one...
  entriesEnd: 10.29,
};

/** Section 8, "score" (how it works, step 3). */
export const SCORE = {
  length: 15.35,
  headlineIn: 0.2,        // "Every case gets a risk rating."
  line1In: 2.21,           // "With the exact evidence behind it."
  gaugeAt: 2.61,           // the risk gauge is ringed...
  docsAt: 3.61,            // ...then the documents to review
  openAt: 4.91,            // the cursor opens the bank statement's review
  reviewAt: 5.31,          // "What was flagged, where, and why." The three are tagged in turn:
  whatAt: 5.91,
  whereAt: 6.71,
  whyAt: 7.51,
  tickAt: 9.01,            // "Officers confirm or dismiss...": the cursor ticks the metadata finding as wrong...
  noteAt: 9.71,            // ...picks a reason and types a note...
  saveAt: 11.61,           // ...and saves the review
  addAt: 12.81,            // swipe to Additional Documents: "And it recommends the documents that would settle it."
  nowAt: 13.51,            // "Now 72 (High) -> Best case 20 (Low)" is ringed...
  recsStart: 14.21,        // ...then each recommended document lights up in turn
};

/** Section 5, "upload" (how it works, step 1). All times in seconds from the start of the section. */
export const UPLOAD = {
  length: 10.8,           // whole section
  headlineIn: 0.2,        // the step tracker is already up; "Upload the whole package." rises
  dragStart: 0.4,         // the cursor drags the stack of files in...
  dropAt: 1.5,            // ...and drops it on the upload box
  cascadeEnd: 2.4,        // the 20 rows have cascaded in by here
  clickAt: 3.0,           // the cursor clicks "Upload 20 files"
  tableAt: 3.4,           // the box clears; the camera moves to the documents table
  line1In: 3.7,           // "Every file is classified and its key fields read,"
  classifyStart: 3.8,     // row by row: processing, then the type stamps on and the status flips to Extracted...
  classifyEnd: 6.9,       // ...last row
  scanAt: 7.3,            // cut to the scanned December statement
  line2In: 7.6,           // "even from scans."
  beamStart: 7.8,         // the scan beam runs down the page; fields fill in as it passes them...
  beamEnd: 9.8,
};

/**
 * Section 4 version.
 *   "v2"  a pronounced intro: "So that's why we introduce..." and the name centred, then the product in 3D angles. Default.
 *   "v1"  the pile is scanned, then flat shots of the trace and detection (first version).
 */
export const PORTAL_VERSION: "v1" | "v2" = "v2";

/** Section 4 v2. All times in seconds from the start of the section. */
export const PORTAL2 = {
  length: 12.1,           // whole section
  introIn: 0.1,          // "So that's why we introduce..." rises, centred
  introOut: 1.65,          // ...and lifts away
  titleIn: 1.85,          // "GLDB Anomaly Detection Portal", big and centred
  underline: 2.5,         // a line draws under it
  heroStart: 3.4,         // the name glides up to the top band as the Case Summary rises in, tilted
  heroEnd: 4.6,
  traceAt: 6.0,           // the Agent trace swings in from the other side
  line1In: 6.2,           // "Agents read every document,"
  traceStart: 6.5,        // its rows light up...
  traceEnd: 8.3,
  detectionAt: 8.7,       // the Detection screen, angled
  line2In: 8.9,           // "and flag what doesn't add up."
  boxIn: 9.3,             // the forged figure gets its red box
  flatten: 10.0,          // the screen turns flat and the camera pushes to the finding...
  flattenEnd: 11.2,
  findingAt: 11.3,        // ...and the top finding is highlighted
};

/** Section 4, "portal". All times in seconds from the start of the section. */
export const PORTAL = {
  length: 10.5,           // whole section
  titleIn: 0.2,           // "GLDB Anomaly Detection Portal"
  scanStart: 0.5,         // a scan line sweeps the pile, checking each document...
  scanEnd: 1.9,           // ...and flags the doctored statement as it passes
  pushStart: 2.2,         // the camera pushes into the flagged statement...
  pushEnd: 2.7,           // ...and cuts to the agent trace
  line1In: 2.6,           // "Agents read every document,"
  traceStart: 3.1,        // the trace's rows light up one by one...
  traceEnd: 5.3,          // ...through the lead agent and the specialists
  detectionAt: 5.8,       // cut to the document's Detection panel
  line2In: 6.1,           // "and flag what doesn't add up."
  boxIn: 6.5,             // a red box lands on the forged figure in the preview
  zoomStart: 7.2,         // the camera pushes to the Detection panel...
  zoomEnd: 8.4,
  findingAt: 8.6,         // ...and the top finding is highlighted
};

/** Section 3, "tamper". All times in seconds from the start of the section. */
export const TAMPER = {
  length: 5.35,            // whole section
  panelsIn: -0.25,        // the two halves land. Negative = already arriving on the first frame
  line1In: 0.15,           // "Even worse, when bad actors are involved,"
  cursorIn: 0.55,          // the text cursor appears on the statement...
  cursorAt: 1.05,          // ...and reaches the closing balance
  select: 1.2,            // the figure gets selected
  erase: 1.55,             // and deleted
  typeStart: 1.7,         // the new figure is typed...
  typeEnd: 2.55,           // ...character by character
  line2In: 2.3,           // "smaller anomalies are even harder to catch."
  shrinkStart: 3.55,       // the statement shrinks into the pile...
  shrinkEnd: 4.45,         // ...and lands among the case's other documents
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
  length: 8.4,              // whole section
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
  length: 9.25,            // whole hook, including the hold on the final ping
  title1In: 0.2,          // "Corporate loan applications need extensive documentation."
  title1Out: 3.45,         // ...lifts away
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
    ? { buried: atSeconds(OVERWHELM3.length), waiting: atSeconds(WAITING_VERSION === "v2" ? WAITING2.length : WAITING.length), tamper: atSeconds(TAMPER.length), portal: atSeconds(PORTAL_VERSION === "v2" ? PORTAL2.length : PORTAL.length), upload: atSeconds(UPLOAD.length), detect: atSeconds(DETECT.length), trace: atSeconds(TRACE.length), score: atSeconds(SCORE.length), finalise: atSeconds(FINALISE.length), dashboard: atSeconds(DASHBOARD.length), difference: atSeconds(DIFFERENCE.length), cta: atSeconds(CTA.length) }
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
