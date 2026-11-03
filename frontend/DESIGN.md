---
name: GLDB Fraud Review
description: A calm case file for checking corporate loan documents for forgery.
colors:
  ink: "#12203d"
  ink-soft: "#5b6478"
  paper: "#f9f9f8"
  white: "#ffffff"
  line: "#e3e2de"
  steel: "#355f96"
  steel-dark: "#284a78"
  amber: "#c6820a"
  amber-bg: "#fbf0da"
  teal: "#166d5f"
  teal-bg: "#e1f1ec"
  red: "#b3402a"
  red-bg: "#fbe8e2"
  text-muted: "#667085"
  stage-idle: "#6b778c"
  stage-system: "#2563b8"
  stage-officer: "#c6820a"
  stage-done: "#1f7a4d"
  stage-closed: "#34455f"
  stage-failed: "#b3402a"
  risk-low: "#e3a6b2"
  risk-medium: "#d9667f"
  risk-medium-tint: "#f8d7de"
  risk-medium-ink: "#7a1530"
  risk-high: "#b4233c"
  risk-critical: "#6b1024"
typography:
  headline:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
    fontSize: "25px"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.01em"
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
    fontSize: "15px"
    fontWeight: 700
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
  body-sm:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.45
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
    fontSize: "11px"
    fontWeight: 500
    letterSpacing: "0.06em"
  identifier:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
    fontSize: "12.5px"
    fontWeight: 600
rounded:
  xs: "4px"
  sm: "6px"
  md: "8px"
  lg: "10px"
  xl: "12px"
  pill: "999px"
spacing:
  xs: "6px"
  sm: "10px"
  md: "16px"
  lg: "20px"
  xl: "28px"
components:
  button-primary:
    backgroundColor: "{colors.steel}"
    textColor: "{colors.white}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.md}"
    padding: "7px 14px"
  button-primary-hover:
    backgroundColor: "{colors.steel-dark}"
  button-strong:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.white}"
    rounded: "{rounded.md}"
    padding: "0 20px"
    height: "52px"
  button-text:
    textColor: "{colors.steel}"
    typography: "{typography.body-sm}"
  input:
    backgroundColor: "{colors.white}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "15px 16px"
  card:
    backgroundColor: "{colors.white}"
    rounded: "{rounded.lg}"
    padding: "18px 20px"
  stage-badge:
    rounded: "{rounded.pill}"
    padding: "2px 10px"
  severity-tag:
    typography: "{typography.label}"
    rounded: "{rounded.xs}"
    width: "58px"
  severity-tag-high:
    backgroundColor: "{colors.risk-high}"
    textColor: "{colors.white}"
---

# Design System: GLDB Fraud Review

## Overview

**Creative North Star: "The Case File"**

The app is a well-kept dossier. The page is bright, near-neutral paper, the content sits on white sheets with hairline edges, and the writing is dark navy ink. The documents, findings and verdicts are the content, and the interface is the folder that holds them in order. Nothing competes with the evidence: colour appears where it carries meaning (a case's stage, a finding's severity, an error) and nowhere else.

It is calm and approachable rather than stern. Corners are softly rounded, tints are gentle, and fields are roomy, so a long review session doesn't feel like work against the tool. One typeface, the system sans, carries everything; case IDs, counts and figures use its tabular digits so columns line up. Dense information is ruled into tables and lists rather than boxed into tiles.

The system is light only. There is no dark theme.

**Key Characteristics:**
- Bright near-neutral paper, white cards, hairline stone-grey borders.
- One accent, steel blue, for actions, links, focus and the current place.
- Stage tones grouped by who acts next, each with its own shape, reserved for a case's stage.
- One crimson scale for risk and severity, separate from the stage tones.
- One typeface, the system sans, with tabular figures for IDs and counts.
- Flat surfaces. Shadows only on things that float.

## Colors

A quiet paper-and-ink base with one steel accent, plus two reserved colour sets that carry meaning: stage and severity.

