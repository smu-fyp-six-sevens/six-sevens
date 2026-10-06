import { useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import { isConfirmedOther, updateDocumentType } from '../../services/uploadService';
import type { DocumentRecord } from '../../services/uploadService';
import { buildFieldDraft } from './fieldDisplay';

interface DocumentTypeEditingParams {
  document: DocumentRecord | null;
  setDocument: Dispatch<SetStateAction<DocumentRecord | null>>;
  setFieldDraft: Dispatch<SetStateAction<Record<string, string>>>;
  extractedFieldsJsonRef: { current: string };
  clearSaveMessages: () => void;
}

// Document type dropdown: the officer's pick, and saving it (which re-runs extraction).
export function useDocumentTypeEditing({
  document,
  setDocument,
  setFieldDraft,
  extractedFieldsJsonRef,
  clearSaveMessages,
}: DocumentTypeEditingParams) {
  const [selectedType, setSelectedType] = useState('');
  const [isSavingType, setIsSavingType] = useState(false);
  const [typeError, setTypeError] = useState<string | null>(null);

  // What the dropdown currently shows: the officer's pick, else the existing type.
  const isUnconfirmedUnrecognised =
    document?.document_type === 'unrecognised' && !isConfirmedOther(document);
  const chosenType =
    selectedType || (isUnconfirmedUnrecognised ? '' : document?.document_type) || '';

  async function handleSaveType() {
    if (!document || !chosenType) {
      return;
    }

    setIsSavingType(true);
    setTypeError(null);

    try {
      const updated = await updateDocumentType(document.id, chosenType);

      extractedFieldsJsonRef.current = JSON.stringify(
        updated.extracted_fields ?? null,
      );
      setDocument(updated);
      setFieldDraft(buildFieldDraft(updated.extracted_fields));
      setSelectedType('');
      clearSaveMessages();
    } catch (error) {
      setTypeError(
        error instanceof Error
          ? error.message
          : 'Failed to update the document type.',
      );
    } finally {
      setIsSavingType(false);
    }
  }

  return {
    selectedType,
    setSelectedType,
    isSavingType,
    typeError,
    setTypeError,
    chosenType,
    isUnconfirmedUnrecognised,
    handleSaveType,
  };
}
