import type { DetectionFinding, DetectionReport, DetectionSeverity } from '../services/uploadService';

type BBox = [number, number, number, number];

// One place a finding points at. A finding can have several (a font deviation or a misspelt word that
// recurs).
export interface HighlightArea {
  page: number;
  bbox: BBox;
}

// A detection finding that names areas of the document, drawn over the preview so an officer can go
// straight to them.
export interface FindingHighlight {
  id: string;
  // In reading order; the first is where the preview scrolls to.
  areas: HighlightArea[];
  units: 'pt' | 'px';
  // The page the bbox was measured on, when the check reports it (a photo's page is sized by its DPI).
  pageSize: [number, number] | null;
  severity: DetectionSeverity;
  label: string;
  summary: string;
}

// The area as fractions of the page, so it scales with however large the page is drawn.
export interface HighlightRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

// The image checks lead their summary with "[Category] "; the label already shows it.
function stripCategory(summary: string): string {
  return summary.replace(/^\[[^\]]+\]\s*/, '');
}

// Labels for the checks whose findings carry no category of their own.
const TOOL_LABELS: Record<string, string> = {
  font_consistency_tool: 'Font',
  spelling_tool: 'Spelling',
  numerical_checks_tool: 'Figures',
  date_consistency_tool: 'Dates',
  external_validation_tool: 'Registry check',
};

function isBBox(bbox: unknown): bbox is BBox {
  return Array.isArray(bbox) && bbox.length === 4 && bbox[2] > bbox[0] && bbox[3] > bbox[1];
}

// Page, then top to bottom, then left to right.
export function readingOrder(a: HighlightArea, b: HighlightArea): number {
  return a.page - b.page || a.bbox[1] - b.bbox[1] || a.bbox[0] - b.bbox[0];
}

function toHighlight(finding: DetectionFinding): FindingHighlight | null {
  // The font and spelling checks list each place; the image and layout checks name one area.
  // An image upload has one page, which the image checks leave unnumbered.
  const areas = (finding.locations ?? [{ page: finding.page, bbox: finding.bbox }])
    .filter((area): area is { page: number | null | undefined; bbox: BBox } => isBBox(area.bbox))
    .map((area) => ({ page: area.page ?? 1, bbox: area.bbox }))
    .sort(readingOrder);
  if (areas.length === 0) {
    return null;
  }
  return {
    // The report's own id ("layout_anomalies_tool-1"), which the case's risk findings carry too.
    id: finding.id,
    areas,
    units: finding.bbox_units ?? 'pt',
    pageSize: finding.page_size ?? null,
    severity: finding.severity,
    label: finding.indicator ?? finding.type ?? TOOL_LABELS[finding.tool] ?? 'Finding',
    summary: stripCategory(finding.summary),
  };
}

// The report's findings that have an area, in reading order of their first area (page, then top to
// bottom), and how many it has without one (metadata, dates, registry lookups...). Info findings were
// judged benign and aren't in the officer's list, so they get no box.
export function highlightsFromReport(report: DetectionReport | null): {
  highlights: FindingHighlight[];
  unlocatedCount: number;
} {
  if (!report) {
    return { highlights: [], unlocatedCount: 0 };
  }
  const findings = report.findings.filter((finding) => finding.severity !== 'info');
  const highlights = findings
    .map(toHighlight)
    .filter((h): h is FindingHighlight => h !== null)
    .sort((a, b) => readingOrder(a.areas[0], b.areas[0]));
  return { highlights, unlocatedCount: findings.length - highlights.length };
}

// Where one of the highlight's areas sits on a page drawn from `page` (its size in `page.units`). Null
// when the bbox is in other units and the check didn't report the page it was measured on.
export function highlightRect(
  highlight: FindingHighlight,
  bbox: BBox,
  page: { width: number; height: number; units: 'pt' | 'px' },
): HighlightRect | null {
  const [width, height] =
    highlight.pageSize ?? (highlight.units === page.units ? [page.width, page.height] : [0, 0]);
  if (width <= 0 || height <= 0) {
    return null;
  }
  const [x0, y0, x1, y1] = bbox;
  const clamp = (v: number) => Math.min(1, Math.max(0, v));
  const left = clamp(x0 / width);
  const top = clamp(y0 / height);
  return {
    left,
    top,
    width: clamp(x1 / width) - left,
    height: clamp(y1 / height) - top,
  };
}
