interface PaginationProps {
  page: number;
  pageSize: number;
  total: number;
  onChange: (page: number) => void;
}

// Always about seven slots, so the controls don't change width as the page changes: 1 2 3 4 5 … 12, 1 … 5 6 7 … 12, 1 … 8 9 10 11 12.
function pageItems(page: number, pageCount: number): (number | 'gap')[] {
  if (pageCount <= 7) return Array.from({ length: pageCount }, (_, i) => i + 1);
  if (page <= 4) return [1, 2, 3, 4, 5, 'gap', pageCount];
  if (page >= pageCount - 3) return [1, 'gap', ...Array.from({ length: 5 }, (_, i) => pageCount - 4 + i)];
  return [1, 'gap', page - 1, page, page + 1, 'gap', pageCount];
}

export default function Pagination({ page, pageSize, total, onChange }: PaginationProps) {
  const pageCount = Math.ceil(total / pageSize);
  if (pageCount <= 1) return null;

  const from = (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);

  return (
    <nav className="pagination" aria-label="Case pages">
      <span className="pagination-range">
        Showing {from}–{to} of {total}
      </span>

      <div className="pagination-controls">
        <button
          type="button"
          className="pagination-step"
          disabled={page <= 1}
          onClick={() => onChange(page - 1)}
        >
          <span aria-hidden="true">←</span> Previous
        </button>

        {pageItems(page, pageCount).map((item, index) =>
          item === 'gap' ? (
            <span key={`gap-${index}`} className="pagination-gap" aria-hidden="true">
              …
            </span>
          ) : (
            <button
              key={item}
              type="button"
              className="pagination-page"
              aria-label={`Page ${item}`}
              aria-current={item === page ? 'page' : undefined}
              onClick={() => onChange(item)}
            >
              {item}
            </button>
          ),
        )}

        <button
          type="button"
          className="pagination-step"
          disabled={page >= pageCount}
          onClick={() => onChange(page + 1)}
        >
          Next <span aria-hidden="true">→</span>
        </button>
      </div>
    </nav>
  );
}
