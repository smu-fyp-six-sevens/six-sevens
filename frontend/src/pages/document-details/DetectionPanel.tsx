import { useEffect, useState } from 'react';
import type { DetectionReport, IndividualCheckId } from '../../services/uploadService';
import { INDIVIDUAL_DETECTION_CHECKS } from '../../services/uploadService';
import { RefreshButton } from '../../components/RefreshButton';
import { ErrorMessage } from '../../components/ErrorMessage';
import ConfirmDialog from '../../components/ConfirmDialog';

// The reasons a run stops that are written for an officer (detection.py); anything else is an exception's text.
const OFFICER_STOP_REASONS = ['Cancelled by loan officer', 'Interrupted: the service was stopping'];

function confidenceLabel(value: number | undefined): string | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
}

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
  const [confirmRerun, setConfirmRerun] = useState(false);
  // A crashed run's stored error is an exception's text: the console has it, the panel says the run didn't finish.
  const lastRunError = detectionReport?.last_run_error;
  const stopReason = lastRunError && OFFICER_STOP_REASONS.includes(lastRunError) ? lastRunError : null;
  useEffect(() => {
    if (lastRunError && !stopReason) console.error('The last detection run failed:', lastRunError);
  }, [lastRunError, stopReason]);
  const running = status === 'detecting';
  // Approved, with nothing stored yet: the run is waiting for a worker.
  const queued = status === 'approved' && !detectionReport && !detectionError;
  const runnable = isApproved && !detectionSkipped && (status === 'approved' || status === 'detected');
  const canCancel = !readOnly && !detectionSkipped && (running || queued);
  const canRerun = !readOnly && runnable && !running && !queued;

  return (
        <section className="details-card debug-card">
          <RefreshButton isRefreshing={isRefreshingDetection} onClick={() => void refreshDetectionReport()} label="detection" />
          <h2>Detection</h2>
        {detectionError && <ErrorMessage className="action-error" message={detectionError} />}
        {lastRunError && !running && (
          <p>The last run did not finish{stopReason ? ` (${stopReason})` : ''}. The report below is from the run before it.</p>
        )}
        {running && detectionReport && <p>Running again… the report below is from the previous run until this one finishes.</p>}
        {(canCancel || canRerun) && (
          <div className="detection-actions">
            {canCancel && (
              <button type="button" disabled={isCancellingDetection} onClick={() => void handleCancelDetection()}>
                {isCancellingDetection ? 'Cancelling…' : 'Cancel detection'}
              </button>
            )}
            {canRerun && (
              <button
                type="button"
                disabled={isRetryingDetection}
                onClick={() => (detectionReport ? setConfirmRerun(true) : void handleRetryDetection())}
              >
                {isRetryingDetection ? 'Starting…' : detectionReport ? 'Run detection again' : 'Retry detection'}
              </button>
            )}
          </div>
        )}
        {cancelError && <ErrorMessage className="action-error" message={cancelError} />}
        {confirmRerun && (
          <ConfirmDialog
            title="Run detection again?"
            message="This is a full run, with its own model cost. When it finishes, its report and findings replace these, and any verdicts on this document's findings are cleared. If it's cancelled or fails, this report stays."
            confirmLabel="Run again"
            busyLabel="Starting…"
            tone="primary"
            isConfirming={isRetryingDetection}
            onConfirm={() => {
              void Promise.resolve(handleRetryDetection()).finally(() => setConfirmRerun(false));
            }}
            onCancel={() => setConfirmRerun(false)}
          />
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
          // With an error, the line above says what happened.
          !detectionError && (
            // Waiting for a worker reads the same as running, as the status above does.
            <p>{running || queued ? 'Scanning…' : 'Detection starts automatically once the document is approved.'}</p>
          )
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
                  {c.dismissed.length > 0 && (
                    <ul>
                      {c.dismissed.map((d) => (
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
            {detectionReport.nothing_to_check.length > 0 && (
              <>
                <div>Nothing to check (passed):</div>
                <ul>
                  {detectionReport.nothing_to_check.map((u, i) => (
                    <li key={i}>{u.specialist} / {u.tool ?? 'tool'} — {u.reason ?? ''}</li>
                  ))}
                </ul>
              </>
            )}
          </>
        )}

        {detectionReport && (
          <details className="debug-section">
            <summary>Specialist review confidence</summary>
            <ul>
              {detectionReport.consulted.map((specialist) => {
                const score = confidenceLabel(
                  detectionReport.agent_decisions.find((decision) => decision.agent === specialist.specialist)?.confidence,
                );
                return (
                  <li key={specialist.specialist}>
                    {specialist.title}: {score ?? 'not recorded'}
                    {specialist.summary ? ` — ${specialist.summary}` : ''}
                  </li>
                );
              })}
              {detectionReport.consulted.length === 0 && <li>No specialist reviews recorded.</li>}
              <li>Scores are self-reported by the agents, not probabilities of fraud.</li>
            </ul>
          </details>
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
            {individualCheckError && <ErrorMessage className="action-error" message={individualCheckError} />}
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
