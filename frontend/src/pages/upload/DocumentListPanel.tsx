import type { AdditionalDocument, DocumentRecord } from '../../services/uploadService';
import { ExtractedDocumentsCard } from './ExtractedDocumentsCard';
import type { DocumentRowActions, DocumentSelection, GroupId } from './ExtractedDocumentsCard';
import type { Coverage } from './documentChecklist';

export function DocumentListPanel({
  extractedDocuments,
  reviewReasons,
  fulfilledRequirements,
  coverage,
  approvedDocuments,
  rejectedDocuments,
  wsConnected,
  onOpenDocument,
  selection,
  rowActions,
  readOnly = false,
  requestedDocuments,
  initialGroup,
  onOpenRequest,
}: {
  extractedDocuments: DocumentRecord[];
  reviewReasons?: Map<string, string>;
  fulfilledRequirements: Set<string>;
  coverage: Coverage;
  approvedDocuments: DocumentRecord[];
  rejectedDocuments: DocumentRecord[];
  wsConnected: boolean;
  onOpenDocument: (documentId: string) => void;
  selection: DocumentSelection;
  rowActions: DocumentRowActions;
  // A finalised case: documents can be opened, but nothing in the list can be changed.
  readOnly?: boolean;
  requestedDocuments?: AdditionalDocument[];
  initialGroup?: GroupId;
  onOpenRequest?: (key: string) => void;
}) {
  // Always shown: with nothing uploaded it is the checklist of what is missing.
  return (
    <ExtractedDocumentsCard
      extractedDocuments={extractedDocuments}
      reviewReasons={reviewReasons}
      fulfilledRequirements={fulfilledRequirements}
      coverage={coverage}
      approvedDocuments={approvedDocuments}
      rejectedDocuments={rejectedDocuments}
      wsConnected={wsConnected}
      onOpenDocument={onOpenDocument}
      selection={selection}
      rowActions={rowActions}
      readOnly={readOnly}
      requestedDocuments={requestedDocuments}
      initialGroup={initialGroup}
      onOpenRequest={onOpenRequest}
    />
  );
}
