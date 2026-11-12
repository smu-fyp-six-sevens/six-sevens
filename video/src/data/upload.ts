// Section 5 - "how it works", step 1. Script: "Start by uploading the full package. Documents are classified
// automatically and key fields are extracted in the background, even from scanned files."
// Files and types are case GLDB-02's from the portal's regression fixture (names neutralised); the scanned
// December statement is the film's own. "\n" is a line break you chose.

/** The "how it works" steps. Every product section shows them; the tracker moves on within a section too. */
export const STEPS = ["Upload", "Classify", "Extract", "Detect", "Score", "Finalise"] as const;

export const UPLOAD_COPY = {
  headline: "Upload the documents.",
  line1: "Every file is classified and its key fields read.",
  line2: "Even from scans.",
  /** On the stack the cursor drags in. */
  stackBadge: (n: number) => `${n} files`,
};

/** The package: file name and the type the portal classifies it as. */
export const PACKAGE: { file: string; type: string; scan?: boolean }[] = [
  { file: "POR_UtilityBill_20251028.jpg", type: "Proof of Address" },
  { file: "Passport_6086.png", type: "Passport" },
  { file: "Anchorpoint_BankStatement_Dec2025_scan.pdf", type: "Bank Statement", scan: true },
  { file: "AnchorpointInsuranceBrokers_BizFile_Officers_20251128.pdf", type: "Certificate of Incumbency" },
  { file: "Director_NRIC_front.jpg", type: "NRIC" },
  { file: "FATCA_Form_20250522.pdf", type: "FATCA/CRS Declaration" },
  { file: "Shareholder_SourceOfWealth.pdf", type: "Source of Wealth" },
  { file: "LoanOffer_8415_3.pdf", type: "List of Loans" },
  { file: "Anchorpoint_BankStatement_Jan2026.pdf", type: "Bank Statement" },
  { file: "BizFile_333996.pdf", type: "Certificate of Incorporation" },
  { file: "Anchorpoint_IRAS_NOA.pdf", type: "Notice of Assessment" },
  { file: "Credit_Bureau_Report_8027.pdf", type: "Credit Report" },
  { file: "FacilityLetter_AIB_2026-03-09_1.pdf", type: "List of Loans" },
  { file: "Director_NRIC_back.jpg", type: "NRIC" },
  { file: "LoanFacility_56439_2.pdf", type: "List of Loans" },
  { file: "AnchorpointInsuranceBrokers_Constitution_20260805.pdf", type: "Company Constitution" },
  { file: "Financials_BalanceSheet_617.pdf", type: "Source of Wealth" },
  { file: "Director_ProofAddr_BankStmt_2956.pdf", type: "Bank Statement" },
  { file: "Company_UtilityBill_45040.pdf", type: "Proof of Address" },
  { file: "Director_Payslip_Jan2026.pdf", type: "Payslip" },
];

/** The scanned December statement: what is printed on it (its closing balance is January's opening). */
export const DEC_STATEMENT = {
  file: "Anchorpoint_BankStatement_Dec2025_scan.pdf",
  title: "Harbourline Bank",
  company: "Anchorpoint Insurance Brokers Pte. Ltd.",
  account: "Account 0123-456789-001",
  period: "1 Dec 2025 - 31 Dec 2025",
  rows: [
    { date: "01/12", text: "Opening balance", amount: "58,400.00" },
    { date: "04/12", text: "Client receipts", amount: "72,500.00" },
    { date: "11/12", text: "Payroll", amount: "-38,900.00" },
    { date: "15/12", text: "Office rent", amount: "-12,500.00" },
    { date: "19/12", text: "Client receipts", amount: "58,900.00" },
    { date: "23/12", text: "Supplier payment", amount: "-42,150.00" },
  ],
  closingLabel: "Closing balance",
  closing: "96,250.00",
};

/** What the portal reads off it, in its Extracted Information card. `from` is where it sits on the page. */
export const SCAN_FIELDS: { name: string; value: string; confidence: string; from: "title" | "company" | "account" | "closing" }[] = [
  { name: "Bank Name", value: "Harbourline Bank", confidence: "0.97", from: "title" },
  { name: "Company Name", value: "Anchorpoint Insurance Brokers Pte. Ltd.", confidence: "0.97", from: "company" },
  { name: "Account Number", value: "0123-456789-001", confidence: "0.97", from: "account" },
  { name: "Statement Period", value: "1 Dec 2025 - 31 Dec 2025", confidence: "0.97", from: "account" },
  { name: "Ledger Balance", value: "96,250", confidence: "0.93", from: "closing" },
];

export const SCREENS_HOW = {
  documents: { src: "ui/how/documents.png", w: 1440, h: 900 },
  scan: { src: "ui/how/scan-top.png", w: 1440, h: 900 },
};
