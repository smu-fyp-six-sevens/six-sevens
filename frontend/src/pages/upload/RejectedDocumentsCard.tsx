import type { DocumentRecord } from '../../services/uploadService';
import { formatDocumentType } from '../../services/uploadService';
import { LiveBadge } from './LiveBadge';

// Documents the officer rejected: locked and no longer counted towards the case, kept here for the record.
export function RejectedDocumentsCard({
  rejectedDocuments,
  wsConnected,
  onOpenDocument,
}: {
  rejectedDocuments: DocumentRecord[];
  wsConnected: boolean;
  onOpenDocument: (documentId: string) => void;
}) {
  return (
    <section className="documents-card rejected-documents-card">
      <div className="documents-card-header">
        <div className="documents-card-title">
          <span className="documents-card-icon">✕</span>
          <span>Rejected Documents</span>
        </div>
        <LiveBadge connected={wsConnected} />
      </div>

      <div className="documents-table documents-table--selectable">
        <div className="documents-table-header">
          {/* Empty spacer matching the Uploaded Files table's checkbox column, so the tables' columns line up. */}
          <span className="document-select-cell" />
          <span>Filename</span>
          <span>Document Type</span>
          <span>Status</span>
          <span>Routing Outcome</span>
        </div>

        {rejectedDocuments.map((document) => (
          <div
            key={document.id}
            className="documents-table-row documents-table-row--clickable"
            role="button"
            tabIndex={0}
            aria-label={`View ${document.filename}`}
            onClick={() => onOpenDocument(document.id)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                onOpenDocument(document.id);
              }
            }}
          >
            <span className="document-select-cell" />
            <span className="document-filename">{document.filename}</span>
            <span className="document-type-cell">
              {formatDocumentType(document.document_type, document.classification_confidence)}
            </span>
            <span className="document-status-cell">
              <span className="status-dot status-dot--rejected" />
              <span>Rejected</span>
            </span>
            <span className="document-routing">Rejected by officer</span>
          </div>
        ))}
      </div>
    </section>
  );
}
