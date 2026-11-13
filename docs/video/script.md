# Voiceover script (1:59.8 cut, line by line)

Brian (ElevenLabs) reads each on-screen caption word for word, starting as it rises. Each line is its own clip in
`video/public/vo/lines/`, listed in `video/public/vo/cues.json` with the caption it belongs to.

| Starts | Section | Clip | Line |
|---|---|---|---|
| 0:00.2 | buried | `h1` | Corporate loan applications need extensive documentation. |
| 0:03.8 | buried | `h2` | Waaaaay too many documents to review. |
| 0:06.0 | buried | `h3` | For one case. |
| 0:07.7 | buried | `sigh` | *[sigh]* |
| 0:10.0 | waiting | `w1` | A loan officer may spend over an hour staring at details on the screen, |
| 0:13.6 | waiting | `w2` | while the loan customer anxiously waits on the other end for any news of approval. |
| 0:18.4 | tamper | `t1` | Even worse, when bad actors are involved, |
| 0:22.6 | tamper | `t2` | smaller anomalies are even harder to catch. |
| 0:25.2 | portal | `p1` | So that's why we introduce... |
| 0:26.9 | portal | `p2` | the G-L-D-B Anomaly Detection Portal. |
| 0:31.3 | portal | `p3` | Agents read every document, |
| 0:34.0 | portal | `p4` | and flag what doesn't add up. |
| 0:37.4 | upload | `u1` | Upload the documents. |
| 0:40.9 | upload | `u2` | Every file is classified, and its key fields read. |
| 0:44.8 | upload | `u3` | Even from scans. |
| 0:48.2 | detect | `d1` | Agents investigate every document. |
| 0:50.2 | detect | `d2` | A lead agent plans the investigation. |
| 0:52.8 | detect | `d3` | Specialists check metadata, fonts, alignment, totals, spelling, and across documents. |
| 0:58.5 | detect | `d4` | A doctored figure, or a mismatched address, gets caught. |
| 1:02.3 | trace | `r1` | And it's not a black box. |
| 1:03.8 | trace | `r2` | The agent trace shows every step, every tool call, and what it returned. |
| 1:09.5 | trace | `r3` | Every finding can be audited. |
| 1:13.6 | score | `s1` | Every case gets a risk rating, |
| 1:15.1 | score | `s2` | with the exact evidence behind it. |
| 1:18.2 | score | `s3` | What was flagged, where, and why. |
| 1:21.9 | score | `s4` | Officers confirm or dismiss each finding, and add notes. |
| 1:25.7 | score | `s5` | And it recommends the documents that would settle it. |
| 1:28.4 | finalise | `f1` | Then lock the case. |
| 1:29.4 | finalise | `f2` | Finalised cases become view-only, with every step on record. |
| 1:32.9 | dashboard | `b1` | Every case, at a glance. |
| 1:34.2 | dashboard | `b2` | The whole pipeline, from upload to finalised. |
| 1:36.6 | dashboard | `b3` | The most common flags, across every case. |
| 1:39.7 | dashboard | `b4` | Risk across every case in view. |
| 1:41.8 | dashboard | `b5` | And the riskiest cases first. |
| 1:43.8 | difference | `x1` | What took hours of screening now takes minutes. |
| 1:46.3 | difference | `x2` | The AI does the reading. Your loan officers make the call. |
| 1:49.3 | difference | `x3` | And customers hear back sooner. |
| 1:51.5 | cta | `c1` | Bring your next loan case. |
| 1:53.2 | cta | `c2` | G-L-D-B Anomaly Detection Portal. |
| 1:55.3 | cta | `c3` | Upload the package, and see what the portal catches. |
| 1:57.4 | cta | `c4` | Try it on your next case. |

## Notes
- **"read"** in "Agents read every document" is spelled "reed" in `cues.json` so the voice says it in the present tense. The upload line's "its key fields read" is past tense, as written.
- **"G-L-D-B"** is hyphenated so it's read letter by letter.
- **Changing or re-recording a line:** replace its clip in `public/vo/lines/` (same name), update its `dur` in `cues.json`, then run `npm run vo:fit`. Captions that would rise before the previous line has finished move later, with the rest of their section.
- Voice: Brian (`nPczCjzI2devNBz1zQrb`), eleven_multilingual_v2, stability 0.45, similarity 0.8, style 0.15; clips sped up 6%. Credit "Voice by ElevenLabs" (free plan).
