import { Link } from 'react-router-dom';
import type { AdverseMediaCheck, CaseAnalysis } from '../../services/uploadService';
import { analysisFindingAnchor, checkedAndFine } from './analysisSummary';

// A due-diligence aid, not a score: a web search for recent sanctions, regulatory action or negative news about
// the company. Every run that has the check says what happened; a skipped or failed one says why, quietly. Only a
// run from before the check existed shows nothing.
// Which company was searched (the name its documents give, not necessarily the case's title), when, and whether this
// run used the case's cached result rather than searching again.
function CheckedWhen({ check }: { check: AdverseMediaCheck }) {
  if (!check.checked_at) return null;
  const when = new Date(check.checked_at).toLocaleString('en-SG', {
    day: 'numeric', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit',
  });
  // A former name is searched too: companies often rename after a scandal, so older coverage uses the old name.
  const former = check.former_names.length > 0 ? ` (also as ${check.former_names.join(', ')})` : '';
  const company = check.query ? ` “${check.query}”${former}` : '';
  return <span className="analysis-brief-media-when">Searched{company} on {when}{check.reused && ' (cached)'}</span>;
}

function AdverseMedia({ check }: { check: AdverseMediaCheck | null | undefined }) {
  if (!check) return null;
  if (check.status === 'skipped' || check.status === 'failed') {
    return (
      <p className="analysis-brief-media analysis-brief-media--muted">
        <span className="analysis-brief-label">Media check</span>
        <span className="analysis-brief-media-text">
          {check.summary ||
            (check.status === 'skipped' ? 'Not run: the case has no company name.' : 'Couldn’t complete the web search this time.')}
        </span>
      </p>
    );
  }
  const concerns = check.status === 'concerns';
  return (
    <div className={`analysis-brief-media analysis-brief-media--${concerns ? 'concerns' : 'clean'}`}>
      <span className="analysis-brief-label">Media check</span>
      <span className="analysis-brief-media-text">
        {check.summary}
        {!concerns && <span aria-label="clear"> ✓</span>}
      </span>
      <CheckedWhen check={check} />
      {concerns && check.sources.length > 0 && (
        <ul className="analysis-brief-media-sources">
          {check.sources.map((source) => (
            <li key={source.url}>
              <a href={source.url} target="_blank" rel="noopener noreferrer">{source.title}</a>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

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
        <AdverseMedia check={analysis.adverse_media} />
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

      <AdverseMedia check={analysis.adverse_media} />

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
