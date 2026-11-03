import { KIND_LABELS, type TraceNodeKind } from './buildTrace';

const GLYPHS: Record<TraceNodeKind, string> = {
  run: '◆',
  agent: '◉',
  llm: '✦',
  tool: '⚒',
  chain: '◇',
  event: '•',
};

export function KindIcon({ kind }: { kind: TraceNodeKind }) {
  return (
    <span className={`trace-icon trace-icon--${kind}`} title={KIND_LABELS[kind]} aria-hidden="true">
      {GLYPHS[kind]}
    </span>
  );
}
