// Sections 6-8 - "how it works", steps 2 and 3. Captions, and what the rebuilt parts say. The portal's own wording
// is in the captured screens (capture/story2.spec.ts). Everything is invented for the film's story.

export const DETECT_COPY = {
  headline: "Agents investigate every document.",
  beats: [
    { text: "A lead agent plans the investigation.", at: "planIn" },
    { text: "Specialists check metadata, fonts, alignment, totals, spelling, and across documents.", at: "specialistsIn" },
    { text: "A doctored figure or a mismatched address gets caught.", at: "catchAt", accent: true },
  ],
};

/** The lead agent's plan, typed out line by line. */
export const PLAN = [
  "Bank statements: check the totals, fonts and layout.",
  "Every file: check metadata and spelling.",
  "Across documents: compare names, addresses and figures.",
];

/** The six checks; `found` is how many findings each turns up (the case's story). */
export const CHECKS: { name: string; agent: string; found: number }[] = [
  { name: "Metadata", agent: "Digital Authenticity Analyst", found: 1 },
  { name: "Fonts", agent: "Layout & Rendering Analyst", found: 1 },
  { name: "Alignment", agent: "Layout & Rendering Analyst", found: 0 },
  { name: "Totals", agent: "Content Validation Analyst", found: 1 },
  { name: "Spelling", agent: "Content Validation Analyst", found: 0 },
  { name: "Across documents", agent: "Cross-document analysis", found: 1 },
];

export const CATCHES = {
  figure: {
    label: "Doctored figure",
    finding: "Closing balance 812,400.00 does not reconcile: the transactions give 182,400.00.",
  },
  address: {
    label: "Mismatched address",
    a: { doc: "Anchorpoint_BusinessProfile.pdf", label: "Registered address", value: "6 Serangoon North Avenue 5, #02-11" },
    b: { doc: "Anchorpoint_BankStatement_Jan2026.pdf", label: "Company address", value: "18 Cross Street, #07-01" },
    finding: "The registered address differs between the business profile and the bank statement.",
  },
};

export const TRACE_COPY = {
  headline: "And it's not a black box.",
  beats: [
    { text: "The agent trace shows every step, every tool call, and what it returned.", at: "line1In" },
    { text: "Every step can be audited.", at: "auditAt", accent: true },
  ],
};

export const SCORE_COPY = {
  headline: "Every case gets a risk rating.",
  beats: [
    { text: "With the exact evidence behind it.", at: "line1In" },
    { text: "What was flagged, where, and why.", at: "reviewAt" },
    { text: "Officers confirm or dismiss each finding, and add notes.", at: "tickAt", accent: true },
    { text: "And it recommends documents that would reduce overall risk score.", at: "addAt" },
  ],
  what: "What",
  where: "Where",
  why: "Why",
  saved: "Review saved",
};

export const SCREENS_STORY2 = {
  risk: { src: "ui/story/risk.png", w: 1440, h: 900 },
  review: { src: "ui/story/review.png", w: 1440, h: 900 },
  dismissed: { src: "ui/story/review-dismissed.png", w: 1440, h: 900 },
  trace: { src: "ui/story/trace.png", w: 1440, h: 900 },
  traceTool: { src: "ui/story/trace-tool.png", w: 1440, h: 900 },
  audit: { src: "ui/story/audit.png", w: 1440, h: 900 },
  additional: { src: "ui/story/additional.png", w: 1440, h: 900 },
  dashboard: { src: "ui/story/dashboard-month-full.png", w: 1440, h: 2169 },
  finaliseReady: { src: "ui/story/finalise-ready.png", w: 1440, h: 900 },
  finaliseConfirm: { src: "ui/story/finalise-confirm.png", w: 1440, h: 900 },
  finaliseDone: { src: "ui/story/finalise-done.png", w: 1440, h: 900 },
};

/** Additional Documents, measured on its capture (CSS px): the score line and each recommended document. */
export const ADDITIONAL_BOXES = {
  now: { x: 125, y: 264, w: 290, h: 36 },
  items: [
    { x: 117, y: 309, w: 1283, h: 100 },
    { x: 117, y: 410, w: 1283, h: 101 },
    { x: 117, y: 512, w: 1283, h: 148 },
  ],
};

// ---- Sections 9-11: the business value and the call to action ----------------------------------

export const FINALISE_COPY = {
  headline: "Finally, lock the case.",
  beats: [
    { text: "Finalised cases become view-only, with every step on record.", at: "readyAt", accent: true },
  ],
};

export const FINALISE_BOXES = {
  sidebarSummary: { x: 44, y: 170 },
  button: { x: 1240.75, y: 205.5, w: 139.25, h: 37.5 },
  confirm: { x: 586.5, y: 491.75, w: 132, h: 44.5 },
  card: { x: 553.7, y: 188.5, w: 847.3, h: 194 },
  badge: { x: 672.7, y: 205.5, w: 90.9, h: 24 },
  banner: { x: 1140, y: 112, w: 330, h: 22 },
};

export const DASHBOARD_COPY = {
  headline: "Every case, at a glance.",
  beats: [
    { text: "The whole pipeline, from upload to finalised.", at: "pipelineAt" },
    { text: "The most common flags, across every case.", at: "flagsAt" },
    { text: "Risk across every case in view.", at: "riskAt" },
    { text: "And the riskiest cases first.", at: "attentionAt", accent: true },
  ],
};

/** The dashboard with a month of cases (story2-dashboard-month.json), CSS px of the full page. */
export const DASHBOARD_BOXES = {
  pipeline: { x: 24, y: 135, w: 1377, h: 172 },
  flags: { x: 24, y: 323, w: 750, h: 342 },
  types: { x: 788, y: 323, w: 613, h: 342 },
  risk: { x: 24, y: 679, w: 1377, h: 297 },
  attention: { x: 24, y: 990, w: 808, h: 516 },
};

export const DIFFERENCE_COPY = {
  line1: "What took hours of screening now takes minutes.",
  line2: "The AI does the reading. Your loan officers make the call.",
  line3: "And customers hear back sooner.",
  officerBusy: "Reviewing with the portal",
  officerDone: "Review complete",
  customerWaiting: "Waiting for news",
  customerDone: "Heard back",
  news: { name: "GLDB Lending", team: "Loan team", text: "Good news on your loan application, we're moving to approval.", tint: "#355f96" },
};

export const CTA_COPY = {
  lead: "Bring your next loan case.",
  name: "GLDB Anomaly Detection Portal",
  sub: "Upload the package, and see what the portal catches.",
  button: "Try it out today",
};
