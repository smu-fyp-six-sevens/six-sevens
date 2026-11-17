# Pending work

Open product and platform work only: delete an item once done. Updated 2026-11-17. How things are built today: `HOW_IT_WORKS.md`. Elsewhere: bugs (detection included), performance and security in `CODEBASE_AUDIT.md`; tests in `TEST_SUITE_AUDIT.md` and `PENDING_TESTS.md`; cloud work in `CLOUD_ARCHITECTURE_PLAN.md`.

## Handover documentation

- **CPF rates each January.** *Issue:* the 2027 CPF table is a placeholder (2026's rates), so 2027 payslips skip the CPF checks unless `CPF_USE_PROVISIONAL_RATES` is on. CPF publishes new rates and ceilings every year, so GLDB's team will need to repeat this. *Do:* when CPF publishes 2027, replace the five 2027 rows and the ceiling in `fraud_detection/external_validation/sg_reference/cpf.py` and remove the date from `PROVISIONAL_RATE_TABLES`; write the yearly steps into the handover documentation.

## Security

- **Login token in `localStorage`.** *Issue:* the 7-day token sits in `localStorage`, so an injected script could read it (the CSP now makes that much harder). *Do:* a shorter token (`TOKEN_TTL_SECONDS` in `auth.py`, for example 12 hours).

## Product

- **Skip repeated runs** (decided 2026-11-01). *Issue:* "Run again" on an unchanged case spends ~12k tokens for the same answer. *Do:* show the last complete run when the fingerprint matches.

## Claude

- **Billed cost per role.** *Issue:* per-call cost is estimated from list prices, not the bill. *Do:* one Claude workspace and key per worker role; the Cost API then reports billed USD per workspace.
- **On-demand live check.** *Issue:* CI, the kind run and the load tests stub Claude, so an API-side break (like the `temperature` 400) isn't caught. *Do:* one tiny call per model, run by hand after an SDK or model change.

## Report wording

- **Data residency note.** Put this wherever the report or the commercialisation document describes the model calls: *"This prototype calls the public Claude API. In production, GLDB would reach Claude through an enterprise agreement or its cloud provider (for example Vertex AI), with regional processing and zero data retention, subject to its outsourcing and MAS requirements."* Add that the adverse-media check sends the company's name (and former names) to Claude's web search. Don't name a model, region or bank without a source.

## Detection and the document pipeline

- **Cross-document category.** *Issue:* two documents disagreeing (e.g. share capital) is labelled `unsupported_claim`. *Do:* a `cross_document_variance` label (schema change).
- **Audit the "Other" checks:** `date_anomalies`, `metadata_analysis` and `image_manipulation` use the document type in places; mark each rule type-agnostic or type-keyed.
- **Model output gaps.** *Issue:* nothing checks an extracted value's type (text in a number field), and the binder (`field_checks/binder.py`) and `llm_classify.py` skip `validate`, so repairs aren't traced.

## Case analysis (design: `HOW_IT_WORKS.md`, Step 6)

- **Cancel the outdated run.** *Issue:* a replaced run finishes anyway and spends its calls; only its result is dropped (`superseded`).
- **Reminder: make the case analysis and the recommended documents more powerful.** *Issue:* the analysis tests at most three hypotheses in one plan-then-judge pass, with the tools it has today; recommended documents come from three fixed rules (large trade transactions, related-party balances, borrowings up 40%). *Ideas:* more cross-document tools (salary credits against payslips, CPF against payroll, invoices against statement receipts); a follow-up round when a verdict is inconclusive; each recommended document tied to the finding it would settle, ranked by how far it could move the score. Measure with the case-analysis evals before and after.
- **Debounce** (5–10 s) before an automatic start, for a burst of officer changes.
- **Dismissing a cross-document finding** (officer, with a reason; leaves the score, kept on record).
- **No follow-up round** in plan-then-judge: add one if evals show low recall.
- **Monitoring:** false-positive, dismissal and citation-discard rates, cost per case.
- **Evidence requests need rethinking.** *Issue:* a document can only lower the score, so "ask for everything" always looks best. *Ideas:* the smallest set that reaches a target level (code); score documents that could go either way; an agent that checks existing documents first. The score stays deterministic.

## Evals and verification (all on demand: they spend tokens)

- **Agent evals:** the sensibility checks are built; what's open (tests, tuning, the officer-verdict accuracy table) is in `CLOUD_ARCHITECTURE_PLAN.md`, "Agent evaluation". No golden set, by decision.
- **Case analysis evals:** false positives on clean generated cases (Other documents included), recall on planted contradictions, false-positive parity across subgroups.
- **Extraction accuracy:** text-only vs second look, and code-read vs model-read statement transactions, on labelled statements and payslips.
- **Does officer feedback help?** Same cases with and without `finding_feedback`.
- **One real end-to-end run** (never done with real Claude): a bank statement and a payslip through the local app, including a second-look disagreement reaching Manual Review.
- **Check screens by eye:** low-confidence reasons, second-look tables in the trace, the adverse-media label.
- **Real bank statement layouts:** `statement_table.py` is tested on synthetic ones only.

## Performance

- **Opening a case by direct link waits in sequence** (case lookup, ticket, socket, first snapshot). *Do:* fetch the first snapshot over HTTP alongside the socket, and put the case's details in the snapshot.

## Cloud operations

- **Remove the unused publishable Supabase key:** the code no longer lists it (`roles.json`, the chart's values, the kind smoke test). Push and let the GKE deploy go green first (pods mount their secrets at start), then `terraform apply` in `deploy/terraform`: it deletes the secret and Cloud Run's references to it.
- **Make Snyk blocking again** when its quota resets: delete the two `continue-on-error: true` lines in `.github/workflows/ci-cd.yml`, and "temporarily non-blocking" from CLAUDE.md, README and `HOW_IT_WORKS.md` §9.
- **Frontend fully non-root:** `nginx-unprivileged` on 8080 (change `nginx.conf`, compose and the chart's `templates/frontend.yaml` port, with `runAsNonRoot`, together; `run.tf`'s `container_port` too while Cloud Run exists).
- **Dead letters:** an event that failed delivery 5 times lands in `domain-events-dead-letter-pull`, and the alert email says so. Nothing reads them. *Do (no code change):* when the email comes, look without removing them (`gcloud pubsub subscriptions pull domain-events-dead-letter-pull --limit=10 --project fyp-six-sevens`), check the worker's logs for why, redo the action in the app if the sweeper hasn't (for example "Run detection again"), then remove them (the same command with `--auto-ack`).

## Frontend

- Dev StrictMode sends some document-page requests twice.
