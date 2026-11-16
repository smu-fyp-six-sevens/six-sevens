import type { DocumentRecord } from '../../services/uploadService';
import { ExtractedDocumentsCard } from './ExtractedDocumentsCard';
import type { DocumentRowActions, DocumentSelection } from './ExtractedDocumentsCard';

export function DocumentListPanel({
  extractedDocuments,
  reviewReasons,
  approvedDocuments,
  rejectedDocuments,
  wsConnected,
  onOpenDocument,
  selection,
  rowActions,
  readOnly = false,
}: {
  extractedDocuments: DocumentRecord[];
  reviewReasons?: Map<string, string>;
  approvedDocuments: DocumentRecord[];
  rejectedDocuments: DocumentRecord[];
  wsConnected: boolean;
  onOpenDocument: (documentId: string) => void;
  selection: DocumentSelection;
  rowActions: DocumentRowActions;
  // A finalised case: documents can be opened, but nothing in the list can be changed.
  readOnly?: boolean;
}) {
  return (
    <ExtractedDocumentsCard
      extractedDocuments={extractedDocuments}
      reviewReasons={reviewReasons}
      approvedDocuments={approvedDocuments}
      rejectedDocuments={rejectedDocuments}
      wsConnected={wsConnected}
      onOpenDocument={onOpenDocument}
      selection={selection}
      rowActions={rowActions}
      readOnly={readOnly}
    />
  );
}
