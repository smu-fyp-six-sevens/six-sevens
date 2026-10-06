import { assertOk } from './request';
import { authFetch } from '../../lib/api';
import type {
  BulkDeleteResponse,
  BulkMoveResponse,
  BulkUploadResponse,
  DocumentDownloadResponse,
  DocumentRecord,
  PreviewKind,
} from './types';

export async function deleteDocument(documentId: string): Promise<void> {
  const response = await authFetch(`/documents/${documentId}`, { method: 'DELETE' });

  await assertOk(response, 'Failed to delete document');
}

export async function getDocument(documentId: string): Promise<DocumentRecord> {
  const response = await authFetch(`/documents/${documentId}`);

  await assertOk(response, 'Failed to fetch document');

  return (await response.json()) as DocumentRecord;
}

// inline=true asks the backend to omit Storage's download option.
export async function getDocumentDownloadUrl(
  documentId: string,
  { inline = false }: { inline?: boolean } = {},
): Promise<DocumentDownloadResponse> {
  const response = await authFetch(
    `/documents/${documentId}/download${inline ? '?inline=true' : ''}`,
  );

  await assertOk(response, 'Failed to get a document URL');

  return (await response.json()) as DocumentDownloadResponse;
}

export function previewKindForFilename(filename: string): PreviewKind {
  const lowered = filename.toLowerCase();
  if (lowered.endsWith('.pdf')) {
    return 'pdf';
  }
  if (/\.(png|jpe?g)$/.test(lowered)) {
    return 'image';
  }
  return 'unsupported';
}

export async function listDocuments(caseId: string): Promise<DocumentRecord[]> {
  const response = await authFetch(`/documents?case_id=${encodeURIComponent(caseId)}`);

  await assertOk(response, 'Failed to fetch documents');

  return (await response.json()) as DocumentRecord[];
}

// Mirrors UpdateExtractedFieldsRequest in backend/app/models.py.
export async function updateExtractedFields(
  documentId: string,
  extractedFields: Record<string, unknown>,
): Promise<DocumentRecord> {
  const response = await authFetch(`/documents/${documentId}/extracted-fields`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ extracted_fields: extractedFields }),
  });

  await assertOk(response, 'Failed to update extracted fields');

  return (await response.json()) as DocumentRecord;
}

export async function addExtractedField(
  documentId: string,
  fieldName: string,
  fieldValue: unknown,
): Promise<DocumentRecord> {
  const response = await authFetch(`/documents/${documentId}/extracted-fields`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      field_name: fieldName,
      field_value: fieldValue,
    }),
  });

  await assertOk(response, 'Failed to add extracted field');

  return (await response.json()) as DocumentRecord;
}

export async function deleteExtractedField(
  documentId: string,
  fieldId: string,
): Promise<DocumentRecord> {
  const response = await authFetch(
    `/documents/${documentId}/extracted-fields/${fieldId}`,
    {
      method: 'DELETE',
    },
  );

  await assertOk(response, 'Failed to delete extracted field');

  return (await response.json()) as DocumentRecord;
}

export async function approveDocument(
  documentId: string,
): Promise<DocumentRecord> {
  const response = await authFetch(`/documents/${documentId}/approve`, {
    method: 'POST',
  });

  await assertOk(response, 'Failed to approve document');

  return (await response.json()) as DocumentRecord;
}

export async function rejectDocument(
  documentId: string,
): Promise<DocumentRecord> {
  const response = await authFetch(
    `/documents/${documentId}/reject`,
    {
      method: 'POST',
    },
  );

  await assertOk(response, 'Reject failed');

  return (await response.json()) as DocumentRecord;
}

export async function cancelDocument(documentId: string): Promise<DocumentRecord> {
  const response = await authFetch(`/documents/${documentId}/cancel`, {
    method: 'POST',
  });

  await assertOk(response, 'Failed to cancel document');

  return (await response.json()) as DocumentRecord;
}

export async function retryDocument(documentId: string): Promise<DocumentRecord> {
  const response = await authFetch(`/documents/${documentId}/retry`, {
    method: 'POST',
  });

  await assertOk(response, 'Failed to retry document');

  return (await response.json()) as DocumentRecord;
}

// Mirrors UpdateDocumentTypeRequest in backend/app/models.py.
export async function updateDocumentType(
  documentId: string,
  documentType: string,
): Promise<DocumentRecord> {
  const response = await authFetch(`/documents/${documentId}/document-type`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ document_type: documentType }),
  });

  await assertOk(response, 'Failed to update document type');

  return (await response.json()) as DocumentRecord;
}

export async function moveDocument(documentId: string, caseId: string): Promise<DocumentRecord> {
  const response = await authFetch(`/documents/${documentId}/move`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ case_id: caseId }),
  });

  await assertOk(response, 'Failed to move document');

  return (await response.json()) as DocumentRecord;
}

// One request for the whole selection.
export async function bulkMoveDocuments(
  documentIds: string[],
  caseId: string,
): Promise<BulkMoveResponse> {
  const response = await authFetch('/documents/bulk-move', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ document_ids: documentIds, case_id: caseId }),
  });

  await assertOk(response, 'Failed to move documents');

  return (await response.json()) as BulkMoveResponse;
}

export async function bulkDeleteDocuments(documentIds: string[]): Promise<BulkDeleteResponse> {
  const response = await authFetch('/documents/bulk-delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ document_ids: documentIds }),
  });

  await assertOk(response, 'Failed to delete documents');

  return (await response.json()) as BulkDeleteResponse;
}

export async function uploadDocuments(
  caseId: string,
  files: File[],
): Promise<BulkUploadResponse> {
  const formData = new FormData();
  formData.append('case_id', caseId);

  for (const file of files) {
    formData.append('files', file);
  }

  const response = await authFetch('/documents/upload-batch', {
    method: 'POST',
    body: formData,
  });

  await assertOk(response, 'Upload failed');

  return (await response.json()) as BulkUploadResponse;
}
