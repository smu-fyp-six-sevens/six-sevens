import { useState } from 'react';
import type { CaseRisk, CaseRiskFinding } from '../../services/uploadService';
import { formatDocumentType } from '../../services/uploadService';

// "font_consistency_tool" -> "Font consistency"
function formatTool(tool: string | null): string {
  if (!tool) return 'Check';
  const words = tool.replace(/_tool$/, '').replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

type Severity = CaseRiskFinding['severity'];
const SEVERITY_ORDER: Severity[] = ['high', 'medium', 'low'];

// Hidden behind "Show more": lows are "odd but plausibly innocent", and an
// unreviewed finding only got its severity by default (the specialist never
// assessed it). Both still count towards the score.
function isMinor(finding: CaseRiskFinding): boolean {
  return finding.severity === 'low' || finding.unreviewed;
}

interface FindingRow {
  finding: CaseRiskFinding;
  // How many findings on this document share this check and summary.
  count: number;
}

interface DocumentGroup {
  documentId: string;
  filename: string;
  documentType: string | null;
  findings: CaseRiskFinding[];
  rows: FindingRow[];
}

// Grouped per document, keeping the backend's highest-points-first order;
// identical findings (same check, same summary) collapse into one row.
function groupByDocument(findings: CaseRiskFinding[]): DocumentGroup[] {
  const groups = new Map<string, DocumentGroup>();
  for (const finding of findings) {
    let group = groups.get(finding.document_id);
    if (!group) {
      group = {
        documentId: finding.document_id,
        filename: finding.filename,
        documentType: finding.document_type,
        findings: [],
        rows: [],
      };
      groups.set(finding.document_id, group);
    }
    group.findings.push(finding);
    const repeat = group.rows.find(
      (row) => row.finding.tool === finding.tool && row.finding.summary === finding.summary,
    );
    if (repeat) {
      repeat.count += 1;
    } else {
      group.rows.push({ finding, count: 1 });
    }
  }
  return Array.from(groups.values());
}

function countBySeverity(findings: CaseRiskFinding[]): [Severity, number][] {
  return SEVERITY_ORDER.map(
    (severity): [Severity, number] => [severity, findings.filter((f) => f.severity === severity).length],
  ).filter(([, count]) => count > 0);
}

function hiddenLabel(lowCount: number, unreviewedCount: number): string {
  const parts = [
    lowCount > 0 ? `${lowCount} low-severity` : null,
    unreviewedCount > 0 ? `${unreviewedCount} unreviewed` : null,
  ].filter(Boolean);
  return `Show ${parts.join(' and ')} ${lowCount + unreviewedCount === 1 ? 'finding' : 'findings'}`;
}

function NotScoredMessage({ risk }: { risk: CaseRisk | null }) {
  const [title, body, tone] = !risk
    ? ['Risk summary unavailable.', 'Detection results could not be loaded. Refresh to try again.', 'info']
    : risk.state === 'no_documents'
      ? ['No documents to score yet.', 'A risk level appears once documents are uploaded, approved and scanned.', 'info']
      : risk.state === 'failed'
        ? ['Detection failed on a document.', 'Retry detection on the failed document to complete the risk score.', 'warning']
        : [
            `${risk.documents_scanned} of ${risk.documents_total} documents scanned.`,
            'The risk score is calculated once every document has been approved and scanned.',
            'info',
          ];

  return (
    <div className={`summary-message summary-message--${tone}`}>
      <span className="summary-message-icon">{tone === 'warning' ? '⚠' : 'ⓘ'}</span>
      <div>
        <strong>{title}</strong>
        <p>{body}</p>
      </div>
    </div>
  );
}

export function CaseRiskCard({ risk }: { risk: CaseRisk | null }) {
  const scored = risk?.state === 'scored' && risk.level ? risk : null;
  const [showMinor, setShowMinor] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const findings = scored?.contributing_findings ?? [];
  const lowCount = findings.filter((finding) => finding.severity === 'low').length;
  const unreviewedCount = findings.filter((finding) => finding.unreviewed && finding.severity !== 'low').length;
  const visibleFindings = showMinor ? findings : findings.filter((finding) => !isMinor(finding));
  const documents = groupByDocument(visibleFindings);

  function toggleDocument(documentId: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(documentId)) {
        next.delete(documentId);
      } else {
        next.add(documentId);
      }
      return next;
    });
  }

  return (
    <section className="summary-card">
      <div className="summary-card-header">
        <div className="summary-card-title">
          <span className="summary-icon summary-icon--risk">▥</span>
          <h2>Customer Risk Summary</h2>
        </div>

        {scored && (
          <span className={`risk-badge risk-badge--${scored.level!.toLowerCase()}`}>
            {scored.level} risk
          </span>
        )}
      </div>

      {!scored ? (
        <NotScoredMessage risk={risk} />
      ) : (
        <>
          <div className="risk-overview">
            <span>
              Score <strong>{scored.score}</strong>/100
            </span>
            <span>
              Based on {scored.documents_scanned}{' '}
              {scored.documents_scanned === 1 ? 'document' : 'documents'}
            </span>
            {findings.length > 0 && (
              <span>
                {countBySeverity(findings)
                  .map(([severity, count]) => `${count} ${severity}`)
                  .join(' · ')}
              </span>
            )}
            {scored.reasons.map((reason) => (
              <span key={reason} className="risk-reason">
                {reason}
              </span>
            ))}
          </div>

          {findings.length === 0 ? (
            <div className="summary-message summary-message--success">
              <span className="summary-message-icon">✓</span>
              <div>
                <strong>No contributing findings.</strong>
                <p>Detection found nothing worth flagging across this case's documents.</p>
              </div>
            </div>
          ) : documents.length === 0 ? (
            <div className="summary-message summary-message--info">
              <span className="summary-message-icon">ⓘ</span>
              <div>
                <strong>Only minor findings.</strong>
                <p>Every finding is low severity or unreviewed. They're hidden below.</p>
              </div>
            </div>
          ) : (
            <div className="flagged-list">
              {documents.map((document) => {
                const isOpen = expanded.has(document.documentId);
                return (
                  <div key={document.documentId} className="risk-document">
                    <button
                      type="button"
                      className="risk-document-toggle"
                      aria-expanded={isOpen}
                      onClick={() => toggleDocument(document.documentId)}
                    >
                      <span className="risk-document-heading">
                        <strong>{document.filename}</strong>
                        <span className="risk-document-meta">
                          {formatDocumentType(document.documentType)}
                          {countBySeverity(document.findings).map(([severity, count]) => (
                            <span key={severity} className={`risk-severity risk-severity--${severity}`}>
                              {count} {severity}
                            </span>
                          ))}
                        </span>
                        {!isOpen && (
                          <span className="risk-document-preview">
                            {formatTool(document.rows[0].finding.tool)}: {document.rows[0].finding.summary}
                          </span>
                        )}
                      </span>
                      <span className={`risk-chevron ${isOpen ? 'risk-chevron--open' : ''}`} aria-hidden="true">
                        ›
                      </span>
                    </button>

                    {isOpen && (
                      <ul className="risk-findings">
                        {document.rows.map(({ finding, count }) => (
                          <li key={finding.finding_id}>
                            <span className={`risk-severity risk-severity--${finding.severity}`}>
                              {finding.severity}
                            </span>
                            <span className="risk-finding-text">
                              <span className="risk-finding-summary">
                                {formatTool(finding.tool)}: {finding.summary}
                                {count > 1 && <span className="risk-repeat">×{count}</span>}
                              </span>
                              <span className="risk-finding-rationale">
                                {finding.unreviewed ? 'Not reviewed by a specialist.' : finding.rationale}
                              </span>
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {lowCount + unreviewedCount > 0 && (
            <button
              type="button"
              className="risk-toggle"
              aria-expanded={showMinor}
              onClick={() => setShowMinor((current) => !current)}
            >
              {showMinor ? 'Show less' : hiddenLabel(lowCount, unreviewedCount)}
            </button>
          )}

          {scored.unassessed_checks.length > 0 && (
            <details className="risk-unassessed">
              <summary>Couldn't verify ({scored.unassessed_checks.length})</summary>
              <ul>
                {scored.unassessed_checks.map((check, index) => (
                  <li key={`${check.document_id}-${check.tool}-${index}`}>
                    {check.filename} — {formatTool(check.tool)}
                    {check.reason ? `: ${check.reason}` : ''}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </section>
  );
}
