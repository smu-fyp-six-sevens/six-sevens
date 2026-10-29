import { useEffect, useId, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { ErrorMessage } from './ErrorMessage';

export interface BreadcrumbMenuItem {
  key: string;
  to: string;
  // Shown in mono before the label (a case number).
  tag?: string;
  label: string;
  current?: boolean;
}

// One page of a long menu: `hasMore` adds a "load more" button that asks for the next page.
export interface BreadcrumbMenuPage {
  items: BreadcrumbMenuItem[];
  hasMore: boolean;
}

export interface Breadcrumb {
  label: string;
  to: string;
  // The page you're on (or the item it belongs to): bold, and not a link.
  current?: boolean;
  // Makes the crumb a switcher: hovering it (or clicking its caret) lists the other items at its
  // level (e.g. every case) to jump between, while its label still links back to `to`. Called when
  // the list opens, so it can fetch. A paged menu returns a page, and is called again with how many
  // items it already has when the officer asks for more.
  loadMenu?: (offset: number) => BreadcrumbMenuItem[] | BreadcrumbMenuPage | Promise<BreadcrumbMenuItem[] | BreadcrumbMenuPage>;
  menuLabel?: string;
  // What the load more button says, e.g. "Load more cases".
  loadMoreLabel?: string;
}

// Hover opens the list after a short pause (so passing over the bar doesn't flash it) and closes it
// shortly after the pointer leaves (so it can travel into the list). Only on devices that can hover.
const HOVER_OPEN_MS = 180;
const HOVER_CLOSE_MS = 250;
const canHover = () => typeof window !== 'undefined' && window.matchMedia?.('(hover: hover)').matches;

function CaretIcon() {
  return (
    <svg className="breadcrumb-caret" viewBox="0 0 12 12" width="10" height="10" aria-hidden="true">
      <path d="M2.5 4.5 6 8l3.5-3.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function CrumbMenu({ crumb }: { crumb: Breadcrumb }) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<BreadcrumbMenuItem[] | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const rootRef = useRef<HTMLSpanElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const menuId = useId();
  // Opened by hovering: it closes when the pointer leaves, and doesn't take keyboard focus.
  // A click pins it open until the next click outside or Escape.
  const openedByHoverRef = useRef(false);
  const hoverTimerRef = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearTimeout(hoverTimerRef.current), []);

  function handlePointerEnter() {
    window.clearTimeout(hoverTimerRef.current);
    if (open || !canHover()) return;
    hoverTimerRef.current = window.setTimeout(() => {
      openedByHoverRef.current = true;
      setOpen(true);
    }, HOVER_OPEN_MS);
  }

  function handlePointerLeave() {
    window.clearTimeout(hoverTimerRef.current);
    if (!openedByHoverRef.current) return;
    hoverTimerRef.current = window.setTimeout(() => setOpen(false), HOVER_CLOSE_MS);
  }

  function handleToggleClick() {
    window.clearTimeout(hoverTimerRef.current);
    if (open && openedByHoverRef.current) {
      // Already showing from the hover: the click pins it (and moves focus into it) rather than closing it.
      openedByHoverRef.current = false;
      focusCurrentItem();
      return;
    }
    openedByHoverRef.current = false;
    setOpen((current) => !current);
  }

  function focusCurrentItem() {
    const links = listRef.current?.querySelectorAll<HTMLAnchorElement>('a');
    const target = listRef.current?.querySelector<HTMLAnchorElement>('a[aria-current="page"]') ?? links?.[0];
    target?.focus();
    target?.scrollIntoView({ block: 'nearest' });
  }
  // Read through a ref so a parent re-render (a new crumb object) doesn't reload an open list.
  const loadMenuRef = useRef(crumb.loadMenu);
  loadMenuRef.current = crumb.loadMenu;

  useEffect(() => {
    const loadMenu = loadMenuRef.current;
    if (!open || !loadMenu) return;

    let cancelled = false;
    setError(null);
    Promise.resolve(loadMenu(0))
      .then((loaded) => {
        if (cancelled) return;
        const page = Array.isArray(loaded) ? { items: loaded, hasMore: false } : loaded;
        setItems(page.items);
        setHasMore(page.hasMore);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load the list.');
      });

    return () => {
      cancelled = true;
    };
  }, [open]);

  async function handleLoadMore() {
    const loadMenu = loadMenuRef.current;
    if (!loadMenu || !items || loadingMore) return;
    const firstNew = items.length;
    setLoadingMore(true);
    try {
      const loaded = await loadMenu(firstNew);
      const page = Array.isArray(loaded) ? { items: loaded, hasMore: false } : loaded;
      setItems([...items, ...page.items]);
      setHasMore(page.hasMore);
      // Keep keyboard focus moving down the list: onto the first of the new items.
      requestAnimationFrame(() => listRef.current?.querySelectorAll<HTMLAnchorElement>('a')[firstNew]?.focus());
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Could not load more.');
    } finally {
      setLoadingMore(false);
    }
  }

  useEffect(() => {
    if (!open) return;

    function onPointerDown(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }

    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [open]);

  // Opened by click or keyboard: start on the current item (scrolled into view), or the first one.
  // Opened by hover: only scroll it into view, so focus stays where it was.
  const listShown = open && items !== null;
  useEffect(() => {
    if (!listShown) return;
    if (openedByHoverRef.current) {
      listRef.current?.querySelector('a[aria-current="page"]')?.scrollIntoView({ block: 'nearest' });
      return;
    }
    focusCurrentItem();
  }, [listShown]);

  function handleKeyDown(event: React.KeyboardEvent) {
    if (event.key === 'Escape') {
      setOpen(false);
      openedByHoverRef.current = false;
      rootRef.current?.querySelector<HTMLButtonElement>('button')?.focus();
      return;
    }

    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    const links = Array.from(listRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
    if (!links.length) return;
    event.preventDefault();
    const index = links.indexOf(document.activeElement as HTMLElement);
    const step = event.key === 'ArrowDown' ? 1 : -1;
    links[(index + step + links.length) % links.length].focus();
  }

  return (
    <span
      className="breadcrumb-menu"
      ref={rootRef}
      onKeyDown={handleKeyDown}
      onMouseEnter={handlePointerEnter}
      onMouseLeave={handlePointerLeave}
    >
      {crumb.current ? (
        // The page you're on: the label has nowhere to go, so the whole crumb opens the list.
        <button
          type="button"
          className="breadcrumb-switcher breadcrumb-switcher--current"
          aria-label={crumb.menuLabel ? `${crumb.label}: ${crumb.menuLabel}` : crumb.label}
          aria-haspopup="menu"
          aria-expanded={open}
          aria-controls={open ? menuId : undefined}
          aria-current="page"
          title={crumb.label}
          onClick={handleToggleClick}
        >
          <span className="breadcrumb-switcher-label">{crumb.label}</span>
          <CaretIcon />
        </button>
      ) : (
        // Above the page you're on: the label goes back to it, the caret opens the list.
        <span className={`breadcrumb-split${open ? ' breadcrumb-split--open' : ''}`}>
          <Link to={crumb.to} className="breadcrumb-split-link" title={crumb.label}>
            {crumb.label}
          </Link>
          <button
            type="button"
            className="breadcrumb-split-caret"
            aria-label={crumb.menuLabel ? `${crumb.label}: ${crumb.menuLabel}` : `Other items like ${crumb.label}`}
            aria-haspopup="menu"
            aria-expanded={open}
            aria-controls={open ? menuId : undefined}
            onClick={handleToggleClick}
          >
            <CaretIcon />
          </button>
        </span>
      )}

      {open && (
        <ul id={menuId} role="menu" className="breadcrumb-list" ref={listRef}>
          {error ? (
            <li className="breadcrumb-list-note" role="none"><ErrorMessage inline message={error} /></li>
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
          {items && items.length > 0 && hasMore && !error && (
            <li role="none">
              <button
                type="button"
                role="menuitem"
                className="breadcrumb-list-more"
                disabled={loadingMore}
                onClick={handleLoadMore}
              >
                {loadingMore ? 'Loading…' : (crumb.loadMoreLabel ?? 'Load more')}
              </button>
            </li>
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
