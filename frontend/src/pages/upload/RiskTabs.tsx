import { useRef, type KeyboardEvent } from 'react';
import { RISK_TABS, type CaseSection, type RiskTab } from './caseSections';

// The Risk & Analysis page's two tabs: Overview (the score, why it is that score, earlier runs), then Findings
// (every finding, to review). Each is its own section in the URL (?section=risk / ?section=findings), so they are
// links in a nav rather than an ARIA tablist; the arrow keys still move between them.
export function RiskTabs({
  active,
  findingCount,
  toReview,
  onSelect,
}: {
  active: RiskTab;
  // Every finding behind the score (on documents and across them); null while the case isn't scored.
  findingCount: number | null;
  // Findings still waiting for the officer's verdict, when the case is at Pending Evaluation.
  toReview: number | null;
  onSelect: (section: CaseSection) => void;
}) {
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    const next = (index + step + RISK_TABS.length) % RISK_TABS.length;
    buttons.current[next]?.focus();
    onSelect(RISK_TABS[next].id);
  }

  return (
    <nav className="case-tabs" aria-label="Risk and analysis">
      {RISK_TABS.map((tab, index) => {
        const reviewCount = tab.id === 'findings' && toReview ? toReview : null;
        const count = tab.id === 'findings' && findingCount ? findingCount : null;
        return (
          <button
            key={tab.id}
            ref={(element) => {
              buttons.current[index] = element;
            }}
            type="button"
            className={tab.id === active ? 'active' : undefined}
            aria-current={tab.id === active ? 'page' : undefined}
            onClick={() => onSelect(tab.id)}
            onKeyDown={(event) => onKeyDown(event, index)}
          >
            {tab.label}
            {/* While verdicts are owed, the count is what is left to review; otherwise how many findings there are. */}
            {reviewCount !== null ? (
<span className="case-tabs-count case-tabs-count--due">{reviewCount} to review</span>
            ) : (
              count !== null && (
                <span className="case-tabs-count">
                  {count}
                  <span className="sr-only"> {count === 1 ? 'finding' : 'findings'}</span>
                </span>
              )
            )}
          </button>
        );
      })}
    </nav>
  );
}
