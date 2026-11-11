# Brief - GLDB Fraud Detection Portal demo film

## Inputs
- Product: GLDB Fraud Detection Portal (`frontend/`), UI captured from the real app on its mock backend, case GLDB-02 (Anchorpoint Insurance Brokers, 20 documents).
- Brand: `frontend/DESIGN.md` tokens (ink #12203d, steel #355f96, paper #f9f9f8). Font: Inter, standing in for the portal's system UI stack.
- Format: 16:9, 1920x1080, 30 fps.
- Sound: none. The owner adds voiceover/music and sets timing themselves.

## Timing
Not beat-synced (`BEAT_SYNCED = false`). Every time is in seconds in `video/src/timing.ts` (`HOOK`).

## The argument (whole film, draft)
One case is too much paper for one person to read; the portal reads all of it, and shows every step it took.

## Script - built so far
**Hook v2 (default) · 0:00-0:11 · overwhelm.** No app yet; the app is introduced as the solution later.
- Visual: the officer's screen (generic browser + PDF viewer, no real product's look). PDF windows pile up faster and faster, tabs multiply until they are icons, pings from other teams stack bottom-right. Slow push toward the screen.
- On-screen copy (placeholder): **Corporate loan / applications need / extensive / documentation.** then **Waaaaaaaaay / too many / documents / to review.** / **For one case.**
- Pings (placeholder, invented people): "Hey, have you reviewed the Anchorpoint docs yet?", "How's the case going? Committee is at 3pm", "Customer emailed us again", "2 more statements just came in, added to the folder", "Any update??", "Need the KYC checks before sign-off".

**Hook v1 (kept behind HOOK_VERSION) · 0:00-0:11 · package + tooMany.** The portal's upload box filling with the case's 20 real files, then a pull-back. Renders in `video/renders/v1/`.

## Assumptions (veto any)
0. v2: people, teams and filenames are invented; documents are schematic (grey bars, made-up figures). The company name Anchorpoint Insurance Brokers comes from the test fixture.
1. On-screen headlines included by default; `Master-NoText` has none.
2. Real filenames from the regression fixture are shown, including personal names in filenames (e.g. "MuhammadD_..."). Check this data is synthetic before publishing.
3. The upload hint keeps the portal's own em-dash ("PDF · PNG · JPG — up to 25MB each"), as it is the product's real string.
4. "Waaaay" kept from the owner's script as the hook's one playful move.
5. Inter used instead of the system font stack so renders match on every machine.
