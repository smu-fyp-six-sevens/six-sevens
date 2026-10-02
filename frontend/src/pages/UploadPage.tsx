import { useEffect, useMemo, useRef, useState } from 'react';
import type { CaseRecord } from '../App';
import AppHeader from '../components/AppHeader';
import ConfirmDialog from '../components/ConfirmDialog';
import DropZone from '../components/DropZone';
import FileRow from '../components/FileRow';
import KebabMenu from '../components/KebabMenu';
import { subscribeToCaseStatus } from '../services/caseSocket';
import type { CaseStatusSubscription } from '../services/caseSocket';
import {
  approveDocument,
  cancelDocument,
  canCancelDocument,
  canRetryDocument,
  deleteCase,
  isDocumentStuckProcessing,
  retryDocument,
  ADDITIONAL_DOCUMENT_TYPES,
  REQUIRED_DOCUMENT_TYPES,
  EXTRACTION_CONFIDENCE_THRESHOLD,
  TERMINAL_DOCUMENT_STATUSES,
  documentDetectionStage,
  formatDocumentStatus,
  formatDocumentStatusLabel,
  isDocumentStatusInProgress,
  formatDocumentType,
  formatRoutingOutcome,
  lowestFieldConfidence,
  bulkDeleteDocuments,
  bulkMoveDocuments,
  listCaseOptions,
  updateCase,
  updateExtractedFields,
  uploadDocuments,
  CLASSIFICATION_CONFIDENCE_THRESHOLD,
} from '../services/uploadService';
import type {
  BulkUploadItemResult,
  CaseOption,
  DocumentCaseMatch,
  DocumentRecord,
} from '../services/uploadService';
import {
  MAX_BULK_FILES,
  MAX_FILE_SIZE_BYTES,
  checkFileCount,
  recordUploadedHash,
  validateFiles,
} from '../utils/fileValidation';
import type { FileIssue } from '../utils/fileValidation';
import {
  MAX_COMPANY_NAME_LENGTH,
  validateCompanyName,
} from '../utils/caseValidation';


const MAX_FILE_SIZE_MB = Math.floor(MAX_FILE_SIZE_BYTES / (1024 * 1024));

const REQUIREMENT_LABELS = new Map(
  [...REQUIRED_DOCUMENT_TYPES, ...ADDITIONAL_DOCUMENT_TYPES].map((requirement) => [
    requirement.value,
    requirement.label,
  ]),
);

interface UploadPageProps {
  selectedCase: CaseRecord;
  onBack: () => void;
  onOpenDocument: (documentId: string) => void;
  onCaseUpdated: (companyName: string) => void;
  onCaseDeleted: () => void;
}

function isExtractionNotConfiguredFailure(reason: string | null | undefined): boolean {
  return Boolean(reason?.startsWith('Extraction invocation failed'));
}

// Extracted field keys are snake_case identifiers (e.g. "bank_name",
// "id_number") since they're also used as object keys sent back on save —
// this only affects how they're displayed, not the underlying data.
const FIELD_KEY_ACRONYMS = new Set(['id', 'uen', 'tin', 'nric']);

// Fields that carry structured data (arrays/objects) rather than a single
// display-able value — shown nowhere in this UI (no natural single-line or
// text-input representation), but still round-tripped untouched on save so
// editing an unrelated field never wipes them out (see saveFieldDraft).
const NON_DISPLAYED_FIELD_KEYS = new Set(['transactions']);

function displayableFieldEntries(fields: Record<string, unknown>): [string, unknown][] {
  return Object.entries(fields).filter(([key]) => !NON_DISPLAYED_FIELD_KEYS.has(key));
}

// True for a value the LLM returned as an array or nested object rather
// than a single scalar — e.g. multiple individual_name entries on a
// document with co-borrowers. String(value) on these renders as
// "[object Object]" (or a comma-joined run of them), so they need their
// own formatting (see formatFieldValue) and can't be a plain text input.
function isStructuredValue(value: unknown): boolean {
  return Array.isArray(value) || (typeof value === 'object' && value !== null);
}

// Renders any field value — including an array of scalars, an array of
// objects, or a single nested object — as a readable string instead of
// relying on String(value), which stringifies each object element via its
// default toString() ("[object Object]"). Recurses so an array of objects
// (e.g. multiple {name, id_number} entries) reads as
// "name: John Doe, id_number: S1234567A; name: Jane Doe, id_number: S7654321B".
function formatFieldValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (Array.isArray(value)) {
    return value.map((item) => formatFieldValue(item)).join('; ');
  }
  if (typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, nested]) => `${humanizeFieldKey(key)}: ${formatFieldValue(nested)}`)
      .join(', ');
  }
  return String(value);
}

// Displayable fields whose value is structured (array/object) — shown
// read-only even when the rest of the document's fields are editable,
// since flattening them into a single text input would be lossy on save.
function structuredFieldEntries(fields: Record<string, unknown>): [string, unknown][] {
  return displayableFieldEntries(fields).filter(([, value]) => isStructuredValue(value));
}

function humanizeFieldKey(key: string): string {
  return key
    .split('_')
    .filter(Boolean)
    .map((word) =>
      FIELD_KEY_ACRONYMS.has(word.toLowerCase())
        ? word.toUpperCase()
        : word.charAt(0).toUpperCase() + word.slice(1),
    )
    .join(' ');
}

function fieldsToDraft(fields: Record<string, unknown>): Record<string, string> {
  const draft: Record<string, string> = {};
  for (const [key, value] of displayableFieldEntries(fields)) {
    if (isStructuredValue(value)) continue;
    draft[key] = value === null || value === undefined ? '' : String(value);
  }
  return draft;
}

// The case snapshot wraps each extracted field as {value, confidence}.
function extractedFieldValue(
  fields: Record<string, unknown> | null,
  key: string,
): unknown {
  const field = fields?.[key];
  if (field && typeof field === 'object' && 'value' in field) {
    return (field as { value: unknown }).value;
  }
  return field;
}

// Names of the individuals whose NRIC/passport has been accepted, one per
// person — an NRIC's front and back (or the same person's NRIC and passport)
// group by nric_number, falling back to name, like mapper.py's identities.
// A back capture carries no name, so it adds nothing until its front is in.
function acceptedIdentityNames(documents: DocumentRecord[]): string[] {
  const namesByIdentity = new Map<string, string>();

  for (const document of documents) {
    if (
      document.status !== 'analyzed' ||
      document.routing_status !== 'accepted' ||
      (document.document_type !== 'nric' && document.document_type !== 'passport')
    ) {
      continue;
    }

    const name = extractedFieldValue(document.extracted_fields, 'individual_name');
    const idNumber = extractedFieldValue(document.extracted_fields, 'nric_number');
    const displayName = typeof name === 'string' ? name.trim() : '';
    const identityKey =
      (typeof idNumber === 'string' ? idNumber.replace(/\s+/g, '').toUpperCase() : '') ||
      displayName.toUpperCase().replace(/\s+/g, ' ');
    if (!identityKey) continue;

    if (displayName || !namesByIdentity.has(identityKey)) {
      namesByIdentity.set(identityKey, namesByIdentity.get(identityKey) || displayName);
    }
  }

  return Array.from(namesByIdentity.values()).filter(Boolean);
}

