// Every string on screen in the hook. The filenames are the real package of case GLDB-02
// (Anchorpoint Insurance Brokers) from the portal's regression fixture, in upload order.
// UI strings are verbatim from the portal (video/public/ui/*.png).

export const CASE = {
  number: "GLDB-02",
  company: "Anchorpoint Insurance Brokers",
};

export const UI = {
  brand: "GLDB",
  brandRest: "Fraud Review",
  sectionTitle: "Upload supporting documents",
  dropTitle: "Drag files here or",
  dropLink: "browse",
  dropHint: "PDF · PNG · JPG — up to 25MB each",
  rowMeta: "Ready to upload",
  rowBadge: "Ready",
  selected: (n: number) => `${n} ${n === 1 ? "file" : "files"} selected`,
  button: (n: number) => `Upload ${n} ${n === 1 ? "file" : "files"}`,
};

export const FILES: string[] = [
  "POR_UtilityBill_20251028.jpg",
  "Passport_6086.png",
  "MuhammadD_Wealth_Statement_scan.pdf",
  "AnchorpointInsuranceBrokers_BizFile_Officers_20251128.pdf",
  "MuhammadD_NRIC_front.jpg",
  "FATCA_Form_20250522.pdf",
  "SimS_SourceOfWealth.pdf",
  "LoanOffer_8415_3.pdf",
  "Statement_128262.pdf",
  "BizFile_333996.pdf",
  "Anchorpoint_IRAS_NOA.pdf",
  "Credit_Bureau_Report_8027.pdf",
  "FacilityLetter_AIB_2026-03-09_1.pdf",
  "MuhammadD_NRIC_back.jpg",
  "LoanFacility_56439_2.pdf",
  "AnchorpointInsuranceBrokers_Constitution_20260805.pdf",
  "Financials_BalanceSheet_617.pdf",
  "MuhammadD_ProofAddr_BankStmt_2956.pdf",
  "Company_UtilityBill_45040.pdf",
  "MuhammadD_Payslip_Jan2026.pdf",
];

// Display copy. "\n" is a line break you chose; lines never wrap on their own.
export const COPY = {
  // Scene 1
  packageTitle: "Corporate loan\napplications need\nextensive documentation.",
  // Scene 2. The "way" word stretches from "Way" to WAY_MAX_A letters of "a".
  kicker: `Case ${CASE.number} · ${CASE.company}`,
  wayBefore: "W",
  wayAfter: "y too many",
  wayMaxA: 9,
  tooManyLine2: "documents to review.",
  footnote: `${FILES.length} files for a single case.`,
};
