# GLDB Fraud Detection Portal - demo film (Remotion)

Visuals only: no music, no voiceover. You set the timing.

So far this is the **hook** (scenes 1-2, about 11 s):

| Scene | On screen | Script line it covers |
|---|---|---|
| 1 `package` | The real package of case GLDB-02 (20 files) drops into the portal's upload box, faster and faster | "Corporate loan applications require extensive documentation..." |
| 2 `tooMany` | Pull back to all 20 files; "Way" stretches to "Waaaaaaaaay too many documents to review." | "...waaaay too many documents to review for a case." |

Renders: `renders/hook.mp4` (with headlines), `renders/hook-no-text.mp4` (same shots, no headlines), `renders/hook-poster.png`.

## Fine-tuning

```bash
cd video
npm install
npm run dev        # Remotion Studio: scrub, preview, edit props live
```

- **Timing:** `src/timing.ts`, the `HOOK` object. Everything is in seconds: scene lengths, when the headline rises, when the first and last file land, when the "a"s stretch. Save and Studio updates.
- **Words:** `src/data/hook.ts` (`COPY`). `\n` is a line break; lines never wrap by themselves. `wayMaxA` is how many "a"s.
- **Files shown:** `FILES` in the same file (the case's real filenames, in upload order).
- **Colours / font:** `src/theme.ts` (taken from `frontend/DESIGN.md`). Inter is bundled in `public/fonts`.
- **Layout:** `src/scenes/hookLayout.ts` (box position, size, scale `U`).
- **No headlines:** render `Master-NoText`, or set `showText: false` in Studio's props panel.

## Rendering

```bash
npx remotion render Master renders/hook.mp4 --color-space=bt709 --crf=16
npx remotion render Master-NoText renders/hook-no-text.mp4 --color-space=bt709 --crf=16
npx remotion still Master renders/hook-poster.png --frame=320
```

Single scenes for quick checks: `Scene-Package`, `Scene-TooMany`.

## Where the UI comes from

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
