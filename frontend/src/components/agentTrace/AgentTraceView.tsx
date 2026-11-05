import { useMemo } from 'react';
import { buildTrace, type TraceStep } from './buildTrace';
import { TraceDrawer } from './TraceDrawer';

// The panel showing a trace's runs, opened from the page action menu.
export function AgentTraceView({
  title,
  steps,
  emptyText,
  isRefreshing,
  onRefresh,
  onClose,
}: {
  title: string;
  steps: TraceStep[] | null;
  emptyText: string;
  isRefreshing: boolean;
  onRefresh: () => void | Promise<void>;
  onClose: () => void;
}) {
  const trace = useMemo(() => buildTrace(steps ?? []), [steps]);
  const runs = trace.roots.filter((node) => node.kind === 'run');
  const initialKey = (runs[runs.length - 1] ?? trace.roots[0])?.key ?? '';

  return (
    <TraceDrawer
      title={title}
      trace={trace}
      initialKey={initialKey}
      emptyText={emptyText}
      isRefreshing={isRefreshing}
      onRefresh={onRefresh}
      onClose={onClose}
    />
  );
}
