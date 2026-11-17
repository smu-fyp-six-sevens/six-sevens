# Cloud architecture: what's left

Open cloud work only: delete an item once it's done, and this file once it's empty. Updated 2026-11-17. The cloud as built, and why (the GKE design, Helm, releases, decisions, cost, risks): `HOW_IT_WORKS.md` §9. Operations and every problem met: `deploy/terraform/README.md`.

## Status and next steps (read this first)

**Now:** the app runs on GKE at `sixsevens.me`; every consumer pulls its events there. Cloud Run still exists as a fallback, taking no events.

**Next, in order:**

| # | Step | Who | When |
|---|---|---|---|
| 1 | Failure tests (below) | Both | 2026-11-17 or 18 |
| 2 | Delete Cloud Run (phase 6), with `CODEBASE_AUDIT.md` B1: its services, the push identity and its grants, the Cloud Run deploy in CI, the second frontend image, the Grafana panels moved to GKE (`build_dashboards.py` needs GKE versions of the `service_name`, `run_googleapis_com:*` and cost panels). Rollback: re-apply them from git history | Me to prepare, you to apply | By 2026-11-18 |
| 3 | After the demo: workers back to scaling from zero (`min` 0 in each worker's `gke` block in `roles.json`, `gen_values.py`, push) | You | 2026-11-20 or 21 |
| 4 | Delete the cluster | You | Around 2026-12-20 |

**If a worker breaks on GKE:** remove that role from `deploy/terraform/migration.json`, run `gen_values.py`, `terraform apply`, push. Cloud Run takes its work again.

## Open decisions

1. **Argo CD:** replace Cloud Deploy or run beside it, and how image digests reach git (CI commits them to a values file, or Argo CD Image Updater).

## To do

- **Failure tests** (a deliverable), on production on synthetic cases at a quiet time: kill a worker pod mid-run; flood uploads; stop audit-metrics; a poison message; a lost publish (set a document to `queued` without publishing; the sweeper should recover it). The lost publish writes to the shared database.
- **Argo CD:** releases pulled from git (GitOps) instead of pushed by Cloud Deploy. Check its vCPU on the free trial's 8 first; move the verify step and one-step rollback over.
- **Shared Anthropic rate budget:** today each instance caps itself.
- **Kong behaviour test in CI** (routes, CORS, rate limits, WebSocket upgrade): CI only checks the config parses.

## Stage 2 (after the supervisor's feedback, 2026-10-26)

Steps 1–6 before the final presentation (2026-11-20); the rest if time is left.

1. **Load test, the rest:** the same run on the compose stack (`deploy/loadtest/compose.yaml`, Pub/Sub emulator; the one-process result is in `HOW_IT_WORKS.md` §9); for the presentation, a live run on production with the Workloads page and Grafana showing a second detection-worker being added.
2. **Tune autoscaling** (KEDA, pod sizes) from the results, recording before and after.
3. **Failure tests** on production (above).
4. **Accuracy from officer verdicts** (below).
5. **Supply-chain extras left:** Trivy's high findings blocking (it reports only today); CodeQL and GitHub's own attestations need a paid plan on a private repository.
6. **Argo CD** (above).
7. **Event contracts:** every event type checked against a schema in CI, so no service can publish what another can't read.
8. **Architecture decision records:** one short page per big decision (events over direct calls, GKE over Cloud Run, Claude only, the score set by code, no staging), in `docs/adr/`.
9. **Shared rate budget** (above).

**Not doing:** staging (decided; its design is in `HOW_IT_WORKS.md` §9); nightly evals or model-comparison runs (they spend tokens).

## Agent evaluation

The sensibility checks are built (`HOW_IT_WORKS.md`, "Sensibility checks on each agent run"). Open:

- **Tests:** `PENDING_TESTS.md` rows 29, 30 and 31 (the rest are in `tests/test_evals.py`), then one real run per run kind (a payslip, a bank statement, a case) to see the checks on a live trace.
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
