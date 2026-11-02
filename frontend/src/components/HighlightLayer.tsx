import { useId } from 'react';
import type { FindingHighlight, HighlightRect } from './findingHighlights';
import { highlightRect } from './findingHighlights';
import type { HighlightOptions } from './HighlightedDocument';

// Which of the selected finding's areas is being looked at (its card goes beside that one), and where it falls
// among the places of its row in the findings list ("Place 2 of 3"), when it has several.
export interface ActivePlace {
  area: number;
  label: string | null;
}

interface HighlightLayerProps extends HighlightOptions {
  highlights: FindingHighlight[];
  // Which page this layer covers (1 for an image); only areas on it are drawn.
  pageNumber: number;
  // The page as drawn by the preview: its size in `units` (PDF points, or an image's pixels).
  pageWidth: number;
  pageHeight: number;
  units: 'pt' | 'px';
  activeId: string | null;
  activePlace?: ActivePlace;
  // A box clicked: its finding, and which of the finding's areas it is.
  onSelect: (id: string, area: number) => void;
}

// Boxes over one page, one per finding's area on it, coloured by severity. Laid over the page, so it must sit
// in a positioned element the page's own size.
export default function HighlightLayer({
  highlights,
  pageNumber,
  pageWidth,
  pageHeight,
  units,
  activeId,
  activePlace,
  onSelect,
  shade = false,
  detail = false,
}: HighlightLayerProps) {
  // useId's ":r1:" isn't safe inside url(#…).
  const maskId = `finding-shade-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`;
  const page = { width: pageWidth, height: pageHeight, units };
  // Every area on this page, with where it sits.
  const placed = highlights.flatMap((highlight) =>
    highlight.areas.flatMap((area, index) => {
      const rect = area.page === pageNumber ? highlightRect(highlight, area.bbox, page) : null;
      return rect ? [{ highlight, index, rect }] : [];
    }),
  );
  // The selected finding's card goes beside the area being looked at, when that area is on this page.
  const anchor = detail
    ? placed.find(({ highlight, index }) => highlight.id === activeId && index === (activePlace?.area ?? 0))
    : undefined;

  return (
    <div className={`finding-highlight-layer${shade ? ' finding-highlight-layer--shaded' : ''}`}>
      {/* X-ray: the page darkened everywhere but the flagged areas, which a mask cuts out of the wash. */}
      {shade && (
        <svg className="finding-highlight-shade" viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden="true">
          <mask id={maskId}>
            <rect width="1" height="1" fill="white" />
            {placed.map(({ highlight, index, rect }) => (
              <rect
                key={`${highlight.id}-${index}`}
                x={rect.left - 0.006}
                y={rect.top - 0.006}
                width={rect.width + 0.012}
                height={rect.height + 0.012}
                rx="0.004"
                fill="black"
              />
            ))}
          </mask>
          <rect width="1" height="1" mask={`url(#${maskId})`} />
        </svg>
      )}

      {placed.map(({ highlight, index, rect }) => {
        const isActive = highlight.id === activeId;
        // A box at the top of the page has no room for its tag above it.
        const tagBelow = rect.top < 0.04;
        return (
          <button
            key={`${highlight.id}-${index}`}
            type="button"
            data-highlight-id={highlight.id}
            data-highlight-area={index}
            className={`finding-highlight finding-highlight--${highlight.severity}${isActive ? ' is-active' : ''}${
              tagBelow ? ' finding-highlight--tag-below' : ''
            }`}
            style={{
              left: `${rect.left * 100}%`,
              top: `${rect.top * 100}%`,
              width: `${rect.width * 100}%`,
              height: `${rect.height * 100}%`,
            }}
            title={detail ? undefined : `${highlight.label}: ${highlight.summary}`}
            aria-label={`${highlight.severity} finding, ${highlight.label}: ${highlight.summary}`}
            aria-pressed={isActive}
            onClick={() => onSelect(highlight.id, index)}
          >
            <span className="finding-highlight-tag">{highlight.label}</span>
          </button>
        );
      })}

      {anchor && <FindingCard highlight={anchor.highlight} place={activePlace?.label ?? null} rect={anchor.rect} />}
    </div>
  );
}

// What the selected finding is, beside its box: under it, or over it near the foot of the page; from its left
// edge, or its right edge on the page's right half, so it stays on the page.
function FindingCard({ highlight, place, rect }: { highlight: FindingHighlight; place: string | null; rect: HighlightRect }) {
  const bottom = rect.top + rect.height;
  const below = bottom < 0.7;
  const onLeft = rect.left + rect.width / 2 < 0.5;

  return (
    <div
      className={`finding-card finding-card--${highlight.severity}`}
      style={{
        ...(below ? { top: `calc(${bottom * 100}% + 10px)` } : { bottom: `calc(${(1 - rect.top) * 100}% + 10px)` }),
        ...(onLeft ? { left: `${rect.left * 100}%` } : { right: `${(1 - rect.left - rect.width) * 100}%` }),
      }}
      aria-hidden="true"
    >
      <span className="finding-card-severity">{highlight.severity}</span>
      <strong className="finding-card-label">{highlight.label}</strong>
      <p className="finding-card-summary">{highlight.summary}</p>
      {place && <span className="finding-card-places">{place}</span>}
    </div>
  );
}
