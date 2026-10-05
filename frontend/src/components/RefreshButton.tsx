export function RefreshButton({
  isRefreshing,
  onClick,
  label,
}: {
  isRefreshing: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      className={`panel-refresh-button${isRefreshing ? ' is-refreshing' : ''}`}
      aria-label={isRefreshing ? `Refreshing ${label}` : `Refresh ${label}`}
      title={isRefreshing ? 'Refreshing…' : 'Refresh'}
      disabled={isRefreshing}
      onClick={onClick}
    >
      <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M20 11A8 8 0 1 0 18.6 16" />
        <path d="M20 5v6h-6" />
      </svg>
    </button>
  );
}
