import { useState } from 'react';
import { downloadCaseReport, REPORT_FAILED, RequestError } from '../../services/uploadService';

// Saves the case report PDF (the case menu's "Download report"). Works before the case analysis has run: the report
// then says so in a banner.
export function useCaseReportDownload(
  caseId: string,
  caseNumber: string | null | undefined,
  onError: (message: string | null) => void,
) {
  const [isDownloading, setIsDownloading] = useState(false);

  const download = async () => {
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
      // A server error already reads plainly (RequestError); anything else (the connection dropping while the PDF
      // arrives, the browser refusing the download) is logged and shown as one plain sentence.
      if (error instanceof RequestError) {
        onError(error.message);
      } else {
        console.error('Case report download failed:', error);
        onError(error instanceof Error && error.message === REPORT_FAILED ? error.message : REPORT_FAILED);
      }
    } finally {
      setIsDownloading(false);
    }
  };

  return { isDownloading, download };
}
