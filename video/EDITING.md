# Editing the GLDB demo film

A short guide to previewing the film on your own computer, changing words and timing, and exporting it.

The film is code, made with [Remotion](https://www.remotion.dev): each scene is a React component, and every
time and every word lives in two kinds of files you can edit without touching the animation code.

## What's in it so far

| Section | Time in film | What it shows |
|---|---|---|
| Hook ("buried") | 0:00 - 0:09.6 | The officer's screen buried in PDFs, tabs and pings; "Waaaaay too many documents to review."; ends on the big ping and a hold for your sigh |
| Section 2 ("waiting") | 0:09.6 - 0:18.6 | Loan Officer and Customer avatars either side of a clock racing 9:02 to 10:14 |
| Section 3 ("tamper") | 0:18.6 - 0:28.1 | A bad actor edits a bank statement's closing balance; it vanishes into the pile |
| Section 4 ("portal") | 0:28.1 - 0:38.6 | The real app: the pile is scanned and the statement flagged, the Agent trace lights up, the Detection panel shows the catch |

No sound: you add the voiceover and music in your video editor.

## 1. Get the branch

You need [Node.js](https://nodejs.org) 18 or newer (`node -v` to check) and git.

```bash
git clone https://github.com/smu-fyp-six-sevens/six-sevens.git
```

```bash
cd six-sevens
```

```bash
git checkout claude/practical-thompson-vcvcw4
```

Already have the repo? Then just:

```bash
git fetch origin claude/practical-thompson-vcvcw4
```

```bash
git checkout claude/practical-thompson-vcvcw4
```

## 2. Install (once)

```bash
cd video
```

```bash
npm install
```

## 3. Preview

```bash
npm run dev
```

This opens **Remotion Studio** in your browser (usually http://localhost:3000).

- **Left sidebar:** pick what to watch.
  - `Master`: the whole film so far, with captions.
  - `Master-NoText`: the same, without captions.
  - `Scene-Buried`, `Scene-Waiting`, `Scene-Tamper`, `Scene-Portal`: one section alone. These are quicker to work on.
- **Playback:** Space plays and pauses. The left and right arrow keys step one frame. You can also drag along the timeline at the bottom.
- **Right panel ("Props"):** switch `showText` off to hide the captions.
- **Live edits:** keep Studio open while you edit. Every time you save a file, the preview updates by itself.

If port 3000 is busy: `npm run dev -- --port 3001`.

## 4. Edit

Everything you're likely to change is in two folders.

**Timing: `src/timing.ts`.** Every time is in **seconds**, counted from the start of that section.

| Section | Object | The knobs you'll use most |
|---|---|---|
| Hook | `OVERWHELM3` | `stretchStart` / `stretchEnd` (how long you drag out "waaaay"), `finalPingIn` (the sigh moment), `length` (the hold after it), `pings` (one time per message) |
| Section 2 | `WAITING2` | `officerLineIn`, `customerLineIn`, `clockStart` / `clockEnd` (how long the "hour" takes), `length` |
| Section 4 | `PORTAL` | `scanStart` / `scanEnd`, `traceStart` / `traceEnd`, `detectionAt`, `zoomStart` / `zoomEnd`, `findingAt`, `length` |
| Section 3 | `TAMPER` | `cursorIn`, `select`, `typeStart` / `typeEnd`, `line2In`, `shrinkStart` / `shrinkEnd`, `length` |

**Words and numbers: `src/data/`.**

| File | What's in it |
|---|---|
| `overwhelm.ts` | Hook: the PDFs, the extra tabs, the chat pings (`PINGS3`), the final ping (`FINAL_PING`), the headlines (`COPY2`), and how long "Waaaay" gets (`WAY3_MAX_A`) |
| `waiting.ts` | Section 2: the caption (`CAPTION`), the role labels (`ROLES`), the page and inbox counts, the clock's start and end times |
| `portal.ts` | Section 4: the title and the two short lines. (The app's own wording is in the captured screenshots, from `capture/story.spec.ts`.) |
| `tamper.ts` | Section 3: the caption (`CAPTION3`), the statement's rows, and the edited figure (`before` / `after`) |

In a caption, `\n` is a line break. Lines never wrap on their own, so if a line gets too long, break it yourself.

### Common edits

- **Line the film up with your voiceover:** record the voiceover first. In Studio, scrub to where each word lands, note the time, and type it into the matching knob.
- **Change a caption:** edit the text in `src/data/...`, save, and check it in Studio.
- **Make a section longer or shorter:** change its `length`. The sections after it move along automatically.
- **Remove a chat ping from the hook:** delete it from `PINGS3` in `overwhelm.ts` *and* remove one time from `OVERWHELM3.pings`.
- **Change the edited figure:** `before` and `after` in `tamper.ts`.
- **Go back to an earlier version:**
  - Hook: `HOOK_VERSION` in `timing.ts` (`"v1"` is the upload box, `"v2"` is the side screen).
  - Section 2: `WAITING_VERSION` (`"v1"` uses initials and names).

The drawings themselves (avatars, horns, glasses and so on) are in `src/components/Silhouette.tsx`, and each scene's layout is in `src/scenes/`. You only need those to change how things look, not what they say or when.

## 5. Export

All of these write into `video/renders/`.

| Command | What you get |
|---|---|
| `npm run render` | **Use this one for your edit.** The whole film in 4K at high quality: `renders/4k/film-so-far-4k.mp4` |
| `npm run render:notext` | The same, without captions |
| `npm run render:preview` | A quick, small 1080p preview: `renders/film-so-far.mp4`. The text is softer, so don't use it for the final edit |
| `npm run still` | One frame as a PNG (change `--frame=` in `package.json`) |

One section on its own, in 4K:

```bash
npx remotion render Scene-Tamper renders/4k/03-tamper-4k.mp4 --scale=2 --crf=10 --x264-preset=slow --color-space=bt709
```

The least compression possible, if your editor (Premiere, Final Cut, DaVinci Resolve) takes ProRes. The files are big:

```bash
npx remotion render Master renders/film-so-far.mov --codec=prores --prores-profile=4444 --scale=2
```

The first render downloads a headless browser (about 90 MB), once. A 4K render of the whole film takes a few minutes.

## Troubleshooting

- **Studio shows an error after an edit:** usually a missing comma or quote in the file you just changed. Studio's error message names the file and line.
- **"Multiple versions of Remotion detected":** don't upgrade one `remotion` package on its own. Run `npx remotion upgrade`, which moves them all together.
- **Check the code before rendering:** `npm run typecheck` finds mistakes without opening Studio.
- **More detail:** `README.md` in this folder explains each scene. `docs/video/notes.md` at the repo root logs every decision so far.
