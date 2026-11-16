import { useEffect, useRef, useState } from 'react';
import HighlightedDocument from './HighlightedDocument';
import { Icon } from './Icon';
import { readingOrder } from './findingHighlights';
import type { FindingHighlight } from './findingHighlights';
import type { PreviewKind } from '../services/uploadService';

// How long the scan line takes to sweep the page; the first finding is picked once it has passed. It starts
// once the document has drawn, so the shading and glow don't play over an empty pane.
const SCAN_MS = 1100;

// A document in X-ray: the page shaded except where detection flagged something, a scan line sweeping it
// once, and a bar to step through the flagged areas (arrow keys too), each place of a finding found in several.
// The selected one glows brightest and opens a card beside the place being looked at. The caller owns which
// finding and which of its places are selected, so a list beside the document can follow and pick them too.
// Given the list's rows (`order`), the findings are numbered and stepped through in the list's order, a row's
// places together; without it, in reading order.
export default function XrayView({
  filename,
  previewKind,
  previewUrl,
  error,
  highlights,
  order,
  activeId,
  activeArea,
  onSelect,
}: {
  filename: string;
  previewKind: PreviewKind;
  previewUrl: string | null;
  error: string | null;
  highlights: FindingHighlight[];
  // The report finding ids of each row of the list beside the document, in its order.
  order?: string[][];
  activeId: string | null;
  // Which of the selected finding's areas is being looked at.
  activeArea: number;
  // A place clicked or stepped to (a finding and which of its areas); null when the card is closed.
  onSelect: (id: string | null, area?: number) => void;
}) {
  const stageRef = useRef<HTMLDivElement>(null);
  // The document has drawn: the shade, glow and scan wait for it.
  const [ready, setReady] = useState(false);
  // The page is dimmed around the flagged areas each time X-ray opens; "Dim page" turns it off while it's open.
  const [dimmed, setDimmed] = useState(true);
  const byId = new Map(highlights.map((highlight) => [highlight.id, highlight]));
  // The list's rows, then (not expected) any placed finding no row names, as rows of their own.
  const named = new Set(order?.flat());
  const rows = [...(order ?? []), ...highlights.filter((highlight) => !named.has(highlight.id)).map((highlight) => [highlight.id])];
  // Every place of every row, a row's places in reading order: what ← and → step through. A row with nothing
  // placed has none, but still counts in the numbering, so it matches the list.
  const stops = rows.flatMap((ids, row) =>
    ids
      .flatMap((id) => (byId.get(id)?.areas ?? []).map((place, area) => ({ id, area, row, place })))
      .sort((a, b) => readingOrder(a.place, b.place)),
  );

  const active: FindingHighlight | undefined = activeId ? byId.get(activeId) : undefined;
  const current = stops.find((stop) => stop.id === activeId && stop.area === activeArea);
  const rowStops = current ? stops.filter((stop) => stop.row === current.row) : [];
  // "2 of 3", when the row is in more than one place.
  const placeOf = current && rowStops.length > 1 ? `${rowStops.indexOf(current) + 1} of ${rowStops.length}` : null;
  const placedRows = new Set(stops.map((stop) => stop.row)).size;

  function step(direction: 1 | -1) {
    if (stops.length === 0) return;
    const at = current ? stops.indexOf(current) : -1;
    const next = at < 0 ? (direction === 1 ? 0 : stops.length - 1) : (at + direction + stops.length) % stops.length;
    onSelect(stops[next].id, stops[next].area);
  }

  // The selected box brought into view, however it was chosen (a box, the bar, a row beside the document),
  // unless it's already in view. Only the document's own scroller moves, not the page under it.
  useEffect(() => {
    if (!activeId) return;
    const frame = requestAnimationFrame(() => {
      const box = stageRef.current?.querySelector(
        `[data-highlight-id="${CSS.escape(activeId)}"][data-highlight-area="${activeArea}"]`,
      );
      const scroller = box?.closest('.preview-pdf-pages, .preview-image-scroll');
      if (!box || !scroller) return;
      const boxRect = box.getBoundingClientRect();
      const scrollerRect = scroller.getBoundingClientRect();
      if (boxRect.top >= scrollerRect.top && boxRect.bottom <= scrollerRect.bottom - 120) return;
      // A third of the way down the pane, leaving room under it for the finding's card.
      scroller.scrollBy({ top: boxRect.top - scrollerRect.top - scrollerRect.height / 3, behavior: 'smooth' });
    });
    return () => cancelAnimationFrame(frame);
  }, [activeId, activeArea, ready]);

  // Once the document has drawn and the scan has passed, go to the first finding, unless one was already chosen.
  const firstId = stops[0]?.id;
  const chosenRef = useRef(activeId);
  chosenRef.current = activeId;
  useEffect(() => {
    if (!firstId || !ready) return;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const timer = window.setTimeout(() => {
      if (chosenRef.current === null) onSelect(firstId, stops[0].area);
    }, reduced ? 0 : SCAN_MS);
    return () => window.clearTimeout(timer);
    // Only when the document is ready or the report's findings arrive, not on every selection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [firstId, ready]);

  // ← and → step through the findings, Esc closes the card; not while typing (a missed issue, a note).
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target?.closest('input, textarea, select, [contenteditable="true"]')) return;
      if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
        event.preventDefault();
        step(event.key === 'ArrowRight' ? 1 : -1);
      } else if (event.key === 'Escape' && activeId) {
        // The card closes first; the next Esc closes the dialog.
        event.stopImmediatePropagation();
        onSelect(null);
      }
    }
    // Capturing, so the card's Esc is seen before the dialog's own.
    window.addEventListener('keydown', onKeyDown, true);
    return () => window.removeEventListener('keydown', onKeyDown, true);
  });

  // A click elsewhere closes the card: on this page around the boxes, or on plain text beside the document. This
  // document's own boxes, card and bar keep it open; so does a click in another document beside it (the compare
  // view), which only closes that one's card; and so does a control beside it (the findings list's "Next place", a
  // tick), which moves or keeps the card itself.
  useEffect(() => {
    if (!activeId) return;
    function onPointerDown(event: PointerEvent) {
      const target = event.target as Element | null;
      const stage = target?.closest('.xray-stage');
      if (stage) {
        if (stage !== stageRef.current) return;
        if (target?.closest('.finding-highlight, .finding-card, .xray-bar')) return;
      } else if (target?.closest('button, a, input, select, textarea, label, [role="button"]')) {
        return;
      }
      onSelect(null);
    }
    window.addEventListener('pointerdown', onPointerDown, true);
    return () => window.removeEventListener('pointerdown', onPointerDown, true);
  });

  const status = !ready
    ? 'Loading the document…'
    : stops.length === 0
      ? 'Nothing placed on the page'
      : current
        ? `Finding ${current.row + 1} of ${rows.length}${placeOf ? ` · place ${placeOf}` : ''}`
        : `${placedRows} ${placedRows === 1 ? 'finding' : 'findings'} on the page`;

  return (
    <div ref={stageRef} className={`xray-stage${ready ? ' xray-stage--ready' : ''}`}>
      <HighlightedDocument
        filename={filename}
        previewKind={previewKind}
        previewUrl={previewUrl}
        error={error}
        highlights={highlights}
        activeId={activeId}
        activePlace={{ area: activeArea, label: placeOf && `Place ${placeOf}` }}
        // The place already open, clicked again, closes its card.
        onSelect={(id, area) => (id === activeId && area === activeArea ? onSelect(null) : onSelect(id, area))}
        onReady={() => setReady(true)}
        shade={dimmed}
        detail
      />

      {ready && <div className="xray-scan" aria-hidden="true" />}

      <div className="xray-bar">
        {highlights.length > 0 && (
          <button type="button" className="xray-step" onClick={() => step(-1)} aria-label="Previous finding" title="Previous (←)">
            <Icon name="chevronLeft" size={16} />
          </button>
        )}
        <span className="xray-bar-status" aria-live="polite">
          {status}
          {active && (
            <>
              <span className={`xray-severity xray-severity--${active.severity}`}>{active.severity}</span>
              <span className="xray-bar-label">{active.label}</span>
              {/* The card beside the box is for the eye; a screen reader hears the finding here. */}
              <span className="sr-only">: {active.summary}</span>
            </>
          )}
        </span>
        {highlights.length > 0 && (
          <button type="button" className="xray-step" onClick={() => step(1)} aria-label="Next finding" title="Next (→)">
            <Icon name="chevronRight" size={16} />
          </button>
        )}
        {/* Off, the page shows as printed with the flagged areas outlined, to read the parts around them. */}
        <button type="button" className="xray-dim" aria-pressed={dimmed} onClick={() => setDimmed(!dimmed)} title="Darken the page around the flagged areas">
          <span className="xray-dim-switch" aria-hidden="true" />
          Dim page
        </button>
      </div>
    </div>
  );
}
