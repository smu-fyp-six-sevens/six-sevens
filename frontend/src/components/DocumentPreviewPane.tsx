import DocumentPreviewBody from './DocumentPreviewBody';
import { useDocumentPreviewUrl } from './useDocumentPreviewUrl';

interface DocumentPreviewPaneProps {
  documentId: string;
  filename: string;
  onExpand: () => void;
  onCollapse: () => void;
}

// A window with its left panel marked: the usual show/hide-side-panel icon. Filled when the panel is shown.
export function SidePanelIcon({ shown }: { shown: boolean }) {
  return (
    <svg className="side-panel-icon" viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
      <rect x="1.75" y="2.25" width="12.5" height="11.5" rx="2" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <rect x="1.75" y="2.25" width="4.75" height="11.5" rx="2" fill={shown ? 'currentColor' : 'none'} opacity={shown ? 0.35 : 1} />
      <line x1="6.5" y1="2.5" x2="6.5" y2="13.5" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  );
}

// The document beside its extracted fields, so an officer can check each value against the page
// without opening and closing the preview. The filename is already the page's heading, so the
// toolbar only carries the controls. Full screen opens the modal (with Download).
export default function DocumentPreviewPane({ documentId, filename, onExpand, onCollapse }: DocumentPreviewPaneProps) {
  const { previewKind, previewUrl, error } = useDocumentPreviewUrl(documentId, filename);

  return (
    <section className="document-preview-pane" aria-label={`Preview of ${filename}`}>
      <div className="document-preview-toolbar">
        <button
          type="button"
          className="document-preview-tool"
          onClick={onCollapse}
          title="Hide document preview"
        >
          <SidePanelIcon shown />
          Hide
        </button>
        <div className="document-preview-toolbar-actions">
          {previewUrl && (
            <a className="document-preview-tool" href={previewUrl} target="_blank" rel="noopener noreferrer">
              Open in new tab
            </a>
          )}
          <button type="button" className="document-preview-tool" onClick={onExpand}>
            ⤢ Full screen
          </button>
        </div>
      </div>
      <DocumentPreviewBody filename={filename} previewKind={previewKind} previewUrl={previewUrl} error={error} />
    </section>
  );
}

// Shown in the document's header while the preview is hidden, to bring it back.
export function ShowDocumentButton({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" className="document-preview-tool document-preview-tool--outlined" onClick={onClick}>
      <SidePanelIcon shown={false} />
      Show document
    </button>
  );
}
