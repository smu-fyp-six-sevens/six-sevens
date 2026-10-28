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
  cancelError,
  isRefreshingDetection,
  refreshDetectionReport,
  handleRetryDetection,
  handleCancelDetection,
  runningCheckId,
  individualCheckError,
  individualCheckResults,
  handleRunIndividualCheck,
  readOnly = false,
}: {
  status: string;
  detectionError: string | null;
  detectionReport: DetectionReport | null;
  detectionSkipped: boolean;
  isApproved: boolean;
  isRetryingDetection: boolean;
  isCancellingDetection: boolean;
  cancelError: string | null;
  isRefreshingDetection: boolean;
  refreshDetectionReport: () => void | Promise<void>;
  handleRetryDetection: () => void | Promise<void>;
  handleCancelDetection: () => void | Promise<void>;
  runningCheckId: IndividualCheckId | null;
  individualCheckError: string | null;
  individualCheckResults: Partial<Record<IndividualCheckId, unknown>>;
  handleRunIndividualCheck: (checkId: IndividualCheckId) => void | Promise<void>;
  // A finalised case: the report shows, but detection can't be retried or cancelled.
  readOnly?: boolean;
}) {
  return (
        <section className="details-card debug-card">
          <RefreshButton isRefreshing={isRefreshingDetection} onClick={() => void refreshDetectionReport()} label="detection" />
          <h2>Detection</h2>
        {detectionError && (
          <p className="debug-error">{detectionError}</p>
        )}
        {status === 'rejected' ? (
          // Reject only works on an extracted, unapproved document, so a rejected one has never been scanned.
          <p>Detection doesn't run on rejected documents.</p>
        ) : detectionSkipped && !detectionReport ? (
          <p>
            Detection doesn't run on documents with no type.
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
            {readOnly ? null : detectionError ? (
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
            {cancelError && <p className="debug-error">{cancelError}</p>}
          </>
        ) : (
          <>
            <p>
              <strong>Verdict: {detectionReport.verdict.toUpperCase()}</strong> — {detectionReport.summary}
            </p>
            <div>Specialists:</div>
            <ul>
              {detectionReport.consulted.map((c) => (
                <li key={c.specialist}>
                  {c.title}{c.second_look ? ' (second look)' : ''} — {c.brief} [tools: {c.tools_called.join(', ') || 'none'}]
                  {(c.dismissed ?? []).length > 0 && (
                    <ul>
                      {(c.dismissed ?? []).map((d) => (
                        <li key={`${d.tool}-${d.id}`}>dismissed: {d.summary} — {d.reason}</li>
                      ))}
                    </ul>
                  )}
                </li>
              ))}
              {detectionReport.consulted.length === 0 && <li>none</li>}
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
            {(detectionReport.nothing_to_check?.length ?? 0) > 0 && (
              <>
                <div>Nothing to check (passed):</div>
                <ul>
                  {detectionReport.nothing_to_check?.map((u, i) => (
                    <li key={i}>{u.specialist} / {u.tool ?? 'tool'} — {u.reason ?? ''}</li>
                  ))}
                </ul>
              </>
            )}
          </>
        )}

        <h3>Run an individual check</h3>
        {status === 'rejected' ? (
          <p>Not available for rejected documents.</p>
        ) : detectionSkipped && !detectionReport ? (
          <p>Not available for documents with no type.</p>
        ) : !detectionReport ? (
          <p>Run full detection above first — individual checks unlock once a report exists.</p>
        ) : (
          <>
            <div className="debug-actions">
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
              <p className="debug-error">{individualCheckError}</p>
            )}
            {INDIVIDUAL_DETECTION_CHECKS.map((check) =>
              individualCheckResults[check.id] !== undefined ? (
                <details key={check.id} className="debug-section">
                  <summary>{check.label} result</summary>
                  <pre className="debug-json">
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
