import { lazy, Suspense, useEffect, useState, type ReactNode } from 'react';
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useNavigate,
  useParams,
} from 'react-router-dom';

import { CasePageSkeleton, DocumentPageSkeleton, PageSkeleton } from './components/Skeletons';
import CaseQueuePage from './pages/CaseQueuePage';
import CreateCasePage from './pages/CreateCasePage';
import LoginPage from './pages/LoginPage';
import NotFoundState from './components/NotFoundState';
import { useCaseByNumber, type CaseHandover } from './pages/useCaseByNumber';
import { useDashboardCases } from './pages/useDashboardCases';

// Loaded on first visit to keep the initial bundle small.
const loadUploadPage = () => import('./pages/UploadPage');
const loadDocumentDetailsPage = () => import('./pages/DocumentDetailsPage');
const UploadPage = lazy(loadUploadPage);
const DocumentDetailsPage = lazy(loadDocumentDetailsPage);
const DashboardPage = lazy(() => import('./pages/DashboardPage'));
import { getToken } from './lib/api';
import { LoadErrorState } from './components/LoadErrorState';

function CaseQueueRoute() {
  const navigate = useNavigate();

  // Once the queue has settled, fetch the code of the pages a click leads to, so opening one doesn't wait for it.
  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadUploadPage();
      void loadDocumentDetailsPage();
    }, 1000);

    return () => window.clearTimeout(timer);
  }, []);

  return (
    <CaseQueuePage
      onOpenCase={(selectedCase) =>
        navigate(`/cases/${selectedCase.caseNumber ?? selectedCase.id}`, { state: { caseRecord: selectedCase } satisfies CaseHandover })
      }
    />
  );
}

function LoadError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="shell">
      <LoadErrorState message={message} onRetry={onRetry} />
    </div>
  );
}

function CaseDetailsRoute() {
  const { caseNumber } = useParams();
  const navigate = useNavigate();
  const { lookup, retry, rename } = useCaseByNumber(caseNumber);

  // The page's code loads while the case is looked up, so the two waits overlap instead of following each other.
  useEffect(() => {
    void loadUploadPage();
  }, []);

  if (lookup.status === 'loading') {
    return <CasePageSkeleton />;
  }

  if (lookup.status === 'error') {
    return <LoadError message={lookup.message} onRetry={retry} />;
  }

  if (lookup.status === 'notFound') {
    return (
      <NotFoundState
        title="We couldn't find that case"
        description={"The case you're looking for doesn't exist or may have been deleted."}
        hints={[
          'Check that the case number in the URL is correct.',
          'The case may have been deleted.',
          'Search for it from the case queue instead.',
        ]}
      />
    );
  }

  const selectedCase = lookup.caseRecord;

  return (
    <UploadPage
      selectedCase={selectedCase}
      onOpenDocument={(documentId) =>
        navigate(
          `/cases/${selectedCase.caseNumber ?? selectedCase.id}/documents/${documentId}`,
          { state: { caseRecord: selectedCase } satisfies CaseHandover },
        )
      }
      onCaseUpdated={rename}
      onCaseDeleted={() => navigate('/cases')}
    />
  );
}

function DocumentDetailsRoute() {
  const { caseNumber, documentId } = useParams();
  const navigate = useNavigate();
  const { lookup, retry } = useCaseByNumber(caseNumber);

  useEffect(() => {
    void loadDocumentDetailsPage();
  }, []);

  if (lookup.status === 'loading') {
    return <DocumentPageSkeleton />;
  }

  if (lookup.status === 'error') {
    return <LoadError message={lookup.message} onRetry={retry} />;
  }

  if (lookup.status === 'notFound' || !documentId) {
    return (
      <NotFoundState
        title="We couldn't find that document"
        description="The document you're looking for doesn't exist or may have been deleted."
        hints={[
          'Check that the link you followed is complete and correct.',
          'The document or its case may have been deleted.',
          'Open the case from the case queue and select the document from its list.',
        ]}
      />
    );
  }

  const selectedCase = lookup.caseRecord;

  return (
    <DocumentDetailsPage
      selectedCase={selectedCase}
      documentId={documentId}
      // Back to the case's document list, where the officer was working.
      onDocumentDeleted={() =>
        navigate(
          `/cases/${selectedCase.caseNumber ?? selectedCase.id}?section=documents`
        )
      }
    />
  );
}

function DashboardRoute() {
  const { cases, isLoading, isRefreshing, refresh } = useDashboardCases();

  return <DashboardPage cases={cases} isLoading={isLoading} isRefreshing={isRefreshing} onRefresh={() => void refresh()} />;
}

function CreateCaseRoute() {
  const navigate = useNavigate();

  return (
    <CreateCasePage
      onBack={() => navigate('/cases')}
      onCreated={() => navigate('/cases')}
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
  const [isAuthed, setIsAuthed] = useState(() => getToken() !== null);

  return (
    <BrowserRouter>
      <Suspense fallback={<PageSkeleton />}>
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
              <CaseQueueRoute />
            </RequireAuth>
          }
        />

        <Route
          path="/cases/:caseNumber"
          element={
            <RequireAuth isAuthed={isAuthed}>
              {/* The same skeleton as the lookup's, so waiting for the page's code doesn't swap it for another. */}
              <Suspense fallback={<CasePageSkeleton />}>
                <CaseDetailsRoute />
              </Suspense>
            </RequireAuth>
          }
        />

        <Route
          path="/cases/:caseNumber/documents/:documentId"
          element={
            <RequireAuth isAuthed={isAuthed}>
              <Suspense fallback={<DocumentPageSkeleton />}>
                <DocumentDetailsRoute />
              </Suspense>
            </RequireAuth>
          }
        />

        <Route
          path="/dashboard"
          element={
            <RequireAuth isAuthed={isAuthed}>
              <DashboardRoute />
            </RequireAuth>
          }
        />


        <Route
          path="/create-case"
          element={
            <RequireAuth isAuthed={isAuthed}>
              <CreateCaseRoute />
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
