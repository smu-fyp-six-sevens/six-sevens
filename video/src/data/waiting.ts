// Section 2 - "A loan officer may spend over an hour staring at details on the screen, while the loan
// customer anxiously waits on the other end for any news of approval."
// People are invented. "\n" is a line break you chose.

export const OFFICER = {
  initials: "SN",
  name: "Sarah Ng",
  role: "Loan Officer",
  tint: "#355f96",
  /** "Reviewing page {n} of {total}": n creeps from pageFrom to pageTo over the hour. */
  pageFrom: 14,
  pageTo: 19,
  pageTotal: 212,
};

export const CUSTOMER = {
  initials: "JK",
  name: "Jason Koh",
  role: "Director, Anchorpoint Insurance Brokers",
  tint: "#c6820a",
  /** "Checked inbox {n}×": n climbs from checksFrom to checksTo over the hour. */
  checksFrom: 1,
  checksTo: 14,
};

export const CLOCK = {
  /** Minutes after midnight. 9:02 to 10:14. */
  from: 9 * 60 + 2,
  to: 10 * 60 + 14,
};

export const COPY_WAIT = {
  line1: "Over an hour on the details,",
  line2: "while the customer waits for news.",
  officerStatus: (page: number, total: number) => `Reviewing page ${page} of ${total}`,
  customerStatus: (n: number) => `Checked inbox ${n}×`,
};
