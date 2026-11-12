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
racing from 9:02 to 10:14. Her status creeps from page 14 to 19 of 212; their "Checked inbox" count climbs to 14×.
The customer starts pulsing and typing when their half of the caption appears. The first version (initials and
names) is kept behind `WAITING_VERSION`; its renders are in `renders/v1/`.

Renders: `renders/film-so-far.mp4` (hook + section 2, 18.6 s), `renders/02-waiting.mp4` (section 2 alone), each with a
`-no-text` twin. `renders/hook.mp4` (with headlines), `renders/hook-no-text.mp4` (same shots, no headlines), `renders/hook-poster.png`.

## Fine-tuning

```bash
cd video
npm install
npm run dev        # Remotion Studio: scrub, preview, edit props live
```

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

```bash
npx remotion render Master renders/hook.mp4 --color-space=bt709 --crf=16
npx remotion render Master-NoText renders/hook-no-text.mp4 --color-space=bt709 --crf=16
npx remotion render Scene-Waiting renders/02-waiting.mp4 --color-space=bt709 --crf=16
npx remotion still Master renders/hook-poster.png --frame=175
```

Single scenes for quick checks: `Scene-Waiting` (section 2), `Scene-Buried` (v3), `Scene-Overwhelm` (v2), `Scene-Package` and `Scene-TooMany` (v1).

## Where the UI comes from (v1)

The upload box is the portal's own UI rebuilt in React from its measured DOM (sizes x1.45), not a drawing.
`capture/` reruns that capture: it serves the real frontend against the frontend's own mock backend
(`frontend/e2e/support/mockBackend.ts`) with the captured case `anchorpoint` (GLDB-02), and saves screenshots
and measurements to `public/ui/`.

```bash
# from frontend/, after npm ci
VITE_API_URL=http://api.test npx vite build --outDir dist-capture
CAPTURE_DIST=dist-capture npx playwright test -c ../video/capture/playwright.config.ts
```

(`capture/` needs `node_modules` linked to the frontend's: `ln -s ../../frontend/node_modules video/capture/node_modules`.)
