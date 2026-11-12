# Voiceover script (for the 1:51 cut)

Each line fits its section at a calm pace (about 2.5 words a second, with room to breathe). The times are where each section starts in `video/renders/film-novo.mp4`. One audio file per section; the file names are what the film expects in `video/public/vo/`.

| # | Starts | Length | File | Voiceover | On screen at the time |
|---|---|---|---|---|---|
| 1 | 0:00.0 | 9.1 s | `hook` | Corporate loan applications come with a mountain of documents. *Waaay* too many to review, for a single case. **[sigh]** | PDFs and pings pile up; "Waaaay" stretches; the big ping lands at 0:07.1, then a 2 s hold for the sigh |
| 2 | 0:09.1 | 8.3 s | `waiting` | A loan officer can spend over an hour on the details, while the customer waits for any news of approval. | Officer and customer, the clock racing |
| 3 | 0:17.4 | 7.0 s | `tamper` | And when a bad actor slips in an edit, the smallest changes are the hardest to catch. | The closing balance retyped; it vanishes into the pile |
| 4 | 0:24.4 | 12.2 s | `portal` | So that's why we built… the G-L-D-B Anomaly Detection Portal. Its agents read every document in the case, and flag what doesn't add up. | "So that's why we introduce…" at 0:24.5; the name lands at 0:26.3. Pause before the name. |
| 5 | 0:36.6 | 11.0 s | `upload` | Start by uploading the documents. Each one is classified, and its key fields extracted, even from scans. | Upload, then Classify (0:40.4), then Extract (0:43.9) light up |
| 6 | 0:47.6 | 11.5 s | `detect` | A lead agent plans the investigation. Specialists check metadata, fonts, alignment, totals, spelling, and whether the documents agree, so a doctored figure or mismatched address gets caught. | The plan, six checks, the two catches (from 0:53.9) |
| 7 | 0:59.1 | 10.0 s | `trace` | And it's not a black box. The agent trace shows every step and every tool it ran, so every finding can be audited. | Agent trace, a tool's output; the audit trail from 1:05.4 |
| 8 | 1:09.1 | 14.5 s | `score` | Every case gets a risk rating, with the evidence behind it: what was flagged, where, and why. Officers confirm or dismiss each finding, add notes, and see which documents would settle any doubt. | The 72; What / Where / Why from 1:13.4; the review from 1:16.5; recommendations from 1:20.3 |
| 9 | 1:23.6 | 4.8 s | `finalise` | When the review is done, they lock the case. | Finalise Case, confirm, Finalised |
| 10 | 1:28.4 | 8.5 s | `dashboard` | And the dashboard shows every case at a glance: the pipeline, the common flags, and the riskiest cases first. | A tour of the dashboard |
| 11 | 1:36.9 | 6.4 s | `difference` | Hours of screening, down to minutes. Your officers make the call, and customers hear back sooner. | Review complete; the good-news ping at 1:40.6 |
| 12 | 1:43.3 | 7.5 s | `cta` | Bring your next loan case, and see what the portal catches. Try the G-L-D-B Anomaly Detection Portal today. | The name, then "Try it on your next case" pressed at 1:47.1 |

About 240 words in all. The same lines, as plain text for pasting into ElevenLabs, are in `video/public/vo/lines.json`.

## Recording notes
- **"G-L-D-B"** is spelled with hyphens so the voice reads it letter by letter. In ElevenLabs you can also add a pronunciation dictionary entry for "GLDB".
- **The sigh:** most ElevenLabs voices won't sigh on cue. Record it yourself, or generate it as its own clip (the Sound Effects tool, "tired exhale sigh") and save it as `video/public/vo/sigh.wav`.
- **"Waaay":** type it as "Waaay" and pick the take that stretches it best; or regenerate with a lower Stability for more expression.
- **A line runs long?** Raise that section's `length` in `video/src/timing.ts`; everything after it moves along, sound included.
