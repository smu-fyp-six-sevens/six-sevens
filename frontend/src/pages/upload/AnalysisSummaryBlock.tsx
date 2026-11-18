import { useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Icon } from '../../components/Icon';
import type { AdverseMediaCheck, CaseAnalysis } from '../../services/uploadService';
import { analysisFindingAnchor, checkedAndFine, riskFindingAnchor, unverifiedChecks } from './analysisSummary';

// A due-diligence aid, not a score: a web search for recent sanctions, regulatory action or negative news about
// the company. A run says what happened; a skipped or failed one says why, quietly; one that didn't run it shows nothing.
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
  return (
    <span className="analysis-brief-media-when">
      Searched{company} on {when}{check.reused && ' (cached)'} · Last 3 weeks, selected news and regulator sites
    </span>
  );
}

// A row like the others, not a box inside the card: the outcome is the mark before the text (a tick when the search
// was clear, an alert when it found something, nothing when it didn't run), never a coloured edge.
function AdverseMedia({ check }: { check: AdverseMediaCheck | null | undefined }) {
  if (!check) return null;
  if (check.status === 'skipped' || check.status === 'failed') {
    return (
      <div className="analysis-brief-row analysis-brief-media analysis-brief-media--muted">
        <span className="analysis-brief-label">Media check</span>
        <span className="analysis-brief-media-text">
          {check.summary ||
            (check.status === 'skipped' ? 'Not run: the case has no company name.' : 'Couldn’t complete the web search this time.')}
        </span>
      </div>
    );
  }
  const concerns = check.status === 'concerns';
  return (
    <div className={`analysis-brief-row analysis-brief-media analysis-brief-media--${concerns ? 'concerns' : 'clean'}`}>
      <span className="analysis-brief-label">Media check</span>
      <div className="analysis-brief-media-body">
        <span className="analysis-brief-media-text">
          <span className="analysis-brief-media-mark" role="img" aria-label={concerns ? 'Concern found' : 'Nothing found'}>
            <Icon name={concerns ? 'alert' : 'check'} size={14} />
          </span>
          {check.summary}
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
    </div>
  );
}

