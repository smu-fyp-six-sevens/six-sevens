import { authFetch } from '../lib/api';

// Mirrors ExtractionTestResponse / ExtractionTestPage in backend/app/models.py
export interface ExtractionTestPage {
  page_number: number;
  text: string;
  elements: Record<string, unknown>[];
}

export interface ExtractionTestResult {
  file_name: string;
  file_type: string;
  metadata: Record<string, unknown>;
  extraction_method: string;
  raw_text: string;
  pages: ExtractionTestPage[];
  needs_review: boolean;
  status: string;
}

export async function testExtraction(file: File): Promise<ExtractionTestResult> {
  const formData = new FormData();
  formData.append('file', file);

  const response = await authFetch('/extraction/test', {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Extraction failed (${response.status}): ${detail}`);
  }

  return (await response.json()) as ExtractionTestResult;
}
