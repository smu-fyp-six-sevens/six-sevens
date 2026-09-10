import { useMemo, useState } from 'react';
import AppHeader from '../components/AppHeader';
import CaseCard from '../components/CaseCard';
import type { CaseRecord } from '../App';

interface CaseQueuePageProps {
  cases: CaseRecord[];
  isLoading: boolean;
  error: string | null;
  onRetry: () => void;
  onOpenCase: (caseRecord: CaseRecord) => void;
  onOpenExtractionTest: () => void;
}

function displayCaseNumber(caseRecord: CaseRecord): string {
  return caseRecord.caseNumber || caseRecord.id;
}

export default function CaseQueuePage({
  cases,
  isLoading,
  error,
  onRetry,
  onOpenCase,
  onOpenExtractionTest,
}: CaseQueuePageProps) {
  const [search, setSearch] = useState('');

  const matches = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return cases;

    return cases.filter((item) =>
      [displayCaseNumber(item), item.id, item.applicant, item.type, item.status]
        .some((value) => value.toLowerCase().includes(query)),
    );
  }, [cases, search]);

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const query = search.trim().toLowerCase();
    if (!query) return;

    const exact = cases.find((item) =>
      [displayCaseNumber(item), item.id]
        .some((value) => value.toLowerCase() === query),
    );

    if (exact) onOpenCase(exact);
  }

  return (
    <main className="shell">
      <AppHeader />

      <section className="queue-view">
        <p className="eyebrow">My case queue</p>
        <h1>Find a case</h1>
        <p className="sub">
          Search by case number or applicant, or pick from your assigned cases below.
        </p>

        <form className="search-row" onSubmit={handleSubmit}>
          <label className="search-box">
            <span aria-hidden="true">⌕</span>
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Enter case number, e.g. GLDB-22841"
              aria-label="Search case number or applicant"
            />
          </label>
          <button className="go-btn" type="submit">Go</button>
        </form>

        <div className="queue-label">
          Assigned to you ({cases.length})
          <button type="button" className="dev-tool-link" onClick={onOpenExtractionTest}>
            Test extraction logic →
          </button>
        </div>

        {isLoading ? (
          <div className="no-results">Loading cases…</div>
        ) : error ? (
          <div className="no-results">
            <div>{error}</div>
            <button type="button" className="go-btn queue-retry" onClick={onRetry}>
              Retry
            </button>
          </div>
        ) : matches.length > 0 ? (
          matches.map((caseRecord) => (
            <CaseCard
              key={caseRecord.id}
              caseRecord={caseRecord}
              onOpen={onOpenCase}
            />
          ))
        ) : (
          <div className="no-results">
            No case matches "{search}". Check the case number and try again.
          </div>
        )}
      </section>
    </main>
  );
}
