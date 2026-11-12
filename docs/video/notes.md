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

## Wave 6 - section 2, "waiting"
- Owner's picks: avatar circles + clock (over split screen, illustrated people, footage); the clock fast-forwards to show "over an hour".
- Built WaitingScene (7 s) after the v3 hook in Master; knobs in WAITING, words and people in data/waiting.ts.
- Decision: the avatars are already arriving on the first frame (avatarsIn -0.25) so the cut from the hook lands on a picture.
- Measured: stills at frames 0, 10, 60, 120, 200 and a contact sheet every 18 frames: nothing clipped, counters and clock agree (10:14, page 19, 14×). Not watched in motion.

## Wave 7 - section 2 v2: silhouettes, roles only, caption = script
- Owner: use stock-style person icons (own version, some personality), no names (just Loan Officer and Customer), follow the caption.
- Built WaitingScene2 + Silhouette; WAITING_VERSION "v2" default, v1 renders moved to renders/v1/. Section is 9 s so the 28-word caption can be read.
- Measured: stills at frames 0, 100, 140, 230: caption on four lines, no wrapping or clipping; clock and counters agree. Not watched in motion.

## Wave 8 - gentler numbers
- Owner: "of 212" -> "of 95"; inbox checks end at 8x, not 14x.
- Owner noticed soft quality: previews were 1080p at about 1.8 Mbit/s. Deliverables now rendered 4K, CRF 10, x264 slow (renders/4k).

## Wave 9 - section 3, "tamper"
- Owner: split screen, bad actor icon with devil horns, the other side amending a value.
- Owner's picks: bank statement closing balance (182,400.00 -> 812,400.00); end "lost in the pile"; caption tidied.
- Decision: the forgery's tells match the product's later checks (fonts, alignment, totals) so the film can pay them off when the product appears.
- Measured: stills at frames 0, 70, 105, 125, 175, 210, 280: statement readable at 2.1x, no clipping, the doctored statement lands in slot 8 of the grid. Not watched in motion.
