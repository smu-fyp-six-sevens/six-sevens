import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import { Icon } from '../../components/Icon';
import { useDismiss } from '../../components/useDismiss';
import { formatCheck } from '../../utils/checkLabels';

// A document's checks under the specialist that runs them (backend agent_registry), so the list reads in three short
// groups rather than one long one. Anything else (Other, a cross-document category) follows, ungrouped.
const CHECK_GROUPS: [string, string[]][] = [
  ['Layout & Rendering', ['hidden_text_tool', 'font_consistency_tool', 'layout_anomalies_tool', 'image_tampering_tool', 'image_manipulation_tool']],
  ['Content Validation', ['spelling_tool', 'date_consistency_tool', 'page_consistency_tool', 'numerical_checks_tool', 'external_validation_tool']],
  ['Digital Authenticity', ['metadata_tool', 'ai_generation_tool', 'prompt_injection_tool']],
];

function checkGroups(checks: string[]): [string | null, string[]][] {
  const grouped = CHECK_GROUPS.map(([group, members]): [string, string[]] => [
    group,
    members.filter((check) => checks.includes(check)),
  ]).filter(([, members]) => members.length > 0);
  const placed = new Set(grouped.flatMap(([, members]) => members));
  const rest = checks.filter((check) => !placed.has(check));
  return [...grouped, ...(rest.length > 0 ? [[null, rest] as [null, string[]]] : [])];
}

// The list is about this tall; below that much room under the field, it opens upwards instead.
const LIST_HEIGHT = 320;

// The check that should have caught a missed issue: a field that opens a list of the checks, grouped by specialist,
// the current one ticked. Arrow keys move through it, Enter or a click chooses, Escape closes it (not the dialog).
export function CheckPicker({
  id,
  checks,
  value,
  onChange,
}: {
  id?: string;
  checks: string[];
  value: string;
  onChange: (check: string) => void;
}) {
  const listId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const optionRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const [open, setOpen] = useState(false);
  const [upwards, setUpwards] = useState(false);
  const groups = checkGroups(checks);
  const ordered = groups.flatMap(([, members]) => members);

  useDismiss(rootRef, open, () => setOpen(false));

  // Opening focuses the current choice, or the first check.
  useEffect(() => {
    if (open) optionRefs.current[Math.max(ordered.indexOf(value), 0)]?.focus();
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  function toggle() {
    if (!open) {
      const box = triggerRef.current?.getBoundingClientRect();
      setUpwards(box ? window.innerHeight - box.bottom < LIST_HEIGHT && box.top > window.innerHeight - box.bottom : false);
    }
    setOpen((isOpen) => !isOpen);
  }

  function close() {
    setOpen(false);
    triggerRef.current?.focus();
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (!open) return;
    if (event.key === 'Escape') {
      // The review dialog closes on Escape too: this one only closes the list.
      event.preventDefault();
      event.stopPropagation();
      close();
      return;
    }
    const moves: Record<string, (current: number) => number> = {
      ArrowDown: (current) => (current + 1) % ordered.length,
      ArrowUp: (current) => (current - 1 + ordered.length) % ordered.length,
      Home: () => 0,
      End: () => ordered.length - 1,
    };
    if (!moves[event.key]) return;
    event.preventDefault();
    const current = optionRefs.current.findIndex((item) => item === document.activeElement);
    optionRefs.current[moves[event.key](Math.max(current, 0))]?.focus();
  }

  let index = -1;
  return (
    <div ref={rootRef} className={`check-picker${upwards ? ' check-picker--up' : ''}`} onKeyDown={onKeyDown}>
      <button
        ref={triggerRef}
        id={id}
        type="button"
        className={`check-picker-trigger${value ? '' : ' check-picker-trigger--empty'}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        onClick={toggle}
      >
        <span className="check-picker-value">{value ? formatCheck(value) : 'Choose a Check…'}</span>
        <svg className="check-picker-chevron" viewBox="0 0 12 8" aria-hidden="true">
          <path d="m1 1.5 5 5 5-5" />
        </svg>
      </button>

      {open && (
        <div id={listId} className="check-picker-list" role="listbox" aria-label="Checks">
          {groups.map(([group, members]) => (
            <div key={group ?? 'other'} role="group" aria-label={group ?? 'Other'} className="check-picker-group">
              {group && <div className="check-picker-heading" aria-hidden="true">{group}</div>}
              {members.map((check) => {
                index += 1;
                const position = index;
                const selected = check === value;
                return (
                  <button
                    key={check}
                    ref={(element) => {
                      optionRefs.current[position] = element;
                    }}
                    type="button"
                    role="option"
                    aria-selected={selected}
                    className="check-picker-option"
                    onClick={() => {
                      onChange(check);
                      close();
                    }}
                  >
                    <span>{formatCheck(check)}</span>
                    {selected && <Icon name="check" size={14} className="check-picker-tick" />}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
