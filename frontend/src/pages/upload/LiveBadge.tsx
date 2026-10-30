export function LiveBadge({ connected }: { connected: boolean }) {
  return (
    <span
      className={`live-badge${connected ? ' live' : ''}`}
      title={connected ? 'Connected — updates push in real time' : 'Disconnected — reconnecting…'}
    >
      {connected ? 'LIVE' : 'OFFLINE'}
    </span>
  );
}
