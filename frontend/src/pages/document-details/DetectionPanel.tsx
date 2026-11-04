import { useEffect, useState } from 'react';
import type { DetectionReport, IndividualCheckId } from '../../services/uploadService';
import { INDIVIDUAL_DETECTION_CHECKS } from '../../services/uploadService';
import { RefreshButton } from '../../components/RefreshButton';
import { ErrorMessage } from '../../components/ErrorMessage';
import ConfirmDialog from '../../components/ConfirmDialog';
import { formatCheck } from '../../utils/checkLabels';

// The reasons a run stops that are written for an officer (detection.py); anything else is an exception's text.
const OFFICER_STOP_REASONS = ['Cancelled by loan officer', 'Interrupted: the service was stopping'];

const VERDICT_LABELS: Record<DetectionReport['verdict'], string> = {
  flagged: 'Flagged',
  inconclusive: 'Inconclusive',
  clean: 'Clean',
};

// A check the report names (a specialist and a tool), in words.
function checkName(specialists: Map<string, string>, check: { specialist: string; tool: string | null }): string {
  return check.tool ? formatCheck(check.tool) : `${specialists.get(check.specialist) ?? check.specialist}'s checks`;
}

// The detection report for an officer: the verdict and its summary first, what couldn't be checked in plain view
// (a check that didn't run is never clean), the findings as rows with their severity, and one fold for the working
// (specialists, a second look, the checks with nothing to check, and running one check on its own).
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
  const specialists = new Map((detectionReport?.consulted ?? []).map((c) => [c.specialist, c.title]));

  return (
    <section className="details-card detection-card" aria-labelledby="detection-title">
      <div className="detection-head">
        <h2 id="detection-title">Detection</h2>
        <div className="detection-head-actions">
          {canCancel && (
            <button
              type="button"
              className="detection-button"
              disabled={isCancellingDetection}
              onClick={() => void handleCancelDetection()}
            >
              {isCancellingDetection ? 'Cancelling…' : 'Cancel detection'}
            </button>
          )}
          {canRerun && (
            <button
              type="button"
              className="detection-button"
              disabled={isRetryingDetection}
              onClick={() => (detectionReport ? setConfirmRerun(true) : void handleRetryDetection())}
            >
              {isRetryingDetection ? 'Starting…' : detectionReport ? 'Run detection again' : 'Retry detection'}
            </button>
          )}
          <RefreshButton isRefreshing={isRefreshingDetection} onClick={() => void refreshDetectionReport()} label="detection" />
        </div>
      </div>

      {detectionError && <ErrorMessage className="action-error" message={detectionError} />}
      {cancelError && <ErrorMessage className="action-error" message={cancelError} />}
      {lastRunError && !running && (
        <p className="detection-note">
          The last run did not finish{stopReason ? ` (${stopReason})` : ''}. The report below is from the run before it.
        </p>
      )}
      {running && detectionReport && (
        <p className="detection-note">Running again… the report below is from the previous run until this one finishes.</p>
      )}
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
        <p className="detection-empty">Detection doesn't run on rejected documents.</p>
      ) : detectionSkipped && !detectionReport ? (
        <p className="detection-empty">
          Detection doesn't run on documents with no type.
          {status !== 'approved' && ' Choose a document type above to have it scanned after approval.'}
        </p>
      ) : !detectionReport ? (
        // With an error, the line above says what happened.
        !detectionError && (
          // Waiting for a worker reads the same as running, as the status above does.
          <p className="detection-empty">
            {running || queued ? 'Scanning…' : 'Detection starts automatically once the document is approved.'}
          </p>
        )
      ) : (
        <>
          <div className={`detection-verdict detection-verdict--${detectionReport.verdict}`}>
            <span className="detection-verdict-label">{VERDICT_LABELS[detectionReport.verdict]}</span>
            <p className="detection-verdict-summary">{detectionReport.summary}</p>
          </div>

          {detectionReport.unable_to_assess.length > 0 && (
            <div className="detection-unassessed">
              <h3 className="detection-subhead">Could not be checked</h3>
              <ul>
                {detectionReport.unable_to_assess.map((check, index) => (
                  <li key={index}>
                    <strong>{checkName(specialists, check)}</strong>
                    {check.reason ? `: ${check.reason}` : ''}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* No findings needs no section: the verdict already says so. */}
          {detectionReport.findings.length > 0 && (
            <>
              <h3 className="detection-subhead">
                Findings <span className="detection-count">{detectionReport.findings.length}</span>
              </h3>
              <ul className="detection-findings">
                {detectionReport.findings.map((finding) => (
                  <li key={`${finding.tool}-${finding.id}`}>
                    <span className={`risk-severity risk-severity--${finding.severity}`}>
                      {finding.severity}
                    </span>
                    <div className="detection-finding-text">
                      <strong>{finding.summary}</strong>
                      <p>{finding.rationale}</p>
                      <span className="detection-finding-meta">
                        {formatCheck(finding.tool)} · {finding.specialists.map((id) => specialists.get(id) ?? id).join(', ')}
                        {finding.via_second_look ? ' · second look' : ''}
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            </>
          )}

          <details className="detection-more">
            <summary>How it was checked</summary>
            <div className="detection-more-body">
              <ul className="detection-specialists">
                {detectionReport.consulted.map((c) => (
                  <li key={c.specialist}>
                    <strong>
                      {c.title}
                      {c.second_look ? ' (second look)' : ''}
                    </strong>
                    <p>{c.brief}</p>
                    <span className="detection-finding-meta">
                      Ran {c.tools_called.length ? c.tools_called.map(formatCheck).join(', ') : 'no checks'}
                    </span>
                    {c.dismissed.length > 0 && (
                      <ul className="detection-dismissed">
                        {c.dismissed.map((d) => (
                          <li key={`${d.tool}-${d.id}`}>
                            Set aside: {d.summary}. {d.reason}
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                ))}
                {detectionReport.consulted.length === 0 && <li>No specialists were consulted.</li>}
              </ul>
              {detectionReport.second_look && (
                <p className="detection-second-look">
                  <strong>Second look</strong>: {specialists.get(detectionReport.second_look.from) ?? detectionReport.second_look.from}
                  {' asked '}
                  {specialists.get(detectionReport.second_look.to) ?? detectionReport.second_look.to}: “{detectionReport.second_look.question}”
                  {' '}({detectionReport.second_look.reason})
                </p>
              )}
              {detectionReport.nothing_to_check.length > 0 && (
                <>
                  <h4>Nothing to check (passed)</h4>
                  <ul className="detection-plain-list">
                    {detectionReport.nothing_to_check.map((check, index) => (
                      <li key={index}>
                        {checkName(specialists, check)}
                        {check.reason ? `: ${check.reason}` : ''}
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          </details>

          <h3 className="detection-subhead">Run a single check</h3>
          <div className="detection-checks">
            {INDIVIDUAL_DETECTION_CHECKS.map((check) => (
              <button
                key={check.id}
                type="button"
                className="detection-button"
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
              <details key={check.id} className="detection-more">
                <summary>{check.label} result</summary>
                <pre className="debug-json">{JSON.stringify(individualCheckResults[check.id], null, 2)}</pre>
              </details>
            ) : null,
          )}
        </>
      )}
    </section>
  );
}
