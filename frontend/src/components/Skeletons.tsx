import AppHeader from './AppHeader';
import CaseFrame from './CaseFrame';

// Placeholders shaped like the pages they stand in for, so nothing moves when the real page arrives.
// The pulsing bar itself (.skeleton-bar) is shared with the case queue.
function Bar({ size }: { size: 'title' | 'line' | 'short' | 'medium' | 'tall' | 'pill' }) {
  return <span className={`skeleton-bar skeleton-bar--${size}`} />;
}

function Card({ tall = false }: { tall?: boolean }) {
  return (
    <div className={`skeleton-card${tall ? ' skeleton-card--tall' : ''}`}>
      <Bar size="medium" />
      <Bar size="line" />
      <Bar size="short" />
    </div>
  );
}

// The Case Summary's severity card (pages/upload/SeverityGauge.tsx) while the first snapshot arrives.
export function SeverityCardSkeleton() {
  return (
    <div role="status" aria-busy="true">
      <span className="sr-only">Loading the case's severity…</span>
      <Card tall />
    </div>
  );
}

// The summary dashboard while the case's first snapshot arrives.
export function CaseOverviewSkeleton() {
  return (
    <div className="case-overview-skeleton" role="status" aria-busy="true">
      <span className="sr-only">Loading the latest case information…</span>

      <div className="skeleton-row skeleton-row--topline" aria-hidden="true">
        <Card tall />
        <Card tall />
      </div>

      <div className="skeleton-row skeleton-row--pair" aria-hidden="true">
        <Card tall />
        <Card tall />
      </div>

      <div className="skeleton-row skeleton-row--pair" aria-hidden="true">
        <Card />
        <Card />
      </div>
    </div>
  );
}

function HeaderSkeleton() {
  return (
    <div className="case-header skeleton-header" aria-hidden="true">
      <Bar size="medium" />
      <Bar size="title" />
    </div>
  );
}

// A case page while the case itself is being looked up.
export function CasePageSkeleton({ label = 'Loading case…' }: { label?: string }) {
  return (
    <main className="shell shell--upload">
      <AppHeader />

      <span className="sr-only" role="status">{label}</span>

      <CaseFrame
        nav={
          <div className="skeleton-nav" aria-hidden="true">
            {Array.from({ length: 9 }, (_, index) => (
              <Bar key={index} size="pill" />
            ))}
          </div>
        }
      >
        <HeaderSkeleton />

        <div className="case-content" aria-hidden="true">
          <CaseOverviewSkeleton />
        </div>
      </CaseFrame>
    </main>
  );
}

// The body of a document page (its fields next to the preview).
export function DocumentBodySkeleton() {
  return (
    <div className="document-details-body document-details-body--split skeleton-document" role="status" aria-busy="true">
      <span className="sr-only">Loading document details…</span>

      <div className="document-details-split" aria-hidden="true">
        <div className="document-details-main">
          <Card tall />
          <Card tall />
          <Card />
        </div>

        <div className="skeleton-preview" />
      </div>
    </div>
  );
}

// A document page while the case it belongs to is being looked up.
export function DocumentPageSkeleton() {
  return (
    <div className="shell shell--upload document-details-page">
      <AppHeader />

      <CaseFrame
        nav={
          <div className="skeleton-nav" aria-hidden="true">
            {Array.from({ length: 9 }, (_, index) => (
              <Bar key={index} size="pill" />
            ))}
          </div>
        }
      >
        <HeaderSkeleton />

        <DocumentBodySkeleton />
      </CaseFrame>
    </div>
  );
}

// While a lazily loaded page's code arrives.
export function PageSkeleton() {
  return (
    <div className="shell" role="status" aria-busy="true">
      <span className="sr-only">Loading…</span>

      <div className="skeleton-card skeleton-card--tall" aria-hidden="true">
        <Bar size="title" />
        <Bar size="line" />
        <Bar size="medium" />
      </div>
    </div>
  );
}

// The /dashboard while its cases load: the stat cards, the two insight panels and the table, in the dashboard's own
// grid, so nothing moves when the real figures arrive.
export function DashboardSkeleton() {
  return (
    <div className="dashboard-skeleton" role="status" aria-busy="true">
      <span className="sr-only">Loading cases…</span>

      <div className="dashboard-panel case-pipeline" aria-hidden="true">
        <Bar size="short" />
        <ol className="case-pipeline-track">
          {Array.from({ length: 10 }, (_, index) => (
            <li key={index} className="case-pipeline-step">
              <span className="case-pipeline-count" />
              <Bar size="short" />
            </li>
          ))}
        </ol>
      </div>

      <div className="dashboard-insight-grid" aria-hidden="true">
        <Card tall />
        <Card tall />
      </div>

      <div className="dashboard-lower-grid" aria-hidden="true">
        <Card tall />
        <Card tall />
      </div>
    </div>
  );
}
