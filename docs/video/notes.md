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

## Wave 10 - section 4, "portal"
- Owner: tailor the portal's data to the story; name it GLDB Fraud Detection Portal; caption only what's important.
- Captured the real frontend at 3x on its mock backend (capture/story.spec.ts) with a story detection report, plus element boxes (public/ui/story/boxes.json) for the overlays.
- Decision: the empty document preview in the capture is filled with the film's statement, boxed at the forged figure.
- Measured: stills at frames 0, 40, 62, 76, 100, 145, 185, 215, 260, 290, 300: overlays sit on their rows and badge; no clipping. Not watched in motion. The title sits over the pile during the 0.5 s push.

## Wave 11 - section 4 v2: a pronounced introduction, angles
- Owner: make the introduction more pronounced ("So that's why we introduce..." then the name, centred), more angles, less flat.
- Built PortalScene2: lead-in and name centred (the lead-in shows even without captions, it is the introduction), name glides to the top band; Case Summary rises tilted, the trace swings in from the right, detection from the left and flattens for the readable finding. Gentle sways keep angled shots alive. v1 kept behind PORTAL_VERSION.
- Measured: stills at frames 30, 75, 108, 125, 150, 200, 230, 275, 300, 350: no clipping of text; the final finding is flat and legible. Not watched in motion.

