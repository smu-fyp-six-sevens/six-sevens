import { describe, expect, it } from 'vitest';
import { requestedDocumentName } from './requestedDocument';

describe('requestedDocumentName', () => {
  it.each([
    ['Company name change / registration letter from ACRA', 'ACRA'],
    ['Recent utility bill for Lim Hui Ling at current residential address', 'Utility Bill'],
    ['Original certified copy of certificate of incorporation with bank-verified UEN', 'Certificate of Incorporation'],
    ['Corrected and re-signed FATCA CRS declaration from the company', 'FATCA/CRS Declaration'],
    ['Purchase Order', 'Purchase Order'],
    ['A full, re-issued bank statement directly from the bank', 'Bank Statement'],
    ['Bank-issued statement for the same period', 'Bank Statement'],
    ['Bank-certified statement showing the closing balance', 'Bank Statement'],
    ['Official ACRA company profile', 'ACRA'],
    ['Bank Loan Statements or Bank Loan Repayment Schedules', 'Loan Statement'],
    ['Recent shareholder register for the company', 'Shareholder Register'],
    ['Recent register of members and directors', 'Register of Members and Directors'],
    ['Payslips for the last 3 months', 'Payslip'],
  ])('%s -> %s', (title, name) => {
    expect(requestedDocumentName(title)).toBe(name);
  });
});
