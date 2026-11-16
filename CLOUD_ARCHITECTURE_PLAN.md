# Cloud architecture: what's left

Open cloud work only: delete an item once it's done, and this file once it's empty. Updated 2026-11-16. The cloud as built, and why (the GKE design, Helm, releases, decisions, cost, risks): `HOW_IT_WORKS.md` §9. Operations and every problem met: `deploy/terraform/README.md`.

## Status and next steps (read this first)

**Now:** the app runs on GKE at `sixsevens.me`. Every consumer pulls its events there. Cloud Run still exists as a fallback and its `run.app` URLs still work, but it takes no events.

**Next, in order:**

| # | Step | Who | When |
|---|---|---|---|
| 1 | Cloud Armor now enforces: log in, upload, save a field on `sixsevens.me`. A 403 means a rule caught real traffic; send the request for an exclusion | You, me to propose the fix | Now |
| 2 | After the demo (Friday 2026-11-20): put the workers back to scaling from zero. In `deploy/terraform/roles.json`, each worker's `gke` block has `min` 1 (always warm, about $5 a day, so teammates never wait on a cold start) and doc- and detection-worker `max` 2; set `min` back to 0, run `gen_values.py`, push. Warm them again the same way before the Cloud Native presentation | You | 2026-11-20 or 21 |
| 3 | Delete Cloud Run (phase 6): its services, the push identity and its grants, the Cloud Run deploy in CI, the second frontend image, the Grafana panels moved to GKE (`build_dashboards.py` needs GKE versions of the `service_name`, `run_googleapis_com:*` and cost panels). Rollback: re-apply them from git history | Me to prepare, you to apply | About 2026-11-21, once GKE has been stable |
| 4 | Failure tests (below) | Both | After step 3 |

**If a worker breaks on GKE:** remove that role from `deploy/terraform/migration.json`, run `gen_values.py`, `terraform apply`, push. Cloud Run takes its work again.

**Phases 2–4 still being watched:** every event stored; sweeps run; p95 and failures within 10% of Cloud Run; no more recoveries or dead letters (`PENDING_TESTS.md`, manual checks).

## Open decisions

1. **Login:** keep `AUTH_USERS` (hashed now) as a stated limit, or move to Supabase Auth.
2. **The budget's new amount** for GKE.

## To do

- **Failure tests** (a deliverable), on production after the GKE move, on synthetic cases at a quiet time: kill a worker pod mid-run; flood uploads; stop audit-metrics; a poison message; a lost publish (set a document to `queued` without publishing; the sweeper should recover it). The lost publish writes to the shared database.
- **Argo CD (planned, after the demo):** releases pulled from git (GitOps) instead of pushed by Cloud Deploy. Open points: replace Cloud Deploy or run beside it (replace, so there is one deployer); how image digests reach git (CI commits them to a values file, or Argo CD Image Updater); its vCPU on the free trial's 8 (check the peak first, after step 2 lowers the workers); the verify step and one-step rollback moved over; Binary Authorization unchanged (it checks at admission, whoever deploys).
- **Shared Anthropic rate budget:** today each instance caps itself.
- **Kong behaviour test in CI** (routes, CORS, rate limits, WebSocket upgrade): CI only checks the config parses.
- **Delete the cluster** after the assessment if nobody takes it over.
- **Optional:** ingestion out of api.

## Stage 2 (after the supervisor's feedback, 2026-10-26; updated 2026-11-14)

Steps 1–6 before the final presentation; the rest after the demo, as time allows.

1. **Load tests as code** (k6 or Locust) against the local compose stack, written up as a capacity report (uploads a minute, time for KEDA to add a worker, p95 per stage). **No Claude calls** (stubbed) and **no Supabase writes** (writes go to an in-memory store). For the presentation, a live run with the Workloads page and Grafana showing pods being added.
2. **Tune autoscaling** (KEDA, pod sizes) from the results, recording before and after.
3. **Failure tests** on production (above).
4. **Accuracy from officer verdicts** (below).
5. **Service-level objectives** with burn-rate alerts in Cloud Monitoring: for example 99% of API requests succeed, 95% of detection runs finish within 3 minutes; an alert when the error budget burns too fast.
6. **Supply-chain extras:** build provenance (`actions/attest-build-provenance`, SLSA), CodeQL and gitleaks, and Trivy on each image with high findings blocking, beside the signing and Binary Authorization already in place.
7. **Argo CD** (above).
8. **Event contracts:** every event type checked against a schema in CI, so no service can publish what another can't read.
9. **Disaster recovery:** one Supabase backup-and-restore drill, timed, with stated recovery-time and data-loss targets (RTO, RPO); and the self-host kit (`docker compose up` install and the Helm chart).
10. **Uptime checks and a status page:** Cloud Monitoring checks on `sixsevens.me` and the API from several regions.
11. **Architecture decision records:** one short page per big decision (events over direct calls, GKE over Cloud Run, Claude only, the score set by code, no staging), in `docs/adr/`.
12. **Cost:** infrastructure cost per case (each namespace's share from the billing export in BigQuery) beside the Claude cost per case; and the guardrails: a shared rate budget, a spend cap per case, budget alerts (the cluster costs money idle).

**Not doing:** staging (decided; its design is in `HOW_IT_WORKS.md` §9); nightly evals or model-comparison runs (they spend tokens).

## Agent evaluation

The sensibility checks are built (`HOW_IT_WORKS.md`, "Sensibility checks on each agent run"). Open:

- **Tests:** `PENDING_TESTS.md` rows 36, 37 and 39 (the rest are in `tests/test_evals.py`), then one real run per run kind (a payslip, a bank statement, a case) to see the checks on a live trace.
- **Tune from real runs:** the overconfidence gap (0.3); the expected-checks table (`evals/detection.py`: today only Content Validation's numerical checks on income documents and bank statements, and Digital Authenticity's metadata check); the field-name rules for dates and amounts (`evals/extraction.py`). A check that warns on most runs is noise: change or drop it.
- **Extraction rerun:** records no `extraction` step, so its field checks don't run (only its second look is checked).

### Accuracy from officer verdicts (not built)

*In plain words:* when a case is finalised, the officers' verdicts on each agent's findings are counted against what the agent said. This is the accuracy figure. It's human-labelled and comes from real use, so no answer key is needed.

| Part | Design |
|---|---|
| Which cases | Every case created with reviews on (`cases.reviews_required`) |
| When | When the case is finalised (`case.finalised`): every verdict is in |
| Who writes it | audit-metrics: one handler, idempotent on the case id |
| What it stores | One `evaluation_results` row per case: per check and document type, true positives, false alarms, wrong dismissals, missed issues where an officer adds one; prompt version, models, cost, time. Counts only, no personal data. Cascades from the case |
| Figures | Precision and recall per check and document type (weighted to income documents and bank statements), dismissal accuracy, cost and time, by prompt version and model. Recall is only as good as what officers add, so it is labelled as such |
| Where it shows | The dashboard's accuracy panel reads stored rows (also removes the `list_reviews` load); one Grafana panel per prompt version |
| Use | A trend to watch, not a gate. A drop after a prompt or model change prompts a person to look; nothing blocks automatically |
| Never | Changes a score, severity or verdict, or reaches a prompt |
| Size | M: one table (dated SQL file), one handler, one route, one panel |
