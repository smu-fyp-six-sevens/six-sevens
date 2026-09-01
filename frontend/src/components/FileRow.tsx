import type { BulkUploadItemResult } from '../services/uploadService';

interface FileRowProps {
  file: File;
  result?: BulkUploadItemResult;
  rejected?: string;
  isUploading: boolean;
  onRemove: () => void;
}

function extension(name: string) {
  const ext = name.split('.').pop()?.toUpperCase() ?? 'FILE';
  return ext === 'JPEG' ? 'JPG' : ext;
}

export default function FileRow({
  file,
  result,
  rejected,
  isUploading,
  onRemove,
}: FileRowProps) {
  const status = rejected
    ? 'Rejected'
    : result
      ? result.success
        ? 'Uploaded'
        : 'Failed'
      : isUploading
        ? 'Uploading'
        : 'Ready';

  const badgeClass = rejected || result?.success === false
    ? 'err'
    : result?.success
      ? 'ok'
      : isUploading
        ? 'pending'
        : 'pending';

  let meta = 'Ready to upload';
  if (rejected) meta = rejected;
  else if (result?.success) {
    meta = result.document_id
      ? `Document ID: ${result.document_id}`
      : `Uploaded · ${result.status ?? 'success'}`;
  } else if (result?.error) {
    meta = result.error;
  } else if (isUploading) {
    meta = 'Uploading…';
  }

  return (
    <div className="file-row">
      <div className="f-icon">{extension(file.name)}</div>

      <div className="f-main">
        <div className="f-name" title={file.name}>{file.name}</div>
        <div className={`f-meta${rejected || result?.success === false ? ' err' : ''}`}>
          {meta}
        </div>

        {isUploading && (
          <div className="progress-track" aria-label="Upload in progress">
            <div className="progress-fill" />
          </div>
        )}
      </div>

      <span className={`badge ${badgeClass}`}>{status}</span>

      {!isUploading && !result && !rejected && (
        <button
          type="button"
          className="remove-btn"
          title={`Remove ${file.name}`}
          onClick={onRemove}
        >
          ×
        </button>
      )}
    </div>
  );
}
