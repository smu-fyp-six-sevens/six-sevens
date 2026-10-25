import type { DocumentCaseMatch, DocumentRecord } from '../../services/uploadService';
import { ApprovedDocumentsCard } from './ApprovedDocumentsCard';
import { ExtractedDocumentsCard } from './ExtractedDocumentsCard';
import type { DocumentRowActions, DocumentSelection } from './ExtractedDocumentsCard';

export function DocumentListPanel({
  documents,
  extractedDocuments,
  approvedDocuments,
  caseMatches,
  wsConnected,
  onOpenDocument,
  selection,
  rowActions,
}: {
  documents: DocumentRecord[];
  extractedDocuments: DocumentRecord[];
  approvedDocuments: DocumentRecord[];
  caseMatches: Map<string, DocumentCaseMatch>;
  wsConnected: boolean;
  onOpenDocument: (documentId: string) => void;
  selection: DocumentSelection;
  rowActions: DocumentRowActions;
}) {
  if (documents.length === 0) return null;
  return (
    <>
      {extractedDocuments.length > 0 && (
        <ExtractedDocumentsCard
          extractedDocuments={extractedDocuments}
          caseMatches={caseMatches}
          wsConnected={wsConnected}
          onOpenDocument={onOpenDocument}
          selection={selection}
          rowActions={rowActions}
        />
      )}
      {approvedDocuments.length > 0 && (
        <ApprovedDocumentsCard approvedDocuments={approvedDocuments} wsConnected={wsConnected} onOpenDocument={onOpenDocument} />
      )}
    </>
  );
}
