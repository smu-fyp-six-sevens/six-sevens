import type { CSSProperties } from 'react';
import { SeverityGauge } from './upload/SeverityGauge';
import { SCORE_BANDS } from './upload/scoreBands';

// The dashboard Overview's charts: the commonest flags as runs of case pills, how many cases include each document
// type as rings, and the cases' risk as the Case Summary's severity dial beside one bar split by level.

// More cases than this and each pill stands for several, so a row stays one line.
const MAX_PILLS = 20;

type FlagSeverity = 'High' | 'Medium' | 'Low';

export interface FlagInsight {
  name: string;
  severity: FlagSeverity;
  // The cases in view with this flag.
  caseIds: string[];
}

// Each flag as a run of pills, one per case in view, those with the flag filled in its severity's colour: how many
// cases, and how serious, at a glance. A row opens the Flags tab.
export function FlagPills({ flags, caseCount, onOpen }: { flags: FlagInsight[]; caseCount: number; onOpen: () => void }) {
  const pills = Math.max(1, Math.min(caseCount, MAX_PILLS));
  const casesPerPill = caseCount / pills;

  return (
    <>
      <div className="flag-pill-list">
        {flags.map((flag, index) => {
          const count = flag.caseIds.length;
          // At least one pill for a flag in view.
          const filled = count ? Math.max(1, Math.round(count / casesPerPill)) : 0;
          return (
            <button
              key={flag.name}
              type="button"
              className="flag-pill-row"
              onClick={onOpen}
              title={`${flag.caseIds.slice(0, 5).join(', ')}${count > 5 ? ` +${count - 5} more` : ''}`}
              aria-label={`${flag.name}: ${count} of ${caseCount} cases, ${flag.severity.toLowerCase()} severity. View flags`}
            >
              <span className="bar-rank">{String(index + 1).padStart(2, '0')}</span>
              <span className="bar-label">{flag.name}</span>
              <span className="flag-pills-stack" aria-hidden="true">
                <span className="flag-pills">
                  {Array.from({ length: pills }, (_, pill) => (
                    <i
                      key={pill}
                      className={pill < filled ? `flag-pill--${flag.severity.toLowerCase()}` : undefined}
                      style={{ '--pill': pill } as CSSProperties}
                    />
                  ))}
                </span>
              </span>
              <strong>{count}</strong>
              <span className={`flag-pill-severity flag-pill-severity--${flag.severity.toLowerCase()}`}>{flag.severity}</span>
            </button>
          );
        })}
      </div>
      <p className="insight-note">
        Each pill is {casesPerPill === 1 ? 'a case' : `about ${Math.round(casesPerPill)} cases`} in view; filled, it has the flag.
      </p>
    </>
  );
}

// Each document type as a ring: the share of the cases in view that include it. A case holds several types, so
// these are separate shares, not slices of one whole.
export function CoverageRings({ types, caseCount }: { types: [string, number][]; caseCount: number }) {
  return (
    <ul className="coverage-rings">
      {types.map(([type, count]) => {
        const share = caseCount ? Math.round((count / caseCount) * 100) : 0;
        return (
          <li key={type} title={`${count} of ${caseCount} cases (${share}%) include a ${type}`}>
            <svg className="coverage-ring" viewBox="0 0 64 64" aria-hidden="true">
              <circle className="coverage-ring-track" cx="32" cy="32" r="26" />
              <circle
                className="coverage-ring-fill"
                cx="32"
                cy="32"
                r="26"
                pathLength={100}
                strokeDasharray={`${share} 100`}
                style={{ '--share': share } as CSSProperties}
              />
            </svg>
            <span className="coverage-ring-count">
              {count}
              <small>/{caseCount}</small>
            </span>
            <span className="coverage-ring-label">{type}</span>
            <span className="sr-only">
              : {count} of {caseCount} cases
            </span>
          </li>
        );
      })}
    </ul>
  );
}

type Risk = (typeof SCORE_BANDS)[number]['level'];
const RISK_ORDER: Risk[] = ['Critical', 'High', 'Medium', 'Low'];

// The cases' risk: their average score on the severity dial, and one bar split by level (unscored cases too), the
// count of each above it.
type RiskCase = { id: string; risk: Risk | null; score: number | null };

function averageScore(cases: RiskCase[]): number | null {
  const scores = cases.flatMap((item) => (item.score === null ? [] : [item.score]));
  return scores.length ? Math.round(scores.reduce((sum, score) => sum + score, 0) / scores.length) : null;
}

export function RiskOverview({ cases }: { cases: RiskCase[] }) {
  const scores = cases.flatMap((item) => (item.score === null ? [] : [item.score]));
  const average = averageScore(cases);
  const averageLevel = average === null ? null : SCORE_BANDS.find((band) => average >= band.min && average <= band.max)?.level ?? null;
  const groups = [
    ...RISK_ORDER.map((risk) => ({ key: risk.toLowerCase(), label: risk, cases: cases.filter((item) => item.risk === risk) })),
    { key: 'none', label: 'Not scored', cases: cases.filter((item) => item.risk === null) },
  ];

  return (
    <div className="risk-overview">
      <div className="risk-overview-dial">
        <SeverityGauge score={average} level={averageLevel} />
        <span className="risk-overview-dial-caption">
          {average === null ? 'No case scored yet' : `Average of ${scores.length} scored ${scores.length === 1 ? 'case' : 'cases'}`}
        </span>
      </div>

      <div className="risk-overview-split">
        <ul className="risk-overview-legend">
          {groups.map((group) => (
            <li key={group.key} className={group.cases.length ? undefined : 'risk-overview-legend--empty'}>
              <span className="risk-overview-count">
                <strong>{group.cases.length}</strong>
              </span>
              <span>
                <i className={`risk-overview-swatch risk-overview--${group.key}${group.key === 'none' ? '' : ` risk-gauge-band--${group.key}`}`} aria-hidden="true" />
                {group.label}
              </span>
            </li>
          ))}
        </ul>
        <div className="risk-overview-bar" aria-hidden="true">
          {groups.map((group) =>
            group.cases.length ? (
              <i
                key={group.key}
                className={`risk-overview--${group.key}${group.key === 'none' ? '' : ` risk-gauge-band--${group.key}`}`}
                style={{ flexGrow: group.cases.length }}
                title={`${group.label}: ${group.cases.slice(0, 5).map((item) => item.id).join(', ')}${group.cases.length > 5 ? ` +${group.cases.length - 5} more` : ''}`}
              />
            ) : null,
          )}
        </div>
      </div>
    </div>
  );
}
