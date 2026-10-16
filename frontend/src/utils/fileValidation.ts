export type FileIssue =
  | { type: 'empty' }
  | { type: 'too-large' }
  | { type: 'invalid-signature' }
  | { type: 'invalid-filename' }
  | { type: 'duplicate' };

// Mirrors settings.MAX_FILE_SIZE_BYTES / MAX_BULK_FILES in backend/app/config.py
export const MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024;
export const MAX_BULK_FILES = 20;

export interface ValidatedFile {
  uploadFile: File;
  originalName: string;
  hash: string;
}

export interface ValidationResult {
  ok: ValidatedFile[];
  rejected: { file: File; issue: FileIssue }[];
}

interface MagicSignature {
  contentType: string;
  bytes: number[];
}

// Mirrors settings.ALLOWED_CONTENT_TYPES in backend/app/config.py — the
// browser-supplied file.type is never trusted, only what the bytes say.
const MAGIC_SIGNATURES: MagicSignature[] = [
  { contentType: 'application/pdf', bytes: [0x25, 0x50, 0x44, 0x46] }, // %PDF
  { contentType: 'image/png', bytes: [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a] },
  { contentType: 'image/jpeg', bytes: [0xff, 0xd8, 0xff] },
];

const SIGNATURE_HEADER_LENGTH = Math.max(...MAGIC_SIGNATURES.map((sig) => sig.bytes.length));

async function sniffContentType(file: File): Promise<string | null> {
  const buffer = await file.slice(0, SIGNATURE_HEADER_LENGTH).arrayBuffer();
  const bytes = new Uint8Array(buffer);

  for (const signature of MAGIC_SIGNATURES) {
    if (signature.bytes.every((byte, index) => bytes[index] === byte)) {
      return signature.contentType;
    }
  }
  return null;
}

const MAX_FILENAME_LENGTH = 200;
const UNSAFE_FILENAME_CHARS = /[^a-zA-Z0-9. _()-]/g;

export function sanitizeFilename(name: string): string | null {
  // Strip any path segments a browser might supply, then collapse ".."
  // traversal sequences before touching anything else.
  const base = name.split(/[/\\]/).pop() ?? '';
  const noTraversal = base.replace(/\.\.+/g, '.');
  const cleaned = noTraversal.replace(UNSAFE_FILENAME_CHARS, '_').trim();

  if (!cleaned || cleaned === '.') {
    return null;
  }

  if (cleaned.length <= MAX_FILENAME_LENGTH) {
    return cleaned;
  }

  const dot = cleaned.lastIndexOf('.');
  const extension = dot > 0 ? cleaned.slice(dot) : '';
  const stem = dot > 0 ? cleaned.slice(0, dot) : cleaned;
  return stem.slice(0, MAX_FILENAME_LENGTH - extension.length) + extension;
}

export async function hashFile(file: File): Promise<string> {
  const buffer = await file.arrayBuffer();
  const digest = await crypto.subtle.digest('SHA-256', buffer);
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('');
}

// documentId -> content hash of each file uploaded to the case. A hash only
// blocks a re-upload while its document is still in the case's live snapshot,
// so deleting or moving the document out (from any browser) frees the file.
const dedupStoreKey = (caseId: string) => `uploaded-documents:${caseId}`;
// The old format kept bare hashes with no document, so a delete could never
// release them. Dropped rather than migrated.
const legacyDedupStoreKey = (caseId: string) => `uploaded-file-hashes:${caseId}`;

// Per-browser only (localStorage) — can't detect the same file uploaded from
// a different device/browser. A server-side content-hash check would be the
// real fix; out of scope here since no such backend endpoint exists yet.
function getUploadedDocuments(caseId: string): Record<string, string> {
  try {
    const raw = localStorage.getItem(dedupStoreKey(caseId));
    return raw ? (JSON.parse(raw) as Record<string, string>) : {};
  } catch {
    return {};
  }
}

function setUploadedDocuments(caseId: string, documents: Record<string, string>): void {
  try {
    localStorage.removeItem(legacyDedupStoreKey(caseId));
    if (Object.keys(documents).length === 0) {
      localStorage.removeItem(dedupStoreKey(caseId));
    } else {
      localStorage.setItem(dedupStoreKey(caseId), JSON.stringify(documents));
    }
  } catch {
    // best-effort only — localStorage may be unavailable (private mode, quota)
  }
}

// Hashes of uploaded files whose documents are still in the case, forgetting the
// rest. liveDocumentIds is null until the first case snapshot arrives; until then
// every recorded upload still counts.
function getLiveUploadedHashes(caseId: string, liveDocumentIds: Set<string> | null): Set<string> {
  const documents = getUploadedDocuments(caseId);
  if (liveDocumentIds === null) return new Set(Object.values(documents));

  const live = Object.fromEntries(
    Object.entries(documents).filter(([documentId]) => liveDocumentIds.has(documentId)),
  );
  if (Object.keys(live).length !== Object.keys(documents).length) {
    setUploadedDocuments(caseId, live);
  }
  return new Set(Object.values(live));
}

export function recordUploadedDocument(caseId: string, documentId: string, hash: string): void {
  setUploadedDocuments(caseId, { ...getUploadedDocuments(caseId), [documentId]: hash });
}

export function checkFileCount(files: File[]): string | null {
  if (files.length > MAX_BULK_FILES) {
    return `Too many files selected (${files.length}). Maximum is ${MAX_BULK_FILES} per upload.`;
  }
  return null;
}

export async function validateFiles(
  files: File[],
  caseId: string,
  liveDocumentIds: Set<string> | null,
): Promise<ValidationResult> {
  const ok: ValidatedFile[] = [];
  const rejected: { file: File; issue: FileIssue }[] = [];

  const previouslyUploaded = getLiveUploadedHashes(caseId, liveDocumentIds);
  const seenInThisBatch = new Set<string>();

  for (const file of files) {
    if (file.size === 0) {
      rejected.push({ file, issue: { type: 'empty' } });
      continue;
    }

    if (file.size > MAX_FILE_SIZE_BYTES) {
      rejected.push({ file, issue: { type: 'too-large' } });
      continue;
    }

    const sanitizedFilename = sanitizeFilename(file.name);
    if (!sanitizedFilename) {
      rejected.push({ file, issue: { type: 'invalid-filename' } });
      continue;
    }

    const detectedContentType = await sniffContentType(file);
    if (!detectedContentType) {
      rejected.push({ file, issue: { type: 'invalid-signature' } });
      continue;
    }

    const hash = await hashFile(file);
    if (previouslyUploaded.has(hash) || seenInThisBatch.has(hash)) {
      rejected.push({ file, issue: { type: 'duplicate' } });
      continue;
    }
    seenInThisBatch.add(hash);

    // A new File is required (not a mutation) — File.name/.type aren't
    // writable in place, and this is what actually makes the sanitized name
    // and sniffed (not claimed) content-type the ones sent to the backend.
    const uploadFile = new File([file], sanitizedFilename, { type: detectedContentType });
    ok.push({ uploadFile, originalName: file.name, hash });
  }

  return { ok, rejected };
}
