// How a login is named in the header. Hardcoded on purpose: edit this list when AUTH_USERS
// (backend/.env) gains an account. A username not listed here is shown as it is.
const OFFICER_NAMES: Record<string, string> = {
  'greenlink-tester': 'J. Tan',
  'demo': 'K. Lim',
  'bob': 'B. Smith',
  'carol': 'C. Wong',
};

export function officerName(username: string): string {
  return OFFICER_NAMES[username] ?? username;
}

export function officerLabel(username: string | null): string {
  if (!username) return 'Loan Officer';

  return `Loan Officer · ${officerName(username)}`;
}
