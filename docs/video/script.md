# Voiceover script (1:55.1 cut, line by line)

Brian (ElevenLabs) reads each on-screen caption word for word, starting as it rises. Each line is its own clip in
`video/public/vo/lines/`, listed in `video/public/vo/cues.json` with the caption it belongs to.

| Starts | Section | Clip | Line |
|---|---|---|---|
| 0:00.2 | buried | `h1` | Corporate loan applications need extensive documentation, |
| 0:03.8 | buried | `h2` | which means waaaaaay too many documents to review. |
| 0:07.3 | buried | `sigh` | *[sigh]* |
| 0:09.6 | waiting | `w1` | A loan officer may spend over an hour staring at details on the screen, |
| 0:13.2 | waiting | `w2` | while the loan customer anxiously waits on the other end for any news of approval. |
| 0:18.0 | tamper | `t1` | Even worse, when bad actors are involved, |
| 0:22.2 | tamper | `t2` | smaller anomalies are even harder to catch. |
| 0:24.8 | portal | `p1` | So that's why we introduce... |
| 0:26.6 | portal | `p2` | the G-L-D-B Anomaly Detection Portal. |
| 0:30.9 | portal | `p3` | Agents read every document, |
| 0:33.6 | portal | `p4` | and flag what doesn't add up. |
| 0:37.0 | upload | `u1` | First step, upload the documents. |
| 0:40.5 | upload | `u2` | Every file is classified, and its key fields read. |
| 0:44.4 | upload | `u3` | Even from scans. |
| 0:47.8 | detect | `d1` | Next step, agents investigate every document. A lead agent directs its specialists across metadata, fonts, and alignment. |
| 0:55.0 | detect | `d4` | That way, a doctored figure or mismatched address gets caught. |
| 0:59.0 | trace | `r1` | And it's not a black box. |
| 1:00.5 | trace | `r2` | The agent trace shows every step, every tool call, and what it returned. |
| 1:06.2 | trace | `r3` | Every finding can be audited. |
| 1:10.3 | score | `s1` | Next step, every case gets a risk rating, |
| 1:12.3 | score | `s2` | with the exact evidence behind it. |
| 1:15.4 | score | `s3` | What was flagged, where, and why. |
| 1:19.1 | score | `s4` | Officers confirm or dismiss each finding, and add notes. |
| 1:22.9 | score | `s5` | And it recommends the documents that would settle it. |
| 1:25.6 | finalise | `f1` | Finally, lock the case. |
| 1:27.3 | finalise | `f2` | Finalised cases become view-only, with every step on record. |
| 1:30.8 | dashboard | `b1` | Every case, at a glance. |
| 1:32.1 | dashboard | `b2` | The whole pipeline, from upload to finalised. |
| 1:34.5 | dashboard | `b3` | The most common flags, across every case. |
| 1:37.7 | dashboard | `b4` | Risk across every case in view. |
| 1:39.8 | dashboard | `b5` | And the riskiest cases first. |
| 1:41.8 | difference | `x1` | What took hours of screening now takes minutes. |
| 1:44.2 | difference | `x2` | The AI does the reading. Your loan officers make the call. |
| 1:47.3 | difference | `x3` | And customers hear back sooner. |
| 1:49.4 | cta | `c1` | Bring your next loan case. |
| 1:51.2 | cta | `c2` | G-L-D-B Anomaly Detection Portal. |
| 1:53.2 | cta | `c4` | Try it out today. |

## Notes
- **"read"** in "Agents read every document" is spelled "reed" in `cues.json` so the voice says it in the present tense. The upload line's "its key fields read" is spelled "red" so it's said in the past tense.
- **"G-L-D-B"** is hyphenated so it's read letter by letter.
- **Changing or re-recording a line:** replace its clip in `public/vo/lines/` (same name), update its `dur` in `cues.json`, then run `npm run vo:fit`. Captions that would rise before the previous line has finished move later, with the rest of their section.
- Voice: Brian (`nPczCjzI2devNBz1zQrb`), eleven_multilingual_v2, stability 0.45, similarity 0.8, style 0.15; clips sped up 6%. Credit "Voice by ElevenLabs" (free plan).
