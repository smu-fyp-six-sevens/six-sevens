import { lazy, Suspense, useEffect, useState, type ReactNode } from 'react';
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useNavigate,
  useParams,
} from 'react-router-dom';

import CaseQueuePage from './pages/CaseQueuePage';
import CreateCasePage from './pages/CreateCasePage';
import LoginPage from './pages/LoginPage';

// Loaded on first visit to keep the initial bundle small.
const UploadPage = lazy(() => import('./pages/UploadPage'));
const DocumentDetailsPage = lazy(() => import('./pages/DocumentDetailsPage'));
import { authFetch, getToken } from './lib/api';

export interface CaseRecord {
  id: string;
  caseNumber?: string | null;
  applicant: string;
  type: string;
  status: string;
  caseStatus: string;
  created_at?: string | null;
}

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
  onCaseUpdated,
  onCaseDeleted,
}: {
  cases: CaseRecord[];
  isLoading: boolean;
  error: string | null;
  onRetry: () => void;
  onCaseUpdated: (caseId: string, companyName: string) => void;
  onCaseDeleted: (caseId: string) => void;
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
      onCreateCase={() => navigate('/create-case')}
      onCaseUpdated={onCaseUpdated}
      onCaseDeleted={onCaseDeleted}
    />
  );
}

function CaseDetailsRoute({
  cases,
  onCaseUpdated,
  onCaseDeleted,
}: {
  cases: CaseRecord[];
  onCaseUpdated: (caseId: string, companyName: string) => void;
  onCaseDeleted: (caseId: string) => void;
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
      onCaseUpdated={(companyName) => onCaseUpdated(selectedCase.id, companyName)}
      onCaseDeleted={() => {
        onCaseDeleted(selectedCase.id);
        navigate('/cases');
      }}
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
      onDocumentDeleted={() =>
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

function RequireAuth({
  isAuthed,
  children,
}: {
  isAuthed: boolean;
  children: ReactNode;
}) {
  if (!isAuthed) {
    return <Navigate to="/login" replace />;
  }

  return <>{children}</>;
}

function LoginRoute({ onLoggedIn }: { onLoggedIn: () => void }) {
  const navigate = useNavigate();

  return (
    <LoginPage
      onLoggedIn={() => {
        onLoggedIn();
        navigate('/cases', { replace: true });
      }}
    />
  );
}

export default function App() {
  const [cases, setCases] = useState<CaseRecord[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isAuthed, setIsAuthed] = useState(() => getToken() !== null);

  async function loadCases(signal?: AbortSignal) {
    setIsLoading(true);
    setError(null);

    try {
      const response = await authFetch('/cases', { signal });

      if (!response.ok) {
        throw new Error(`Failed to load cases (${response.status}).`);
      }

      const data = (await response.json()) as Array<{
        id: string;
        greenlink_case_id?: string | null;
        company_name?: string | null;
        status?: string | null;
        documents_status?: string | null;
        created_at?: string | null;
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
          created_at: item.created_at ?? null,
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
    if (!isAuthed) {
      return;
    }

    const controller = new AbortController();

    void loadCases(controller.signal);

    return () => controller.abort();
  }, [isAuthed]);

  return (
    <BrowserRouter>
      <Suspense fallback={<p className="loading">Loading…</p>}>
      <Routes>
        <Route
          path="/"
          element={<Navigate to="/cases" replace />}
        />

        <Route
          path="/login"
          element={<LoginRoute onLoggedIn={() => setIsAuthed(true)} />}
        />

        <Route
          path="/cases"
          element={
            <RequireAuth isAuthed={isAuthed}>
              <CaseQueueRoute
                cases={cases}
                isLoading={isLoading}
                error={error}
                onRetry={() => void loadCases()}
                onCaseUpdated={(caseId, companyName) =>
                  setCases((current) =>
                    current.map((item) =>
                      item.id === caseId ? { ...item, applicant: companyName } : item
                    )
                  )
                }
                onCaseDeleted={(caseId) =>
                  setCases((current) => current.filter((item) => item.id !== caseId))
                }
              />
            </RequireAuth>
          }
        />

        <Route
          path="/cases/:caseNumber"
          element={
            <RequireAuth isAuthed={isAuthed}>
              {isLoading ? (
                <div className="shell">
                  <p>Loading case...</p>
                </div>
              ) : (
                <CaseDetailsRoute
                  cases={cases}
                  onCaseUpdated={(caseId, companyName) =>
                    setCases((current) =>
                      current.map((item) =>
                        item.id === caseId ? { ...item, applicant: companyName } : item
                      )
                    )
                  }
                  onCaseDeleted={(caseId) =>
                    setCases((current) => current.filter((item) => item.id !== caseId))
                  }
                />
              )}
            </RequireAuth>
          }
        />

        <Route
          path="/cases/:caseNumber/documents/:documentId"
          element={
            <RequireAuth isAuthed={isAuthed}>
              {isLoading ? (
                <div className="shell">
                  <p>Loading document...</p>
                </div>
              ) : (
                <DocumentDetailsRoute cases={cases} />
              )}
            </RequireAuth>
          }
        />

        <Route
          path="/create-case"
          element={
            <RequireAuth isAuthed={isAuthed}>
              <CreateCaseRoute
                onCreated={() => undefined}
                onReloadCases={() => void loadCases()}
              />
            </RequireAuth>
          }
        />

        <Route
          path="*"
          element={<Navigate to="/cases" replace />}
        />
      </Routes>
      </Suspense>
    </BrowserRouter>
  );
}