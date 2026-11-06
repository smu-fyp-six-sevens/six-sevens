import { KIND_LABELS, type TraceNodeKind } from './buildTrace';

const GLYPHS: Record<Exclude<TraceNodeKind, 'event'>, string> = {
  run: '◆',
  agent: '◉',
  llm: '✦',
  tool: '⚒',
  chain: '◇',
};

export function KindIcon({ kind }: { kind: TraceNodeKind }) {
  if (kind === 'event') return null;
  return (
    <span className={`trace-icon trace-icon--${kind}`} title={KIND_LABELS[kind] || undefined} aria-hidden="true">
      {GLYPHS[kind]}
    </span>
  );
}
