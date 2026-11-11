// Every string in the v2 hook ("overwhelm": no app yet, just the pile).
// Names, teams and filenames are invented. Edit freely; "\n" is a line break you chose.

export type DocKind = "statement" | "letter" | "form" | "financials" | "id";

/** PDF windows that open on the officer's screen, in order. */
export const DOCS: { title: string; file: string; kind: DocKind; pages: number }[] = [
  { title: "Corporate Bank Statement", file: "Anchorpoint_BankStatement_Jan2026.pdf", kind: "statement", pages: 6 },
  { title: "Notice of Assessment", file: "Anchorpoint_NOA_YA2025.pdf", kind: "financials", pages: 2 },
  { title: "Certificate of Incumbency", file: "Cert_Incumbency_2025.pdf", kind: "letter", pages: 3 },
  { title: "Balance Sheet FY2025", file: "Financials_BalanceSheet_FY25.pdf", kind: "financials", pages: 4 },
  { title: "Facility Letter", file: "FacilityLetter_AIB_v2.pdf", kind: "letter", pages: 9 },
  { title: "Credit Bureau Report", file: "Credit_Bureau_Report_8027.pdf", kind: "statement", pages: 5 },
  { title: "FATCA / CRS Declaration", file: "FATCA_CRS_Form.pdf", kind: "form", pages: 2 },
  { title: "Director ID", file: "Director_ID_front.jpg", kind: "id", pages: 1 },
  { title: "Proof of Address", file: "UtilityBill_Oct2025.pdf", kind: "statement", pages: 2 },
  { title: "Company Constitution", file: "Constitution_2026.pdf", kind: "letter", pages: 24 },
  { title: "Payslip", file: "Payslip_Jan2026.pdf", kind: "form", pages: 1 },
  { title: "Corporate Bank Statement", file: "Anchorpoint_BankStatement_Feb2026.pdf", kind: "statement", pages: 7 },
  { title: "Source of Wealth", file: "SourceOfWealth_Declaration.pdf", kind: "letter", pages: 3 },
  { title: "Director ID", file: "Director_Passport.png", kind: "id", pages: 1 },
  { title: "Corporate Bank Statement", file: "Anchorpoint_BankStatement_Mar2026.pdf", kind: "statement", pages: 6 },
  { title: "Profit and Loss FY2025", file: "Financials_PnL_FY25.pdf", kind: "financials", pages: 3 },
];

/** Extra browser tabs that open between the PDFs: the "random related documents". */
export const EXTRA_TABS = [
  "Re: Anchorpoint loan docs",
  "Company registry search",
  "Credit memo DRAFT.docx",
  "Checklist v3 FINAL.xlsx",
  "Re: Re: missing statement",
  "Sanctions screening",
  "Fwd: updated financials",
  "Policy: KYC requirements",
  "Exchange rates",
  "Credit memo DRAFT (2).docx",
];

/** Pings from other teams, in order. `at` times are in timing.ts (OVERWHELM.pings). */
export const PINGS: { name: string; team: string; text: string; tint: string }[] = [
  { name: "Priya", team: "Sales Team", text: "Hey, have you reviewed the Anchorpoint docs yet?", tint: "#355f96" },
  { name: "Daniel", team: "Credit Risk", text: "How's the case going? Committee is at 3pm", tint: "#166d5f" },
  { name: "Mei Ling", team: "Client Services", text: "Customer emailed us again 😅", tint: "#c6820a" },
  { name: "Ops Team", team: "Operations", text: "2 more statements just came in, added to the folder", tint: "#6b778c" },
  { name: "Priya", team: "Sales Team", text: "Any update??", tint: "#355f96" },
  { name: "Aaron", team: "Compliance", text: "Need the KYC checks before sign-off", tint: "#b3402a" },
];

export const COPY2 = {
  title1: "Corporate loan\napplications need\nextensive\ndocumentation.",
  wayBefore: "W",
  wayAfter: "y",
  wayMaxA: 9,
  title2: "too many\ndocuments\nto review.",
  footnote: "For one case.",
  company: "Anchorpoint Insurance Brokers Pte. Ltd.",
};

// ---- v3 "buried" (full screen) -------------------------------------------------------
/** Pings in v3, before the final one. Times are OVERWHELM3.pings in timing.ts. */
export const PINGS3 = PINGS.filter((p) => p.text !== "Any update??");

/** The last ping: everything stops, it lands in the middle of the frame, and the shot holds. */
export const FINAL_PING = {
  name: "Priya",
  team: "Sales Team",
  text: "Customer's on the line again. Any update on Anchorpoint's loan status?",
  tint: "#355f96",
};

/** v3: how many "a"s "Waaaay" grows to. 28 runs the word across into the right side of the frame. */
export const WAY3_MAX_A = 28;
/** v3: how many of the PDF windows open while the word stretches (the rest open before it). */
export const WAY3_DOCS = 8;
