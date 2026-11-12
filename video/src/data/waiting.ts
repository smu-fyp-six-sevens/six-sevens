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

// ---- v2 (default): silhouette avatars, roles only, the script line as the caption -------------
export const ROLES = {
  officer: { label: "Loan Officer", tint: "#355f96", bg: "#e4ebf4" },
  customer: { label: "Customer", tint: "#c6820a", bg: "#fbf0da" },
};

/** The script line, in two halves: the first lands with the officer, the second with the customer. */
export const CAPTION = {
  officer: "A loan officer may spend over an hour\nstaring at details on the screen,",
  customer: "while the loan customer anxiously waits\non the other end for any news of approval.",
};
