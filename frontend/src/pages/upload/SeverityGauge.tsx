import type { CaseRisk } from '../../services/uploadService';
import { SCORE_BANDS } from './scoreBands';

// The case's 0-100 risk score on a half-circle dial, split into the levels' score bands. The arc fills, band by
// band, up to the score, and a pointer outside the arc marks it (the Risk Summary's bar gauge, bent into a dial).
// Unscored, the dial is a faint track with no pointer.

const CX = 110;
const CY = 112;
const R = 88;
const STROKE = 16;
// Half the 2px gap between bands, in score units (the arc is π·R px long for 100 units).
const HALF_GAP = 1 / ((Math.PI * R) / 100);

// The point on the arc for a score: 0 at the left end, 100 at the right.
function point(score: number, radius = R): [number, number] {
  const angle = Math.PI * (1 - score / 100);
  return [CX + radius * Math.cos(angle), CY - radius * Math.sin(angle)];
}

// A band's stretch of the arc, a 2px gap short of its neighbours.
function bandArc(band: (typeof SCORE_BANDS)[number]): string {
  return arc(band.min === 0 ? 0 : band.min - 0.5 + HALF_GAP, band.max === 100 ? 100 : band.max + 0.5 - HALF_GAP);
}

function arc(from: number, to: number): string {
  const [x1, y1] = point(from);
  const [x2, y2] = point(to);
  return `M ${x1} ${y1} A ${R} ${R} 0 0 1 ${x2} ${y2}`;
}

export function SeverityGauge({ score, level }: { score: number | null; level: string | null }) {
  const scored = score !== null;
  const value = scored ? Math.min(100, Math.max(0, score)) : 0;
  // The pointer is drawn at the top of the dial (score 50) and turned to the score.
  const turn = (value - 50) * 1.8;

  return (
    <svg
      className={`severity-gauge${scored ? '' : ' severity-gauge--empty'}`}
      viewBox="0 0 220 132"
      role="img"
      aria-label={scored ? `Risk score ${value} out of 100, ${level ?? 'unrated'}` : 'Not scored yet'}
    >
      <defs>
        {/* Reveals the coloured bands from 0 up to the score; sweeps in when the card appears. */}
        <mask id="severity-gauge-fill">
          <path
            className="severity-gauge-sweep"
            d={arc(0, 100)}
            pathLength={100}
            strokeDasharray="100 100"
            strokeDashoffset={100 - value}
          />
        </mask>
      </defs>

      {SCORE_BANDS.map((band) => (
        <path
          key={`track-${band.level}`}
          className={`severity-gauge-track risk-gauge-band--${band.level.toLowerCase()}`}
          d={bandArc(band)}
        >
          <title>{`${band.level}: ${band.min}–${band.max}`}</title>
        </path>
      ))}

      {scored && (
        <g mask="url(#severity-gauge-fill)">
          {SCORE_BANDS.map((band) => (
            <path
              key={`fill-${band.level}`}
              className={`severity-gauge-fill risk-gauge-band--${band.level.toLowerCase()}`}
              d={bandArc(band)}
            />
          ))}
        </g>
      )}

      {scored && (
        <g className="severity-gauge-pointer" style={{ transform: `rotate(${turn}deg)` }}>
          <line x1={CX} y1={CY - R - STROKE / 2 - 1} x2={CX} y2={CY - R + STROKE / 2 + 1} />
          <path d={`M ${CX} ${CY - R - STROKE / 2 - 3} l -6 -9 h 12 Z`} />
        </g>
      )}

      <text className="severity-gauge-value" x={CX} y={CY - 8} textAnchor="middle">
        {scored ? value : '—'}
      </text>
      <text className="severity-gauge-unit" x={CX} y={CY + 12} textAnchor="middle">
        {scored ? 'out of 100' : 'not scored'}
      </text>
      <text className="severity-gauge-end" x={point(0)[0]} y={CY + 18} textAnchor="middle">0</text>
      <text className="severity-gauge-end" x={point(100)[0]} y={CY + 18} textAnchor="middle">100</text>
    </svg>
  );
}

// What each part of the dial means, the case's own level marked, so the level never rests on colour alone.
export function SeverityLegend({ level }: { level: string | null }) {
  return (
    <ul className="severity-legend" aria-label="Score bands">
      {SCORE_BANDS.map((band) => (
        <li key={band.level} className={band.level === level ? 'severity-legend-current' : undefined}>
          <span className={`severity-legend-swatch risk-gauge-band--${band.level.toLowerCase()}`} aria-hidden="true" />
          {band.level} <span className="severity-legend-range">{band.min}–{band.max}</span>
        </li>
      ))}
    </ul>
  );
}

// The Case Summary's Overall Case Severity card: its title, then the dial with the level, why, and what each band
// means beside it.
export function SeverityCard({ risk }: { risk: CaseRisk | null }) {
  const scored = risk?.state === 'scored' && risk.level;
  return (
    <article className="case-overview-risk">
      <h2 className="case-overview-risk-title">Overall Case Severity</h2>
      <div className="case-overview-risk-body">
        <SeverityGauge score={scored && risk.score != null ? risk.score : null} level={scored ? risk.level : null} />
        <div className="case-overview-risk-copy">
          <strong>{scored ? `${risk.level} Risk` : risk?.state === 'pending' ? 'Assessment in progress' : 'Not scored yet'}</strong>
          <span>{scored ? 'Based on completed document checks' : risk?.state === 'failed' ? 'A document check failed to complete' : 'Run case analysis after documents are approved and scanned'}</span>
          <SeverityLegend level={scored ? risk.level : null} />
        </div>
      </div>
    </article>
  );
}
