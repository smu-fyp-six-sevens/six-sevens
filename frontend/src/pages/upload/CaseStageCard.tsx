import { useState } from 'react';
import type { CaseFailure, CaseRequirementCoverage, CaseStage } from '../../services/uploadService';
import { finaliseCase } from '../../services/uploadService';
import ConfirmDialog from '../../components/ConfirmDialog';
import { caseFailureMessage, caseStageLabel, caseStageTone } from '../../utils/caseStages';
import { missingRequirementsText, type CaseSection } from './caseSections';

// The path every case follows. A stage off it (incomplete, failed) shows at the step it belongs to.
const TRACK: CaseStage[] = [
  'open',
  'processing_documents',
  'pending_review',
  'fraud_detection',
  'ready_for_analysis',
  'pending_evaluation',
  'case_analysed',
  'finalised',
];

// Where a failure happened along the track.
const FAILED_AT: Record<CaseFailure, CaseStage> = {
  document_processing: 'processing_documents',
  fraud_detection: 'fraud_detection',
  case_analysis: 'pending_evaluation',
};

// Stages that take over a step of the track: the step then reads as the stage itself.
const SHOWN_AT: Partial<Record<CaseStage, CaseStage>> = {
  documents_incomplete: 'pending_review',
};

interface NextStep {
  text: string;
  link?: { label: string; section: CaseSection };
}

// What the officer should know or do now, and where to go for it.
function nextStep({ stage, failure, analysisRunning, findingsToReview, missingRequirements }: {
  stage: string;
  failure: CaseFailure | null;
  analysisRunning: boolean;
  findingsToReview: number | null;
  missingRequirements: number;
}): NextStep {
  switch (stage) {
    case 'open':
      return { text: 'Upload the case documents to get started.', link: { label: 'Upload documents', section: 'documents' } };
    case 'processing_documents':
      return { text: 'Documents are being classified and extracted. Nothing to do yet.' };
    case 'pending_review':
      return { text: 'Extracted documents are waiting for you to approve or reject them.', link: { label: 'Review documents', section: 'documents' } };
    case 'documents_incomplete':
      return {
        // This case needs every required document approved; with none outstanding, every document was rejected.
        text: missingRequirements
          ? `Every required document must be approved: ${missingRequirementsText(missingRequirements)}.`
          : 'Every document was rejected. Upload new documents to continue.',
        link: { label: 'Upload documents', section: 'documents' },
      };
    case 'fraud_detection':
      return { text: 'Approved documents are being scanned for fraud.', link: { label: 'View approved documents', section: 'approved' } };
    case 'ready_for_analysis':
      return analysisRunning
        ? { text: 'The case analysis is running: cross-document validation and risk scoring.', link: { label: 'View progress', section: 'analysis' } }
        : { text: 'Every document is scanned. Run the case analysis to score the case.', link: { label: 'Go to case analysis', section: 'analysis' } };
    case 'pending_evaluation':
      return {
        text: findingsToReview
          ? `The case has been analysed. Mark each finding correct or wrong for precision and recall: ${findingsToReview} still need${findingsToReview === 1 ? 's' : ''} your verdict.`
          : 'The case has been analysed. Mark each finding correct or wrong for precision and recall.',
        link: { label: 'Evaluate findings', section: 'risk' },
      };
    case 'case_analysed':
      return { text: 'The case has been analysed. Check the risk summary, then finalise the case.', link: { label: 'View risk summary', section: 'risk' } };
    case 'finalised':
      return { text: 'This case is finalised and closed. It is view-only, so no further edits can be made.' };
    case 'failed':
      return {
        text: caseFailureMessage(failure) ?? 'Something failed. Check the documents and the analysis.',
        link: failure === 'case_analysis'
          ? { label: 'Go to case analysis', section: 'analysis' }
          : failure === 'fraud_detection'
            ? { label: 'View approved documents', section: 'approved' }
            : { label: 'View documents', section: 'documents' },
      };
    default:
      return { text: '' };
  }
}

// Where the case sits on the track, and how that step reads: a detour or failure takes over its step.
function trackPosition(stage: string, failure: CaseFailure | null): { index: number; kind: 'current' | 'detour' | 'failed' } {
  if (stage === 'failed') return { index: TRACK.indexOf(failure ? FAILED_AT[failure] : 'processing_documents'), kind: 'failed' };
  const shownAt = SHOWN_AT[stage as CaseStage];
  if (shownAt) return { index: TRACK.indexOf(shownAt), kind: 'detour' };
  return { index: Math.max(TRACK.indexOf(stage as CaseStage), 0), kind: 'current' };
}

