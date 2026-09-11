import { useEffect, useState } from 'react';
import CaseQueuePage from './pages/CaseQueuePage';
import UploadPage from './pages/UploadPage';

export interface CaseRecord {
  id: string;
  caseNumber?: string | null;
  applicant: string;
  type: string;
  status: string;
  caseStatus: string;
}

const API_BASE_URL =
  import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

function mapStatus(status: string): string {
  switch (status) {
    case 'no_documents':
      return 'Documents pending';
    case 'stuck':
      return 'Processing stuck';
    case 'failed':
      return 'Documents failed';
    case 'in_progress':
      return 'In progress';
    case 'analyzed':
      return 'Ready for review';
    default:
      return status || 'Open';
  }
}

export default function App() {
  const [cases, setCases] = useState<CaseRecord[]>([]);
  const [selectedCase, setSelectedCase] = useState<CaseRecord | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function loadCases(signal?: AbortSignal) {
    setIsLoading(true);
    setError(null);

    try {
      const response = await fetch(`${API_BASE_URL}/cases`, { signal });

      if (!response.ok) {
        throw new Error(`Failed to load cases (${response.status}).`);
      }

      const data = (await response.json()) as Array<{
        id: string;
        greenlink_case_id?: string | null;
        company_name?: string | null;
        status?: string | null;
        documents_status?: string | null;
      }>;
      
      setCases(
        data.map((item) => ({
          id: item.id,
          caseNumber: item.greenlink_case_id ?? null,
          applicant: item.company_name ?? 'Unnamed company',
          type: 'Corporate Loan',
          status: mapStatus(
            item.documents_status ?? item.status ?? 'open'
          ),
          caseStatus: item.status ?? 'open',
        }))
      );
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        return;
      }

      setError(
        err instanceof Error
          ? err.message
          : 'Failed to load cases.'
      );
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    const controller = new AbortController();

    void loadCases(controller.signal);

    return () => controller.abort();
  }, []);

  if (selectedCase) {
    return (
      <UploadPage
        selectedCase={selectedCase}
        onBack={() => setSelectedCase(null)}
      />
    );
  }

  return (
    <CaseQueuePage
      cases={cases}
      isLoading={isLoading}
      error={error}
      onRetry={() => void loadCases()}
      onOpenCase={setSelectedCase}
    />
  );
}