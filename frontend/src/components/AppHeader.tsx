import { useId, useMemo, useState } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import type { CaseRecord } from '../App';
import { useCases } from '../casesContext';
import { clearToken, getUsername } from '../lib/api';
import { officerLabel } from '../lib/officers';
import { caseNumberLabel, findExactCase, matchesCaseQuery } from '../utils/caseSearch';
import BrandLink from './BrandLink';

const NAV_LINKS = [
  { to: '/cases', label: 'Case queue' },
  { to: '/dashboard', label: 'Dashboard' },
  { to: '/settings', label: 'Settings' },
];

const MAX_SUGGESTIONS = 6;

// Exact case number first, then names/numbers starting with the query, then the rest.
function rankMatch(caseRecord: CaseRecord, query: string): number {
  const number = caseNumberLabel(caseRecord).toLowerCase();
  const name = caseRecord.applicant.toLowerCase();
  if (number === query) return 0;
  if (number.startsWith(query) || name.startsWith(query)) return 1;
  return 2;
}

// Jump-to-case search (an ARIA combobox): typing lists matching cases to open directly,
// and the last option shows every match in the case queue (/cases?q=…).
function HeaderSearch() {
  const cases = useCases();
  const navigate = useNavigate();
  const listId = useId();
  const [value, setValue] = useState('');
  const [isOpen, setIsOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const query = value.trim().toLowerCase();

  const { suggestions, total } = useMemo(() => {
    if (!query) return { suggestions: [], total: 0 };
    const matching = cases.filter((item) => matchesCaseQuery(item, query));
    const ranked = matching
      .map((item, index) => ({ item, index, rank: rankMatch(item, query) }))
      .sort((a, b) => a.rank - b.rank || a.index - b.index)
      .map(({ item }) => item);
    return { suggestions: ranked.slice(0, MAX_SUGGESTIONS), total: matching.length };
  }, [cases, query]);

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
    navigate(`/cases/${caseRecord.caseNumber ?? caseRecord.id}`);
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
        const exact = findExactCase(cases, query);
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
        <span aria-hidden="true">⌕</span>

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
            <li className="header-search-empty">No cases match “{value.trim()}”</li>
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

export default function AppHeader() {
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
        <HeaderSearch />

        <div className="officer-tag">
          <span className="officer-name">{officerLabel(getUsername())}</span>
          <button
            type="button"
            className="signout-btn"
            onClick={() => {
              clearToken();
              window.location.assign('/login');
            }}
          >
            Sign out
          </button>
        </div>
      </div>
    </header>
  );
}
