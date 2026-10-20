import { useEffect, useId, useRef, useState } from 'react';
import { Link } from 'react-router-dom';

export interface BreadcrumbMenuItem {
  key: string;
  to: string;
  // Shown in mono before the label (a case number).
  tag?: string;
  label: string;
  current?: boolean;
}

export interface Breadcrumb {
  label: string;
  to: string;
  // The page you're on (or the item it belongs to): bold, and not a link.
  current?: boolean;
  // Makes the crumb a switcher: clicking it lists the other items at its level (e.g. every
  // case) to jump between. Called when the list opens, so it can fetch.
  loadMenu?: () => BreadcrumbMenuItem[] | Promise<BreadcrumbMenuItem[]>;
  menuLabel?: string;
}

function CrumbMenu({ crumb }: { crumb: Breadcrumb }) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<BreadcrumbMenuItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const rootRef = useRef<HTMLSpanElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const menuId = useId();
  // Read through a ref so a parent re-render (a new crumb object) doesn't reload an open list.
  const loadMenuRef = useRef(crumb.loadMenu);
  loadMenuRef.current = crumb.loadMenu;

  useEffect(() => {
    const loadMenu = loadMenuRef.current;
    if (!open || !loadMenu) return;

    let cancelled = false;
    setError(null);
    Promise.resolve(loadMenu())
      .then((loaded) => {
        if (!cancelled) setItems(loaded);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load the list.');
      });

    return () => {
      cancelled = true;
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;

    function onPointerDown(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }

    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [open]);

  // Start on the current item (scrolled into view), or the first one.
  useEffect(() => {
    if (!open || !items) return;
    const links = listRef.current?.querySelectorAll<HTMLAnchorElement>('a');
    const target = listRef.current?.querySelector<HTMLAnchorElement>('a[aria-current="page"]') ?? links?.[0];
    target?.focus();
    target?.scrollIntoView({ block: 'nearest' });
  }, [open, items]);

  function handleKeyDown(event: React.KeyboardEvent) {
    if (event.key === 'Escape') {
      setOpen(false);
      rootRef.current?.querySelector<HTMLButtonElement>('button')?.focus();
      return;
    }

    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    const links = Array.from(listRef.current?.querySelectorAll<HTMLAnchorElement>('a') ?? []);
    if (!links.length) return;
    event.preventDefault();
    const index = links.indexOf(document.activeElement as HTMLAnchorElement);
    const step = event.key === 'ArrowDown' ? 1 : -1;
    links[(index + step + links.length) % links.length].focus();
  }

  return (
    <span className="breadcrumb-menu" ref={rootRef} onKeyDown={handleKeyDown}>
      <button
        type="button"
        className={`breadcrumb-switcher${crumb.current ? ' breadcrumb-switcher--current' : ''}`}
        aria-label={crumb.menuLabel ? `${crumb.label}: ${crumb.menuLabel}` : crumb.label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-current={crumb.current ? 'page' : undefined}
        title={crumb.label}
        onClick={() => setOpen((current) => !current)}
      >
        <span className="breadcrumb-switcher-label">{crumb.label}</span>
        <svg className="breadcrumb-caret" viewBox="0 0 12 12" width="10" height="10" aria-hidden="true">
          <path d="M2.5 4.5 6 8l3.5-3.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open && (
        <ul id={menuId} role="menu" className="breadcrumb-list" ref={listRef}>
          {error ? (
            <li className="breadcrumb-list-note" role="none">{error}</li>
          ) : !items ? (
            <li className="breadcrumb-list-note" role="none">Loading…</li>
          ) : items.length === 0 ? (
            <li className="breadcrumb-list-note" role="none">Nothing here yet</li>
          ) : (
            items.map((item) => (
              <li key={item.key} role="none">
                <Link
                  role="menuitem"
                  to={item.to}
                  className="breadcrumb-list-item"
                  aria-current={item.current ? 'page' : undefined}
                  title={item.tag ? `${item.tag} ${item.label}` : item.label}
                  onClick={() => setOpen(false)}
                >
                  {item.tag && <span className="breadcrumb-list-tag">{item.tag}</span>}
                  <span className="breadcrumb-list-label">{item.label}</span>
                </Link>
              </li>
            ))
          )}
        </ul>
      )}
    </span>
  );
}

export default function Breadcrumbs({ crumbs }: { crumbs: Breadcrumb[] }) {
  return (
    <nav className="breadcrumbs" aria-label="Breadcrumb">
      <ol>
        {crumbs.map((crumb) => (
          <li key={crumb.to}>
            {crumb.loadMenu ? (
              <CrumbMenu crumb={crumb} />
            ) : crumb.current ? (
              <span className="breadcrumb-current" aria-current="page" title={crumb.label}>
                {crumb.label}
              </span>
            ) : (
              <Link to={crumb.to} className="breadcrumb-link">
                {crumb.label}
              </Link>
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
}
