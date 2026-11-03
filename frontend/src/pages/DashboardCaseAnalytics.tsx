import { useState, type CSSProperties } from 'react';
import SelectMenu from '../components/SelectMenu';
import CompareDelta from '../components/CompareDelta';
import { formatDuration } from './upload/processingTimes';
import { median, usd } from './dashboardCosts';

// The dashboard's Cases tab: what the cases in view cost and took, where they are, and how they scored. The case
// queue lists the cases; this answers questions about them as a set.

type Risk = 'Critical' | 'High' | 'Medium' | 'Low';
type Level = Risk | 'Not scored';
const LEVELS: Level[] = ['Critical', 'High', 'Medium', 'Low', 'Not scored'];
const RISK_RANK: Record<Risk, number> = { Critical: 4, High: 3, Medium: 2, Low: 1 };
const levelClass = (level: Level) => (level === 'Not scored' ? 'risk-overview--none' : `risk-gauge-band--${level.toLowerCase()}`);

export interface AnalyticsCase {
  id: string;
  // Set for a live case, which opens; sample rows don't.
  routeId?: string;
  applicant: string;
  // The stage's label, and its tone (utils/caseStages.ts).
  status: string;
  tone: string;
  risk: Risk | null;
  score: number | null;
  // Null until the case has been analysed.
  processingMs: number | null;
  // Claude spend in USD; null when none is recorded (or the costs haven't loaded).
  cost: number | null;
}

// Where the costs stand: loading, failed, or not recorded at all (sample cases).
export type CostState = 'loaded' | 'loading' | 'error' | 'unavailable';

function costNote(state: CostState): string {
  if (state === 'loading') return 'Loading…';
  if (state === 'error') return "Couldn't load the costs. Try refreshing.";
  if (state === 'unavailable') return 'Recorded for live cases only';
  return 'No Claude spend recorded for these cases';
}

