export const MAX_COMPANY_NAME_LENGTH = 100;

export function validateCompanyName(companyName: string): string | null {
  if (!companyName.trim()) {
    return 'Company name is required.';
  }

  if (companyName.length > MAX_COMPANY_NAME_LENGTH) {
    return `Company name must be ${MAX_COMPANY_NAME_LENGTH} characters or fewer.`;
  }

  return null;
}