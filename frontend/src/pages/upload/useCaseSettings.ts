import { useEffect, useState } from 'react';
import type { CaseRecord } from '../../App';
import { deleteCase, updateCase } from '../../services/uploadService';
import { validateCompanyName } from '../../utils/caseValidation';

interface CaseSettingsParams {
  selectedCase: CaseRecord;
  onCaseUpdated: (companyName: string) => void;
  onCaseDeleted: () => void;
}

// Company-name editing and case deletion. deleteError is shared with bulk document deletes.
export function useCaseSettings({ selectedCase, onCaseUpdated, onCaseDeleted }: CaseSettingsParams) {
  const [isEditingCase, setIsEditingCase] = useState(false);
  const [companyNameDraft, setCompanyNameDraft] = useState(selectedCase.applicant);
  const [isSavingCase, setIsSavingCase] = useState(false);
  const [saveCaseError, setSaveCaseError] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [isDeletingCase, setIsDeletingCase] = useState(false);
  const [caseDeleteOpen, setCaseDeleteOpen] = useState(false);

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

  return {
    isEditingCase,
    setIsEditingCase,
    companyNameDraft,
    setCompanyNameDraft,
    isSavingCase,
    saveCaseError,
    handleSaveCase,
    handleCancelEditCase,
    deleteError,
    setDeleteError,
    isDeletingCase,
    caseDeleteOpen,
    setCaseDeleteOpen,
    confirmDeleteCase,
  };
}
