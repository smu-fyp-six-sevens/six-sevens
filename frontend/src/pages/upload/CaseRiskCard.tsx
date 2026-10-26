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

function severityRank(severity: Severity): number {
  const index = SEVERITY_ORDER.indexOf(severity);
  return index === -1 ? SEVERITY_ORDER.length : index;
}

// Most severe first, then most points. Points alone would put a repeated high (scored at a quarter)
// below a medium.
function compareFindings(a: CaseRiskFinding, b: CaseRiskFinding): number {
  return severityRank(a.severity) - severityRank(b.severity) || b.points - a.points;
}

// The score range of each level; mirrors SCORE_BANDS in backend/app/case_pipeline/risk/case_risk.py.
const SCORE_BANDS = [
  { level: 'Low', min: 0, max: 24 },
  { level: 'Medium', min: 25, max: 49 },
  { level: 'High', min: 50, max: 79 },
  { level: 'Critical', min: 80, max: 100 },
] as const;

function contentWords(text: string): string[] {
  return text.toLowerCase().match(/[a-z0-9][a-z0-9.-]*[a-z0-9]|[a-z0-9]/g)?.filter((w) => w.length > 2 || /\d/.test(w)) ?? [];
}

// The rationale without the sentences that only restate the summary, so each row reads as title + what it adds.
function addedDetail(rationale: string, summary: string): string {
  const said = new Set(contentWords(summary));
  return rationale
    .split(/(?<=[.!?])\s+/)
    .filter((sentence) => {
      const words = contentWords(sentence);
      if (words.length === 0) return false;
      const repeated = words.filter((word) => said.has(word)).length;
      return repeated / words.length < 0.6;
    })
    .join(' ');
}

function ScoreGauge({ score, level }: { score: number; level: string }) {
  const band = SCORE_BANDS.find((b) => b.level === level);
  return (
    <div className="risk-score">
      <div className="risk-score-figure">
        <span className="risk-score-value">{score}</span>
        <span className="risk-score-scale">/100</span>
      </div>
      <div className="risk-score-detail">
        <span className={`risk-score-level risk-score-level--${level.toLowerCase()}`}>
          {level} risk{band ? ` · ${band.min}–${band.max}` : ''}
        </span>
        <div className="risk-gauge" role="img" aria-label={`Score ${score} out of 100, ${level} risk`}>
          {SCORE_BANDS.map((b) => (
            <span
              key={b.level}
              className={`risk-gauge-band risk-gauge-band--${b.level.toLowerCase()} ${b.level === level ? 'risk-gauge-band--current' : ''}`}
              style={{ flexGrow: b.max - b.min + 1 }}
              title={`${b.level}: ${b.min}–${b.max}`}
            />
          ))}
          <span className="risk-gauge-marker" style={{ left: `${Math.min(100, Math.max(0, score))}%` }} />
        </div>
        <div className="risk-gauge-labels" aria-hidden="true">
          {SCORE_BANDS.map((b) => (
            <span key={b.level} style={{ flexGrow: b.max - b.min + 1 }}>
              {b.level}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

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

// Grouped per document, most severe first (documents by their most severe finding, then total points);
// identical findings (same check, same summary) collapse into one row.
function groupByDocument(findings: CaseRiskFinding[]): DocumentGroup[] {
  const groups = new Map<string, DocumentGroup>();
  for (const finding of [...findings].sort(compareFindings)) {
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
  const total = (group: DocumentGroup) => group.findings.reduce((sum, f) => sum + f.points, 0);
  return Array.from(groups.values()).sort(
    (a, b) => compareFindings(a.findings[0], b.findings[0]) || total(b) - total(a),
  );
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
  // The score only comes from a case analysis run, never computed on its own.
  const [title, body, tone] = !risk
    ? ['Not scored yet.', 'Run the case analysis once every approved document has been scanned.', 'info']
    : risk.state === 'no_documents'
      ? ['No documents to score yet.', 'A risk level appears once documents are uploaded, approved and scanned.', 'info']
      : risk.state === 'failed'
        ? [
            'Detection failed on a document.',
            `${risk.documents_scanned} of ${risk.documents_total} documents scanned. Retry detection on the failed document to complete the risk score.`,
            'warning',
          ]
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
          {scored.score !== null && scored.score !== undefined && (
            <ScoreGauge score={scored.score} level={scored.level!} />
          )}
          <div className="risk-overview">
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
                          <span className="risk-document-preview">{document.rows[0].finding.summary}</span>
                        )}
                      </span>
                      <span className={`risk-chevron ${isOpen ? 'risk-chevron--open' : ''}`} aria-hidden="true">
                        ›
                      </span>
                    </button>

                    {isOpen && (
                      <ul className="risk-findings">
                        {document.rows.map(({ finding, count }) => {
                          const detail = finding.unreviewed
                            ? 'Not reviewed by a specialist.'
                            : addedDetail(finding.rationale, finding.summary);
                          return (
                            <li key={finding.finding_id}>
                              <span className={`risk-severity risk-severity--${finding.severity}`}>
                                {finding.severity}
                              </span>
                              <span className="risk-finding-text">
                                <span className="risk-finding-check">{formatTool(finding.tool)}</span>
                                <span className="risk-finding-summary">
                                  {finding.summary}
                                  {count > 1 && <span className="risk-repeat">×{count}</span>}
                                </span>
                                {detail && <span className="risk-finding-rationale">{detail}</span>}
                                {finding.repeat_of && (
                                  <span className="risk-finding-rationale">
                                    Also found on another document; counted once there, not again here.
                                  </span>
                                )}
                                {(finding.also_on ?? 0) > 0 && (
                                  <span className="risk-finding-rationale">
                                    Also found on {finding.also_on} other{' '}
                                    {finding.also_on === 1 ? 'document' : 'documents'}; counted once.
                                  </span>
                                )}
                              </span>
                            </li>
                          );
                        })}
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
