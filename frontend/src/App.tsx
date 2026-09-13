import { useEffect, useState } from 'react';
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useNavigate,
  useParams,
} from 'react-router-dom';

import CaseQueuePage from './pages/CaseQueuePage';
import ExtractionTestPage from './pages/ExtractionTestPage';
import UploadPage from './pages/UploadPage';
import CreateCasePage from './pages/CreateCasePage';
import DocumentDetailsPage from './pages/DocumentDetailsPage';

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

function CaseQueueRoute({
  cases,
  isLoading,
  error,
  onRetry,
}: {
  cases: CaseRecord[];
  isLoading: boolean;
  error: string | null;
  onRetry: () => void;
}) {
  const navigate = useNavigate();

  return (
    <CaseQueuePage
      cases={cases}
      isLoading={isLoading}
      error={error}
      onRetry={onRetry}
      onOpenCase={(selectedCase) =>
        navigate(`/cases/${selectedCase.caseNumber ?? selectedCase.id}`)
      }
      onOpenExtractionTest={() => navigate('/test-extraction')}
      onCreateCase={() => navigate('/create-case')}
    />
  );
}

function CaseDetailsRoute({
  cases,
}: {
  cases: CaseRecord[];
}) {
  const { caseNumber } = useParams();
  const navigate = useNavigate();

  const selectedCase = cases.find(
    (item) =>
      item.caseNumber === caseNumber ||
      item.id === caseNumber
  );

  if (!selectedCase) {
    return (
      <div className="shell">
        <p>Case not found.</p>
        <button onClick={() => navigate('/cases')}>
          Back to case queue
        </button>
      </div>
    );
  }

  return (
    <UploadPage
      selectedCase={selectedCase}
      onBack={() => navigate('/cases')}
      onOpenDocument={(documentId) =>
        navigate(
          `/cases/${selectedCase.caseNumber ?? selectedCase.id}/documents/${documentId}`
        )
      }
    />
  );
}

function DocumentDetailsRoute({
  cases,
}: {
  cases: CaseRecord[];
}) {
  const { caseNumber, documentId } = useParams();
  const navigate = useNavigate();

  const selectedCase = cases.find(
    (item) =>
      item.caseNumber === caseNumber ||
      item.id === caseNumber
  );

  if (!selectedCase || !documentId) {
    return (
      <div className="shell">
        <p>Document not found.</p>
        <button onClick={() => navigate('/cases')}>
          Back to case queue
        </button>
      </div>
    );
  }

  return (
    <DocumentDetailsPage
      selectedCase={selectedCase}
      documentId={documentId}
      onBack={() =>
        navigate(
          `/cases/${selectedCase.caseNumber ?? selectedCase.id}`
        )
      }
    />
  );
}

function CreateCaseRoute({
  onCreated,
  onReloadCases,
}: {
  onCreated: () => void;
  onReloadCases: () => void;
}) {
  const navigate = useNavigate();

  return (
    <CreateCasePage
      onBack={() => navigate('/cases')}
      onCreated={() => {
        onCreated();
        onReloadCases();
        navigate('/cases');
      }}
    />
  );
}

function ExtractionTestRoute() {
  const navigate = useNavigate();

  return (
    <ExtractionTestPage
      onBack={() => navigate('/cases')}
    />
  );
}

export default function App() {
  const [cases, setCases] = useState<CaseRecord[]>([]);
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

  return (
    <BrowserRouter>
      <Routes>
        <Route
          path="/"
          element={<Navigate to="/cases" replace />}
        />

        <Route
          path="/cases"
          element={
            <CaseQueueRoute
              cases={cases}
              isLoading={isLoading}
              error={error}
              onRetry={() => void loadCases()}
            />
          }
        />

        <Route
          path="/cases/:caseNumber"
          element={
            isLoading ? (
              <div className="shell">
                <p>Loading case...</p>
              </div>
            ) : (
              <CaseDetailsRoute cases={cases} />
            )
          }
        />

        <Route
          path="/cases/:caseNumber/documents/:documentId"
          element={
            isLoading ? (
              <div className="shell">
                <p>Loading document...</p>
              </div>
            ) : (
              <DocumentDetailsRoute cases={cases} />
            )
          }
        />

        <Route
          path="/create-case"
          element={
            <CreateCaseRoute
              onCreated={() => undefined}
              onReloadCases={() => void loadCases()}
            />
          }
        />

        <Route
          path="/test-extraction"
          element={<ExtractionTestRoute />}
        />

        <Route
          path="*"
          element={<Navigate to="/cases" replace />}
        />
      </Routes>
    </BrowserRouter>
  );
}