function describeIssue(issue: FileIssue): string {
  switch (issue.type) {
    case 'empty':
      return 'File is empty (0 bytes).';
    case 'too-large':
      return `File exceeds the ${MAX_FILE_SIZE_MB} MB limit.`;
    case 'invalid-signature':
      return 'File content does not match a supported type.';
    case 'invalid-filename':
      return 'Filename is invalid or unsafe.';
    case 'duplicate':
      return 'This exact file has already been uploaded for this case.';
  }
}

export default function UploadPage({
  selectedCase,
  onBack,
  onOpenDocument,
  onCaseUpdated,
  onCaseDeleted,
}: UploadPageProps) {
  const [isEditingCase, setIsEditingCase] = useState(false);
  const [companyNameDraft, setCompanyNameDraft] = useState(selectedCase.applicant);
  const [isSavingCase, setIsSavingCase] = useState(false);
  const [saveCaseError, setSaveCaseError] = useState<string | null>(null);

  useEffect(() => {
    setCompanyNameDraft(selectedCase.applicant);
  }, [selectedCase.applicant]);

  async function handleSaveCase() {
    const trimmed = companyNameDraft.trim();
    const validationError = validateCompanyName(companyNameDraft);
    if (validationError) {
      setSaveCaseError(validationError);
      return;
    }

    setIsSavingCase(true);
    setSaveCaseError(null);

    try {
      const updated = await updateCase(selectedCase.id, trimmed);
      onCaseUpdated(updated.company_name ?? trimmed);
      setIsEditingCase(false);
    } catch (err) {
      setSaveCaseError(err instanceof Error ? err.message : 'Failed to save case.');
    } finally {
      setIsSavingCase(false);
    }
  }

  function handleCancelEditCase() {
    setCompanyNameDraft(selectedCase.applicant);
    setSaveCaseError(null);
    setIsEditingCase(false);
  }

  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [countError, setCountError] = useState<string | null>(null);
  const [rejections, setRejections] = useState<{ file: File; issue: FileIssue }[]>([]);
  const [results, setResults] = useState<BulkUploadItemResult[]>([]);
  const [justSubmitted, setJustSubmitted] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [documentDetails, setDocumentDetails] = useState<Map<string, DocumentRecord>>(new Map());
  const [fulfilledRequirements, setFulfilledRequirements] = useState<Set<string>>(new Set());
  const [proofOfAddressCoverage, setProofOfAddressCoverage] = useState<{
    covered: number;
    total: number;
    unmatched_names: string[];
  }>({ covered: 0, total: 0, unmatched_names: [] });
  const [idDocumentCoverage, setIdDocumentCoverage] = useState<{
    covered: number;
    total: number;
    missing_side: string | null;
  }>({ covered: 0, total: 0, missing_side: null });
  const [fieldDrafts, setFieldDrafts] = useState<Map<string, Record<string, string>>>(new Map());
  const [savingFields, setSavingFields] = useState<Set<string>>(new Set());
  const [saveFieldErrors, setSaveFieldErrors] = useState<Map<string, string>>(new Map());
  const [wsConnected, setWsConnected] = useState(false);
  const [approvingDocumentId, setApprovingDocumentId] = useState<string | null>(null);
  const [approveError, setApproveError] = useState<string | null>(null);
  const [cancellingDocumentId, setCancellingDocumentId] = useState<string | null>(null);
  const [cancelActionError, setCancelActionError] = useState<string | null>(null);
  const [retryingDocumentId, setRetryingDocumentId] = useState<string | null>(null);
  const [retryActionError, setRetryActionError] = useState<string | null>(null);
  // Forces a re-render every so often while any document is PROCESSING, so
  // a "Stuck"/Retry-eligible document surfaces purely from time passing —
  // isDocumentStuckProcessing itself reads Date.now() fresh each render.
  const [, setStuckTick] = useState(0);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [isDeletingCase, setIsDeletingCase] = useState(false);
  const [caseDeleteOpen, setCaseDeleteOpen] = useState(false);
  // Bulk selection — replaces the old per-row action icons (cancel stays
  // per-row below, it's a one-click "emergency stop" on an in-flight
  // document, a different lifecycle stage from these terminal-document
  // actions, and doesn't make sense as a batch).
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<Set<string>>(new Set());
  const [documentsToDelete, setDocumentsToDelete] = useState<DocumentRecord[]>([]);
  const [isBulkDeleting, setIsBulkDeleting] = useState(false);
  const [isApprovingSelected, setIsApprovingSelected] = useState(false);
  const [isMoveDialogOpen, setIsMoveDialogOpen] = useState(false);
  const [caseOptions, setCaseOptions] = useState<CaseOption[] | null>(null);
  const [isLoadingCaseOptions, setIsLoadingCaseOptions] = useState(false);
  const [moveTargetCaseId, setMoveTargetCaseId] = useState('');
  const [isMovingDocuments, setIsMovingDocuments] = useState(false);
  const [moveError, setMoveError] = useState<string | null>(null);
  // Case-match result per document (see backend/app/case_match.py), pushed
  // with every case snapshot — folded into the displayed routing outcome.
  const [caseMatches, setCaseMatches] = useState<Map<string, DocumentCaseMatch>>(new Map());

  // A case-match flag needs manual review until an officer approves the
  // document — approving it is that review.
  const caseMatchNeedsReview = (document: DocumentRecord) =>
    document.status !== 'approved' &&
    caseMatches.get(document.id)?.status === 'manual_review';

  const flaggedDocuments = Array.from(documentDetails.values()).filter((document) => {
    const lowestConfidence = lowestFieldConfidence(document.extracted_fields);
    return (
      document.routing_status === 'manual_review' ||
      caseMatchNeedsReview(document) ||
      (lowestConfidence != null && lowestConfidence < EXTRACTION_CONFIDENCE_THRESHOLD)
    );
  });

  const identityNames = useMemo(
    () => acceptedIdentityNames(Array.from(documentDetails.values())),
    [documentDetails],
  );

  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const hasOutcome = results.length > 0 || rejections.length > 0;

  // One WebSocket per case (see caseSocket.ts / backend routers/ws.py)
  // replaces the old per-document poll loop: the backend pushes every
  // document's status plus the case-level requirement checklist together
  // whenever anything changes, including documents uploaded after this
  // subscription opened — no separate per-upload poll kickoff needed.
  // Kept in a ref (rather than just returned from the effect) so
  // handleSubmit can call .refresh() after a successful upload — see its
  // docstring in caseSocket.ts for why that matters once a case goes idle.
  const caseSocketRef = useRef<CaseStatusSubscription | null>(null);
  useEffect(() => {
    setWsConnected(false);

    const subscription = subscribeToCaseStatus(
      selectedCase.id,
      ({ documents, caseSummary, caseMatch }) => {
        if (!isMountedRef.current) return;

        setDocumentDetails(new Map(documents.map((document) => [document.id, document])));
        setCaseMatches(
          new Map((caseMatch?.documents ?? []).map((match) => [match.document_id, match])),
        );
        setFulfilledRequirements(new Set(caseSummary.fulfilled_requirements));
        setProofOfAddressCoverage(caseSummary.proof_of_address_coverage);
        setIdDocumentCoverage(caseSummary.id_document_coverage);
      },
      (connected) => {
        if (isMountedRef.current) setWsConnected(connected);
      },
    );
    caseSocketRef.current = subscription;

    return () => {
      caseSocketRef.current = null;
      subscription.unsubscribe();
    };
  }, [selectedCase.id]);

  const resultByFilename = useMemo(
    () => new Map(results.map((result) => [result.filename, result])),
    [results],
  );

  function handleFiles(files: File[]) {
    setSelectedFiles((current) => {
      const combined = [...current, ...files];
      setCountError(checkFileCount(combined));
      return combined.slice(0, MAX_BULK_FILES);
    });

    setResults([]);
    setRejections([]);
    setError(null);
    setJustSubmitted(false);
  }

  async function handleSubmit() {
    if (!selectedFiles.length || countError) return;

    setIsSubmitting(true);
    setError(null);
    setResults([]);
    setRejections([]);
    setJustSubmitted(true);

    try {
      const { ok, rejected } = await validateFiles(
        selectedFiles,
        selectedCase.id,
      );

      setRejections(rejected);

      if (ok.length === 0) return;

      const response = await uploadDocuments(
        selectedCase.id,
        ok.map((validated) => validated.uploadFile),
      );

      setResults(response.results);

      response.results.forEach((result, index) => {
        if (result.success) {
          recordUploadedHash(selectedCase.id, ok[index].hash);
        }
      });

      // Wakes the backend's poll loop immediately instead of waiting out
      // its idle interval (up to 30s once a case has nothing in flight —
      // see IDLE_POLL_INTERVAL_SECONDS in routers/ws.py) — otherwise the
      // moment someone's most likely to be watching for feedback could be
      // the one moment it's slowest to arrive.
      if (response.results.some((result) => result.success)) {
        caseSocketRef.current?.refresh();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed.');
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleApproveDocument(documentId: string) {
  setApprovingDocumentId(documentId);
  setApproveError(null);

  try {
    const updated = await approveDocument(documentId);

    if (!isMountedRef.current) {
      return;
    }

    setDocumentDetails((current) => {
      const next = new Map(current);
      next.set(documentId, updated);
      return next;
    });
  } catch (err) {
    if (!isMountedRef.current) {
      return;
    }

    setApproveError(
      err instanceof Error
        ? err.message
        : 'Failed to approve document.',
    );
  } finally {
    if (isMountedRef.current) {
      setApprovingDocumentId(null);
    }
  }
}

async function handleCancelDocument(documentId: string) {
  setCancellingDocumentId(documentId);
  setCancelActionError(null);

  try {
    const updated = await cancelDocument(documentId);

    if (!isMountedRef.current) {
      return;
    }

    setDocumentDetails((current) => {
      const next = new Map(current);
      next.set(documentId, updated);
      return next;
    });
  } catch (err) {
    if (!isMountedRef.current) {
      return;
    }

    setCancelActionError(
      err instanceof Error ? err.message : 'Failed to cancel document.',
    );
  } finally {
    if (isMountedRef.current) {
      setCancellingDocumentId(null);
    }
  }
}

async function handleRetryDocument(documentId: string) {
  setRetryingDocumentId(documentId);
  setRetryActionError(null);

  try {
    const updated = await retryDocument(documentId);

    if (!isMountedRef.current) {
      return;
    }

    setDocumentDetails((current) => {
      const next = new Map(current);
      next.set(documentId, updated);
      return next;
    });
  } catch (err) {
    if (!isMountedRef.current) {
      return;
    }

    setRetryActionError(
      err instanceof Error ? err.message : 'Failed to retry document.',
    );
  } finally {
    if (isMountedRef.current) {
      setRetryingDocumentId(null);
    }
  }
}

// A document is "eligible" (for selecting, then approving) once it's
// analyzed and its case-match is clean — a document flagged as belonging to
// another company/individual is never swept up this way.
function isEligibleForApproval(
  document: DocumentRecord,
  caseMatches: Map<string, DocumentCaseMatch>,
): boolean {
  return (
    document.status === 'analyzed' &&
    formatRoutingOutcome(document, caseMatches.get(document.id)) === 'Accepted'
  );
}
  function getFieldDraft(
    documentId: string,
    extractedFields: Record<string, unknown>,
  ): Record<string, string> {
    return fieldDrafts.get(documentId) ?? fieldsToDraft(extractedFields);
  }

  function setFieldDraftValue(
    documentId: string,
    extractedFields: Record<string, unknown>,
    key: string,
    value: string,
  ) {
    setFieldDrafts((current) => {
      const next = new Map(current);
      const draft = {
        ...getFieldDraft(documentId, extractedFields),
        [key]: value,
      };
      next.set(documentId, draft);
      return next;
    });
  }

  async function saveFieldDraft(
    documentId: string,
    originalFields: Record<string, unknown>,
  ) {
    const draft = fieldDrafts.get(documentId);
    if (!draft) return;

    setSavingFields((current) => new Set(current).add(documentId));

    setSaveFieldErrors((current) => {
      const next = new Map(current);
      next.delete(documentId);
      return next;
    });

    // Start from the original fields so non-displayed ones (e.g.
    // "transactions") round-trip untouched instead of being dropped by a
    // save that only ever saw the displayable subset. Empty string means
    // "cleared by the officer" — send null rather than "" so it reads as
    // "not present" the same way an LLM-omitted field does.
    const payload: Record<string, unknown> = { ...originalFields };

    for (const [key, value] of Object.entries(draft)) {
      payload[key] = value.trim() === '' ? null : value;
    }

    try {
      const updated = await updateExtractedFields(documentId, payload);

      if (!isMountedRef.current) return;

      setDocumentDetails(
        (current) => new Map(current).set(documentId, updated),
      );

      setFieldDrafts((current) => {
        const next = new Map(current);
        next.delete(documentId);
        return next;
      });
    } catch (err) {
      if (!isMountedRef.current) return;

      setSaveFieldErrors((current) =>
        new Map(current).set(
          documentId,
          err instanceof Error ? err.message : 'Failed to save fields.',
        ),
      );
    } finally {
      if (isMountedRef.current) {
        setSavingFields((current) => {
          const next = new Set(current);
          next.delete(documentId);
          return next;
        });
      }
    }
  }

  function removeFile(index: number) {
    setSelectedFiles((files) => files.filter((_, i) => i !== index));
    setCountError(null);
    setResults([]);
    setRejections([]);
    setError(null);
    setJustSubmitted(false);
  }

  async function confirmDeleteCase() {
    setIsDeletingCase(true);
    setDeleteError(null);

    try {
      await deleteCase(selectedCase.id);
      onCaseDeleted();
    } catch (err) {
      setCaseDeleteOpen(false);
      setDeleteError(err instanceof Error ? err.message : 'Failed to delete case.');
      setIsDeletingCase(false);
    }
  }

  function clearDocumentSelection() {
    setSelectedDocumentIds(new Set());
  }

  function toggleDocumentSelected(documentId: string) {
    setSelectedDocumentIds((current) => {
      const next = new Set(current);
      if (next.has(documentId)) {
        next.delete(documentId);
      } else {
        next.add(documentId);
      }
      return next;
    });
  }

  async function confirmDeleteDocuments() {
    if (documentsToDelete.length === 0) return;

    const targets = documentsToDelete;
    setIsBulkDeleting(true);
    setDeleteError(null);

    try {
      const result = await bulkDeleteDocuments(targets.map((document) => document.id));
      const deletedIds = new Set(result.deleted);

      if (deletedIds.size > 0) {
        setDocumentDetails((current) => {
          const next = new Map(current);
          for (const id of deletedIds) next.delete(id);
          return next;
        });
        setCaseMatches((current) => {
          const next = new Map(current);
          for (const id of deletedIds) next.delete(id);
          return next;
        });
        setSelectedDocumentIds((current) => {
          const next = new Set(current);
          for (const id of deletedIds) next.delete(id);
          return next;
        });
        caseSocketRef.current?.refresh();
      }

      if (result.failed.length > 0) {
        setDeleteError(
          result.failed.length === targets.length
            ? 'Failed to delete the selected document(s).'
            : `${result.failed.length} of ${targets.length} documents could not be deleted.`,
        );
      }
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : 'Failed to delete the selected document(s).');
    }

    setDocumentsToDelete([]);
    setIsBulkDeleting(false);
  }

  function handleSelectAllEligible() {
    setSelectedDocumentIds(
      new Set(
        extractedDocuments
          .filter((document) => isEligibleForApproval(document, caseMatches))
          .map((document) => document.id),
      ),
    );
  }

  async function handleApproveSelected() {
    const eligible = extractedDocuments.filter(
      (document) => selectedDocumentIds.has(document.id) && isEligibleForApproval(document, caseMatches),
    );
    if (eligible.length === 0) return;

    setIsApprovingSelected(true);
    setApproveError(null);

    try {
      const updatedDocuments = await Promise.all(
        eligible.map((document) => approveDocument(document.id)),
      );
      if (!isMountedRef.current) return;

      setDocumentDetails((current) => {
        const next = new Map(current);
        for (const updated of updatedDocuments) next.set(updated.id, updated);
        return next;
      });
      setSelectedDocumentIds((current) => {
        const next = new Set(current);
        for (const updated of updatedDocuments) next.delete(updated.id);
        return next;
      });
    } catch (err) {
      if (!isMountedRef.current) return;
      setApproveError(err instanceof Error ? err.message : 'Failed to approve selected documents.');
    } finally {
      if (isMountedRef.current) setIsApprovingSelected(false);
    }
  }

  async function openMoveDialog() {
    setMoveError(null);
    setMoveTargetCaseId('');
    setIsMoveDialogOpen(true);
    if (caseOptions !== null) return;

    setIsLoadingCaseOptions(true);
    try {
      const options = await listCaseOptions();
      if (isMountedRef.current) {
        setCaseOptions(options.filter((option) => option.id !== selectedCase.id));
      }
    } catch (err) {
      if (isMountedRef.current) {
        setMoveError(err instanceof Error ? err.message : 'Failed to load cases.');
      }
    } finally {
      if (isMountedRef.current) setIsLoadingCaseOptions(false);
    }
  }

  async function confirmMoveSelected() {
    if (!moveTargetCaseId) return;

    const eligible = extractedDocuments.filter(
      (document) => selectedDocumentIds.has(document.id) && TERMINAL_DOCUMENT_STATUSES.has(document.status),
    );
    if (eligible.length === 0) {
      setIsMoveDialogOpen(false);
      return;
    }

    setIsMovingDocuments(true);
    setMoveError(null);

    try {
      const result = await bulkMoveDocuments(eligible.map((document) => document.id), moveTargetCaseId);
      const movedIds = new Set(result.moved);

      if (movedIds.size > 0) {
        setDocumentDetails((current) => {
          const next = new Map(current);
          for (const id of movedIds) next.delete(id);
          return next;
        });
        setCaseMatches((current) => {
          const next = new Map(current);
          for (const id of movedIds) next.delete(id);
          return next;
        });
        setSelectedDocumentIds((current) => {
          const next = new Set(current);
          for (const id of movedIds) next.delete(id);
          return next;
        });
        caseSocketRef.current?.refresh();
      }

      if (result.failed.length > 0) {
        setMoveError(
          result.failed.length === eligible.length
            ? 'Failed to move the selected document(s).'
            : `${result.failed.length} of ${eligible.length} documents could not be moved.`,
        );
      } else {
        setIsMoveDialogOpen(false);
      }
    } catch (err) {
      setMoveError(err instanceof Error ? err.message : 'Failed to move the selected document(s).');
    }

    setIsMovingDocuments(false);
  }

  const documents = Array.from(documentDetails.values()).sort((a, b) =>
    a.uploaded_at.localeCompare(b.uploaded_at),
  );

  const anyDocumentProcessing = documents.some((document) => document.status === 'processing');
  // Mirrors routers/cases.py's delete_case guard: every status not in
  // TERMINAL_DOCUMENT_STATUSES is in-flight (the two sets are exact
  // complements of DocumentStatus).
  const caseHasInFlightDocument = documents.some(
    (document) => !TERMINAL_DOCUMENT_STATUSES.has(document.status),
  );
  useEffect(() => {
    if (!anyDocumentProcessing) {
      return;
    }
    const interval = window.setInterval(() => setStuckTick((n) => n + 1), 15000);
    return () => window.clearInterval(interval);
  }, [anyDocumentProcessing]);

  const extractedDocuments = documents.filter(
    (document) => document.status !== 'approved',
  );

  const approvedDocuments = documents.filter(
    (document) => document.status === 'approved',
  );

  const selectedExtractedDocuments = extractedDocuments.filter((document) =>
    selectedDocumentIds.has(document.id),
  );
  const allExtractedSelected =
    extractedDocuments.length > 0 && selectedExtractedDocuments.length === extractedDocuments.length;
  const eligibleForApprovalCount = extractedDocuments.filter((document) =>
    isEligibleForApproval(document, caseMatches),
  ).length;
  const selectedApprovableCount = selectedExtractedDocuments.filter((document) =>
    isEligibleForApproval(document, caseMatches),
  ).length;
  const selectedMovableOrDeletableCount = selectedExtractedDocuments.filter((document) =>
    TERMINAL_DOCUMENT_STATUSES.has(document.status),
  ).length;

  const completedCount = results.length + rejections.length;
  const successfulCount = results.filter((result) => result.success).length;
  const failedCount =
    results.filter((result) => !result.success).length + rejections.length;

  return (
    <main className="shell shell--upload">
      <AppHeader />

      <div className="upload-view">
        <section className="upload-main">
          <button type="button" className="back-link" onClick={onBack}>
            ← Back to case queue
          </button>

          {isEditingCase ? (
            <div className="add-field-form">
              <div className="add-field-form-row">
                <label htmlFor="case-company-name">Company name</label>
                <input
                  id="case-company-name"
                  type="text"
                  value={companyNameDraft}
                  onChange={(event) => setCompanyNameDraft(event.target.value)}
                  maxLength={MAX_COMPANY_NAME_LENGTH}
                  disabled={isSavingCase}
                  aria-invalid={saveCaseError ? true : undefined}
                  aria-describedby={saveCaseError ? 'case-company-name-error' : undefined}
                />
              </div>

              {saveCaseError && (
                <p id="case-company-name-error" className="add-field-error" role="alert">
                  {saveCaseError}
                </p>
              )}

              <div className="add-field-form-actions">
                <button type="button" onClick={handleCancelEditCase} disabled={isSavingCase}>
                  Cancel
                </button>
                <button type="button" onClick={() => void handleSaveCase()} disabled={isSavingCase}>
                  {isSavingCase ? 'Saving…' : 'Save'}
                </button>
              </div>
            </div>
          ) : (
            <div className="case-header-row">
              <p className="eyebrow">
                Case #{selectedCase.caseNumber} · {selectedCase.type} —{' '}
                {selectedCase.applicant}
              </p>
              <div className="case-header-actions">
                <KebabMenu
                  label={isDeletingCase ? 'Deleting case' : 'Case actions'}
                  disabled={isDeletingCase}
                  items={[
                    {
                      id: 'edit',
                      label: 'Edit case details',
                      onSelect: () => setIsEditingCase(true),
                    },
                    {
                      id: 'delete',
                      label: isDeletingCase ? 'Deleting…' : 'Delete case',
                      danger: true,
                      disabled: isDeletingCase || caseHasInFlightDocument,
                      onSelect: () => setCaseDeleteOpen(true),
                    },
                  ]}
                />
              </div>
            </div>
          )}

          {deleteError && (
            <div className="toast show mixed" role="alert">
              ⚠ {deleteError}
            </div>
          )}

          <h1>Upload supporting documents</h1>

          <p className="sub">
            Accepted files are logged and assigned a document ID for the
            investigation record.
          </p>

          <DropZone onFiles={handleFiles} disabled={isSubmitting} />

          {selectedFiles.length > 0 && (
            <>
              <div className="section-label">
                <span>Selected files</span>
                <span>{selectedFiles.length} selected</span>
              </div>

              <div className="file-list">
                {selectedFiles.map((file, index) => {
                  const rejection = rejections.find(
                    (item) => item.file === file,
                  );

                  return (
                    <FileRow
                      key={`${file.name}-${index}`}
                      file={file}
                      result={resultByFilename.get(file.name)}
                      rejected={
                        rejection
                          ? describeIssue(rejection.issue)
                          : undefined
                      }
                      isUploading={isSubmitting}
                      onRemove={() => removeFile(index)}
                    />
                  );
                })}
              </div>

              <div className="action-bar">
                <span className="count">
                  {hasOutcome
                    ? `${completedCount} / ${selectedFiles.length} processed`
                    : `${selectedFiles.length} file${
                        selectedFiles.length > 1 ? 's' : ''
                      } selected`}
                </span>

                <button
                  type="button"
                  className="upload-btn"
                  disabled={isSubmitting || selectedFiles.length === 0}
                  onClick={() => {
                    void handleSubmit();
                  }}
                >
                  {isSubmitting
                    ? 'Uploading…'
                    : hasOutcome
                      ? 'Upload again'
                      : `Upload ${selectedFiles.length} file${
                          selectedFiles.length > 1 ? 's' : ''
                        }`}
                </button>
              </div>
            </>
          )}

          {selectedFiles.length === 0 && !isSubmitting && (
            <div className="empty">
              No files selected yet — drag files above or click browse
            </div>
          )}

          {countError && (
            <div className="toast show mixed">⚠ {countError}</div>
          )}

          {error && (
            <div className="toast show mixed">⚠ {error}</div>
          )}

          {justSubmitted && hasOutcome && !error && !isSubmitting && (
            <div
              className={`toast show ${
                failedCount > 0 ? 'mixed' : 'all-ok'
              }`}
            >
              <span>{failedCount > 0 ? '⚠' : '✓'}</span>

              <span>
                <b>
                  {successfulCount} of {selectedFiles.length} documents
                  uploaded.
                </b>{' '}
                {failedCount > 0
                  ? `${failedCount} need attention above.`
                  : 'All documents uploaded successfully.'}
              </span>
            </div>
          )}

            {documents.length > 0 && (
              <>
                {/* EXTRACTED DOCUMENTS */}
                {extractedDocuments.length > 0 && (
                  <section className="documents-card">
                    <div className="documents-card-header">
                      <div className="documents-card-title">
                        <span className="documents-card-icon">▣</span>
                        <span>Extracted Files</span>
                      </div>

                      <div className="documents-card-actions">
                        <div className="bulk-icon-toolbar">
                          {selectedDocumentIds.size > 0 && (
                            <button
                              type="button"
                              className="bulk-icon-clear"
                              title="Clear selection"
                              onClick={clearDocumentSelection}
                            >
                              Clear ({selectedDocumentIds.size})
                            </button>
                          )}

                          <button
                            type="button"
                            className="bulk-icon-btn"
                            title="Select all eligible for approval"
                            aria-label="Select all eligible for approval"
                            disabled={eligibleForApprovalCount === 0}
                            onClick={handleSelectAllEligible}
                          >
                            <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M9 11l3 3L22 4" />
                              <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
                            </svg>
                            <span className="bulk-icon-count">{eligibleForApprovalCount}</span>
                          </button>

                          <button
                            type="button"
                            className="bulk-icon-btn bulk-icon-btn--approve"
                            title="Approve selected"
                            aria-label="Approve selected"
                            disabled={isApprovingSelected || selectedApprovableCount === 0}
                            onClick={() => void handleApproveSelected()}
                          >
                            <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2.3" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M4 12l5 5L20 6" />
                            </svg>
                            <span className="bulk-icon-count">{selectedApprovableCount}</span>
                          </button>

                          <button
                            type="button"
                            className="bulk-icon-btn"
                            title="Move selected to another case"
                            aria-label="Move selected to another case"
                            disabled={selectedMovableOrDeletableCount === 0}
                            onClick={() => void openMoveDialog()}
                          >
                            <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M5 12h14" />
                              <path d="M13 6l6 6-6 6" />
                            </svg>
                            <span className="bulk-icon-count">{selectedMovableOrDeletableCount}</span>
                          </button>

                          <button
                            type="button"
                            className="bulk-icon-btn bulk-icon-btn--danger"
                            title="Delete selected"
                            aria-label="Delete selected"
                            disabled={selectedMovableOrDeletableCount === 0}
                            onClick={() => setDocumentsToDelete(
                              selectedExtractedDocuments.filter((document) =>
                                TERMINAL_DOCUMENT_STATUSES.has(document.status),
                              ),
                            )}
                          >
                            <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M4 7h16" />
                              <path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
                              <path d="M7 7l1 12a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1l1-12" />
                              <path d="M10 11v6" />
                              <path d="M14 11v6" />
                            </svg>
                            <span className="bulk-icon-count">{selectedMovableOrDeletableCount}</span>
                          </button>
                        </div>
                        <span
                          className={`mock-badge${wsConnected ? ' live' : ''}`}
                          title={
                            wsConnected
                              ? 'Connected — updates push in real time'
                              : 'Disconnected — reconnecting…'
                          }
                        >
                          {wsConnected ? 'LIVE' : 'OFFLINE'}
                        </span>
                      </div>
                    </div>

                    {approveError && (
                      <div className="toast show mixed">⚠ {approveError}</div>
                    )}
                    {cancelActionError && (
                      <div className="toast show mixed">⚠ {cancelActionError}</div>
                    )}
                    {retryActionError && (
                      <div className="toast show mixed">⚠ {retryActionError}</div>
                    )}

                    <div className="documents-table documents-table--selectable">
                      <div className="documents-table-header">
                        <label className="document-select-cell">
                          <input
                            type="checkbox"
                            aria-label="Select all extracted files"
                            checked={allExtractedSelected}
                            onChange={() =>
                              setSelectedDocumentIds(
                                allExtractedSelected
                                  ? new Set()
                                  : new Set(extractedDocuments.map((document) => document.id)),
                              )
                            }
                          />
                        </label>
                        <span>Filename</span>
                        <span>Document Type</span>
                        <span>Status</span>
                        <span>Routing Outcome</span>
                      </div>

                      {extractedDocuments.map((document) => {
                        const status = document.status;
                        const isTerminal = TERMINAL_DOCUMENT_STATUSES.has(status);
                        const isApproving = approvingDocumentId === document.id;
                        const isStuck = isDocumentStuckProcessing(document);
                        const isCancellingRow = cancellingDocumentId === document.id;
                        const isRetryingRow = retryingDocumentId === document.id;
                        const caseMatch = caseMatches.get(document.id);
                        const routingOutcome = formatRoutingOutcome(document, caseMatch);
                        const manualReviewTitle =
                          caseMatch?.status === 'manual_review'
                            ? caseMatch.reason
                            : 'Requires manual review';

                        const isSelected = selectedDocumentIds.has(document.id);

                        return (
                          <div
                            key={document.id}
                            className="documents-table-row documents-table-row--clickable"
                            role="button"
                            tabIndex={0}
                            aria-label={`View ${document.filename}`}
                            onClick={() => onOpenDocument(document.id)}
                            onKeyDown={(event) => {
                              if (event.key === 'Enter' || event.key === ' ') {
                                event.preventDefault();
                                onOpenDocument(document.id);
                              }
                            }}
                          >
                            <label
                              className="document-select-cell"
                              // A <label> wrapping the checkbox makes the whole padded
                              // cell a click target, not just the tiny checkbox square
                              // (fat-finger protection) — native behavior, no extra JS
                              // needed beyond stopping it from also opening the row.
                              onClick={(event) => event.stopPropagation()}
                              onKeyDown={(event) => event.stopPropagation()}
                            >
                              <input
                                type="checkbox"
                                checked={isSelected}
                                aria-label={`Select ${document.filename}`}
                                onChange={() => toggleDocumentSelected(document.id)}
                              />
                            </label>

                            <span className="document-filename">
                              {routingOutcome === 'Manual Review' && (
                                <span
                                  className="document-manual-review-icon"
                                  title={manualReviewTitle}
                                  aria-label={manualReviewTitle}
                                >
                                  ⚠
                                </span>
                              )}

                              {document.filename}
                            </span>

                            <span className="document-type-cell">
                              {formatDocumentType(document.document_type, document.classification_confidence)}
                            </span>

                            <span className="document-status-cell">
                              <span
                                className={`status-dot ${
                                  status === 'analyzed'
                                    ? 'status-dot--success'
                                    : status === 'failed'
                                      ? 'status-dot--failed'
                                      : status === 'cancelled'
                                        ? 'status-dot--cancelled'
                                        : isStuck
                                          ? 'status-dot--stuck'
                                          : ''
                                }`}
                              />

                              <span>
                                {formatDocumentStatus(status)}
                                {!isTerminal && '…'}
                              </span>

                              {isStuck && (
                                <span className="document-status-stuck-badge">Stuck</span>
                              )}

                              {canRetryDocument(document) && (
                                <button
                                  type="button"
                                  className="document-row-action-icon document-row-action-icon--retry"
                                  aria-label={
                                    isRetryingRow
                                      ? `Retrying ${document.filename}`
                                      : `Retry ${document.filename}`
                                  }
                                  title={isRetryingRow ? 'Retrying…' : 'Retry'}
                                  disabled={isRetryingRow}
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    void handleRetryDocument(document.id);
                                  }}
                                  onKeyDown={(event) => event.stopPropagation()}
                                >
                                  <svg
                                    viewBox="0 0 24 24"
                                    aria-hidden="true"
                                    fill="none"
                                    stroke="currentColor"
                                    strokeWidth="2"
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                  >
                                    <path d="M20 11A8 8 0 1 0 18.6 16" />
                                    <path d="M20 5v6h-6" />
                                  </svg>
                                </button>
                              )}

                              {canCancelDocument(status) && (
                                <button
                                  type="button"
                                  className="document-row-action-icon document-row-action-icon--cancel"
                                  aria-label={
                                    isCancellingRow
                                      ? `Cancelling ${document.filename}`
                                      : `Cancel ${document.filename}`
                                  }
                                  title={isCancellingRow ? 'Cancelling…' : 'Cancel'}
                                  disabled={isCancellingRow}
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    void handleCancelDocument(document.id);
                                  }}
                                  onKeyDown={(event) => event.stopPropagation()}
                                >
                                  <svg
                                    viewBox="0 0 24 24"
                                    aria-hidden="true"
                                    fill="none"
                                    stroke="currentColor"
                                    strokeWidth="2"
                                    strokeLinecap="round"
                                  >
                                    <path d="M6 6l12 12" />
                                    <path d="M18 6L6 18" />
                                  </svg>
                                </button>
                              )}
                            </span>

                            <span className="document-routing">
                              {routingOutcome}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  </section>
                )}

                {/* APPROVED DOCUMENTS */}
                {approvedDocuments.length > 0 && (
                  <section className="documents-card approved-documents-card">
                    <div className="documents-card-header">
                      <div className="documents-card-title">
                        <span className="documents-card-icon">✓</span>
                        <span>Approved Documents</span>
                      </div>
                      <span
                        className={`mock-badge${wsConnected ? ' live' : ''}`}
                        title={
                          wsConnected
                            ? 'Connected — updates push in real time'
                            : 'Disconnected — reconnecting…'
                        }
                      >
                        {wsConnected ? 'LIVE' : 'OFFLINE'}
                      </span>
                    </div>

                    <div className="documents-table documents-table--selectable">
                      <div className="documents-table-header">
                        {/* Empty spacer matching the Extracted Files table's checkbox
                            column, so both tables' columns line up — see .documents-table--selectable. */}
                        <span className="document-select-cell" />
                        <span>Filename</span>
                        <span>Document Type</span>
                        <span>Status</span>
                        <span>Routing Outcome</span>
                      </div>

                      {approvedDocuments.map((document) => {
                        const hasDetectionReport = document.has_detection_report ?? false;
                        const detectionStage = documentDetectionStage(document.status, hasDetectionReport);

                        return (
                          <div
                            key={document.id}
                            className="documents-table-row documents-table-row--clickable"
                            role="button"
                            tabIndex={0}
                            aria-label={`View ${document.filename}`}
                            onClick={() => onOpenDocument(document.id)}
                            onKeyDown={(event) => {
                              if (event.key === 'Enter' || event.key === ' ') {
                                event.preventDefault();
                                onOpenDocument(document.id);
                              }
                            }}
                          >
                            <span className="document-select-cell" />

                            <span className="document-filename">
                              {document.filename}
                            </span>

                            <span className="document-type-cell">
                              {formatDocumentType(document.document_type, document.classification_confidence)}
                            </span>

                            <span className="document-status-cell">
                              <span
                                className={`status-dot ${
                                  detectionStage === 'detected' ? 'status-dot--success' : ''
                                }`}
                              />
                              <span>
                                {formatDocumentStatusLabel(document.status, hasDetectionReport)}
                                {isDocumentStatusInProgress(document.status, hasDetectionReport) && '…'}
                              </span>
                            </span>

                            <span className="document-routing">
                              Approved
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  </section>
                )}
              </>
            )}



          <section className="summary-card">
            <div className="summary-card-header">
              <div className="summary-card-title">
                <span className="summary-icon summary-icon--warning">
                  △
                </span>
                <h2>Flagged Items for Manual Review</h2>
              </div>

              {flaggedDocuments.length > 0 && (
                <span className="summary-count">
                  {flaggedDocuments.length}{' '}
                  {flaggedDocuments.length === 1 ? 'item' : 'items'}
                </span>
              )}
            </div>

            {flaggedDocuments.length === 0 ? (
              <div className="summary-message summary-message--warning">
                <span className="summary-message-icon">ⓘ</span>

                <div>
                  <strong>No flagged items at the moment.</strong>

                  <p>
                    If any documents or extracted fields require manual
                    review, they will be displayed here.
                  </p>
                </div>
              </div>
            ) : (
              <div className="flagged-list">
                {flaggedDocuments.map((document) => {
                  const lowestConfidence = lowestFieldConfidence(document.extracted_fields);
                  const lowExtractionConfidence =
                    lowestConfidence != null && lowestConfidence < EXTRACTION_CONFIDENCE_THRESHOLD;
                  const caseMatch = caseMatches.get(document.id);

                  // Classification problems first — a case-match reason is
                  // only meaningful once the document type is right.
                  const reason =
                    document.routing_status !== 'manual_review' &&
                    caseMatch &&
                    caseMatchNeedsReview(document)
                      ? `${caseMatch.reason}. Please verify the document belongs to this case before approving.`
                      : document.routing_status === 'manual_review'
                      ? document.document_type === 'unrecognised'
                        ? 'Document type could not be confidently recognised. Please verify the document type before approving.'
                        : document.classification_confidence != null &&
                            document.classification_confidence < CLASSIFICATION_CONFIDENCE_THRESHOLD
                          ? `Classification confidence (${document.classification_confidence.toFixed(
                              2,
                            )}) is below the required threshold of ${CLASSIFICATION_CONFIDENCE_THRESHOLD.toFixed(
                              2,
                            )}. Please verify the document type before approving.`
                          : document.escalation_reason ??
                            'This document was routed to manual review during classification. Please verify the document before approving.'
                      : lowExtractionConfidence
                        ? 'One or more extracted fields have low confidence and require review.'
                        : 'This document requires manual review.';
                  return (
                    <div
                      key={document.id}
                      className="flagged-item"
                    >
                      <div className="flagged-item-main">
                        <strong>{document.filename}</strong>

                        <div className="flagged-item-meta">
                          <span>
                            {formatDocumentType(document.document_type, document.classification_confidence)}
                          </span>

                          {lowestConfidence != null && (
                            <span>
                              Lowest field confidence:{' '}
                              {lowestConfidence.toFixed(3)}
                            </span>
                          )}

                          {caseMatch?.case_mismatch && caseMatchNeedsReview(document) && (
                            <span className="flagged-status">
                              Case mismatch
                            </span>
                          )}
                        </div>

                        <p>{reason}</p>
                      </div>

                      <button
                        type="button"
                        className="flagged-view-button"
                        onClick={() =>
                          onOpenDocument(document.id)
                        }
                      >
                        View
                      </button>
                    </div>
                  );
                })}
              </div>
            )}
          </section>

          <section className="summary-card">
            <div className="summary-card-header">
              <div className="summary-card-title">
                <span className="summary-icon summary-icon--risk">
                  ▥
                </span>
                <h2>Customer Risk Summary</h2>
              </div>
            </div>

            <div className="summary-message summary-message--info">
              <span className="summary-message-icon">ⓘ</span>

              <div>
                <strong>Risk summary not available yet.</strong>

                <p>
                  Risk analysis results will appear here once document
                  extraction and fraud analysis are completed.
                </p>
              </div>
            </div>
          </section>

          <section className="summary-card">
            <div className="summary-card-header">
              <div className="summary-card-title">
                <span className="summary-icon summary-icon--audit">
                  ◴
                </span>
                <h2>Case Audit Trail</h2>
              </div>
            </div>

            <div className="summary-message summary-message--info">
              <span className="summary-message-icon">ⓘ</span>

              <div>
                <strong>No audit events available yet.</strong>

                <p>
                  Case activity history will appear here once actions
                  are recorded.
                </p>
              </div>
            </div>
          </section>
        </section>

        <aside className="upload-sidebar">
          <div className="section-label">
            <span>Required documents</span>

            <span>
              {
                REQUIRED_DOCUMENT_TYPES.filter((requirement) =>
                  fulfilledRequirements.has(requirement.value),
                ).length
              }{' '}
              / {REQUIRED_DOCUMENT_TYPES.length}
            </span>
          </div>

          <p className="requirements-note">Present in every case</p>

          <ul className="requirements-list">
            {REQUIRED_DOCUMENT_TYPES.map((requirement) => {
              const isFulfilled =
                fulfilledRequirements.has(requirement.value);

              // proof_of_address is the one requirement that isn't just
              // yes/no — it needs an address on file per identified
              // individual (one per ID document), not just once anywhere
              // in the case. Shown as a fraction once there's at least one
              // identity to count; before that there's nothing to divide.
              const isProofOfAddress =
                requirement.value === 'proof_of_address';

              const showProgress =
                isProofOfAddress &&
                proofOfAddressCoverage.total > 0;

              const unmatchedNames = isProofOfAddress
                ? proofOfAddressCoverage.unmatched_names
                : [];

              // id_document needs both a front and a back capture (see
              // IdDocumentCoverage/compute_id_document_side_coverage) — an
              // NRIC's back alone has no name or photo, so without this a
              // back-only upload just silently sat unfulfilled with no
              // indication why. Shown as a fraction once at least one side
              // has been seen; the specific missing side is called out
              // separately so it reads as "waiting on the front", not just
              // "incomplete".
              const isIdDocument = requirement.value === 'id_document';

              const showIdProgress =
                isIdDocument && idDocumentCoverage.total > 0;

              // missing_side is only populated when there's exactly one
              // identified individual in the case (see
              // mapper.compute_id_document_missing_side) — with more than
              // one identity, a single hint can't describe each person's
              // own gap, so it falls back to just the fraction above.
              const idDocumentHint =
                isIdDocument && idDocumentCoverage.missing_side
                  ? idDocumentCoverage.missing_side === 'front'
                    ? 'Back photo present, front missing.'
                    : 'Front photo present, back missing.'
                  : null;

              return (
                <li key={requirement.value}>
                  <div
                    className={`requirement-row${
                      isFulfilled ? ' fulfilled' : ''
                    }`}
                  >
                    <span className="requirement-dot" />

                    {requirement.label}

                    {showProgress && (
                      <span className="requirement-progress">
                        {' '}
                        ({proofOfAddressCoverage.covered}/
                        {proofOfAddressCoverage.total})
                      </span>
                    )}

                    {showIdProgress && (
                      <span className="requirement-progress">
                        {' '}
                        ({idDocumentCoverage.covered}/
                        {idDocumentCoverage.total})
                      </span>
                    )}
                  </div>

                  {unmatchedNames.length > 0 && (
                    <div className="requirement-unmatched">
                      Address on file but no matching ID uploaded:{' '}
                      {unmatchedNames.join(', ')}
                    </div>
                  )}

                  {isIdDocument && identityNames.length > 0 && (
                    <ul className="requirement-identities">
                      {identityNames.map((name) => (
                        <li key={name}>{name}</li>
                      ))}
                    </ul>
                  )}

                  {idDocumentHint && (
                    <div className="requirement-unmatched">{idDocumentHint}</div>
                  )}
                </li>
              );
            })}
          </ul>

          <div className="section-label">
            <span>Additional documents</span>
          </div>

          <p className="requirements-note">May be required depending on the case</p>

          <ul className="requirements-list">
            {ADDITIONAL_DOCUMENT_TYPES.map((requirement) => (
              <li key={requirement.value}>
                <div
                  className={`requirement-row${
                    fulfilledRequirements.has(requirement.value)
                      ? ' fulfilled'
                      : ''
                  }`}
                >
                  <span className="requirement-dot" />

                  {requirement.label}
                </div>

              </li>
            ))}
          </ul>
        </aside>
      </div>

      {caseDeleteOpen && (
        <ConfirmDialog
          title="Delete this case?"
          message={`Case ${selectedCase.caseNumber || selectedCase.id} (${selectedCase.applicant}) and its documents will be removed and cannot be recovered.`}
          isConfirming={isDeletingCase}
          onCancel={() => {
            if (!isDeletingCase) setCaseDeleteOpen(false);
          }}
          onConfirm={() => void confirmDeleteCase()}
        />
      )}

      {documentsToDelete.length > 0 && (
        <ConfirmDialog
          title={documentsToDelete.length === 1 ? 'Delete this document?' : `Delete ${documentsToDelete.length} documents?`}
          message={
            documentsToDelete.length === 1
              ? `${documentsToDelete[0].filename} will be removed from this case and cannot be recovered.`
              : `${documentsToDelete.length} documents will be removed from this case and cannot be recovered.`
          }
          isConfirming={isBulkDeleting}
          onCancel={() => {
            if (!isBulkDeleting) setDocumentsToDelete([]);
          }}
          onConfirm={() => void confirmDeleteDocuments()}
        />
      )}

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
              <p className="save-error">{moveError}</p>
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
                {moveError && <p className="save-error">{moveError}</p>}
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
    </main>
  );
}