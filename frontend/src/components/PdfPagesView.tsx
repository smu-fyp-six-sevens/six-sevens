import { useEffect, useRef, useState } from 'react';
import type { PDFPageProxy } from 'pdfjs-dist';
import HighlightLayer from './HighlightLayer';
import type { ActivePlace } from './HighlightLayer';
import type { FindingHighlight } from './findingHighlights';
import type { HighlightOptions } from './HighlightedDocument';

// pdf.js is large and only this view needs it, so it loads with the first PDF shown. Its worker is
// bundled by Vite as a .js file (nginx's mime.types has no .mjs, and a worker needs a script type),
// started once and shared by every document.
let pdfjsLoading: Promise<typeof import('pdfjs-dist')> | null = null;

function loadPdfjs() {
  pdfjsLoading ??= Promise.all([
    import('pdfjs-dist'),
    import('pdfjs-dist/build/pdf.worker.min.mjs?worker'),
  ]).then(([pdfjs, { default: PdfWorker }]) => {
    pdfjs.GlobalWorkerOptions.workerPort = new PdfWorker();
    return pdfjs;
  });
  return pdfjsLoading;
}

interface PdfPagesViewProps extends HighlightOptions {
  url: string;
  filename: string;
  highlights: FindingHighlight[];
  activeId: string | null;
  activePlace?: ActivePlace;
  onSelect: (id: string, area: number) => void;
  // Once the first page has drawn, so what goes over it can wait for the document.
  onReady?: () => void;
}

// The PDF drawn page by page at the pane's width, so findings can be boxed on top of it (the browser's
// own viewer, in an iframe, can't be drawn over).
export default function PdfPagesView({
  url,
  filename,
  highlights,
  activeId,
  activePlace,
  onSelect,
  onReady,
  shade,
  detail,
}: PdfPagesViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [pages, setPages] = useState<PDFPageProxy[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let destroy: (() => Promise<void>) | null = null;
    setPages([]);
    setError(null);

    async function load() {
      const pdfjs = await loadPdfjs();
      if (cancelled) return;
      const task = pdfjs.getDocument({ url });
      destroy = () => task.destroy();
      const doc = await task.promise;
      const loaded = await Promise.all(
        Array.from({ length: doc.numPages }, (_, index) => doc.getPage(index + 1)),
      );
      if (!cancelled) setPages(loaded);
    }

    load().catch((err: unknown) => {
      if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to render the PDF.');
    });
    return () => {
      cancelled = true;
      void destroy?.();
    };
  }, [url]);

  // Pages fill the pane's width, redrawn when it resizes.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const observer = new ResizeObserver(([entry]) => {
      setWidth(Math.floor(entry.contentRect.width));
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  return (
    <div ref={containerRef} className="preview-pdf-pages" aria-label={`Preview of ${filename}`}>
      {error && <div className="preview-modal-error">{error}</div>}
      {!error && pages.length === 0 && <div className="preview-modal-message">Loading preview…</div>}
      {width > 0 &&
        pages.map((page) => (
          <PdfPage
            key={page.pageNumber}
            page={page}
            width={width}
            highlights={highlights.filter((h) => h.areas.some((area) => area.page === page.pageNumber))}
            activeId={activeId}
            activePlace={activePlace}
            onSelect={onSelect}
            onRendered={page.pageNumber === 1 ? onReady : undefined}
            shade={shade}
            detail={detail}
          />
        ))}
    </div>
  );
}

function PdfPage({
  page,
  width,
  highlights,
  activeId,
  activePlace,
  onSelect,
  onRendered,
  shade,
  detail,
}: HighlightOptions & {
  page: PDFPageProxy;
  width: number;
  highlights: FindingHighlight[];
  activeId: string | null;
  activePlace?: ActivePlace;
  onSelect: (id: string, area: number) => void;
  onRendered?: () => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  // Read when a render finishes, so a new callback doesn't redraw the page.
  const onRenderedRef = useRef(onRendered);
  onRenderedRef.current = onRendered;
  // At scale 1 a viewport is the page's size in points, the units the checks measure PDFs in.
  const base = page.getViewport({ scale: 1 });
  const scale = width / base.width;
  const height = Math.round(base.height * scale);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const viewport = page.getViewport({ scale: scale * (window.devicePixelRatio || 1) });
    canvas.width = Math.floor(viewport.width);
    canvas.height = Math.floor(viewport.height);
    const task = page.render({ canvas, viewport });
    task.promise.then(
      () => onRenderedRef.current?.(),
      () => {
        // Cancelled by a resize or unmount; the next render replaces it.
      },
    );
    return () => task.cancel();
  }, [page, scale]);

  return (
    <div className="preview-pdf-page" style={{ width, height }}>
      <canvas ref={canvasRef} className="preview-pdf-canvas" aria-label={`Page ${page.pageNumber}`} />
      <HighlightLayer
        highlights={highlights}
        pageNumber={page.pageNumber}
        pageWidth={base.width}
        pageHeight={base.height}
        units="pt"
        activeId={activeId}
        activePlace={activePlace}
        onSelect={onSelect}
        shade={shade}
        detail={detail}
      />
    </div>
  );
}
