import { Link } from 'react-router-dom';
import type { CaseAnalysis } from '../../services/uploadService';
import { analysisFindingAnchor, checkedAndFine } from './analysisSummary';

function scrollToFinding(findingId: string) {
  window.document.getElementById(analysisFindingAnchor(findingId))?.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function CheckedAndFine({ analysis }: { analysis: CaseAnalysis }) {
  const chips = checkedAndFine(analysis);
  if (chips.length === 0) return null;
  return (
    <div className="analysis-brief-checked">
      <span className="analysis-brief-label">Checked and fine</span>
      <ul className="analysis-brief-chips">
        {chips.map(([label, title]) => (
          <li key={label} title={title}>
            {label} <span aria-label="fine">✓</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// The explanation an officer reads first: the level and a one-line headline, the findings it rests on (each linked
// to where it is shown), what was checked and fine, and what would help. `linkToRiskTab` is off for an earlier run,
// whose document findings the tab no longer shows.
export function AnalysisSummaryBlock({ analysis, linkToRiskTab }: { analysis: CaseAnalysis; linkToRiskTab: boolean }) {
  const summary = analysis.structured_summary;
  if (!summary) {
    return (
      <div className="analysis-brief">
        <CheckedAndFine analysis={analysis} />
      </div>
    );
  }

  const level = analysis.risk?.state === 'scored' ? analysis.risk.level : null;
  const findings = analysis.risk?.contributing_findings ?? [];

  return (
    <div className="analysis-brief">
      <p className="analysis-brief-headline">
        {level && <span className={`risk-badge risk-badge--${level.toLowerCase()} analysis-brief-level`}>{level}</span>}
        <strong>{summary.headline}</strong>
      </p>

      {summary.reasons.length > 0 && (
        <ul className="analysis-brief-reasons">
          {summary.reasons.map((reason) => {
            const finding = findings.find(
              (candidate) =>
                candidate.finding_id === reason.finding_id &&
                (candidate.source === 'cross_document' || candidate.document_id === reason.document_id),
            );
            const severity = finding?.severity ?? 'low';
            const onThisCard = finding?.source === 'cross_document';
            return (
              <li key={`${reason.document_id}:${reason.finding_id}`}>
                <span className={`analysis-brief-dot analysis-brief-dot--${severity}`} aria-label={`${severity} severity`} />
                {onThisCard ? (
                  <button type="button" className="analysis-brief-link" onClick={() => scrollToFinding(reason.finding_id)}>
                    {reason.text}
                  </button>
                ) : linkToRiskTab && finding ? (
                  <Link
                    className="analysis-brief-link"
                    to={{ search: '?section=risk', hash: `risk-finding-${finding.document_id}-${finding.finding_id}` }}
                  >
                    {reason.text}
                  </Link>
                ) : (
                  <span>{reason.text}</span>
                )}
              </li>
            );
          })}
        </ul>
      )}

      <CheckedAndFine analysis={analysis} />

      {summary.would_help && (
        <p className="analysis-brief-help">
          <span className="analysis-brief-label">Would help</span>
          <Link className="analysis-brief-link" to={{ search: '?section=additional' }}>
            {summary.would_help}
          </Link>
        </p>
      )}
    </div>
  );
}
