import { useCallback, useEffect, useState } from 'react';
import { AgentTraceView } from '../../components/agentTrace/AgentTraceView';
import { ErrorMessage } from '../../components/ErrorMessage';
import {
  getCaseAnalysis,
  getCaseAnalysisRun,
  listCaseAnalysisRuns,
  type CaseAnalysis,
  type CaseAnalysisOverview,
  type CaseAnalysisRunItem,
} from '../../services/uploadService';
import { PreviousRuns } from './PreviousRuns';

// The case's Agent Trace section: the latest analysis run's trace, or an earlier run's.
export function CaseTraceCard({ caseId, overview }: { caseId: string; overview: CaseAnalysisOverview | null }) {
  const [analysis, setAnalysis] = useState<CaseAnalysis | null>(null);
  const [runs, setRuns] = useState<CaseAnalysisRunItem[]>([]);
  const [viewing, setViewing] = useState<CaseAnalysis | null>(null);
  const [openingRunId, setOpeningRunId] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setIsRefreshing(true);
    try {
      const [latest, listed] = await Promise.all([getCaseAnalysis(caseId), listCaseAnalysisRuns(caseId)]);
      setAnalysis(latest);
      setRuns(listed);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Failed to load the trace');
    } finally {
      setIsRefreshing(false);
    }
  }, [caseId]);

  // The snapshot carries the overview over the socket, so a change in these fields means the trace moved.
  const hasOverview = overview !== null;
  const runId = overview?.run_id;
  const runStatus = overview?.status;
  useEffect(() => {
    setError(null);
    setViewing(null);
    if (hasOverview) void load();
    else {
      setAnalysis(null);
      setRuns([]);
    }
  }, [hasOverview, runId, runStatus, load]);

  async function openRun(id: string | null) {
    setError(null);
    if (id === null) {
      setViewing(null);
      return;
    }
    setOpeningRunId(id);
    try {
      setViewing(await getCaseAnalysisRun(caseId, id));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Failed to load that run');
    } finally {
      setOpeningRunId(null);
    }
  }

  const shown = viewing ?? analysis;
  return (
    <>
      {error && <ErrorMessage message={error} />}
      <AgentTraceView
        title="Case analysis trace"
        steps={shown?.trace ?? null}
        emptyText={shown ? 'No steps recorded yet.' : 'No analysis has been run for this case yet.'}
        isRefreshing={isRefreshing}
        onRefresh={load}
      />
      {runs.length > 1 && (
        <section className="summary-card">
          <PreviousRuns runs={runs} viewingRunId={viewing?.run_id ?? openingRunId} onOpen={(id) => void openRun(id)} />
        </section>
      )}
    </>
  );
}
