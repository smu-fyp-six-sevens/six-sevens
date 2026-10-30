import { useState } from 'react';
import { downloadCaseReport } from '../../services/uploadService';

// Saves the case report PDF. Works before the case analysis has run: the report then says so in a banner.
export function DownloadReportButton({
  caseId,
  caseNumber,
  onError,
}: {
  caseId: string;
  caseNumber?: string | null;
  onError: (message: string | null) => void;
}) {
  const [isDownloading, setIsDownloading] = useState(false);

  const handleClick = async () => {
    setIsDownloading(true);
    onError(null);
    try {
      const { blob, filename } = await downloadCaseReport(caseId, caseNumber);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      // Revoked on the next tick: some browsers start the download only after click() returns.
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (error) {
      onError(error instanceof Error ? error.message : 'Failed to download the case report.');
    } finally {
      setIsDownloading(false);
    }
  };

  return (
    <button
      type="button"
      className="case-report-button"
      onClick={() => void handleClick()}
      disabled={isDownloading}
      title="Download a PDF report of this case"
    >
      <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 4v11" />
        <path d="M7 10l5 5 5-5" />
        <path d="M5 20h14" />
      </svg>
      {isDownloading ? 'Preparing…' : 'Download Report'}
    </button>
  );
}
