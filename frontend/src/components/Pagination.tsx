interface PaginationProps {
  page: number;
  pageSize: number;
  total: number;
  // Until the first page arrives, the row's place is held so the table doesn't move.
  loading?: boolean;
  onChange: (page: number) => void;
}

// Always about seven slots, so the controls don't change width as the page changes: 1 2 3 4 5 … 12, 1 … 5 6 7 … 12, 1 … 8 9 10 11 12.
function pageItems(page: number, pageCount: number): (number | 'gap')[] {
  if (pageCount <= 7) return Array.from({ length: pageCount }, (_, i) => i + 1);
  if (page <= 4) return [1, 2, 3, 4, 5, 'gap', pageCount];
  if (page >= pageCount - 3) return [1, 'gap', ...Array.from({ length: 5 }, (_, i) => pageCount - 4 + i)];
  return [1, 'gap', page - 1, page, page + 1, 'gap', pageCount];
}

export default function Pagination({ page, pageSize, total, loading = false, onChange }: PaginationProps) {
  const pageCount = Math.ceil(total / pageSize);
  if (loading) return <div className="pagination" aria-hidden="true" />;
  if (total === 0) return null;

  const from = (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);

  return (
    <nav className="pagination" aria-label="Case pages">
      <span className="pagination-range">
        Showing {from}–{to} of {total}
      </span>

      {pageCount > 1 && (
      <div className="pagination-controls">
        <button
          type="button"
          className="pagination-step"
          aria-label="Previous page"
          disabled={page <= 1}
          onClick={() => onChange(page - 1)}
        >
          <svg className="pagination-chevron" viewBox="0 0 10 10" aria-hidden="true">
            <path d="m6.5 1.5-3.5 3.5 3.5 3.5" />
          </svg>
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
          aria-label="Next page"
          disabled={page >= pageCount}
          onClick={() => onChange(page + 1)}
        >
          <svg className="pagination-chevron" viewBox="0 0 10 10" aria-hidden="true">
            <path d="m3.5 1.5 3.5 3.5-3.5 3.5" />
          </svg>
        </button>
      </div>
      )}
    </nav>
  );
}
