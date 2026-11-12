# GLDB Fraud Detection Portal - demo film (Remotion)

Visuals only: no music, no voiceover. You set the timing.

So far this is the **hook** (about 11 s). No app on screen yet: the app is the solution and comes later.

**v3 "buried" (current, default), 9.6 s.** Full screen: the loan officer's screen fills the frame. PDF windows pile up,
browser tabs multiply until they squash to icons, pings from other teams stack in the corner. Headlines sit on a soft
wash on the left: "Corporate loan applications need extensive documentation.", then "Waaaaaaaaay too many documents
to review. For one case." **"Waaaay" stretches smoothly to 28 a's, running across into the right of the frame, and
documents keep opening as it grows.** At 7.1 s everything stops and dims, one big
ping lands in the middle ("Customer's on the line again. Any update on Anchorpoint's loan status?") and the shot holds about
2.5 s for your sigh.

**v2 "overwhelm"** (the same pile on a screen at the side) and **v1 "portal"** (the upload box) are kept. Switch with
`HOOK_VERSION` in `src/timing.ts`; their renders are in `renders/v2/` and `renders/v1/`.

**Section 2 "waiting", 9 s.** The script line is the caption: "A loan officer may spend over an hour staring at
details on the screen," then "while the loan customer anxiously waits on the other end for any news of approval."
Two silhouette avatars, roles only: the Loan Officer (glasses, tie, a slow nod toward the screen, a glint on the
lenses) and the Customer (phone to the ear, fidgeting, a sweat drop, a "..." typing bubble), either side of a clock
racing from 9:02 to 10:14. Her status creeps from page 14 to 19 of 95; their "Checked inbox" count climbs to 8×.
The customer starts pulsing and typing when their half of the caption appears. The first version (initials and
names) is kept behind `WAITING_VERSION`; its renders are in `renders/v1/`.

**Section 3 "tamper", 9.5 s.** Caption: "Even worse, when bad actors are involved, / smaller anomalies are even harder
to catch." Split screen: on the left, a Bad Actor (the same silhouette style, with devil horns, a tail, scheming eyes
and a grin, snickering); on the right, a corporate bank statement zoomed in. A text cursor selects the closing balance
182,400.00 and types 812,400.00 over it (two digits swapped). The forgery's tells are deliberately tiny: a slightly
lighter weight, a hair lower, a little wider spaced. Then the statement shrinks into a grid of the case's other
documents and is impossible to pick out.

**Section 4 "portal", 12.5 s. The real app arrives, introduced properly.** Plain text centred: "So that's why we
introduce...", then "GLDB Fraud Detection Portal" big and centred with a line drawing in. The name glides up to the
top band as the portal's real Case Summary rises in, tilted in 3D. The real Agent trace swings in from the other side;
its rows light up as the agents work (amber where they found something): "Agents read every document,". The real
Detection screen arrives angled, the forged 812,400.00 boxed in red in the preview, then turns flat as the camera
pushes in to the HIGH finding: "and flag what doesn't add up." The first version (the pile scanned, flat shots) is
kept behind `PORTAL_VERSION`; its renders are in `renders/v1/`. Screens are captured from `frontend/` on its mock
backend by `capture/story.spec.ts`, with a detection report written for the story.

