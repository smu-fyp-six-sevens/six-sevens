import type { DocumentRecord } from '../../services/uploadService';
import { documentStatusDisplay, formatDocumentType } from '../../services/uploadService';
import { LiveBadge } from './LiveBadge';

export function ApprovedDocumentsCard({
  approvedDocuments,
  wsConnected,
  onOpenDocument,
}: {
  approvedDocuments: DocumentRecord[];
  wsConnected: boolean;
  onOpenDocument: (documentId: string) => void;
}) {
  return (
    <section className="documents-card approved-documents-card">
      <div className="documents-card-header">
        <div className="documents-card-title">
          <span className="documents-card-icon">✓</span>
          <span>Approved Documents</span>
        </div>
        <LiveBadge connected={wsConnected} />
      </div>

      <div className="documents-table documents-table--selectable">
        <div className="documents-table-header">
          {/* Empty spacer matching the Uploaded Files table's checkbox
              column, so both tables' columns line up — see .documents-table--selectable. */}
          <span className="document-select-cell" />
          <span>Filename</span>
          <span>Document Type</span>
          <span>Status</span>
          <span>Routing Outcome</span>
        </div>

        {approvedDocuments.map((document) => (
          <ApprovedDocumentRow key={document.id} document={document} onOpenDocument={onOpenDocument} />
        ))}
      </div>
    </section>
  );
}

function ApprovedDocumentRow({
  document,
  onOpenDocument,
}: {
  document: DocumentRecord;
  onOpenDocument: (documentId: string) => void;
}) {
  const { stage, label, inProgress } = documentStatusDisplay(
    document.status,
    document.has_detection_report ?? false,
    document.document_type,
    document.has_detection_error ?? false,
  );

  return (
    <div
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
        <span className={`status-dot ${stage === 'detected' || stage === 'skipped' ? 'status-dot--success' : ''}`} />
        <span>
          {label}
          {inProgress && '…'}
        </span>
      </span>
      <span className="document-routing">Approved</span>
    </div>
  );
}
