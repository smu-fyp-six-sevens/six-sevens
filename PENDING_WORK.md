# Pending work

Open product and platform work only: delete an item once done. Updated 2026-11-13. Elsewhere: bugs, stability, performance and security in `CODEBASE_AUDIT.md`; tests in `TEST_SUITE_AUDIT.md` and `PENDING_TESTS.md`; detection bugs in `FRAUD_DETECTION_MAP.md` §8; cloud to-dos in `CLOUD_ARCHITECTURE_PLAN.md` §9–10 and Kubernetes in §13–24.

## Handover documentation

- **CPF rates each January.** *Issue:* the 2027 CPF table is a placeholder (2026's rates), so 2027 payslips skip the CPF checks unless `CPF_USE_PROVISIONAL_RATES` is on. CPF publishes new rates and ceilings every year, so GLDB's team will need to repeat this. *Do:* when CPF publishes 2027, replace the five 2027 rows and the ceiling in `fraud_detection/external_validation/sg_reference/cpf.py` and remove the date from `PROVISIONAL_RATE_TABLES`; write the yearly steps into the handover documentation.

## Security

- **Lockout and revocation (if wanted).** *Issue:* passwords are hashed now, but nothing locks an account after repeated failures (only Kong's 20 logins a minute per address), and one leaked token can't be revoked before its 7 days. *Do:* a failure count per name in the audit trail with a short lockout; a token version per user in `AUTH_USERS`, checked in `_verify_token`.
- **Login token in `localStorage`.** *Issue:* the 7-day token sits in `localStorage`, so an injected script could read it (the CSP now makes that much harder). *Do:* a shorter token, or an HttpOnly cookie (`frontend/src/lib/api.ts:11-19`; the WebSocket ticket flow and Kong's CORS would need to follow).

## Product

- **Skip repeated runs** (decided 2026-11-01). *Issue:* "Run again" on an unchanged case spends ~12k tokens for the same answer. *Do:* show the last complete run when the fingerprint matches.

## Claude

- **Billed cost per role.** *Issue:* per-call cost is estimated from list prices, not the bill. *Do:* one Claude workspace and key per worker role; the Cost API then reports billed USD per workspace.
- **On-demand live check.** *Issue:* staging stubs Claude, so an API-side break (like the `temperature` 400) isn't caught. *Do:* one tiny call per model, run by hand after an SDK or model change.

## Report wording

- **Data residency note.** Put this wherever the report or the commercialisation document describes the model calls: *"This prototype calls the public Claude API. In production, GLDB would reach Claude through an enterprise agreement or its cloud provider (for example Vertex AI), with regional processing and zero data retention, subject to its outsourcing and MAS requirements."* Add that the adverse-media check sends the company's name (and former names) to Claude's web search. Don't name a model, region or bank without a source.

## Detection and the document pipeline

- **Cross-document category.** *Issue:* a share-capital conflict is labelled `unsupported_claim`. *Do:* a `cross_document_variance` label (schema change).
- **No checkpointing, by choice.** A crashed run is rerun from the start (the sweeper recovers it, `HOW_IT_WORKS.md` §9); a checkpointer would only keep the model calls already made. If it is ever wanted: `langgraph-checkpoint-postgres`, its own schema, no personal data in state.
- **Model output gaps.** *Issue:* nothing checks an extracted value's type (text in a number field), and the binder (`field_checks/binder.py`) and `llm_classify.py` skip `validate`, so repairs aren't traced.

## Case analysis (design: `CROSS_DOCUMENT_AND_RISK_PLAN.md`)

- **No follow-up round** in plan-then-judge: add one if evals show low recall.
- **Run trace clean-up:** it lists dropped hypotheses, which the design says are never shown.
- **Guardrails still open:** dismissing a cross-document finding, the three sponsor questions in the plan.
- **Evidence requests need rethinking.** *Issue:* a document can only lower the score, so "ask for everything" always looks best. *Ideas:* the smallest set that reaches a target level (code); score documents that could go either way; an agent that checks existing documents first. The score stays deterministic.

## Evals and verification (all on demand: they spend tokens)

- **Agent evals:** the sensibility checks are built; what's open (tests, tuning, the officer-verdict accuracy table) is in `CLOUD_ARCHITECTURE_PLAN.md` §13. No golden set, by decision.
- **Extraction accuracy:** text-only vs second look, and code-read vs model-read statement transactions, on labelled statements and payslips.
- **Does officer feedback help?** Same cases with and without `finding_feedback`.
- **One real end-to-end run** (never done with real Claude): a bank statement and a payslip through the local app, including a second-look disagreement reaching Manual Review.
- **Check screens by eye:** low-confidence reasons, second-look tables in the trace, the adverse-media label.
- **Real bank statement layouts:** `statement_table.py` is tested on synthetic ones only.

## Performance

- A burst of changes can chain snapshot builds; the first requester waits for the next.
- **Opening a case by direct link waits in sequence** (case lookup, ticket, socket, first snapshot). *Do:* fetch the first snapshot over HTTP alongside the socket, and put the case's details in the snapshot.

## Cloud operations

- **Remove the unused publishable Supabase key:** drop `SUPABASE_PUBLISHABLE_KEY` from every role's secrets in `deploy/terraform/roles.json`, run `python deploy/helm/gen_values.py`, `terraform apply`, delete the secret, then disable the legacy keys in Supabase.
- **Make Snyk blocking again** when its quota resets: delete the two `continue-on-error: true` lines in `.github/workflows/ci-cd.yml`, and "temporarily non-blocking" from CLAUDE.md, README and `HOW_IT_WORKS.md` §9.
- **Frontend fully non-root:** `nginx-unprivileged` on 8080 (change `nginx.conf`, `run.tf`'s `container_port` and compose together; apply first).
- **Dead letters** sit in `domain-events-dead-letter-pull` until someone reads them.
- **Socket ticket replay** within 10 s on another instance is accepted; not worth fixing.

## Frontend

- Dev StrictMode sends some document-page requests twice.
