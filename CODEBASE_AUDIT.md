# Codebase audit

Open items only: delete a row once it's fixed. Read from the code, 2026-11-13; nothing was run against live services. Sev: High, Medium, Low. Size: S (minutes), M (hours), L (a day+). Paths are under `backend/app/` unless they say otherwise. "(unconfirmed)" means read from code but not proven.

## 1. Most important

| Items | In plain words |
|---|---|
| 6.1.1 | Two services can be called around the gateway (needs you: it changes the deploy) |
| 6.5.1 | The audit log keeps personal data forever, even after a case is deleted (needs a decision, §7) |
| 6.2.1 | Any officer can see and change any case (needs a decision, §7) |

## 2. Bugs

| # | Sev | In plain words | Fix | Where | Size |
|---|---|---|---|---|---|
| 2.6 | Medium | The AI decides each finding's severity and can dismiss most findings, so it effectively shapes the score | Decide the rule (§7); then maybe a minimum severity per check for income documents and bank statements | `case_pipeline/risk/case_risk.py:156-168`; `langgraph/detection/specialists.py:48-59` | M |
| 2.14 | Low | One unassessed finding alone makes a case Medium (3 points = the Medium threshold) | Decide (§7): lower its points or raise the threshold | `case_pipeline/risk/case_risk.py:58`, `:62` | S |
| 2.18 | Low | Cancelling a detection on another instance only works if audit-metrics is up | Read a cancel flag on the document row | `workers/detection.py:30-32` | M |
| 2.22 | Low | No CPF rate table for 2027 yet, so 2027 payslips skip the CPF checks | Add the 2027 table and ceiling once CPF publishes them | `fraud_detection/external_validation/sg_reference/cpf.py` | S |

## 3. Performance

| # | Sev | In plain words | Fix | Where | Size |
|---|---|---|---|---|---|
| 3.1.2 | Low | Dashboard cost totals load every costed event and add them in Python | Sum in SQL | `audit.py` (`cost_usd_many`) | M |
| 3.2.2 | Medium | Agent loops resend their whole history without caching, each turn at full price (size of bill unmeasured) | Add a cache breakpoint on the last tool result each turn | `langgraph/detection/specialists.py`, `langgraph/classify_graph.py`, `case_pipeline/cross_document/agents.py` | M |
| 3.2.4 | Low | The extraction retry resends the whole document | Decide (§7): accept, or cache the document block (costs more on every first call) | `langgraph/extract.py:107-115` | S |

Also in STABILITY §2: sweeper scan, case search, `list_reviews`.

## 4. Hygiene

| # | In plain words | Fix | Where |
|---|---|---|---|
| 4.2.1 | The two workers' run wrappers are near-copies | One shared runner in `workers/recovery.py` (M) | `workers/detection.py`; `workers/documents.py` |
| 4.2.2 | Token and ticket signing are written twice | One sign/verify pair | `auth.py` |
| 4.3.1 | Raw colours outside the tokens in 8 frontend files | Move to tokens | `frontend/src/styles/*.css`, `DashboardPage.tsx` |
| 4.3.2 | Settings are read three ways; a bad number fails with a bare `ValueError` | One typed reader that names the variable | `config.py` |

## 5. Bigger reorganisations (optional, in order)

1. **One status-transition function**: every status write already checks the current status; one function would make "one writer per transition" checkable in one place. M.
2. **Start case analysis from an event** in the case-worker: untangles imports. M.
3. **Load only each role's code** in `main.py`: faster cold starts. M.
4. **Split `document_service.py`** (710 lines). M.

## 6. Security

| # | Sev | In plain words | Fix | Where | Size |
|---|---|---|---|---|---|
| 6.1.1 | High | `api` and `audit-metrics` are public, so attackers can skip Kong's login rate limit and read `/metrics` | Only Kong's service account may call them (needs the user: changes the deploy) | `deploy/terraform/run.tf`; `main.tf` | M |
| 6.1.5 | Low | Any service can publish any event type, including ones that look like an officer's | Handlers check which role may send each type | `deploy/terraform/iam.tf:37-42` | M |
| 6.2.1 | High | Any officer can read, change or delete any case | Decide the rule (§7), then one check per route | `routers/_dependencies.py:85-97`; `routers/audit.py` | L |
| 6.2.2 | Medium | Passwords stored in plain text, no lockout, 7-day tokens that can't be revoked one by one | Store hashes; a token version per user | `auth.py`; `config.py:32-43` | M |
| 6.3.1 | Medium | The public `api` holds the database key that bypasses every rule | Later: a database role per service. Sooner: restrict the key to the services' network | `deploy/terraform/main.tf:12` | L |
| 6.3.3 | Low | File links last 30 minutes and work for anyone holding them | Decide (§7): accept, or shorten for previews | `config.py` (`SIGNED_URL_EXPIRY_SECONDS`) | S |
| 6.5.1 | High | The audit log keeps old/new field values, file and company names forever, even after a case is deleted | Decide (§7): store "changed" only, or a retention period | `document_service.py`, `routers/upload.py`, `routers/cases.py` | M |
| 6.5.5 | Low | The stored trace has file names and quoted values, and any officer can read it | Decide (§7): accept, or strip values unless debugging | `langgraph/nodes.py:259` | S |
| 6.6.1 | Low | Grafana's admin password sits in Terraform state | Decide (§7): accept, or create it outside Terraform | `deploy/terraform/observability.tf:7-22` | S |
| 6.7.2 | Low | Actions and the base image are pinned by tag, not hash; pip doesn't check hashes | Pin by digest; `--require-hashes` (needs the digests looked up and a hashed lock) | `.github/workflows/ci-cd.yml:63`; `backend/Dockerfile` | S |
| 6.8.1 | Low | The 7-day login token sits in `localStorage`, so any injected script could read it (the CSP now makes that much harder) | Shorter tokens, or an HttpOnly cookie | `frontend/src/lib/api.ts:11-19` | M |

## 7. Decisions needed

- **Who may see which case?** Today every officer can see, change and delete every case. Options: keep it and document it; everyone sees all but changes only their own; or only assigned cases, with a supervisor role. *Recommended: see all, change own.*
- **What should the audit log keep, and for how long?** It's permanent on purpose, but it keeps the values officers corrected (names, IDs, amounts). Options: keep values forever; keep only "field X changed, by whom, when"; or delete entries after a set period. *Recommended: "changed" only, and ask GLDB how long audit records must be kept.*
- **How much may the AI overrule the checks?** Code already stops it lowering anything proved impossible. Option still open: a minimum severity for arithmetic and identity findings on payslips, tax notices and bank statements. *Recommended: yes, after checking with the sponsor.*
- **Should one unassessed finding make a case Medium? (2.14)** *Recommended: no; give it 2 points so it takes two to reach Medium.*
- **Extraction retry cost (3.2.4):** *Recommended: accept; retries are rare and caching every first call costs more.*
- **File link lifetime (6.3.3):** *Recommended: 5 minutes for previews, 30 for runs.*
- **Trace values (6.5.5):** *Recommended: accept for now; the review screen reads the reasons.*
- **Grafana password in state (6.6.1):** *Recommended: accept; the state bucket is private.*
- **Who approves a release to production?** Only `main` can deploy now (the Google sign-in checks the branch). Option still open: a GitHub environment with a named approver on the deploy job. *Recommended: once staging exists.*
