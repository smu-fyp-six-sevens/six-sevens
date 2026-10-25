import { CASE_SECTIONS, type CaseSection, type SectionStatus } from './caseSections';

export function CaseSectionNav({
  activeSection,
  statuses,
  collapsed,
  onToggleCollapsed,
  onSelect,
}: {
  activeSection: CaseSection;
  // Where each section stands; a section without one shows just its label.
  statuses: Partial<Record<CaseSection, SectionStatus>>;
  // Collapsed, only the icons show (the status becomes a dot on the icon).
  collapsed: boolean;
  onToggleCollapsed: () => void;
  onSelect: (section: CaseSection) => void;
}) {
  return (
    <nav className="case-nav" aria-label="Case sections">
      <button
        type="button"
        className="case-nav-toggle"
        aria-expanded={!collapsed}
        aria-label={collapsed ? 'Expand menu' : 'Collapse menu'}
        title={collapsed ? 'Expand menu' : 'Collapse menu'}
        onClick={onToggleCollapsed}
      >
        <svg viewBox="0 0 14 14" aria-hidden="true">
          <path d={collapsed ? 'm5 2 5 5-5 5' : 'm9 2-5 5 5 5'} />
        </svg>
        <span className="case-nav-toggle-label">Collapse</span>
      </button>

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
                aria-label={collapsed ? `${section.label}${status ? `, ${status.text}` : ''}` : undefined}
                title={collapsed ? section.label : undefined}
                onClick={() => onSelect(section.id)}
              >
                <span className="case-nav-icon" aria-hidden="true">
                  {section.icon}
                  {collapsed && status && (
                    <span className={`case-nav-dot case-nav-dot--${status.tone} case-nav-badge`} />
                  )}
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