function RiskLegend() {
  return (
    <ul className="timeline-legend" aria-label="Risk levels">
      {LEVELS.map((level) => (
        <li key={level}>
          <i className={levelClass(level)} aria-hidden="true" />
          {level}
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------------------------------------------
// Headline figures

function caseFigures(cases: AnalyticsCase[]) {
  const timed = cases.flatMap((item) => (item.processingMs != null ? [item.processingMs] : []));
  const costed = cases.flatMap((item) => (item.cost != null ? [item.cost] : []));
  const total = costed.reduce((sum, value) => sum + value, 0);
  const scored = cases.filter((item) => item.risk);
  return {
    timed,
    costed,
    total: costed.length ? total : null,
    average: costed.length ? total / costed.length : null,
    medianTime: timed.length ? median(timed) : null,
    scored: scored.length,
    highRisk: scored.filter((item) => item.risk === 'Critical' || item.risk === 'High').length,
  };
}

// The figures, and when comparing, each one's change in place of its caption.
export function CaseKpis({ cases, compare, costState }: { cases: AnalyticsCase[]; compare?: AnalyticsCase[]; costState: CostState }) {
  const now = caseFigures(cases);
  const before = compare ? caseFigures(compare) : null;
  const { timed, costed } = now;

  return (
    <section className="case-kpi-grid" aria-label="Case figures">
      <article className="dashboard-stat-card">
        <span className="stat-caption">Analysed</span>
        <span className="stat-number">{timed.length}<small> / {cases.length}</small></span>
        {before ? <CompareDelta value={timed.length} previous={before.timed.length} /> : <span className="stat-caption">{cases.length ? `${Math.round((timed.length / cases.length) * 100)}% of cases in view` : 'No cases in view'}</span>}
      </article>
      <article className="dashboard-stat-card">
        <span className="stat-caption">Median processing time</span>
        <span className="stat-number">{now.medianTime != null ? formatDuration(now.medianTime) : '—'}</span>
        {before ? <CompareDelta value={now.medianTime} previous={before.medianTime} format={formatDuration} better="down" /> : <span className="stat-caption">{timed.length ? `${formatDuration(Math.min(...timed))} to ${formatDuration(Math.max(...timed))}` : 'Shown once a case is analysed'}</span>}
      </article>
      <article className="dashboard-stat-card">
        <span className="stat-caption">Total Claude cost</span>
        <span className="stat-number">{now.total != null ? usd(now.total) : '—'}</span>
        {before && costState === 'loaded' ? <CompareDelta value={now.total} previous={before.total} format={usd} better="down" /> : <span className="stat-caption">{costed.length ? `across ${costed.length} ${costed.length === 1 ? 'case' : 'cases'}` : costNote(costState)}</span>}
      </article>
      <article className="dashboard-stat-card">
        <span className="stat-caption">Cost per case</span>
        <span className="stat-number">{now.average != null ? usd(now.average) : '—'}</span>
        {before && costState === 'loaded' ? <CompareDelta value={now.average} previous={before.average} format={usd} better="down" /> : <span className="stat-caption">{costed.length ? `averaged across ${costed.length} ${costed.length === 1 ? 'case' : 'cases'}` : costNote(costState)}</span>}
      </article>
      <article className="dashboard-stat-card">
        <span className="stat-caption">High or critical risk</span>
        <span className="stat-number">{now.highRisk}<small> / {now.scored}</small></span>
        {before ? <CompareDelta value={now.highRisk} previous={before.highRisk} /> : <span className="stat-caption">{now.scored ? `${Math.round((now.highRisk / now.scored) * 100)}% of scored cases` : 'No case scored yet'}</span>}
      </article>
    </section>
  );
}

// ---------------------------------------------------------------------------------------------------------------
// Time and cost by case

const CASE_SORTS = ['Slowest first', 'Costliest first', 'Highest risk first'];
const SHOWN_CASES = 8;

// Each case's processing time and Claude cost as two bars on their own scales, a tick at each median.
export function TimeAndCostByCase({ cases, compare, costState, onOpen }: { cases: AnalyticsCase[]; compare?: AnalyticsCase[]; costState: CostState; onOpen: (item: AnalyticsCase) => void }) {
  const [sort, setSort] = useState(CASE_SORTS[0]);
  const [showAll, setShowAll] = useState(false);
  const measured = cases.filter((item) => item.processingMs != null || item.cost != null);
  const sorted = [...measured].sort((a, b) => {
    if (sort === 'Costliest first') return (b.cost ?? -1) - (a.cost ?? -1);
    if (sort === 'Highest risk first') return (b.risk ? RISK_RANK[b.risk] : 0) - (a.risk ? RISK_RANK[a.risk] : 0) || (b.score ?? -1) - (a.score ?? -1);
    return (b.processingMs ?? -1) - (a.processingMs ?? -1);
  });
  const shown = showAll ? sorted : sorted.slice(0, SHOWN_CASES);
  const times = measured.flatMap((item) => (item.processingMs != null ? [item.processingMs] : []));
  const costs = measured.flatMap((item) => (item.cost != null ? [item.cost] : []));
  const medianTime = times.length ? median(times) : null;
  const medianCost = costs.length ? median(costs) : null;
  // The comparison period's medians, as striped markers on the same scales.
  const compareTimes = compare?.flatMap((item) => (item.processingMs != null ? [item.processingMs] : [])) ?? [];
  const compareCosts = compare?.flatMap((item) => (item.cost != null ? [item.cost] : [])) ?? [];
  const wasTime = compareTimes.length ? median(compareTimes) : null;
  const wasCost = compareCosts.length ? median(compareCosts) : null;
  const maxTime = Math.max(1, ...times, wasTime ?? 0);
  const maxCost = Math.max(0.000001, ...costs, wasCost ?? 0);

  return (
    <section className="dashboard-panel case-cost-panel">
      <div className="panel-heading">
        <div>
          <h2>Time and cost by case</h2>
          <p>Automated processing time and Claude spend per analysed case; the line marks the median{compare ? ', the striped marker the comparison period\'s' : ''}</p>
        </div>
        {measured.length > 1 && <SelectMenu name="Sort cases" options={CASE_SORTS} value={sort} onChange={setSort} />}
      </div>
      {measured.length === 0 ? (
        <p className="empty-insight">No case in this date and status selection has been analysed yet.</p>
      ) : (
        <>
          <div className="case-cost-head" aria-hidden="true">
            <span>CASE</span>
            <span>PROCESSING TIME{medianTime != null && <small> · median {formatDuration(medianTime)}{wasTime != null && `, was ${formatDuration(wasTime)}`}</small>}</span>
            <span>CLAUDE COST{medianCost != null && <small> · median {usd(medianCost)}{wasCost != null && `, was ${usd(wasCost)}`}</small>}</span>
          </div>
          <ul className="case-cost-list">
            {shown.map((item) => {
              const content = (
                <>
                  <span className="case-cost-case">
                    <strong>{item.id}{item.risk && <span className={`case-cost-risk risk-score-${item.risk.toLowerCase()}`}>{item.risk}</span>}</strong>
                    <span>{item.applicant}</span>
                  </span>
                  <span className="case-cost-measure">
                    <span className="case-cost-track">
                      {item.processingMs != null && <i className="case-cost-bar--time" style={{ width: `${(item.processingMs / maxTime) * 100}%` }} />}
                      {medianTime != null && <b style={{ left: `${(medianTime / maxTime) * 100}%` }} />}
                      {wasTime != null && <span className="compare-marker" style={{ left: `${(wasTime / maxTime) * 100}%` }} title={`Comparison median: ${formatDuration(wasTime)}`} />}
                    </span>
                    <span className="case-cost-value">{item.processingMs != null ? formatDuration(item.processingMs) : '—'}</span>
                  </span>
                  <span className="case-cost-measure">
                    <span className="case-cost-track">
                      {item.cost != null && <i className="case-cost-bar--cost" style={{ width: `${(item.cost / maxCost) * 100}%` }} />}
                      {medianCost != null && <b style={{ left: `${(medianCost / maxCost) * 100}%` }} />}
                      {wasCost != null && <span className="compare-marker" style={{ left: `${(wasCost / maxCost) * 100}%` }} title={`Comparison median: ${usd(wasCost)}`} />}
                    </span>
                    <span className="case-cost-value">{item.cost != null ? usd(item.cost) : '—'}</span>
                  </span>
                </>
              );
              return (
                <li key={item.id}>
                  {item.routeId ? (
                    <button type="button" className="case-cost-row" onClick={() => onOpen(item)} aria-label={`Open case ${item.id}`}>{content}</button>
                  ) : (
                    <div className="case-cost-row">{content}</div>
                  )}
                </li>
              );
            })}
          </ul>
          <p className="insight-note">
            {sorted.length > SHOWN_CASES && (
              <button type="button" className="text-button" onClick={() => setShowAll((value) => !value)}>
                {showAll ? 'Show fewer' : `Show all ${sorted.length}`}
              </button>
            )}
            {!costs.length && <span> Claude cost: {costNote(costState).toLowerCase()}.</span>}
          </p>
        </>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------------------------------------------
// Where the cases are

// The cases in view split by stage, in the stages' own order: one bar, then a row per stage holding cases.
export function StageBreakdown({ cases, compare, stages }: { cases: AnalyticsCase[]; compare?: AnalyticsCase[]; stages: { label: string; tone: string }[] }) {
  const counts = stages.map((stage) => ({
    ...stage,
    count: cases.filter((item) => item.status === stage.label).length,
    was: compare?.filter((item) => item.status === stage.label).length ?? 0,
  }));
  // Stages holding cases now, or (when comparing) then.
  const held = counts.filter((stage) => stage.count > 0 || stage.was > 0);

  return (
    <section className="dashboard-panel">
      <div className="panel-heading">
        <div>
          <h2>Where the cases are</h2>
          <p>Cases in view by stage</p>
        </div>
      </div>
      {cases.length === 0 && !compare?.length ? (
        <p className="empty-insight">No cases in this date and status selection.</p>
      ) : (
        <>
          <div className="stage-bar" role="img" aria-label={held.map((stage) => `${stage.label} ${stage.count}`).join(', ')}>
            {held.filter((stage) => stage.count).map((stage) => <i key={stage.label} className={`tone-${stage.tone}`} style={{ flexGrow: stage.count }} title={`${stage.label}: ${stage.count}`} />)}
          </div>
          <ul className="stage-list">
            {held.map((stage) => (
              <li key={stage.label}>
                <span className={`case-status-dot tone-${stage.tone}`} aria-hidden="true" />
                <span className="stage-name">{stage.label}</span>
                <strong>{stage.count}</strong>
                {compare
                  ? <CompareDelta value={stage.count} previous={stage.was} compact />
                  : <span className="stage-share">{Math.round((stage.count / cases.length) * 100)}%</span>}
              </li>
            ))}
          </ul>
          {held.length < stages.length && <p className="insight-note">No cases in the other {stages.length - held.length} stages.</p>}
        </>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------------------------------------------
// Cost against time

const PLOT = { width: 560, height: 250, left: 52, right: 14, top: 12, bottom: 34 };

// Three round ticks from zero to just past the largest value.
function ticks(max: number): number[] {
  const step = 10 ** Math.floor(Math.log10(max / 3));
  const nice = [1, 2, 2.5, 5, 10].map((factor) => factor * step).find((value) => value * 3 >= max) ?? step * 10;
  return [0, nice, nice * 2, nice * 3];
}

// One dot per case with both a time and a cost, coloured by risk level: whether slow cases cost more, and which
// cases sit apart from the rest.
export function CostAgainstTime({ cases, compare, costState, onOpen }: { cases: AnalyticsCase[]; compare?: AnalyticsCase[]; costState: CostState; onOpen: (item: AnalyticsCase) => void }) {
  const [hovered, setHovered] = useState<string | null>(null);
  const plotted = (list: AnalyticsCase[], was: boolean) => list
    .filter((item): item is AnalyticsCase & { processingMs: number; cost: number } => item.processingMs != null && item.cost != null)
    .map((item) => ({ ...item, key: `${was ? 'was' : 'now'}:${item.id}`, was }));
  // The comparison period's dots first, so the period's sit on top.
  const points = [...plotted(compare ?? [], true), ...plotted(cases, false)];
  const xTicks = ticks(Math.max(1000, ...points.map((item) => item.processingMs)));
  const yTicks = ticks(Math.max(0.001, ...points.map((item) => item.cost)));
  const xMax = xTicks[xTicks.length - 1];
  const yMax = yTicks[yTicks.length - 1];
  const plotWidth = PLOT.width - PLOT.left - PLOT.right;
  const plotHeight = PLOT.height - PLOT.top - PLOT.bottom;
  const x = (value: number) => PLOT.left + (value / xMax) * plotWidth;
  const y = (value: number) => PLOT.top + plotHeight - (value / yMax) * plotHeight;
  const active = points.find((item) => item.key === hovered);

  return (
    <section className="dashboard-panel">
      <div className="panel-heading">
        <div>
          <h2>Cost against processing time</h2>
          <p>Each dot is an analysed case, coloured by risk level{compare ? '; striped dots are the comparison period' : ''}</p>
        </div>
      </div>
      {points.length === 0 ? (
        <p className="empty-insight">{costState === 'loaded' ? 'No case in this selection has both a processing time and a cost.' : `Claude cost: ${costNote(costState).toLowerCase()}.`}</p>
      ) : (
        <>
          <RiskLegend />
          <div className="scatter">
            <svg viewBox={`0 0 ${PLOT.width} ${PLOT.height}`} role="img" aria-label={`Claude cost against processing time for ${points.length} cases`}>
              {yTicks.map((value) => (
                <g key={`y${value}`}>
                  <line className={value ? 'scatter-grid' : 'scatter-axis'} x1={PLOT.left} x2={PLOT.width - PLOT.right} y1={y(value)} y2={y(value)} />
                  <text className="scatter-tick" x={PLOT.left - 8} y={y(value) + 4} textAnchor="end">${Number(value.toPrecision(3))}</text>
                </g>
              ))}
              {xTicks.map((value) => (
                <text key={`x${value}`} className="scatter-tick" x={x(value)} y={PLOT.height - 12} textAnchor={value ? 'middle' : 'start'}>{value ? formatDuration(value) : '0s'}</text>
              ))}
              {points.map((item) => (
                <g
                  key={item.key}
                  className={`scatter-point${item.routeId ? ' scatter-point--link' : ''}${item.was ? ' scatter-point--was' : ''}`}
                  onMouseEnter={() => setHovered(item.key)}
                  onMouseLeave={() => setHovered(null)}
                  onClick={() => item.routeId && onOpen(item)}
                >
                  {/* A larger, invisible target than the dot. */}
                  <circle cx={x(item.processingMs)} cy={y(item.cost)} r={12} fill="transparent" />
                  <circle className={`scatter-dot ${levelClass(item.risk ?? 'Not scored')}`} cx={x(item.processingMs)} cy={y(item.cost)} r={hovered === item.key ? 7 : 5.5} />
                  {item.was && <circle className="scatter-dot-stripes" cx={x(item.processingMs)} cy={y(item.cost)} r={hovered === item.key ? 7 : 5.5} />}
                </g>
              ))}
            </svg>
            {active && (
              <span
                className="scatter-tip"
                role="tooltip"
                style={{ left: `${(x(active.processingMs) / PLOT.width) * 100}%`, top: `${(y(active.cost) / PLOT.height) * 100}%` } as CSSProperties}
              >
                <strong>{active.id}{active.was && ' · comparison period'}</strong>
                <span>{active.applicant}</span>
                <span>{formatDuration(active.processingMs)} · {usd(active.cost)}</span>
                <span>{active.risk ? `${active.risk} risk, ${active.score ?? '—'}` : 'Not scored'}</span>
              </span>
            )}
          </div>
        </>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------------------------------------------
// Risk scores

// Scored cases per band of ten points, each column split by risk level.
export function RiskScoreSpread({ cases, compare }: { cases: AnalyticsCase[]; compare?: AnalyticsCase[] }) {
  const bins = Array.from({ length: 10 }, (_, index) => ({ from: index * 10, counts: { Critical: 0, High: 0, Medium: 0, Low: 0 } as Record<Risk, number>, total: 0 }));
  const scored = cases.filter((item): item is AnalyticsCase & { risk: Risk; score: number } => item.risk != null && item.score != null);
  for (const item of scored) {
    const bin = bins[Math.min(9, Math.max(0, Math.floor(item.score / 10)))];
    bin.counts[item.risk] += 1;
    bin.total += 1;
  }
  // The comparison period's scored cases per bin, drawn as striped columns beside this period's.
  const ghosts = Array<number>(10).fill(0);
  compare?.forEach((item) => {
    if (item.risk != null && item.score != null) ghosts[Math.min(9, Math.max(0, Math.floor(item.score / 10)))] += 1;
  });
  const peak = Math.max(1, ...bins.map((bin) => bin.total), ...ghosts);
  const unscored = cases.length - scored.length;
  const medianScore = scored.length ? Math.round(median(scored.map((item) => item.score))) : null;
  const compareScores = compare?.flatMap((item) => (item.risk != null && item.score != null ? [item.score] : [])) ?? [];

  return (
    <section className="dashboard-panel">
      <div className="panel-heading">
        <div>
          <h2>Risk score spread</h2>
          <p>Scored cases per ten points of risk score{compare ? '; striped columns are the comparison period' : ''}</p>
        </div>
      </div>
      {scored.length === 0 && !ghosts.some(Boolean) ? (
        <p className="empty-insight">No case in this selection has been scored yet.</p>
      ) : (
        <>
          <ul className="timeline-legend" aria-label="Risk levels">
            {LEVELS.slice(0, 4).map((level) => (
              <li key={level}><i className={levelClass(level)} aria-hidden="true" />{level}</li>
            ))}
          </ul>
          <div className={`timeline${compare ? ' timeline--compare' : ''}`} role="img" aria-label={`Risk scores of ${scored.length} cases${medianScore != null ? `, median ${medianScore}` : ''}`}>
            <span className="timeline-peak" aria-hidden="true">{peak}</span>
            <div className="timeline-columns">
              {bins.map((bin, index) => (
                <div key={bin.from} className="timeline-column" style={{ '--column': index } as CSSProperties}>
                  {ghosts[index] > 0 && <span className="timeline-ghost" style={{ height: `${(ghosts[index] / peak) * 100}%` }} aria-hidden="true" />}
                  <div className="timeline-stack" style={{ height: `${(bin.total / peak) * 100}%` }}>
                    {(Object.keys(bin.counts) as Risk[]).map((level) =>
                      bin.counts[level] ? <i key={level} className={levelClass(level)} style={{ flexGrow: bin.counts[level] }} /> : null,
                    )}
                  </div>
                  <span className="timeline-tip" role="tooltip">
                    <strong>Score {bin.from}–{bin.from + (index === 9 ? 10 : 9)} · {bin.total} {bin.total === 1 ? 'case' : 'cases'}</strong>
                    {(Object.keys(bin.counts) as Risk[]).filter((level) => bin.counts[level]).map((level) => (
                      <span key={level}><i className={levelClass(level)} aria-hidden="true" />{level} {bin.counts[level]}</span>
                    ))}
                    {compare && <span className="timeline-tip-was">Comparison: {ghosts[index]}</span>}
                  </span>
                  <span className="timeline-label" aria-hidden="true">{index % 2 === 0 ? bin.from : ''}</span>
                </div>
              ))}
            </div>
          </div>
          <p className="insight-note">
            {medianScore != null ? `Median score ${medianScore}` : 'No case scored'}
            {compare && ` (was ${compareScores.length ? Math.round(median(compareScores)) : '—'})`}
            {unscored > 0 && ` · ${unscored} ${unscored === 1 ? 'case' : 'cases'} not scored yet`}
          </p>
        </>
      )}
    </section>
  );
}
