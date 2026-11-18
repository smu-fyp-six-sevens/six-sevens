---
target: Risk & Analysis page
total_score: 24
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 2
target_identity: "file:C:\\wamp64\\www\\six-sevens\\frontend\\src\\pages\\UploadPage.tsx"
target_fingerprint: "sha256:126948f61c13f9ec5ac3b3c953aff44110f92eb3e4fa93c5bc96e9e75c542849"
target_path: "C:\\wamp64\\www\\six-sevens\\frontend\\src\\pages\\UploadPage.tsx"
timestamp: 2026-11-18T04-30-53Z
slug: src-pages-uploadpage-tsx
---
Method: dual-agent (A: design review · B: detector + screenshots). Mock-data artifacts (score 48 under High 50–79; Media check company mismatch) excluded after checking backend banding.

## Design Health Score: 24/40 (Acceptable)
1 Status 3 · 2 Real world 3 · 3 Control 3 · 4 Consistency 2 · 5 Error prevention 2 · 6 Recognition 3 · 7 Efficiency 2 · 8 Minimalist 2 · 9 Error recovery 2 · 10 Help 2

## Design Specificity
Analysis brief is product-specific (headline, cited reasons, "except Hidden Text, which couldn't be verified", media provenance). Risk card (gauge, 4-band legend, level pill) is generic. Detector: markup clean; case-analysis.css 1 side-tab warning (L220, user-requested), 7px radii L38/506/544, 18px font L82; 4 out-of-scope (Claude Cost); L592 false positive.

## Priority Issues
- [P1] Overview's primary action is "Run again" even when current; review-findings CTA only conditional. Fix: demote Run again unless stale/failed; always-present "Review N findings →" at end of brief. /impeccable layout
- [P1] Risk level stated three times; gauge+legend ~220px pushes brief below laptop fold. Fix: drop header pill, fold legend behind "How the score works". /impeccable distill
- [P2] Reviews load error renders inside list under first document beside "Clean", no Retry. Fix: card-level error with Retry; suppress review tags until known. /impeccable harden
- [P2] Hover-only meaning (Hidden Text where/why, score change); aria-label on bare spans; Previous runs header aria-hidden. /impeccable harden
- [P2] Design-system breaks: dotted underline on Hidden Text, ▲/▼ arrows, 2px tab focus ring, 7px radii, 18px font; media side-edge is user's call. /impeccable polish

## Persona Red Flags
Alex: no next-unreviewed jump; tab count not "to review"; runs don't show which findings changed. Sam: tabs lack arrow keys; dots/✓ unlabeled; tooltip-only meaning; run cells unlabeled. Loan officer: no to-verdict count on Overview; "would help" not tied to a finding; Clean beside error.

## Minor
Mobile nav clipped, orphan kebab, truncated Across-documents preview, <44px toggles; runs Findings column counts only cross-doc; blank Change cell; low-contrast gauge labels; no page title.

## Questions
Findings as default tab? Gauge needed at all? Previous runs in Audit Trail?
