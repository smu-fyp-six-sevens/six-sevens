import { CASE_SECTIONS, type CaseSection, type SectionStatus } from './caseSections';

export function CaseSectionNav({
  activeSection,
  statuses,
  onSelect,
}: {
  activeSection: CaseSection;
  // Where each section stands; a section without one shows just its label.
  statuses: Partial<Record<CaseSection, SectionStatus>>;
  onSelect: (section: CaseSection) => void;
}) {
  return (
    <nav className="case-nav" aria-label="Case sections">
      <ul>
        {CASE_SECTIONS.map((section) => {
          const isActive = section.id === activeSection;
          const status = statuses[section.id];

          return (
            <li key={section.id}>
              <button
                type="button"
                className={`case-nav-item${isActive ? ' active' : ''}`}
                aria-current={isActive ? 'page' : undefined}
                onClick={() => onSelect(section.id)}
              >
                <span className="case-nav-icon" aria-hidden="true">
                  {section.icon}
                </span>
                <span className="case-nav-text">
                  <span className="case-nav-label">{section.label}</span>
                  {status && (
                    <span className="case-nav-status">
                      <span className={`case-nav-dot case-nav-dot--${status.tone}`} aria-hidden="true" />
                      {status.text}
                    </span>
                  )}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
