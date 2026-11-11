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

## Wave 3 - full screen, "waaaay" drives the pile, ending ping (v3)
- Owner: graphics full screen with room for text; play with "waaaay"; end on a ping they sigh at in the voiceover.
- Owner's picks: each extra "a" opens another document; the ending is a big centre ping with a dim and hold; text "Customer's on the line again. Any update on Anchorpoint?"
- Decision: v3 default; v2 and v1 kept behind HOOK_VERSION, renders in renders/v2 and renders/v1.
- Decision: the camera pushes into the pile only; tabs, pings and headlines stay fixed (a full-frame push slid the tabs off the top edge).
- Measured: stills at frames 100, 240, 260, 330 and a contact sheet every 24 frames: headlines clear of the edge, tabs and pings inside the frame, the final ping fully legible.
- Not checked: nobody has watched it in motion yet.

## Wave 4 - longer, smoother "waaaay"; clearer final ping
- Owner: "waaaay" longer so it eats into the right side; smoother a's; final ping should say "Anchorpoint's loan status".
- Done: 28 a's (word ends near x 1700), continuous growth with each arriving "a" opening its width (ease-in-out, `stretchEase`), stretch now 5.0-7.0 s; 8 documents open spread along it; a paper-coloured halo keeps the word legible over the windows.
- Final ping: "Customer's on the line again. Any update on Anchorpoint's loan status?"
- Measured: stills at frames 165, 180, 195, 215, 300: no clipping, word inside the frame, ping legible. Not watched in motion.

## Wave 5 - Sales Team, faster
- Priya's team is now "Sales Team" (both pings).
- Every v3 time tightened by about 20%: 12 s -> 9.6 s; final ping at 7.1 s, hold 2.5 s; stretch 4.0-5.6 s.
- Measured: stills at frames 50, 175, 245. Not watched in motion.
