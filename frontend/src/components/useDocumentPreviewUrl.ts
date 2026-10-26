import { useEffect, useState } from 'react';
import { getDocumentDownloadUrl, previewKindForFilename } from '../services/uploadService';

// The inline (render-in-browser) signed URL for a document, shared by the preview modal and the
// document page's side-by-side preview pane.
export function useDocumentPreviewUrl(documentId: string, filename: string) {
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const previewKind = previewKindForFilename(filename);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setError(null);
      setPreviewUrl(null);

      if (previewKind === 'unsupported') {
        return;
      }

      try {
        const result = await getDocumentDownloadUrl(documentId, { inline: true });
        if (!cancelled) {
          setPreviewUrl(result.signed_url);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load preview.');
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
    // previewKind is derived from filename, which doesn't change per document.
  }, [documentId, previewKind]);

  return { previewKind, previewUrl, error, setError };
}
