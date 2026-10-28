import { useEffect, useState } from 'react';
import { getContentGuard } from '../../services/uploadService';
import type { ContentGuardResponse } from '../../services/uploadService';

// What the content guard found at ingest. Re-read when the status changes, since a retry or rerun scans the file again.
export function useContentGuard(documentId: string, status: string | null | undefined) {
  const [contentGuard, setContentGuard] = useState<ContentGuardResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    getContentGuard(documentId)
      .then((result) => {
        if (!cancelled) setContentGuard(result);
      })
      .catch(() => {
        // The warning is extra context; the page works without it.
      });
    return () => {
      cancelled = true;
    };
  }, [documentId, status]);

  return contentGuard;
}
