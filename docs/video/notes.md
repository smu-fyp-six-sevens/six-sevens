# Notes

## Wave 1 - hook (scenes 1-2)
- Built the hook only, on request ("Corporate ... for a case."). Visuals only, no audio.
- Decision: timing by hand in seconds (owner wants to control it), not beat-synced.
- Decision: UI rebuilt from the real portal's DOM (captured via the frontend's mock backend), not screenshots, so it stays sharp and editable.
- Measured: no text wraps or clips in stills at frames 20, 75, 140, 162, 175, 200, 230, 260, 320; frames 164/165 identical in the box region (continuous cut); 26 on-screen words over 11 s (2.4/s).
- Not checked: nobody has watched it in motion yet.

## Wave 2 - hook rebuilt as "overwhelm" (v2)
- Owner: the hook should not show the app (it is the solution, introduced later); show the pile instead: pings from teams, many PDFs, tabs of related documents.
- Decision: v2 is the default; v1 kept behind `HOOK_VERSION` in timing.ts, renders moved to video/renders/v1/.
- Decision: generic chat and browser UI, no real product's look; invented names and filenames.
- Measured: stills at frames 60, 75, 140, 200, 250, 320 and a contact sheet every 22 frames: no clipped headline, toasts do not overlap, headline stays 90+ px from the left edge through the push.
- Words on screen: 15 headline + about 45 in messages over 11 s. The messages are texture, read at a glance; cut some if it feels busy.
- Not checked: nobody has watched it in motion yet.
