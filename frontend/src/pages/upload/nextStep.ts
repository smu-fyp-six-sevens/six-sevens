import type { CaseSection } from './caseSections';

// What the officer does next from the Risk & Analysis page's Overview or Findings tab, so neither ends on "now
// what?": one short line, and a button when the step happens elsewhere. The Summary tab's Case Status card says the
// same for the case as a whole.
export interface SectionNextStep {
  text: string;
  action?: { label: string; section: CaseSection };
}

export interface NextStepContext {
  stage: string;
  // Findings still without the officer's verdict, while the case waits at Pending Evaluation.
  findingsToReview: number | null;
  // The latest analysis finished, and nothing has changed since.
  analysed: boolean;
  // A document changed after the latest analysis, so its findings and score are out of date.
  stale: boolean;
  // Whether this case was created with finding reviews: with them, every finding needs the officer's verdict before
  // the case can be finalised (Pending Evaluation); without them, an analysed case can be finalised straight away.
  reviewsRequired: boolean;
}

function stillToReview(count: number | null): string {
  if (!count) return '';
  return ` ${count} still ${count === 1 ? 'needs' : 'need'} your verdict.`;
}

export function sectionNextStep(tab: 'overview' | 'findings', context: NextStepContext): SectionNextStep | null {
  const { stage, findingsToReview, analysed, stale, reviewsRequired } = context;
  if (stage === 'finalised') return null;

  if (stale) {
    return tab === 'overview'
      ? { text: 'A document changed since this run, so the score and findings are out of date. Run it again.' }
      : {
          text: 'These findings are from before the documents changed. Run the analysis again from Overview.',
          action: { label: 'Go to Overview', section: 'risk' },
        };
  }

  if (!analysed) return null;

  if (stage === 'pending_evaluation') {
    return tab === 'overview'
      ? {
          text: `Mark each finding as a real issue or a false alarm.${stillToReview(findingsToReview)}`,
          action: { label: 'Review findings', section: 'findings' },
        }
      : { text: `Open each document, mark its findings, then save.${stillToReview(findingsToReview)}` };
  }

  if (stage === 'case_analysed') {
    return {
      text: reviewsRequired
        ? 'All findings reviewed. Finalise the case once you are satisfied with the score.'
        : 'Reviewing findings is optional here. Finalise the case once you are satisfied with the score.',
      action: { label: 'Finalise on Summary', section: 'summary' },
    };
  }

  return null;
}
