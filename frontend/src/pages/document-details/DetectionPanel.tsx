import type { DetectionReport, IndividualCheckId } from '../../services/uploadService';
import { INDIVIDUAL_DETECTION_CHECKS } from '../../services/uploadService';
import { RefreshButton } from '../../components/RefreshButton';

export function DetectionPanel({
  status,
  detectionError,
  detectionReport,
  detectionSkipped,
  isApproved,
  isRetryingDetection,
  isCancellingDetection,
  isRefreshingDetection,
  refreshDetectionReport,
  handleRetryDetection,
  handleCancelDetection,
  runningCheckId,
  individualCheckError,
  individualCheckResults,
  handleRunIndividualCheck,
}: {
  status: string;
  detectionError: string | null;
  detectionReport: DetectionReport | null;
  detectionSkipped: boolean;
  isApproved: boolean;
  isRetryingDetection: boolean;
  isCancellingDetection: boolean;
  isRefreshingDetection: boolean;
  refreshDetectionReport: () => void | Promise<void>;
  handleRetryDetection: () => void | Promise<void>;
  handleCancelDetection: () => void | Promise<void>;
  runningCheckId: IndividualCheckId | null;
  individualCheckError: string | null;
  individualCheckResults: Partial<Record<IndividualCheckId, unknown>>;
  handleRunIndividualCheck: (checkId: IndividualCheckId) => void | Promise<void>;
}) {
  return (
        <section className="details-card" style={{ fontFamily: 'monospace', fontSize: '0.85em', position: 'relative' }}>
          <RefreshButton isRefreshing={isRefreshingDetection} onClick={() => void refreshDetectionReport()} label="detection" />
          <h2 style={{ marginTop: 0 }}>Detection</h2>
        {detectionError && (
          <p style={{ color: 'crimson' }}>{detectionError}</p>
        )}
        {status === 'rejected' ? (
          // Reject only works on an extracted, unapproved document, so a rejected one has never been scanned.
          <p>Detection doesn't run on rejected documents.</p>
        ) : detectionSkipped && !detectionReport ? (
          <p>
            Detection doesn't run on unrecognised documents.
            {status !== 'approved' &&
              ' Choose a document type above to have it scanned after approval.'}
          </p>
        ) : !detectionReport ? (
          <>
            <p>
              {detectionError
                ? 'The last detection run did not finish.'
                : 'Scanning… detection starts automatically once the document is approved.'}
            </p>
            {detectionError ? (
              <button type="button" disabled={isRetryingDetection} onClick={() => void handleRetryDetection()}>
                {isRetryingDetection ? 'Retrying…' : 'Retry detection'}
              </button>
            ) : (
              isApproved && (
                <button type="button" disabled={isCancellingDetection} onClick={() => void handleCancelDetection()}>
                  {isCancellingDetection ? 'Cancelling…' : 'Cancel detection'}
                </button>
              )
            )}
          </>
        ) : (
          <>
            <p>
              <strong>Verdict: {detectionReport.verdict.toUpperCase()}</strong> — {detectionReport.summary}
            </p>
            <div>Specialists consulted:</div>
            <ul>
              {detectionReport.consulted.map((c) => (
                <li key={c.specialist}>
                  {c.title}{c.second_look ? ' (second look)' : ''} — {c.reason} [tools: {c.tools_called.join(', ') || 'none'}]
                </li>
              ))}
              {detectionReport.consulted.length === 0 && <li>none</li>}
            </ul>
            {detectionReport.plan_deviation &&
              (detectionReport.plan_deviation.added.length > 0 || detectionReport.plan_deviation.dropped.length > 0) && (
                <p>
                  Deviated from the default team — added: {detectionReport.plan_deviation.added.join(', ') || 'none'};
                  dropped: {detectionReport.plan_deviation.dropped.join(', ') || 'none'}
                </p>
              )}
            <div>Specialists not consulted:</div>
            <ul>
              {detectionReport.skipped.map((s) => (
                <li key={s.specialist}>{s.title} — {s.reason}</li>
              ))}
              {detectionReport.skipped.length === 0 && <li>none</li>}
            </ul>
            {detectionReport.second_look && (
              <p>
                Second look: {detectionReport.second_look.from} → {detectionReport.second_look.to} —{' '}
                {detectionReport.second_look.question} ({detectionReport.second_look.reason})
              </p>
            )}
            <div>Findings ({detectionReport.findings.length}):</div>
            <ul>
              {detectionReport.findings.map((f) => (
                <li key={`${f.tool}-${f.id}`}>
                  [{f.severity}] {f.summary} — {f.rationale} ({f.specialists.join(', ')})
                </li>
              ))}
              {detectionReport.findings.length === 0 && <li>none</li>}
            </ul>
            {detectionReport.unable_to_assess.length > 0 && (
              <>
                <div>Could not assess:</div>
                <ul>
                  {detectionReport.unable_to_assess.map((u, i) => (
                    <li key={i}>{u.specialist} / {u.tool ?? 'tool'} — {u.reason ?? 'unknown reason'}</li>
                  ))}
                </ul>
              </>
            )}
          </>
        )}

        <h3 style={{ marginBottom: '0.25em' }}>Run an individual check</h3>
        {status === 'rejected' ? (
          <p>Not available for rejected documents.</p>
        ) : detectionSkipped && !detectionReport ? (
          <p>Not available for unrecognised documents.</p>
        ) : !detectionReport ? (
          <p>Run full detection above first — individual checks unlock once a report exists.</p>
        ) : (
          <>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5em' }}>
              {INDIVIDUAL_DETECTION_CHECKS.map((check) => (
                <button
                  key={check.id}
                  type="button"
                  disabled={runningCheckId !== null}
                  onClick={() => void handleRunIndividualCheck(check.id)}
                >
                  {runningCheckId === check.id ? 'Running…' : check.label}
                </button>
              ))}
            </div>
            {individualCheckError && (
              <p style={{ color: 'crimson' }}>{individualCheckError}</p>
            )}
            {INDIVIDUAL_DETECTION_CHECKS.map((check) =>
              individualCheckResults[check.id] !== undefined ? (
                <details key={check.id} style={{ marginTop: '0.5em' }}>
                  <summary>{check.label} result</summary>
                  <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                    {JSON.stringify(individualCheckResults[check.id], null, 2)}
                  </pre>
                </details>
              ) : null,
            )}
          </>
        )}
      </section>
  );
}
