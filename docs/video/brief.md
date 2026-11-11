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
**Scene 1 · 0:00-0:05.5 · package**
- Visual: the portal's upload box; the case's 20 real files drop in, accelerating, list scrolls off frame.
- On-screen copy (placeholder): **Corporate loan / applications need / extensive documentation.**
- Motion: headline rises behind a line mask; rows drop and settle; headline lifts out in the last 0.35 s.

**Scene 2 · 0:05.5-0:11 · tooMany**
- Visual: continuous from scene 1; pull back until all 20 files fit on the right.
- On-screen copy (placeholder): **Case GLDB-02 · Anchorpoint Insurance Brokers** / **Waaaaaaaaay too many / documents to review.** / **20 files for a single case.**
- Motion: pull-back leaves the cut at speed and settles; "Way" stretches a letter at a time; slow drift after.

## Assumptions (veto any)
1. On-screen headlines included by default; `Master-NoText` has none.
2. Real filenames from the regression fixture are shown, including personal names in filenames (e.g. "MuhammadD_..."). Check this data is synthetic before publishing.
3. The upload hint keeps the portal's own em-dash ("PDF · PNG · JPG — up to 25MB each"), as it is the product's real string.
4. "Waaaay" kept from the owner's script as the hook's one playful move.
5. Inter used instead of the system font stack so renders match on every machine.