### Primary
- **Steel** (#355f96): the only accent. Primary buttons, text links, the active nav underline, selected rows, focus rings (at 20–30% alpha) and the brand's "Fraud Review" half. Hover deepens to **Steel Dark** (#284a78).

### Neutral
- **Ink** (#12203d): body text, headings and the strongest button (sign in, new case).
- **Ink Soft** (#5b6478): secondary text and captions.
- **Text Muted** (#667085): the quietest text (hints, placeholders, table headers, footnotes). It still passes 4.5:1 on white, paper and the pale slate panels; nothing an officer reads is lighter.
- **Paper** (#f9f9f8): the page and the top bar. Also the fill for set-aside things, like dismissed findings and missed issues.
- **White** (#ffffff): cards, tables, inputs and popovers.
- **Line** (#e3e2de): every hairline border and divider.
- The wider slate family in `tokens.css` (slate-97 to slate-11) supplies muted text greys and table tints between these.

### Status
- **Teal** (#166d5f) on **Teal Background** (#e1f1ec): success and confirmed states.
- **Amber** (#c6820a) on **Amber Background** (#fbf0da): warnings and waiting on the officer.
- **Red** (#b3402a) on **Red Background** (#fbe8e2): errors, destructive actions, failures and below-target rates.

### Stage tones
A case's stage is coloured by **who acts next**, and each group also has its own **shape** (components/StageMark), so a stage never rests on colour alone. Each tone is four tokens: a fill for marks (listed in the frontmatter, 3:1 or more on white), an ink for text, a tint for backgrounds and a border.
- **Idle**, an empty ring: Awaiting Documents.
- **System**, a half-filled ring: Processing Documents and Detecting Fraud. The system is working.
- **Officer**, a solid dot: Pending Review, Ready to Analyse and Pending Evaluation. The officer's turn.
- **Blocked**, an amber warning triangle: Missing Documents. Still the officer's turn, but the case can't go on.
- **Done**, a tick: Ready to Finalise.
- **Closed**, a lock: Finalised.
- **Failed**, a cross: Failed.

Stage names read as a step in the flow: what is happening to the case, or what it waits for.

### Risk and severity
A case's risk level and a finding's severity share one **crimson** scale, light to dark, so a bigger problem looks louder and no level reads as safe. Crimson, not orange, so it never reads as the amber "your turn". **Low** is an outline (risk-low border on white) with its word in ink-soft; **Medium** is a tint with crimson ink; **High** and **Critical** are solid with white text. A finding that was dismissed, or an issue reported as missed, is never scored, so it gets no severity colour: paper fill, line border and ink-soft text.

### Named Rules
**The One Accent Rule.** Steel is the only accent colour. Any other colour on screen must mean a stage, a severity or a status.

**The Tone Means Stage Rule.** The stage tones are only ever used for a case's stage: badges, marks, the stepper and the pipeline. Never use them for decoration or for unrelated categories.

**The Shape Carries the Stage Rule.** Every stage mark draws its group's shape, so the stage reads without its colour, for colour-blind officers too.

**The Low Is Never Green Rule.** Risk and severity use only the crimson scale. Green never marks a level, because no level is safe.

**The Ink on Tint Rule.** Text on a tone uses that tone's ink on its tint. Never put white text on a light tone. Amber as text is orange-27, never the amber fill.

**The Token Only Rule.** Every colour comes from `tokens.css` or `base.css`. To add one, pick the nearest family and lightness rather than writing a new hex into a stylesheet.

## Typography

**Body Font:** the system sans (-apple-system, BlinkMacSystemFont, Segoe UI, Helvetica, Arial)

**Character:** a plain native sans keeps the app feeling like a familiar office tool. There is no second face: identifiers and counts use the same sans with tabular figures.

### Hierarchy
- **Headline** (700, 25px, 1.2, -0.01em): the one page title.
- **Title** (700, 15px): card and panel headings.
- **Body** (400, 14px, 1.5): running text and table cells. Many dense lists drop to 13px.
- **Body Small** (400, 13px, 1.45): captions, panel subtitles, error text and hints.
- **Label** (sans 500, 11px, 0.06em, uppercase): eyebrows and section labels above groups.
- **Identifier** (sans 600, 12.5px, tabular figures): case IDs (GLDB-1234), counts and filter tallies, with tabular numbers.

Emphasis is carried by weight (600 for labels and buttons, 700 for headings) rather than size. The scale is small and tight: most text sits between 12px and 15px.

### Named Rules
**The One Typeface Rule.** Everything is set in the system sans. Figures that line up in columns (IDs, counts, amounts) use tabular-nums, not a second font.

## Layout

- **Top bar:** sticky, 64px, full window width on paper with a bottom hairline. It holds the brand, the centred nav, and search, new case and the officer menu.
- **Page column:** a centred 760px column for simple pages. Working pages (a case, create case, the case queue) widen to the window less 48px.
- **Cards:** pages are stacks of white cards on paper, about 16–18px apart, with 18–20px of padding inside.
- **Tables:** dense, aligned grid rows with one hairline between rows.
- **Responsive:** the header drops the officer's name below 1240px, splits into two rows below 860px and three below 620px. Wide grids, such as the dashboard's case pipeline, become vertical lists below 760px. Device priority is still undecided, so layouts must work from phone width up.
- **Spacing:** the rhythm is loose rather than strict, mostly in steps of 6, 10, 16, 20 and 28px.

## Elevation & Depth

Flat by default. Depth comes from tone (white sheets on paper) and hairline borders, not shadows. Resting cards get at most a barely visible shadow. Real shadows are kept for things that float above the page.

### Shadow Vocabulary
- **Sheet** (`box-shadow: 0 1px 3px var(--ink-a05)`): summary cards at rest. Barely there.
- **Popover** (`box-shadow: 0 12px 32px var(--ink-a16)`): menus, filter lists and search results.
- **Dialog** (`box-shadow: 0 16px 48px var(--navy-a32)`): confirmation dialogs and the document viewer.
- **Focus ring** (`box-shadow: 0 0 0 3px var(--steel-a20)`): keyboard focus on any control, in place of an outline.

### Named Rules
**The Lift Only What Floats Rule.** A shadow means "this is above the page". Cards, tables and banners stay flat.

## Shapes

Softly rounded rectangles throughout, which keeps the folder calm without looking playful.
- **Gently rounded** (8px): the default for buttons, inputs, messages and error boxes.
- **Containers** (10px): cards, tables, menus and popovers.
- **Small controls and menu items** (6px).
- **Severity tags** (4px): typed and squarish.
- **Pills** (999px): stage badges and the officer button.
- **Circles** (50%): avatars, stage dots and the pipeline counts.

Borders are 1px hairlines in Line. A card never carries a thick coloured side edge: a state's colour lives in a mark beside its name (a stage's shape, a risk level's dot, an error's icon).

## Components

The feel is calm and approachable: soft corners, roomy fields and gentle tints, with nothing harsh or shouty.

### Buttons
- **Shape:** gently rounded (7–8px).
- **Primary:** steel fill, white text, 600 weight, 12.5–13px, compact padding (7px 14px). Hover deepens to steel dark.
- **Strong:** ink fill and white text for the one decisive action on a screen (sign in, new case). Hover softens by opacity.
- **Text button:** no box. Steel text at 600–650 weight, used for "View all", "Run again" and inline links.
- **Focus:** the 3px steel focus ring on every button.

### Badges and tags
- **Stage badge:** a pill in the stage's tint, with its ink text and border, 12px at 600 weight.
- **Severity tag:** a fixed 58px wide, squarish, uppercase 11px tag in the severity colour.

### Cards / Containers
- **Corner style:** 10px.
- **Background:** white on paper.
- **Shadow:** none, or the faint sheet shadow.
- **Border:** 1px line.
- **Internal padding:** 18–20px. A card header is set off by a hairline.

### Messages
Inline banners inside cards are an 8px tint block with an icon and a bold first line: info on pale blue, success on pale green, warning on pale red. Errors use the error box: pale red, a hairline border, a red icon, a bold title and a plain hint under it.

### Inputs / Fields
- **Style:** white, 1px line border, 8px corners and roomy padding (15px 16px at 15px text). Search boxes are slimmer (12px 14px).
- **Focus:** the border turns steel, with a 3px steel ring at 15%.
- **Error:** a red inline message under the field.

### Navigation
The top bar nav uses ink-soft 14px links at 500 weight. The current page turns ink, with a 2px steel bar under the text, shown faintly on hover. Inside a case, a side list of documents marks the current one with a steel inset edge.

### Case pipeline (signature)
The dashboard's stepper shows every stage in order, each as a circle holding its count, joined by a hairline. A stage with cases takes its tone's ink, tint and border. An empty stage stays grey. Failed stands apart after a dashed divider. Below 760px it becomes a vertical list.

## Do's and Don'ts

### Do:
- **Do** put content on white cards with a 1px line border and 10px corners, on the paper page.
- **Do** use steel for every action, link and focus state, and the 3px steel focus ring for keyboard focus.
- **Do** show a case's stage with its StageMark and its stage tone: tone ink on tone tint, with the tone border.
- **Do** set case IDs, counts and amounts with tabular figures in the system sans.
- **Do** say a missed target or a failure in red text with a plain sentence.
- **Do** keep text at 11px or more, and the quietest text at text-muted or darker.
- **Do** take every colour from `tokens.css` or `base.css`.

### Don't:
- **Don't** use stage tones or risk colours for anything else, and never colour a risk level green.
- **Don't** put white text on a light tone.
- **Don't** give dismissed findings or missed issues a severity colour, because they were never scored.
- **Don't** add shadows to resting cards, tables or banners.
- **Don't** add a second accent colour.
- **Don't** mark a card's state with a thick coloured side edge; put the colour in a mark beside its name.
- **Don't** mark hover tips with dotted underlines or decorate figures with arrows.
- **Don't** write new hex values into page stylesheets.
