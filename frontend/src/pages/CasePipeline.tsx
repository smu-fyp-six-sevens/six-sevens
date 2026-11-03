import { InfoTip } from '../components/InfoTip';
import type { CaseStage } from '../services/uploadService';
import { CASE_STAGES, caseStageLabel, caseStageTone } from '../utils/caseStages';

function share(count: number, total: number): string {
  const percent = total ? Math.round((count / total) * 100) : 0;
  return `${count} of ${total} ${total === 1 ? 'case' : 'cases'} (${percent}%)`;
}

// How many of the cases in view sit at each stage, in the order a case moves through them. Failed can happen at any
// stage, so it stands apart at the end, off the line.
export function CasePipeline({ counts, total, period }: {
  counts: Record<CaseStage, number>;
  total: number;
  period: string;
}) {
  return (
    <section className="dashboard-panel case-pipeline" aria-labelledby="case-pipeline-title">
      <div className="panel-heading">
        <div>
          <h2 id="case-pipeline-title">Case pipeline</h2>
          <p>{total} {total === 1 ? 'case' : 'cases'} in view · {period}</p>
        </div>
      </div>
      <ol className="case-pipeline-track">
        {CASE_STAGES.map((stage) => (
          <li
            key={stage}
            className={[
              'case-pipeline-step',
              `tone-${caseStageTone(stage)}`,
              counts[stage] > 0 && 'case-pipeline-step--filled',
              stage === 'failed' && 'case-pipeline-step--apart',
            ].filter(Boolean).join(' ')}
          >
            <InfoTip className="case-pipeline-count" tip={share(counts[stage], total)}>{counts[stage]}</InfoTip>
            <span className="case-pipeline-label">{caseStageLabel(stage)}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}
