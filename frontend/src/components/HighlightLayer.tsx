import type { FindingHighlight } from './findingHighlights';
import { highlightRect } from './findingHighlights';

interface HighlightLayerProps {
  highlights: FindingHighlight[];
  // Which page this layer covers (1 for an image); only areas on it are drawn.
  pageNumber: number;
  // The page as drawn by the preview: its size in `units` (PDF points, or an image's pixels).
  pageWidth: number;
  pageHeight: number;
  units: 'pt' | 'px';
  activeId: string | null;
  onSelect: (id: string) => void;
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
  onSelect,
}: HighlightLayerProps) {
  return (
    <div className="finding-highlight-layer">
      {highlights.flatMap((highlight) =>
        highlight.areas.map((area, index) => {
          if (area.page !== pageNumber) {
            return null;
          }
          const rect = highlightRect(highlight, area.bbox, { width: pageWidth, height: pageHeight, units });
          if (!rect) {
            return null;
          }
          const isActive = highlight.id === activeId;
          // A box at the top of the page has no room for its tag above it.
          const tagBelow = rect.top < 0.04;
          return (
            <button
              key={`${highlight.id}-${index}`}
              type="button"
              // Only the first area is the one the preview scrolls to.
              data-highlight-id={index === 0 ? highlight.id : undefined}
              className={`finding-highlight finding-highlight--${highlight.severity}${isActive ? ' is-active' : ''}${
                tagBelow ? ' finding-highlight--tag-below' : ''
              }`}
              style={{
                left: `${rect.left * 100}%`,
                top: `${rect.top * 100}%`,
                width: `${rect.width * 100}%`,
                height: `${rect.height * 100}%`,
              }}
              title={`${highlight.label}: ${highlight.summary}`}
              aria-label={`${highlight.severity} finding, ${highlight.label}: ${highlight.summary}`}
              aria-pressed={isActive}
              onClick={() => onSelect(highlight.id)}
            >
              <span className="finding-highlight-tag">{highlight.label}</span>
            </button>
          );
        }),
      )}
    </div>
  );
}
