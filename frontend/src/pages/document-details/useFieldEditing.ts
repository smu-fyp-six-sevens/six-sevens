import { useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import { updateExtractedFields } from '../../services/uploadService';
import type { DocumentRecord } from '../../services/uploadService';
import { displayableFieldEntries, isPlainObject, valueToString } from './fieldDisplay';
import type { ExtractedField } from './fieldDisplay';

interface FieldEditingParams {
  document: DocumentRecord | null;
  setDocument: Dispatch<SetStateAction<DocumentRecord | null>>;
  fieldDraft: Record<string, string>;
  setFieldDraft: Dispatch<SetStateAction<Record<string, string>>>;
  canEditFields: boolean;
  extractedFields: [string, ExtractedField][];
  reviewFields: [string, ExtractedField][];
  absentRequiredFields: string[];
  needsAttention: (key: string, field: ExtractedField) => boolean;
}

// Manual review save path plus the add and delete field state for the extracted-information card.
export type FieldEditing = ReturnType<typeof useFieldEditing>;

export function useFieldEditing({
  document,
  setDocument,
  fieldDraft,
  setFieldDraft,
  canEditFields,
  extractedFields,
  reviewFields,
  absentRequiredFields,
  needsAttention,
}: FieldEditingParams) {
  const [isAddFieldOpen, setIsAddFieldOpen] = useState(false);
  const [newFieldName, setNewFieldName] = useState('');
  const [newFieldValue, setNewFieldValue] = useState('');
  const [isAddingField, setIsAddingField] = useState(false);
  const [addFieldError, setAddFieldError] = useState<string | null>(null);

  const [deletingFieldId, setDeletingFieldId] = useState<string | null>(null);
  const [deleteFieldError, setDeleteFieldError] = useState<string | null>(null);

  const [isSaving, setIsSaving] = useState(false);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  function clearSaveMessages() {
    setSaveMessage(null);
    setSaveError(null);
  }

  function handleFieldChange(key: string, value: string) {
    setFieldDraft((current) => ({
      ...current,
      [key]: value,
    }));

    clearSaveMessages();
  }

  async function handleSaveFields() {
    if (!document || !canEditFields) {
      return;
    }

    // Only fields that need attention must have a value.
    const hasEmptyField = reviewFields.some(
      ([key, field]) => needsAttention(key, field) && !fieldDraft[key]?.trim(),
    );

    if (hasEmptyField) {
      setSaveMessage(null);
      setSaveError('Please complete all fields before saving.');
      return;
    }

    setIsSaving(true);
    setSaveMessage(null);
    setSaveError(null);

    try {
      // fieldDraft is Record<string, string>.
      const originalValueByKey = new Map(
        extractedFields.map(([key, field]) => [key, field.value]),
      );
      const payload: Record<string, unknown> = {};
      const draftWithAbsent: Record<string, string> = {
        ...Object.fromEntries(absentRequiredFields.map((name) => [name, ''])),
        ...fieldDraft,
      };
      for (const [key, draftValue] of Object.entries(draftWithAbsent)) {
        if (draftValue.trim() === '') {
          payload[key] = null;
          continue;
        }
        const original = originalValueByKey.get(key);
        if (Array.isArray(original) || isPlainObject(original)) {
          try {
            payload[key] = JSON.parse(draftValue);
            continue;
          } catch {
            // Malformed edit.
          }
        }
        payload[key] = draftValue;
      }

      const updated = await updateExtractedFields(
        document.id,
        payload,
      );

      setDocument(updated);

      const nextDraft: Record<string, string> = {};

      for (const [key, field] of displayableFieldEntries(
        updated.extracted_fields,
      )) {
        nextDraft[key] = valueToString(field.value);
      }

      setFieldDraft(nextDraft);

      setSaveMessage(
        'Changes saved. The extracted fields are now verified and can no longer be edited.',
      );
    } catch (error) {
      setSaveError(
        error instanceof Error
          ? error.message
          : 'Failed to save extracted fields.',
      );
    } finally {
      setIsSaving(false);
    }
  }

  return {
    isAddFieldOpen,
    setIsAddFieldOpen,
    newFieldName,
    setNewFieldName,
    newFieldValue,
    setNewFieldValue,
    isAddingField,
    setIsAddingField,
    addFieldError,
    setAddFieldError,
    deletingFieldId,
    setDeletingFieldId,
    deleteFieldError,
    setDeleteFieldError,
    isSaving,
    saveMessage,
    saveError,
    handleFieldChange,
    handleSaveFields,
    clearSaveMessages,
  };
}
