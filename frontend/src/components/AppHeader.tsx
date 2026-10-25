import { useEffect, useId, useRef, useState } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import type { CaseRecord } from '../App';
import { clearToken, getUsername } from '../lib/api';
import { officerLabel } from '../lib/officers';
import { searchCases } from '../services/uploadService';
import { toCaseRecord } from '../utils/caseRecord';
import { caseNumberLabel, findExactCase } from '../utils/caseSearch';
import BrandLink from './BrandLink';

const NAV_LINKS = [
  { to: '/cases', label: 'Case Queue' },
  { to: '/dashboard', label: 'Dashboard' },
  { to: '/settings', label: 'Settings' },
];

const MAX_SUGGESTIONS = 6;
// Typing waits this long before asking the server, so a burst of keystrokes is one request.
const SEARCH_DELAY_MS = 200;

interface SearchResults {
  query: string;
  items: CaseRecord[];
  total: number;
}

// Jump-to-case search (an ARIA combobox): typing lists matching cases to open directly,
// and the last option shows every match in the case queue (/cases?q=…). The server finds and ranks
// the matches (exact number first, then names and numbers starting with the query, then the rest).
function HeaderSearch() {
  const navigate = useNavigate();
  const listId = useId();
  const [value, setValue] = useState('');
  const [isOpen, setIsOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [results, setResults] = useState<SearchResults | null>(null);
  const term = value.trim();
  const query = term.toLowerCase();

  useEffect(() => {
    if (!term) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      searchCases(term, MAX_SUGGESTIONS, controller.signal)
        .then((result) => setResults({ query: term.toLowerCase(), items: result.items.map(toCaseRecord), total: result.total }))
        .catch((err: unknown) => {
          if (err instanceof DOMException && err.name === 'AbortError') return;
          setResults({ query: term.toLowerCase(), items: [], total: 0 });
        });
    }, SEARCH_DELAY_MS);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [term]);

  // Results of an earlier query don't count: until the current one answers, the list says so.
  const current = results !== null && results.query === query ? results : null;
  const suggestions = current?.items ?? [];
  const total = current?.total ?? 0;
  const isSearching = query !== '' && current === null;

  // The suggestions, then "See all results" as the last option (only when something matched).
  const hasShowAll = total > 0;
  const optionCount = suggestions.length + (hasShowAll ? 1 : 0);
  const showList = isOpen && query !== '';
  const optionId = (index: number) => `${listId}-option-${index}`;

  function reset() {
    setValue('');
    setIsOpen(false);
    setActiveIndex(-1);
  }

  function openCase(caseRecord: CaseRecord) {
    reset();
    navigate(`/cases/${caseRecord.caseNumber ?? caseRecord.id}`, { state: { caseRecord } });
  }

  function showAll() {
    const term = value.trim();
    reset();
    navigate(`/cases?q=${encodeURIComponent(term)}`);
  }

  function choose(index: number) {
    if (index < suggestions.length) openCase(suggestions[index]);
    else showAll();
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      if (!optionCount) return;
      event.preventDefault();
      setIsOpen(true);
      const step = event.key === 'ArrowDown' ? 1 : -1;
      setActiveIndex((current) => (current + step + optionCount) % optionCount);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      if (!query) return;
      // A highlighted option wins; otherwise an exact case number opens, anything else lists all matches.
      if (showList && activeIndex >= 0) {
        choose(activeIndex);
      } else {
        const exact = current ? findExactCase(current.items, query) : undefined;
        if (exact) openCase(exact);
        else showAll();
      }
    } else if (event.key === 'Escape') {
      if (showList) setIsOpen(false);
      else reset();
    }
  }

  return (
    <div className="header-search">
      <label className="search-box">
        <svg className="search-icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="11" cy="11" r="7" />
          <path d="m20 20-3.5-3.5" />
        </svg>

        <input
          role="combobox"
          aria-label="Search case number or company name"
          aria-autocomplete="list"
          aria-expanded={showList}
          aria-controls={listId}
          aria-activedescendant={showList && activeIndex >= 0 ? optionId(activeIndex) : undefined}
          value={value}
          onChange={(event) => {
            setValue(event.target.value);
            setIsOpen(true);
            setActiveIndex(-1);
          }}
          onFocus={() => setIsOpen(true)}
          onBlur={() => setIsOpen(false)}
          onKeyDown={handleKeyDown}
          placeholder="Search cases"
        />
      </label>

      {showList && (
        // mousedown is prevented so the input keeps focus (and the list stays open) until the click lands.
        <ul id={listId} role="listbox" className="header-search-list" onMouseDown={(event) => event.preventDefault()}>
          {suggestions.length === 0 && (
            <li className="header-search-empty">
              {isSearching ? 'Searching…' : `No cases match “${value.trim()}”`}
            </li>
          )}

          {suggestions.map((item, index) => (
            <li
              key={item.id}
              id={optionId(index)}
              role="option"
              aria-selected={index === activeIndex}
              className="header-search-option"
              onMouseEnter={() => setActiveIndex(index)}
              onClick={() => openCase(item)}
            >
              <span className="header-search-number">{caseNumberLabel(item)}</span>
              <span className="header-search-name" title={item.applicant}>{item.applicant}</span>
            </li>
          ))}

          {hasShowAll && (
            <li
              id={optionId(suggestions.length)}
              role="option"
              aria-selected={activeIndex === suggestions.length}
              className="header-search-option header-search-all"
              onMouseEnter={() => setActiveIndex(suggestions.length)}
              onClick={showAll}
            >
              See all {total} result{total === 1 ? '' : 's'} in case queue →
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

// The officer's name opens a small menu; sign out lives in it.
function AccountMenu() {
  const menuId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const username = getUsername();

  useEffect(() => {
    if (!isOpen) return;

    function closeOnOutsideClick(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setIsOpen(false);
    }

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') setIsOpen(false);
    }

    document.addEventListener('mousedown', closeOnOutsideClick);
    document.addEventListener('keydown', closeOnEscape);

    return () => {
      document.removeEventListener('mousedown', closeOnOutsideClick);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [isOpen]);

  return (
    <div className="officer-tag" ref={rootRef}>
      <button
        type="button"
        className="officer-trigger"
        aria-label="Account menu"
        aria-haspopup="menu"
        aria-expanded={isOpen}
        aria-controls={isOpen ? menuId : undefined}
        onClick={() => setIsOpen((open) => !open)}
      >
        <span className="officer-avatar" aria-hidden="true">{(username ?? '?').charAt(0).toUpperCase()}</span>
        <span className="officer-name">{officerLabel(username)}</span>
        <svg className="officer-caret" viewBox="0 0 10 6" aria-hidden="true">
          <path d="m1 1 4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {isOpen && (
        <div id={menuId} className="officer-menu" role="menu">
          <div className="officer-menu-name">{officerLabel(username)}</div>
          <button
            type="button"
            role="menuitem"
            className="officer-menu-item"
            onClick={() => {
              clearToken();
              window.location.assign('/login');
            }}
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}

export default function AppHeader() {
  const navigate = useNavigate();

  return (
    <header className="app-header">
      <BrandLink />

      <nav className="app-nav" aria-label="Main">
        {NAV_LINKS.map((link) => (
          <NavLink
            key={link.to}
            to={link.to}
            className="app-nav-link"
          >
            {link.label}
          </NavLink>
        ))}
      </nav>

      <div className="app-header-right">
        <div className="header-tools">
          <HeaderSearch />

          <button type="button" className="header-new-case" aria-label="New case" title="New case" onClick={() => navigate('/create-case')}>
            <svg viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M8 3v10M3 8h10" />
            </svg>
          </button>
        </div>

        <AccountMenu />
      </div>
    </header>
  );
}
