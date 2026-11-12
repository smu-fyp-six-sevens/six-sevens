// Section 4 - the product arrives. Script: "We introduce the GLDB Fraud Detection Portal, a highly agentic
// automated system that can analyse documents more holistically and flag anomalies with high accuracy."
// The screens are the real portal (frontend/) on its mock backend, captured by video/capture/story.spec.ts with a
// detection report written for the film's story. "\n" is a line break you chose.

export const PORTAL_COPY = {
  /** v2: the plain-text lead-in, centred, before the name. */
  intro: "So that's why we introduce...",
  title: "GLDB Fraud Detection Portal",
  line1: "Agents read every document,",
  line2: "and flag what doesn't add up.",
  /** The pill that lands on the doctored statement in the pile; the portal's own word for it. */
  flagged: "Flagged",
};

/** The captured screens, in public/. Sizes are CSS px of the 1440 x 900 window they were taken in (at 3x). */
export const SCREENS = {
  trace: { src: "ui/story/trace.png", w: 1440, h: 900 },
  detection: { src: "ui/story/detection-top.png", w: 1440, h: 1100 },
  summary: { src: "ui/story/case-summary.png", w: 1440, h: 900 },
};
