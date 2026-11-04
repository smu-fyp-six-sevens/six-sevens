import type { ReactNode } from 'react';
import { caseStageTone, type StatusTone } from '../utils/caseStages';

// What each group of stages means, as a shape, so the mark reads without its colour: an empty ring for nothing yet,
// a half-filled ring for the system at work, a dot for the officer's turn, a warning triangle for a case that can't
// go on, a tick for analysed, a lock for finalised, a cross for failed. Drawn in the stage's tone (currentColor).
const SHAPES: Record<StatusTone, ReactNode> = {
  idle: <circle cx="7" cy="7" r="5.2" fill="none" stroke="currentColor" strokeWidth="2" />,
  system: (
    <>
      <circle cx="7" cy="7" r="5.2" fill="none" stroke="currentColor" strokeWidth="2" />
      <path d="M7 1.8 A5.2 5.2 0 0 1 7 12.2 Z" fill="currentColor" />
    </>
  ),
  officer: <circle cx="7" cy="7" r="6" fill="currentColor" />,
  blocked: (
    <>
      <path d="M7 1 L13.3 12.6 H0.7 Z" fill="currentColor" />
      <rect x="6.2" y="5" width="1.6" height="4" rx="0.6" fill="#fff" />
      <circle cx="7" cy="10.6" r="0.95" fill="#fff" />
    </>
  ),
  done: (
    <>
      <circle cx="7" cy="7" r="6" fill="currentColor" />
      <path d="M4.2 7.2 6.2 9.1 9.9 5.1" fill="none" stroke="#fff" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
    </>
  ),
  closed: (
    <>
      <rect x="2.2" y="6" width="9.6" height="7" rx="1.6" fill="currentColor" />
      <path d="M4.6 6V4.6a2.4 2.4 0 0 1 4.8 0V6" fill="none" stroke="currentColor" strokeWidth="1.6" />
    </>
  ),
  failed: (
    <>
      <circle cx="7" cy="7" r="6" fill="currentColor" />
      <path d="M4.8 4.8 9.2 9.2M9.2 4.8 4.8 9.2" stroke="#fff" strokeWidth="1.7" strokeLinecap="round" />
    </>
  ),
};

// A case stage's mark: its group's shape in its group's colour. Decorative: the stage's name is always beside it.
export function StageMark({ stage, className }: { stage: string | null | undefined; className?: string }) {
  const tone = caseStageTone(stage);
  return (
    <svg
      className={['stage-mark', `tone-${tone}`, className].filter(Boolean).join(' ')}
      viewBox="0 0 14 14"
      aria-hidden="true"
      focusable="false"
    >
      {SHAPES[tone]}
    </svg>
  );
}
