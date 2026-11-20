# Pending work

Open product and platform work only: delete an item once done. Updated 2026-11-18. How things are built today: `HOW_IT_WORKS.md`. Elsewhere: bugs (detection included), performance and security in `CODEBASE_AUDIT.md`; tests in `TEST_SUITE_AUDIT.md` and `PENDING_TESTS.md`; cloud work in `CLOUD_ARCHITECTURE_PLAN.md`.

## Security


## Product

- **Skip repeated runs** (decided 2026-11-01). *Issue:* "Run again" on an unchanged case spends ~12k tokens for the same answer. *Do:* show the last complete run when the fingerprint matches.

## Claude

- **Billed cost per role.** *Issue:* per-call cost is estimated from list prices, not the bill. *Do:* one Claude workspace and key per worker role; the Cost API then reports billed USD per workspace.
- **On-demand live check.** *Issue:* CI, the kind run and the load tests stub Claude, so an API-side break (like the `temperature` 400) isn't caught. *Do:* one tiny call per model, run by hand after an SDK or model change.

## Detection and the document pipeline

- **Cross-document category.** *Issue:* two documents disagreeing (e.g. share capital) is labelled `unsupported_claim`. *Do:* a `cross_document_variance` label (schema change).
- **Audit the "Other" checks:** `date_anomalies`, `metadata_analysis` and `image_manipulation` use the document type in places; mark each rule type-agnostic or type-keyed.
- **Model output gaps.** *Issue:* nothing checks an extracted value's type (text in a number field), and the binder (`field_checks/binder.py`) and `llm_classify.py` skip `validate`, so repairs aren't traced.

## Case analysis (design: `HOW_IT_WORKS.md`, Step 6)

- **Cancel the outdated run.** *Issue:* a replaced run finishes anyway and spends its calls; only its result is dropped (`superseded`).
- **The case analysis, what's left after the playbook (2026-11-17).** *Done:* cross-document measures and tools, the planner's candidates and case overview, open questions from the judge, more request rules, ranking (`HOW_IT_WORKS.md`, Step 6). *Open:* CPF against payroll and invoices against statement receipts as tools; a follow-up round when a verdict is inconclusive (decided against for now: it adds a Sonnet call); storing the loan request (amount, purpose) on the case so hypotheses can test scale. Measure with the case-analysis evals before and after (they spend tokens).
- **Debounce** (5–10 s) before an automatic start, for a burst of officer changes.
- **Dismissing a cross-document finding** (officer, with a reason; leaves the score, kept on record).
- **No follow-up round** in plan-then-judge: add one if evals show low recall.
- **Monitoring:** false-positive, dismissal and citation-discard rates, cost per case.
- **Evidence requests need rethinking.** *Issue:* a document can only lower the score, so "ask for everything" always looks best. *Ideas:* the smallest set that reaches a target level (code); score documents that could go either way; an agent that checks existing documents first. The score stays deterministic.

## Evals and verification (all on demand: they spend tokens)

- **Agent evals:** the sensibility checks are built. Open: the tests in `PENDING_TESTS.md` rows 29–31, then one real run per run kind (a payslip, a bank statement, a case) to see the checks on a live trace. Tune from those runs: the overconfidence gap (0.3); the expected-checks table (`evals/detection.py`: today only Content Validation's numerical checks on income documents and bank statements, and Digital Authenticity's metadata check); the field-name rules for dates and amounts (`evals/extraction.py`). A check that warns on most runs is noise: change or drop it. Extraction rerun records no `extraction` step, so its field checks don't run. No golden set, by decision.
- **Case analysis evals:** false positives on clean generated cases (Other documents included), recall on planted contradictions, false-positive parity across subgroups.
- **Extraction accuracy:** text-only vs second look, and code-read vs model-read statement transactions, on labelled statements and payslips.
- **Does officer feedback help?** Same cases with and without `finding_feedback`.
- **One real end-to-end run** (never done with real Claude): a bank statement and a payslip through the local app, including a second-look disagreement reaching Manual Review.
- **Check screens by eye:** low-confidence reasons, second-look tables in the trace, the adverse-media label.
- **Real bank statement layouts:** `statement_table.py` is tested on synthetic ones only.

## Performance

- **Opening a case by direct link waits in sequence** (case lookup, ticket, socket, first snapshot). *Do:* fetch the first snapshot over HTTP alongside the socket, and put the case's details in the snapshot.

## Cloud operations

- **Make Snyk blocking again** from 2026-11-21, when its quota resets: delete the two `continue-on-error: true` lines in `.github/workflows/ci-cd.yml`, and "temporarily non-blocking" from CLAUDE.md, README and `HOW_IT_WORKS.md` §9.
- **Frontend fully non-root:** `nginx-unprivileged` on 8080 (change `nginx.conf`, compose and the chart's `templates/frontend.yaml` port, with `runAsNonRoot`, together).
