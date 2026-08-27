export type FileIssue =
  | { type: 'empty' }
  | { type: 'invalid-signature' }
  | { type: 'invalid-filename' }
  | { type: 'duplicate' };

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

const dedupStoreKey = (caseId: string) => `uploaded-file-hashes:${caseId}`;

// Per-browser only (localStorage) — can't detect the same file uploaded from
// a different device/browser. A server-side content-hash check would be the
// real fix; out of scope here since no such backend endpoint exists yet.
export function getUploadedHashes(caseId: string): Set<string> {
  try {
    const raw = localStorage.getItem(dedupStoreKey(caseId));
    return new Set(raw ? (JSON.parse(raw) as string[]) : []);
  } catch {
    return new Set();
  }
}

export function recordUploadedHash(caseId: string, hash: string): void {
  try {
    const hashes = getUploadedHashes(caseId);
    hashes.add(hash);
    localStorage.setItem(dedupStoreKey(caseId), JSON.stringify([...hashes]));
  } catch {
    // best-effort only — localStorage may be unavailable (private mode, quota)
  }
}

export async function validateFiles(files: File[], caseId: string): Promise<ValidationResult> {
  const ok: ValidatedFile[] = [];
  const rejected: { file: File; issue: FileIssue }[] = [];

  const previouslyUploaded = getUploadedHashes(caseId);
  const seenInThisBatch = new Set<string>();

  for (const file of files) {
    if (file.size === 0) {
      rejected.push({ file, issue: { type: 'empty' } });
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