**Section 5 "upload", 11.5 s. How it works, step 1.** From here on every product section carries the same step
tracker at the top, in the portal's own words (its sign-in page): **1 Upload, 2 Check, 3 Score**, the current step lit.
Headline "Upload the whole package." A cursor drags a fanned stack of "20 files" into the portal's upload box (it
lights up as the stack hovers), the case's 20 files cascade in, and it clicks "Upload 20 files". The box clears and
the documents table fills row by row: a spinner and "Processing", then the document type stamps on, the mark turns
into a green tick and the status flips to "Extracted"; "Documents n of 11 required" climbs as required types are
covered. "Every file is classified and its key fields read," Then the scanned December statement (crooked, grainy,
its closing balance January's opening balance): a scan beam runs down the page, boxing each field it reads, and the
portal's Extracted Information card fills in as it goes, with confidences: "even from scans." The page behind is the
real portal (`capture/howitworks.spec.ts`); the upload box, table and fields card are rebuilt from its measured styles.

Renders: `renders/film-so-far.mp4` (hook + sections 2-5, 52.1 s), `renders/05-upload.mp4` (section 5 alone), `renders/04-portal.mp4` (section 4 alone), `renders/03-tamper.mp4` (section 3 alone), `renders/02-waiting.mp4` (section 2 alone), each with a
`-no-text` twin. `renders/hook.mp4` (with headlines), `renders/hook-no-text.mp4` (same shots, no headlines), `renders/hook-poster.png`.

## Fine-tuning

```bash
cd video
npm install
npm run dev        # Remotion Studio: scrub, preview, edit props live
```

- **Timing (section 5):** `UPLOAD` in `src/timing.ts`: `dragStart`/`dropAt`, `cascadeEnd`, `clickAt`, `tableAt`, `classifyStart`/`classifyEnd`, `scanAt`, `beamStart`/`beamEnd`, `headlineIn`/`line1In`/`line2In`, `length`.
- **Words, files, fields (section 5):** `src/data/upload.ts`: the steps (`STEPS`), the captions, the package (`PACKAGE`: file and type), the scanned statement (`DEC_STATEMENT`) and the fields read off it (`SCAN_FIELDS`).
- **Timing (section 4):** `PORTAL2` in `src/timing.ts`: `introIn`/`introOut`, `titleIn`, `underline`, `heroStart`/`heroEnd`, `traceAt`, `traceStart`/`traceEnd`, `detectionAt`, `boxIn`, `flatten`/`flattenEnd`, `findingAt`, `line1In`/`line2In`, `length`. The angles are in `src/scenes/PortalScene2.tsx` (`summaryTilt`, `traceTilt`, `detectionTilt`, in degrees).
- **Words (section 4):** `src/data/portal.ts`. The portal's own text is in the screenshots; to change it, change the story report in `capture/story.spec.ts` and re-run the capture (see Where the UI comes from).
- **Timing (section 3):** `TAMPER` in `src/timing.ts`: `line1In`, `cursorIn`/`cursorAt`, `select`, `erase`, `typeStart`/`typeEnd`, `line2In`, `shrinkStart`/`shrinkEnd`, `length`.
- **Words and figures (section 3):** `src/data/tamper.ts`: the caption, the statement's rows, and `before`/`after` for the edited balance. The bad actor is drawn in `src/components/Silhouette.tsx`.
- **Timing (section 2):** `WAITING2` in `src/timing.ts`: `officerLineIn`, `customerLineIn`, `clockStart`/`clockEnd` (how long the hour takes), `pulseEvery`, `fidget`, `length`.
- **Words (section 2):** `CAPTION` and `ROLES` in `src/data/waiting.ts`; page and inbox counts and the clock's times are in the same file. The avatars are drawn in `src/components/Silhouette.tsx`.
- **Timing (v3):** `src/timing.ts`, the `OVERWHELM3` object, all in seconds. Line the knobs up with your voiceover: `title1In`/`title1Out` (first line), `wayIn`, `stretchStart`/`stretchEnd` (how long you drag the "waaaay": the a's, and their documents, land evenly in between), `title2In`, `footnoteIn`, `pings` (one per message), `finalPingIn` (the sigh moment), and `length` (sets how long the hold lasts after it).
- **How long the word gets:** `WAY3_MAX_A` (28) in `src/data/overwhelm.ts`; `WAY3_DOCS` is how many documents open during the stretch.
- **Final ping text:** `FINAL_PING` in `src/data/overwhelm.ts`. Messages before it: `PINGS3` (add or remove one, and its time in `OVERWHELM3.pings`).
- **Timing (v2):** `src/timing.ts`, the `OVERWHELM` object. Everything is in seconds: when each headline rises and leaves, the first and last PDF window, the first and last tab, `pings` (one time per message), when the "a"s stretch, the camera push. `ramp` sets how frantic the pile-up gets. Save and Studio updates.
- **Words (v2):** `src/data/overwhelm.ts`: the PDFs (`DOCS`), extra tabs (`EXTRA_TABS`), the messages (`PINGS`: name, team, text, avatar colour) and the headlines (`COPY2`). `\n` is a line break; lines never wrap by themselves. Add or remove a message and add or remove its time in `OVERWHELM.pings`.
- **v1:** timing in `HOOK`, words and the case's real filenames in `src/data/hook.ts`.
- **Colours / font:** `src/theme.ts` (taken from `frontend/DESIGN.md`). Inter is bundled in `public/fonts`.
- **Layout:** `src/scenes/hookLayout.ts` (box position, size, scale `U`).
- **No headlines:** render `Master-NoText`, or set `showText: false` in Studio's props panel.

## Rendering

For your editor, export in 4K at high quality (sharp text, and it still looks crisp in a 1080p timeline):

```bash
npx remotion render Master renders/4k/film-so-far-4k.mp4 --scale=2 --crf=10 --x264-preset=slow --color-space=bt709
npx remotion render Master-NoText renders/4k/film-so-far-no-text-4k.mp4 --scale=2 --crf=10 --x264-preset=slow --color-space=bt709
```

Lowest loss of all, for editing (big files, plays in Premiere, Final Cut, DaVinci Resolve):

```bash
npx remotion render Master renders/film-so-far.mov --codec=prores --prores-profile=4444 --scale=2
```

Quick previews (1080p, small files; text is softer):

```bash
npx remotion render Master renders/film-so-far.mp4 --color-space=bt709 --crf=16
npx remotion render Scene-Waiting renders/02-waiting.mp4 --color-space=bt709 --crf=16
npx remotion render Scene-Tamper renders/03-tamper.mp4 --color-space=bt709 --crf=16
```

Single scenes for quick checks: `Scene-Upload` (section 5), `Scene-Portal` (section 4), `Scene-Tamper` (section 3), `Scene-Waiting` (section 2), `Scene-Buried` (v3), `Scene-Overwhelm` (v2), `Scene-Package` and `Scene-TooMany` (v1).

## Where the UI comes from (v1)

The upload box is the portal's own UI rebuilt in React from its measured DOM (sizes x1.45), not a drawing.
`capture/` reruns that capture: it serves the real frontend against the frontend's own mock backend
(`frontend/e2e/support/mockBackend.ts`) with the captured case `anchorpoint` (GLDB-02), and saves screenshots
and measurements to `public/ui/`.

```bash
# from frontend/, after npm ci
VITE_API_URL=http://api.test npx vite build --outDir dist-capture
CAPTURE_DIST=dist-capture npx playwright test -c ../video/capture/playwright.config.ts            # all captures
CAPTURE_DIST=dist-capture CAPTURE_OUT=../video/public/ui/story npx playwright test -c ../video/capture/playwright.config.ts story   # section 4's screens
```

(`capture/` needs `node_modules` linked to the frontend's: `ln -s ../../frontend/node_modules video/capture/node_modules`.)