function scrollToFinding(findingId: string) {
  window.document.getElementById(analysisFindingAnchor(findingId))?.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

// One line rather than a chip per check: everything else checked out, except what couldn't be verified (checks that
// couldn't run on some document). "Why?" opens where and why, in the text rather than a hover; the full list of what
// passed is folded under "See all".
function CheckedAndFine({ analysis }: { analysis: CaseAnalysis }) {
  const [showWhy, setShowWhy] = useState(false);
  const chips = checkedAndFine(analysis);
  const unverified = unverifiedChecks(analysis);
  if (chips.length === 0 && unverified.length === 0) return null;
  return (
    <div className="analysis-brief-row analysis-brief-checked">
      <span className="analysis-brief-label">Checked and fine</span>
      <div className="analysis-brief-checked-body">
        <p className="analysis-brief-checked-text">
          {/* Beside the label "Checked and fine": "All except X and Y (couldn't verify)." With nothing confirmed,
              "Couldn't verify X."; with nothing unverified, "All checks passed." */}
          {chips.length === 0 ? "Couldn't verify " : unverified.length > 0 ? 'All except ' : 'All checks passed'}
          {unverified.map(({ label }, index) => (
            <span key={label}>
              {index > 0 && (index === unverified.length - 1 ? ' and ' : ', ')}
              <strong className="analysis-brief-unverified">{label}</strong>
            </span>
          ))}
          {chips.length > 0 && unverified.length > 0 && " (couldn't verify)"}
          .
          {unverified.length > 0 && (
            <button
              type="button"
              className="analysis-brief-toggle"
              aria-expanded={showWhy}
              aria-controls="analysis-brief-unverified-why"
              onClick={() => setShowWhy((open) => !open)}
            >
              {showWhy ? 'Hide why' : 'Why?'}
            </button>
          )}
        </p>
        {showWhy && (
          <ul id="analysis-brief-unverified-why" className="analysis-brief-unverified-why">
            {unverified.flatMap(({ label, where }) =>
              where.map((place) => <li key={`${label}-${place}`}>{label} on {place}</li>),
            )}
          </ul>
        )}
        {chips.length > 0 && (
          <details className="analysis-brief-checked-all">
            <summary>{chips.length === 1 ? 'See the check' : `See all ${chips.length}`}</summary>
            <ul className="analysis-brief-chips">
              {chips.map(([label, meaning]) => (
                <li key={label}>
                  <Icon name="check" size={12} className="analysis-brief-chip-mark" />
                  {label}
                  <span className="sr-only">: {meaning}</span>
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </div>
  );
}

// Why the score is what it is, in two parts. The headline and the findings it rests on (each linked to where it is
// reviewed) sit beside the score (.analysis-brief-why); what was checked and fine, the media check and what would
// help run under both (.analysis-brief-checks). The wrapper lays out as its two parts (display: contents in the
// Case Risk card's grid). `linkToRiskTab` is off for an earlier run, whose document findings the tab no longer shows.
export function AnalysisSummaryBlock({
  analysis,
  linkToRiskTab,
  footer,
}: {
  analysis: CaseAnalysis;
  linkToRiskTab: boolean;
  // Under the reasons: on to every finding (Case Risk card's "See all findings").
  footer?: ReactNode;
}) {
  const summary = analysis.structured_summary;
  // Nothing scored: said beside the score, where the reasons would be, so the column isn't left empty.
  const noFindings = analysis.risk !== null && analysis.risk !== undefined && analysis.risk.contributing_findings.length === 0 ? (
    <div className="summary-message summary-message--success analysis-brief-clear">
      <span className="summary-message-icon"><Icon name="check" size={18} /></span>
      <div>
        <strong>No contributing findings.</strong>
        <p>Detection and the case analysis found nothing worth flagging across this case's documents.</p>
      </div>
    </div>
  ) : null;
  if (!summary) {
    return (
      <div className="analysis-brief">
        {noFindings && <div className="analysis-brief-why">{noFindings}</div>}
        <div className="analysis-brief-checks">
          <CheckedAndFine analysis={analysis} />
          <AdverseMedia check={analysis.adverse_media} />
        </div>
      </div>
    );
  }

  const findings = analysis.risk?.contributing_findings ?? [];

  return (
    <div className="analysis-brief">
      <div className="analysis-brief-why">
      {noFindings}
      <p className="analysis-brief-headline">
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
                <span className={`analysis-brief-dot analysis-brief-dot--${severity}`} role="img" aria-label={`${severity} severity`} />
                {linkToRiskTab && finding ? (
                  <Link
                    className="analysis-brief-link"
                    // from=overview so closing a document opened from here returns here, not to Findings (CaseRiskCard).
                    to={{ search: '?section=findings&from=overview', hash: riskFindingAnchor(finding) }}
                  >
                    {reason.text}
                  </Link>
                ) : onThisCard ? (
                  // An earlier run: its cross-document findings are listed on the card under the summary.
                  <button type="button" className="analysis-brief-link" onClick={() => scrollToFinding(reason.finding_id)}>
                    {reason.text}
                  </button>
                ) : (
                  <span>{reason.text}</span>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {footer}
      </div>

      <div className="analysis-brief-checks">
      <CheckedAndFine analysis={analysis} />

      <AdverseMedia check={analysis.adverse_media} />

      {summary.would_help && (
        <p className="analysis-brief-row analysis-brief-help">
          <span className="analysis-brief-label">Would help</span>
          <Link className="analysis-brief-link" to={{ search: '?section=additional' }}>
            {summary.would_help}
          </Link>
        </p>
      )}
      </div>
    </div>
  );
}
