import { assertOk } from './request';
import { authFetch } from '../../lib/api';
import type {
  DocumentCaseMatch,
} from './types';

export async function getDocumentCaseMatch(documentId: string): Promise<DocumentCaseMatch> {
  const response = await authFetch(`/documents/${documentId}/detection/case-match`);

  await assertOk(response, 'Failed to fetch case match');

  return (await response.json()) as DocumentCaseMatch;
}
