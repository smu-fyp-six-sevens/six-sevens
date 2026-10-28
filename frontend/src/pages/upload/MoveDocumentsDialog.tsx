import type { Dispatch, SetStateAction } from 'react';
import type { CaseOption } from '../../services/uploadService';
import { ErrorMessage } from '../../components/ErrorMessage';

export function MoveDocumentsDialog({
  caseOptions,
  confirmMoveSelected,
  isLoadingCaseOptions,
  isMoveDialogOpen,
  isMovingDocuments,
  moveError,
  moveTargetCaseId,
  selectedMovableOrDeletableCount,
  setIsMoveDialogOpen,
  setMoveTargetCaseId,
}: {
  caseOptions: CaseOption[] | null;
  confirmMoveSelected: () => void | Promise<void>;
  isLoadingCaseOptions: boolean;
  isMoveDialogOpen: boolean;
  isMovingDocuments: boolean;
  moveError: string | null;
  moveTargetCaseId: string;
  selectedMovableOrDeletableCount: number;
  setIsMoveDialogOpen: Dispatch<SetStateAction<boolean>>;
  setMoveTargetCaseId: Dispatch<SetStateAction<string>>;
}) {
  return (
    <>
        {isMoveDialogOpen && (
          <div
            className="confirm-dialog-backdrop"
            onClick={() => {
              if (!isMovingDocuments) setIsMoveDialogOpen(false);
            }}
          >
            <div className="confirm-dialog" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
              <h2>Move {selectedMovableOrDeletableCount} document(s) to another case</h2>
              {isLoadingCaseOptions ? (
                <p>Loading cases…</p>
              ) : moveError && !caseOptions ? (
                <ErrorMessage message={moveError} />
              ) : (
                <>
                  <select
                    value={moveTargetCaseId}
                    onChange={(event) => setMoveTargetCaseId(event.target.value)}
                    disabled={isMovingDocuments}
                  >
                    <option value="">Select a case…</option>
                    {(caseOptions ?? []).map((option) => (
                      <option key={option.id} value={option.id}>
                        {option.company_name || option.greenlink_case_id} ({option.greenlink_case_id})
                      </option>
                    ))}
                  </select>
                  {moveError && <ErrorMessage className="action-error" message={moveError} />}
                </>
              )}
              <div className="confirm-dialog-actions">
                <button
                  type="button"
                  className="confirm-dialog-danger"
                  disabled={isMovingDocuments || !moveTargetCaseId}
                  onClick={() => void confirmMoveSelected()}
                >
                  {isMovingDocuments ? 'Moving…' : 'Move'}
                </button>
                <button
                  type="button"
                  className="confirm-dialog-cancel"
                  disabled={isMovingDocuments}
                  onClick={() => setIsMoveDialogOpen(false)}
                >
                  Cancel
                </button>
              </div>
            </div>
          </div>
        )}
    </>
  );
}
