import { CASE_SECTIONS, isCaseSection, menuSection } from '../pages/upload/caseSections';

const APP = 'GLAD Portal';

const PAGES: Record<string, string> = {
  '/login': 'Sign In',
  '/dashboard': 'Dashboard',
  '/agents': 'AI Agents',
  '/create-case': 'New Case',
};

// The browser tab's title for a URL: the app's name on its own pages, the case id on a case's.
export function pageTitle(pathname: string, search: string): string {
  const path = pathname.replace(/\/+$/, '') || '/';
  const caseMatch = /^\/cases\/([^/]+)(\/documents\/[^/]+)?$/.exec(path);
  if (caseMatch) {
    const caseId = decodeURIComponent(caseMatch[1]);
    if (caseMatch[2]) return `${caseId} | Document`;
    const id = new URLSearchParams(search).get('section');
    const section = CASE_SECTIONS.find((item) => item.id === (isCaseSection(id) ? menuSection(id) : 'summary'));
    return `${caseId} | ${section?.label ?? 'Case Summary'}`;
  }
  const page = PAGES[path];
  return page ? `${APP} | ${page}` : APP;
}
