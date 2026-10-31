export const MAX_COMPANY_NAME_LENGTH = 100;
// The character count appears once a company name reaches this length.
export const COMPANY_NAME_COUNT_FROM = 80;

export function validateCompanyName(companyName: string): string | null {
  if (!companyName.trim()) {
    return 'Company name is required.';
  }

  if (companyName.length > MAX_COMPANY_NAME_LENGTH) {
    return `Company name must be ${MAX_COMPANY_NAME_LENGTH} characters or fewer.`;
  }

  return null;
}