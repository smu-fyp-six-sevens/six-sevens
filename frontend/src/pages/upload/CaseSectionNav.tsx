import { useEffect, useRef, useState } from 'react';
import { CASE_SECTIONS, SECTIONS_WITH_STATUS, type CaseSection, type SectionStatus } from './caseSections';
import { Icon } from '../../components/Icon';

// The case's sections, as a rail of icons that never takes more room than that. Opening it lays the full menu over the
// page (labels and where each section stands) without moving anything; choosing a section, clicking away or Escape
// closes it again.
export function CaseSectionNav({
  activeSection,
  statuses,
  statusesLoading,
  onSelect,
}: {
  // None on a document page, which belongs to the case rather than to one section.
  activeSection: CaseSection | null;
  // The snapshot hasn't arrived, so the status lines show a placeholder instead of a wrong "None Uploaded".
  statusesLoading: boolean;
  // Where each section stands; a section without one shows just its label.
  statuses: Partial<Record<CaseSection, SectionStatus>>;
  onSelect: (section: CaseSection) => void;
}) {
  const [open, setOpen] = useState(false);
  const navRef = useRef<HTMLElement>(null);
  // Closed, only the icons show (the status becomes a dot on the icon).
  const collapsed = !open;

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!navRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  return (
    <nav ref={navRef} className={`case-nav${open ? ' case-nav--open' : ''}`} aria-label="Case sections">
      {(
        <button
          type="button"
          className="case-nav-toggle"
          aria-expanded={!collapsed}
          aria-label={collapsed ? 'Expand menu' : 'Collapse menu'}
          title={collapsed ? 'Expand menu' : 'Collapse menu'}
          onClick={() => setOpen((isOpen) => !isOpen)}
        >
          <svg viewBox="0 0 14 14" aria-hidden="true">
            <path d="M2 3.5h10M2 7h10M2 10.5h10" />
          </svg>
          <span className="case-nav-toggle-label">Menu</span>
        </button>
      )}

      <ul>
        {CASE_SECTIONS.map((section) => {
          const isActive = section.id === activeSection;
          const status = statusesLoading ? undefined : statuses[section.id];
          const statusPending = statusesLoading && SECTIONS_WITH_STATUS.includes(section.id);

          return (
            <li key={section.id}>
              <button
                type="button"
                className={`case-nav-item${isActive ? ' active' : ''}`}
                aria-current={isActive ? 'page' : undefined}
                aria-label={collapsed ? `${section.label}${status ? `, ${status.text}` : ''}` : undefined}
                title={collapsed ? section.label : undefined}
                onClick={() => {
                  setOpen(false);
                  onSelect(section.id);
                }}
              >
                <span className="case-nav-icon" aria-hidden="true">
                  <Icon name={section.icon} size={16} />
                  {collapsed && status && (
                    <span className={`case-nav-dot case-nav-dot--${status.tone} case-nav-badge`} />
                  )}
                </span>
                {/* The rail's caption under the icon; the open menu has the full label instead. */}
                <span className="case-nav-short" aria-hidden="true">{section.short}</span>
                <span className="case-nav-text">
                  <span className="case-nav-label">{section.label}</span>
                  {status && (
                    <span className="case-nav-status">
                      <span className={`case-nav-dot case-nav-dot--${status.tone}`} aria-hidden="true" />
                      {status.text}
                    </span>
                  )}
                  {statusPending && (
                    <span className="case-nav-status" aria-hidden="true">
                      <span className="skeleton-bar skeleton-bar--nav-status" />
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
