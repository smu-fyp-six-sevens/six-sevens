import { Icon } from '../../components/Icon';
import type { CaseSection } from './caseSections';
import type { SectionNextStep } from './nextStep';

// The next step, as one line with its button: no heading, no side links.
export function NextStepPanel({
  step,
  onSelectSection,
}: {
  step: SectionNextStep | null;
  onSelectSection: (section: CaseSection) => void;
}) {
  if (!step) return null;
  return (
    <aside className="next-step" aria-label="Next step" data-testid="next-step">
      <span className="next-step-icon" aria-hidden="true">
        <Icon name="chevronRight" size={18} />
      </span>
      <p className="next-step-text">{step.text}</p>
      {step.action && (
        <div className="next-step-actions">
          <button type="button" className="next-step-action" onClick={() => onSelectSection(step.action!.section)}>
            {step.action.label}
            <Icon name="chevronRight" size={16} />
          </button>
        </div>
      )}
    </aside>
  );
}