## Wave 12 - section 5, "upload"
- Owner: generate "Start by uploading the full package..."; keep the graphics consistent so viewers can follow the product sections, with some personality.
- Decision: one format for all product sections: the portal's own steps (Upload / Check / Score, from its sign-in page) as a tracker on top, one headline, one short line; the real portal in the window below.
- Captured documents table and fields card styles (capture/howitworks.spec.ts); rebuilt the moving parts. The fixture's scanned file carried a real-looking person and broker, so the scan is the film's own invented December statement (closing 96,250.00 = January's opening).
- Fixes after the first stills: the upload footer no longer takes space when empty (it covered the table header); the captured, already-processed table is hidden before the upload; framing widened.
- Measured: stills at frames 20, 40, 55, 70, 88, 115, 150, 190, 235, 265, 300, 340. Not watched in motion.

## Wave 13 - sections 6-8 together; steps renamed Upload / Detect / Score
- Owner: generate the rest of the "how it works" lines together, consistently; steps Upload > Detect > Score.
- One format, ProductHeader: tracker + headline + a line that changes per beat.
- Captured (capture/story2.spec.ts): risk summary (story risk: forged balance high, font medium, PDF editor medium, cross-document address high = High 72), the review dialog before and after a dismissal with a note, the trace with a tool call opened, the audit trail. The fixture case predates reviews, so reviews_required is set on it.
- Detect is rebuilt in the portal's style (no screen in the portal shows the plan and checks this way); Trace and Score are the real screens with overlays.
- Measured: stills across all three; the doctored-figure crop fixed after the first stills. Not watched in motion.

## Wave 14 - smoother hand-overs, accurate tags, recommendations
- Owner: the "why" box was off; transitions between stages and screens felt jerky; add the additional recommended documents to the risk part.
- Tags now use the measured text extents (risk-finding-summary, risk-finding-rationale) with labels outside the box.
- One transition for sections 5-8 (swipe.ts): overlapping swipe between shots and at stage boundaries; tracker animates the step change; headlines lift away at a section's end.
- Captured Additional Documents with a story recommendation set (two that would lower the score, one checklist request). Score is 16 s.
- Measured: stills of the tags, the recommendations, and frames either side of each stage boundary. Not watched in motion.

## Wave 15 - flat screens, zooms (sections 5-8)
- Owner: no angled screens from Upload to Score (not value adding); zooms in and out are welcome.
- Decision: removed every tilt and sway in sections 5-8; the swipe slides flat. Added camera paths (keys.ts): Detect close on the plan, out to the checks, in on each catch, out; Upload's scan in on the page then the fields; the audit trail zooms in along its entries; Score in on the gauge, out to the documents, in on the score line in Additional Documents.
- Sections 1-4 keep their angles (the owner asked for angles in section 4).
- Measured: stills of each zoom. Not watched in motion.

## Wave 16 - product renamed
- Owner: the product is now "GLDB Anomaly Detection Portal" (title card in section 4, docs). The app header in the captured screens still reads "GLDB Fraud Review".

## Wave 17 - the whole film, with sound
- Owner: piece the whole film together with voiceover, music and sound effects; show the business value; close as a call to action (B, "Try it on your next case", no contact line); robot guide voice for now; music: the synthwave track (my pick of the two uploads, on measurement: steady 106 BPM, "good" grid; the corporate one drifts).
- New sections: 9 dashboard (real, story case on top of Needs attention), 10 difference (section 2 bookended: clock barely moves, review complete, the good-news ping), 11 call to action.
- Guide VO: espeak-ng (installed from the system mirror), one clip per section from public/vo/lines.json, loudness-normalised. Detect and Trace lengthened to fit their lines.
- Music: synthwave extended by one 12-bar loop on downbeats (public/music/soundtrack.mp3) so its ending lands on the call to action; quiet 0.3 for sections 1-3, 0.5 from the portal, ducked to 55% under the voice.
- SFX: synthesized (make-sfx.sh plus whoosh, chat, pingbig, key, clock, goodnews), cue table in src/sound.ts, each placed with its measured lead. Not listened to: I cannot hear audio.
- Compositions: Master (all), Master-NoText, Master-NoVO (music + effects, for the owner's recording), Master-Silent.

## Wave 18 - readable note, real navigation on the dashboard, under 2 minutes, no voice
- Owner: the officer's typed note was unreadable (the captured box was scrolled); the dashboard camera seemed to drift; deliver without the robot voice, under 2 minutes, faster at the end.
- Note: re-captured with a note that fits its box ("Bank exports via its own PDF tool."), typed in from the left with a caret.
- Dashboard: rebuilt as navigation driven by the cursor (top-bar Dashboard, scroll, GLDB-02, sidebar Audit); the camera works on the viewport and pages scroll inside it, so a new page opens at its top. Score hands over without a swipe.
- Length 114.6 s: difference 10 -> 7, cta 9 -> 7.5, detect 13.5 -> 12.5, trace 11 -> 10.5, score 16 -> 15, dashboard 10. Music re-cut with a 9-bar loop so it ends with the film; no ducking when the voice is off.
- Delivered: renders/film-novo.mp4 (music and effects, no voice). Master (with the guide voice) still renders, but the guide clips no longer fit every shortened section.

## Wave 19 - the 72 and the tags
- Owner: the ring round the gauge was ugly (make the 72 flash slowly); What / Where / Why more obvious and bigger. Only these two segments re-rendered.
- The 72: the ring is gone; the number is redrawn over the page's own (measured .severity-gauge-value box) and flashes slowly ink to red with a soft red glow and a gentle swell, about one breath every 1.2 s.
- Tags: tinted fills, 3.5 px outlines, 19 px uppercase labels; WHAT sits above its box (it clipped at the edge on the right); the camera pushes in gently while they appear.
- Delivered by splicing a fresh render of Scene-Score into renders/film-novo.mp4 (frames 2253-2702, video only; the film's soundtrack kept as is).

## Wave 20 - no contradictory line; the bad actor speaks
- Owner: remove "On the document, but couldn't pinpoint where" (contradicts the Where tag); replace the "Bad Actor" label (repeats the caption) with speech bubbles suggesting bad behaviour, the avatar lower.
- Review re-captured with those lines hidden (capture/story2.spec.ts); every overlay position re-measured from it.
- Bad actor: label removed, avatar at the bottom of its panel; three bubbles timed to the edit ("Let's change the numbers here...", "Just swap two digits.", "No one checks the closing balance anyway 😏"), newest on top, older ones fading out (BAD_ACTOR.lines in data/tamper.ts).
- Spliced fresh renders of Scene-Tamper (frames 558-842) and Scene-Score (2253-2702) into renders/film-novo.mp4; soundtrack unchanged.

## Wave 21 - six steps
- Owner: the steps are Upload > Classify > Extract > Detect > Score > Finalise.
- Mapping: Upload section lights Upload, then Classify as the table stamps types (classifyStart), then Extract with the scan (scanAt); Detect and Trace light Detect; Score lights Score, then Finalise from the review tick (tickAt); Dashboard stays on Finalise. ProductHeader takes stepAt for changes within a section; tracker chips slimmed to fit six.
- Spliced a fresh render of frames 1218-3002 (sections 5-9) into renders/film-novo.mp4; soundtrack unchanged.

## Wave 22 - Finalise means locking the case
- Owner: Finalise = lock the case. The review (confirm / dismiss, notes) stays under Score.
- New beat "finalise" (5.5 s) between Score and the dashboard: from Additional Documents, Summary in the sidebar, "Finalise Case", the real confirmation, "Finalised" ringed and the view-only banner (captured in story2.spec.ts, case at case_analysed). Tracker lights Finalise here.
- Score 15 -> 14.5, dashboard 10 -> 9.5 (it now starts on the finalised summary): film 119.1 s, under 2 minutes. Full re-render (everything after shifts, sound too).

## Wave 23 - no overlap, tighter, "Upload the documents", bigger people
- Owner: the dashboard repeated the audit trail (Trace shows it); show the dashboard as populated as the real one; cut the dead air at each section's end; "Upload the whole package" -> "Upload the documents"; larger avatars and more central text in the officer/customer scenes.
- Dashboard: captured with a month of invented cases (story2.spec.ts "a month of cases": pipeline, flags, documents by type, risk, needs attention); the beat is a scroll tour of those panels, no drill-in.
- Section lengths trimmed to their last event: hook 9.1, waiting 8.3, tamper 8.0, portal 12.2, upload 11.0, detect 11.5, trace 10.0, finalise 4.8, dashboard 8.5, difference 6.4 (score 14.5, cta 7.5): 111.8 s. Music re-cut with a 4-bar loop to end with it.
- People: avatars 260 -> 330, closer together (520 / 1400), larger labels and status pills.

## Wave 24 - dashboard framing, shorter gap
- Owner: dashboard zoom cut off the pipeline and the risk overview; still a lag between "harder to catch" and "why we introduce".
- Dashboard: no zoom on full-width panels (pipeline, flags + types, risk); the page scrolls under them; push in only on Needs attention.
- Tamper 8.0 -> 7.0 s; the portal intro line rises at 0.1 s (was 0.25). Film 110.8 s; script times updated.

## Wave 25 - ElevenLabs voice
- Owner connected ElevenLabs (free plan; credit "Voice by ElevenLabs"). Voice: Brian (nPczCjzI2devNBz1zQrb), eleven_multilingual_v2, stability 0.45, similarity 0.8, style 0.15.
- All 12 lines fit their sections; Detect ran 0.2 s over: started at 0.05 s and sped 3%.
- Sigh: ElevenLabs Sound Effects, two takes (vo/sigh.wav, vo/sigh-alt.wav), 0.25 s after the hook's big ping; part of the voice track (not in Master-NoVO).
- Delivered: renders/film.mp4 (voice, music ducked, effects).

## Wave 26 - the voice follows the captions
- Owner: "read" was past tense; the ending dragged; read the loan officer line 1:1; align the voice with when each caption appears.
- One ElevenLabs clip per on-screen caption (40, public/vo/cues.json), each starting 0.05 s after its caption rises, read word for word. "Agents read" spelled "reed" for the voice.
- tools/fit-vo.py moves a caption (and the rest of its section) later when the previous line has not finished, and ends each section shortly after its last line or event (no carried-over holds). Clips sped 6% to stay under 2 minutes: 119.8 s.
- CTA gains a subIn knob (the line under the name).

## Wave 27 - stage intros, connected hook, softer music, faster close
- Owner: hook reads "Corporate loan applications need extensive documentation, which means waaaay too many documents to review." ("For one case." dropped, on screen too); stage intros "First step," / "Next step," / "Finally,"; Detect intro in the owner's words then "That way, a doctored figure or mismatched address gets caught."; "key fields read" said "red"; the CTA sub line dropped and Brian ends "Try it out today." (button too), the CTA visuals faster; softer music with gentle fades under the voice; music to the last frame.
- Music: quiet 0.2, lifted 0.28, ducked to 60% with a 0.4 s smooth ease in and 0.67 s ease out; soundtrack extended by a 12-bar loop (129.7 s) so it is full to the film's end, with a 1.5 s fade on the last frames.
- 118.65 s. The sigh stays as is (owner).

## Wave 28 - shorter Detect intro, pause before Trace
- Detect intro 29 -> 17 words ("Next step, agents investigate every document. A lead agent directs its specialists across metadata, fonts, and alignment."), 11.4 s -> 7.0 s; 1.2 s of quiet after "...gets caught" (TAILS in fit-vo.py). Film 115.1 s.

## Wave 29 - faster bad-actor section
- Owner: the "Even worse..." section at most 6.5 s, text sooner, tampering faster. Now 5.35 s: edit finishes by 2.55 s (was 3.9), second caption at 2.3 s (was 4.5), shrink at 3.55 s; t1/t2 sped 12%. Film 113.4 s; stems re-exported.

## Owner preference (from wave 29)
- Deliver visuals only: renders/picture.mp4 (Master-Silent, no audio). No voice, music or effects renders unless asked; the owner aligns sound in their own editor (stems from wave 28 are in renders/stems).

## Wave 30 - full-screen dashboard
- Owner: the dashboard segment should cover the whole screen, no step tracker. The window grows from the Finalise screen to the full frame over 0.5 s; no tracker or headline; the page fills the frame. Spliced into renders/picture.mp4 (frames 2668-2995).

## Wave 31 - a clear Dashboard click
- Owner: show the click to the dashboard clearly; more transition time from lock case to dashboard.
- Dashboard starts on Finalise's last framing (badge ring and cursor carried over), eases out to the top bar, the cursor reaches "Dashboard" (ringed), clicks at 1.5 s; only then the page cross-fades and the window grows to full screen (0.6 s). Segment 11.1 s.
- The wave-30 splice left a few stale frames before "The difference"; visuals now rendered whole again, not spliced.

## Wave 32 - "Potential Risk Score"
- The tag on the Additional Documents score line reads "Potential Risk Score" (was "Risk if the documents support the client"). Only that shot re-rendered (renders/additional-documents.mp4, frames 2436-2512) and spliced into renders/picture.mp4. Use out/timing-dump.json (node tools/dump-timing.mjs) for splice ranges: section starts round from cumulative time.

## Wave 33 - subheader
- Score's last line reads "And it recommends documents that would reduce overall risk score." Re-rendered renders/additional-documents-handles.mp4 (frames 2406-2542, 1 s handles) and spliced into renders/picture.mp4.

## Wave 34 - the app title
- Every screen re-captured with the title "GLDB Anomaly Detection Portal" (a renamed copy of the built frontend; the app source is unchanged). Overlay measurements identical.
- tools/diff-ranges.py compared the old and new visuals: four stretches changed (portal intro, agent trace, risk and review, dashboard click). Swap clips with 1 s handles in renders/title-swaps (README.txt has placements); renders/picture.mp4 is the full renamed render.

## Wave 35 - caption
- "Every finding can be audited." -> "Every step can be audited." Rendered renders/audit-caption.mp4 (frames 1905-2081, 1 s handles) and spliced into renders/picture.mp4.
- The owner deleted the remote branch (work not in main): committed locally only, not pushed.

## Wave 36 - a meaningful tool output in the trace
- The font consistency check's trace step now shows story input (the statement, page 1, closing_balance) and output (812,400.00: expected Helvetica-Bold 9pt, detected Helvetica 9pt) (capture/traceSteps.ts); re-captured; the Output highlight and camera re-aimed.
- Whole trace section incl. "Every step can be audited." rendered as renders/trace-section.mp4 (frames 1683-2081, 1 s handles), replacing audit-caption.mp4; spliced into renders/picture.mp4.

## Wave 37 - the swap made obvious
- Owner: show the digits being swapped, not delete-and-retype. SwapFigure: the 1 and 8 get red marks and a double arrow at select, then lift and trade places along an arc (erase..typeEnd), landing as 812,400.00 with the forgery's tells. renders/swap-section.mp4 (frames 500-720, 1 s handles), spliced into renders/picture.mp4.

## Wave 38 - finalise and send a report
- Owner: "At the end, the officers can finalise the case and even send a report of findings for loan decisions." Show an exported report; clip under 7 s with lag into the dashboard.
- The report is the backend's real renderer (app.case_report.render) fed the story case (video/capture/story_report.py, run with placeholder Supabase settings in a venv); 4 pages rasterised to public/ui/report. Case menu "Download report" captured on the finalised case.
- Finalise 4.46 -> 5.6 s: finalise by 1.82 s, menu 2.45, Download report 3.0, report slides up 3.15 (pages fanned, "Downloaded" chip), away at 4.85, ending on the framing the dashboard opens on. Captions "Finalise the case," / "Locked cases become view-only." / "And send a report of findings for loan decisions."
- Everything after Finalise is 1.13 s later. renders/finalise-report.mp4: frames 2498-2704 (0.5 s lead, 0.8 s into the dashboard), 6.9 s.
