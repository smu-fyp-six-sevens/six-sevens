# Agent refinements: what's left

Branch `scrum-327-agent-refinements`. The code in the original handover is done. Read `CLAUDE.md` first. Delete this file once the items below are done.

## Needs the user's OK

- **Does `confidence_missing` fire on many fields in practice?** From the code it fires only when the model skips the confidence wrapper. Measuring it means reading `field_provenance` and the second-look fields from stored `langgraph_trace` rows.

## Not yet verified

- **A real run.** Nothing here has run with real Claude calls or from upload to detection. Worth one bank statement and one payslip through the local app: the second look, a disagreement reaching Manual Review, and the code-read transactions with their counterparties.
- **The screens, by eye.** The reason under each low-confidence field, the second-look tables in the agent trace, and the adverse-media label.
- **Real bank statements.** `statement_table.py` is only tested on the synthetic statements. It works out columns from each document's own header and sends anything unclear to the usual model read, but a real layout could still pass its checks with a column misread.

## Tracked elsewhere

- The on-demand accuracy eval, which now also covers code-read transactions, is in `PENDING_WORK.md`.
