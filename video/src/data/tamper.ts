// Section 3 - "Even worse, when bad actors are involved, smaller anomalies are even harder to catch."
// A bad actor quietly edits the closing balance on a bank statement; then the statement disappears
// into the case's pile. Everything here is invented. "\n" is a line break you chose.

export const CAPTION3 = {
  line1: "Even worse, when bad actors are involved,",
  line2: "smaller anomalies are even harder to catch.",
};

export const BAD_ACTOR = { label: "Bad Actor", tint: "#b3402a", bg: "#fbe8e2" };

export const STATEMENT = {
  file: "Anchorpoint_BankStatement_Jan2026.pdf",
  title: "Corporate Bank Statement",
  company: "Anchorpoint Insurance Brokers Pte. Ltd.",
  account: "Account 0123-456789-001",
  period: "1 Jan 2026 - 31 Jan 2026",
  rows: [
    { date: "01/01", text: "Opening balance", amount: "96,250.00" },
    { date: "05/01", text: "Client receipts", amount: "148,300.00" },
    { date: "12/01", text: "Payroll", amount: "-38,900.00" },
    { date: "15/01", text: "Office rent", amount: "-12,500.00" },
    { date: "22/01", text: "Supplier payment", amount: "-24,750.00" },
    { date: "29/01", text: "Client receipts", amount: "14,000.00" },
  ],
  closingLabel: "Closing balance",
  /** The real figure, and what the bad actor types over it (two digits swapped). */
  before: "182,400.00",
  after: "812,400.00",
};
