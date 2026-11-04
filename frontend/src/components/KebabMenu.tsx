import { useId, useRef, useState } from 'react';
import { useDismiss } from './useDismiss';

export interface KebabMenuItem {
  id: string;
  label: string;
  onSelect: () => void;
  disabled?: boolean;
  danger?: boolean;
}

interface KebabMenuProps {
  label: string;
  items: KebabMenuItem[];
  disabled?: boolean;
}

export default function KebabMenu({
  label,
  items,
  disabled = false,
}: KebabMenuProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLSpanElement>(null);
  const menuId = useId();

  useDismiss(rootRef, open, () => setOpen(false));

  return (
    <span className="kebab-menu" ref={rootRef}>
      <button
        type="button"
        className="kebab-menu-button"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        disabled={disabled}
        onClick={(event) => {
          event.stopPropagation();
          setOpen((current) => !current);
        }}
        onKeyDown={(event) => event.stopPropagation()}
      >
        <svg
          viewBox="0 0 24 24"
          width="18"
          height="18"
          aria-hidden="true"
          fill="currentColor"
        >
          <circle cx="12" cy="5" r="1.7" />
          <circle cx="12" cy="12" r="1.7" />
          <circle cx="12" cy="19" r="1.7" />
        </svg>
      </button>

      {open && (
        <span
          id={menuId}
          className="kebab-menu-list"
          role="menu"
          onClick={(event) => event.stopPropagation()}
          onKeyDown={(event) => event.stopPropagation()}
        >
          {items.map((item) => (
            <button
              key={item.id}
              type="button"
              role="menuitem"
              className={`kebab-menu-item${item.danger ? ' kebab-menu-item--danger' : ''}`}
              disabled={item.disabled}
              onClick={(event) => {
                event.stopPropagation();
                setOpen(false);
                item.onSelect();
              }}
            >
              {item.label}
            </button>
          ))}
        </span>
      )}
    </span>
  );
}