// Where the case stands, what to do next, and the officer's Finalise Case. Each change is recorded in case_status_history.
export function CaseStageCard({
  caseId,
  stage,
  failure,
  onChanged,
  onSelectSection,
  analysisRunning = false,
  caseLabel,
  findingsToReview = null,
  missingRequirements = 0,
}: {
  caseId: string;
  stage: string;
  // Which step failed, while the case is at Failed.
  failure: CaseFailure | null;
  onChanged: (summary: CaseRequirementCoverage) => void;
  onSelectSection: (section: CaseSection) => void;
  // Whether a case analysis is running, which keeps the case at Ready for Analysis.
  analysisRunning?: boolean;
  // The case's number, naming it in the finalise confirmation.
  caseLabel?: string;
  // While the case is analysed: findings still without the officer's verdict. Finalising waits for 0.
  findingsToReview?: number | null;
  // How many required checklist documents are not yet on file.
  missingRequirements?: number;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmingFinalise, setConfirmingFinalise] = useState(false);
  const next = nextStep({ stage, failure, analysisRunning, findingsToReview, missingRequirements });
  const position = trackPosition(stage, failure);
  const canFinalise = stage === 'case_analysed';

  async function changeStage(run: typeof finaliseCase, failed: string) {
    setBusy(true);
    setError(null);
    try {
      onChanged(await run(caseId));
    } catch (err) {
      setError(err instanceof Error && err.message ? err.message : `${failed} Refresh and try again.`);
    } finally {
      setBusy(false);
      setConfirmingFinalise(false);
    }
  }

  return (
    <section className={`summary-card case-status-card tone-${caseStageTone(stage)}`} aria-labelledby="case-stage-title">
      <div className="case-status-top">
        <div className="case-status-summary">
          <div className="case-status-heading">
            <h2 id="case-stage-title">Case Status</h2>
            <span className={`case-stage-badge tone-${caseStageTone(stage)}`}>{caseStageLabel(stage)}</span>
          </div>
          {next.text && (
            <p className={`case-status-next${stage === 'failed' ? ' case-status-next--failed' : ''}`} role="status">
              {stage === 'failed' && <span aria-hidden="true">⚠ </span>}
              {next.text}
              {next.link && (
                <button type="button" className="case-status-link" onClick={() => onSelectSection(next.link!.section)}>
                  {next.link.label} →
                </button>
              )}
            </p>
          )}
        </div>
        {canFinalise && (
          <button type="button" className="case-status-finalise" disabled={busy} onClick={() => setConfirmingFinalise(true)}>
            <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
              <path d="M4 12.5l5 5L20 6.5" />
            </svg>
            Finalise Case
          </button>
        )}
      </div>

      {error && (
        <p className="case-stage-error" role="alert">
          {error}
        </p>
      )}

      <ol className="case-status-track" aria-label="Case stages">
        {TRACK.map((step, index) => {
          const state =
            index < position.index ? 'done' : index === position.index ? position.kind : 'upcoming';
          // A step taken over by another stage reads as that stage (e.g. "Documents Incomplete" in place of Pending Review).
          const label = state === 'detour' ? caseStageLabel(stage) : caseStageLabel(step);
          return (
            <li
              key={step}
              className={`case-status-step case-status-step--${state}`}
              aria-current={state === 'upcoming' || state === 'done' ? undefined : 'step'}
              title={caseStageLabel(step)}
            >
              <span className="case-status-step-dot" aria-hidden="true">
                {state === 'done' ? '✓' : state === 'failed' ? '✕' : ''}
              </span>
              <span className="case-status-step-label">{label}</span>
            </li>
          );
        })}
      </ol>

      {confirmingFinalise && (
        <ConfirmDialog
          title={`Finalise ${caseLabel ?? 'this case'}?`}
          message="The case will be closed and become view-only, and no edits are allowed. This can't be undone."
          confirmLabel="Finalise Case"
          busyLabel="Finalising…"
          tone="primary"
          isConfirming={busy}
          onCancel={() => setConfirmingFinalise(false)}
          onConfirm={() => void changeStage(finaliseCase, "Couldn't finalise the case.")}
        />
      )}
    </section>
  );
}
