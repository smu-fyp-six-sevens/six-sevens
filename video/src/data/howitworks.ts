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
    { text: "Every finding can be audited.", at: "auditAt", accent: true },
  ],
};

export const SCORE_COPY = {
  headline: "Every case gets a risk rating.",
  beats: [
    { text: "With the exact evidence behind it.", at: "line1In" },
    { text: "What was flagged, where, and why.", at: "reviewAt" },
    { text: "Officers confirm or dismiss each finding, and add notes.", at: "tickAt", accent: true },
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
};
