// Shown across a finalised case's pages: finalising is permanent, so the case is view-only.
export default function FinalisedBanner() {
  return (
    <p className="case-finalised-banner" role="status">
      <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="5" y="11" width="14" height="10" rx="2" />
        <path d="M8 11V7a4 4 0 0 1 8 0v4" />
      </svg>
      <span>
        <strong>This case is view-only.</strong> No edits are allowed.
      </span>
    </p>
  );
}
