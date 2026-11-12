# Brief - GLDB Anomaly Detection Portal demo film

## Inputs
- Product: GLDB Anomaly Detection Portal (`frontend/`), UI captured from the real app on its mock backend, case GLDB-02 (Anchorpoint Insurance Brokers, 20 documents).
- Brand: `frontend/DESIGN.md` tokens (ink #12203d, steel #355f96, paper #f9f9f8). Font: Inter, standing in for the portal's system UI stack.
- Format: 16:9, 1920x1080, 30 fps.
- Sound: none. The owner adds voiceover/music and sets timing themselves.

## Timing
Not beat-synced (`BEAT_SYNCED = false`). Every time is in seconds in `video/src/timing.ts` (`HOOK`).

## The argument (whole film, draft)
One case is too much paper for one person to read; the portal reads all of it, and shows every step it took.

## Script - built so far
**Hook v3 (default) · 0:00-0:09.6 · buried.** Full-screen version of v2, built for a voiceover with a sigh at the end.
- Visual: the officer's screen fills the frame; PDFs pile up, tabs squash, pings stack bottom-right. A page-coloured wash on the left carries the headlines.
- On-screen copy (placeholder): **Corporate loan / applications need / extensive / documentation.** then **Waaaaaaaaay / too many / documents / to review.** / **For one case.**
- Signature move: "Waaaay" grows smoothly to 28 a's, across into the right side of the frame, opening documents as it goes.
- Ending: at 7.1 s the pile freezes, blurs and dims; one large ping lands centre: Priya, Sales Team, "Customer's on the line again. Any update on Anchorpoint's loan status?" Hold to 9.6 s for the sigh.

**Hook v2 (default) · 0:00-0:11 · overwhelm.** No app yet; the app is introduced as the solution later.
- Visual: the officer's screen (generic browser + PDF viewer, no real product's look). PDF windows pile up faster and faster, tabs multiply until they are icons, pings from other teams stack bottom-right. Slow push toward the screen.
- On-screen copy (placeholder): **Corporate loan / applications need / extensive / documentation.** then **Waaaaaaaaay / too many / documents / to review.** / **For one case.**
- Pings (placeholder, invented people): "Hey, have you reviewed the Anchorpoint docs yet?", "How's the case going? Committee is at 3pm", "Customer emailed us again", "2 more statements just came in, added to the folder", "Any update??", "Need the KYC checks before sign-off".

**Hook v1 (kept behind HOOK_VERSION) · 0:00-0:11 · package + tooMany.** The portal's upload box filling with the case's 20 real files, then a pull-back. Renders in `video/renders/v1/`.

**Section 2 · after the hook · 0:09 · waiting (v2, default).**
- Visual: two silhouette avatars, roles only. Loan Officer: glasses, tie, slow nod, lens glint. Customer: phone to the ear, fidgets, sweat drop, "..." typing bubble, pulse ring. A clock between them fast-forwards 9:02 to 10:14; dashed line joins them ("on the other end"). Status pills: "Reviewing page 14 of 95" creeps to 19; "Checked inbox 1×" climbs to 8×.
- On-screen copy (locked, the script line): **A loan officer may spend over an hour / staring at details on the screen,** then **while the loan customer anxiously waits / on the other end for any news of approval.**
- v1 (initials, invented names) kept behind WAITING_VERSION.

**Section 3 · after section 2 · 0:09.5 · tamper.**
- Visual: split screen. Left: Bad Actor silhouette (horns, tail, scheming eyes, grin). Right: corporate bank statement, zoomed; cursor selects the closing balance 182,400.00 and types 812,400.00 (digits swapped) with tiny tells (lighter weight, a hair low, wider spacing). Then the statement shrinks into an 18-document grid and blends in.
- On-screen copy (owner chose the tidied line): **Even worse, when bad actors are involved, / smaller anomalies are even harder to catch.**

**Section 4 · after section 3 · 0:10.5 · portal (the product arrives).**
- Visual: (A) scan line over the pile; checks on each document; the doctored statement flagged. (B) push in, cut to the real Agent trace; rows light up (amber where a finding was made). (C) the real Detection panel for the statement: the forged figure boxed in the preview, Flagged badge pulse, push to the HIGH finding.
- On-screen copy: **GLDB Anomaly Detection Portal** / **Agents read every document, and flag what doesn't add up.** (owner: capture what's important, not the whole line).
- Data: the portal's real UI on its mock backend; the detection report is written for the story (BS.TOTALS high, font weight medium, PDF editor medium), in the backend's real report shape. File names on screen are invented.
- The app's header reads "GLDB Fraud Review"; the film names the product "GLDB Anomaly Detection Portal" (owner's call).

**Section 5 · after section 4 · 0:11.5 · upload (how it works, step 1).**
- Format for every product section from here: step tracker on top (Upload / Detect / Score, owner's names), one headline, a short line that changes with each beat; the real portal in the same window below.
- Visual: drag the package in, 20 rows cascade, click Upload; table fills row by row (Processing, type stamps on, Extracted); scanned December statement read by a beam into the Extracted Information card.
- On-screen copy: **Upload the whole package.** / **Every file is classified and its key fields read, even from scans.**

**Section 6 · detect (step 2) · 12.5 s.** Lead agent's plan types out; six checks branch out and run; four findings; catches: doctored figure, mismatched address. Copy: **Agents investigate every document.** / beats: A lead agent plans the investigation. → Specialists check metadata, fonts, alignment, totals, spelling, and across documents. → A doctored figure or a mismatched address gets caught.

**Section 7 · trace (step 2) · 10.5 s.** Real Agent trace, a tool call opened and its output boxed; real audit trail. Copy: **And it's not a black box.** / The agent trace shows every step, every tool call, and what it returned. → Every finding can be audited.

**Section 8 · score (step 3) · 13.5 s.** Real Risk Summary (High 72); real review dialog with What / Where / Why; one finding dismissed with a reason and a note; saved. Copy: **Every case gets a risk rating.** / With the exact evidence behind it. → What was flagged, where, and why. → Officers confirm or dismiss each finding, and add notes.

## Assumptions (veto any)
0. v2: people, teams and filenames are invented; documents are schematic (grey bars, made-up figures). The company name Anchorpoint Insurance Brokers comes from the test fixture.
1. On-screen headlines included by default; `Master-NoText` has none.
2. Real filenames from the regression fixture are shown, including personal names in filenames (e.g. "MuhammadD_..."). Check this data is synthetic before publishing.
3. The upload hint keeps the portal's own em-dash ("PDF · PNG · JPG — up to 25MB each"), as it is the product's real string.
4. "Waaaay" kept from the owner's script as the hook's one playful move.
5. Inter used instead of the system font stack so renders match on every machine.
