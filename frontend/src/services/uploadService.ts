// Mirrors BulkUploadItemResult / BulkUploadResponse in backend/app/models.py
export interface BulkUploadItemResult {
  filename: string;
  success: boolean;
  document_id?: string | null;
  status?: string | null;
  error?: string | null;
}

export interface BulkUploadResponse {
  results: BulkUploadItemResult[];
}

const apiBaseUrl = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

export async function uploadDocuments(caseId: string, files: File[]): Promise<BulkUploadResponse> {
  const formData = new FormData();
  formData.append('case_id', caseId);
  for (const file of files) {
    formData.append('files', file);
  }

  const response = await fetch(`${apiBaseUrl}/documents/upload-batch`, {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Upload failed (${response.status}): ${detail}`);
  }

  return (await response.json()) as BulkUploadResponse;
}
