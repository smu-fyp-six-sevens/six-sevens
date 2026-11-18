# Cloud architecture: what's left

Open cloud work only: delete an item once it's done, and this file once it's empty. Updated 2026-11-18. The cloud as built, and why (the GKE design, Helm, releases, decisions, cost, risks): `HOW_IT_WORKS.md` §9. Operations and every problem met: `deploy/terraform/README.md`.

## Status and next steps (read this first)

**Now:** the app runs only on GKE at `sixsevens.me`; every consumer pulls its events there. Cloud Run's services, its push identity and its CI deploy are removed in the code (2026-11-18), waiting for your `terraform apply`.

**Next, in order:**

| # | Step | Who | When |
|---|---|---|---|
| 1 | Apply phase 6: `terraform plan` should only destroy (the Cloud Run services and their grants, the push identity, `kong-gmp-config`) and update the subscriptions in place; then `terraform apply`, then push | You | 2026-11-18 |
| 2 | Failure tests: the runbook is in `deploy/terraform/README.md`, "Failure tests"; paste the results and they go in `HOW_IT_WORKS.md` §9 | You run, me to write up | 2026-11-18 or 19 |
| 3 | After the demo: the backend's push code, with `CODEBASE_AUDIT.md` B1 | Me | After 2026-11-20 |
| 4 | After the demo: workers back to scaling from zero (`min` 0 in each worker's `gke` block in `roles.json`, `gen_values.py`, push) | You | 2026-11-20 or 21 |
| 5 | After step 4: Argo CD (below) | Both | From 2026-11-21 |
| 6 | Delete the cluster | You | Around 2026-12-20 |

**If a worker breaks on GKE:** roll back with Cloud Deploy (`deploy/terraform/README.md`). There's no second platform any more.

## Open decisions

1. **Argo CD's end state:** replace Cloud Deploy, or keep both (Argo CD watching, Cloud Deploy releasing). The plan below replaces it in steps, so this can wait until its step 7.

## To do

- **Failure tests** (a deliverable): run them from the runbook (`deploy/terraform/README.md`, "Failure tests"), then record the results.
- **Argo CD** (decided 2026-11-18: after the demo). *Why not before:* the cluster peaks at about 6.9 of the trial's 8 vCPUs under load, and Argo CD adds about 0.85 vCPU, so the demo's second worker could fail to start. Once the workers scale from zero again (step 4), there's room. The files are ready: `deploy/argocd/values.yaml` (smallest install, explicit requests) and `deploy/argocd/application.yaml` (the chart at `main`, automated sync off at first). Steps:
  1. Binary Authorization: allow `quay.io/argoproj/**` and `public.ecr.aws/docker/library/redis*` (`supply_chain.tf`, beside KEDA's); apply.
  2. A read-only GitHub deploy key for the repository, registered in Argo CD (`argocd repo add` or a repository secret); the repository is private.
  3. `deploy/helm/six-sevens/values-gitops.yaml` (committed): `projectId`, `supabaseUrl`, `gateway.address`, which `deploy.yml` passes as `--set` today.
  4. CI writes the signed digests to `values-images.yaml` and commits it to `main` with `[skip ci]` (the gke job needs `contents: write`; check branch protection allows the bot). Cloud Deploy keeps releasing meanwhile.
  5. Install (`helm install`, as in `values.yaml`'s header), apply the Application, sync by hand once and check it shows *Synced* with nothing to change against Cloud Deploy's release.
  6. Move the verify step to an Argo CD `PostSync` hook Job (the same `expect` checks as `deploy/clouddeploy/skaffold.yaml`), and rollback to `argocd app rollback six-sevens` or a revert of `values-images.yaml`.
  7. Turn automated sync on, and remove the Cloud Deploy release from `deploy.yml` (keeping the build, scan and sign).
- **Shared Anthropic rate budget:** today each instance caps itself.
- **Kong behaviour test in CI** (routes, CORS, rate limits, WebSocket upgrade): CI only checks the config parses.

## Stage 2 (after the supervisor's feedback, 2026-10-26)

**Before the final presentation (2026-11-20):** only 1 and 2. The rest after it.

1. **Live load run for the presentation:** on production, with the Workloads page and Grafana showing a second detection-worker being added (the one-process load test is done: `HOW_IT_WORKS.md` §9).
2. **Failure tests** on production (above).
3. **Tune autoscaling** (KEDA, pod sizes) from the results, recording before and after.
4. **Accuracy from officer verdicts** (below).
5. **Load test on the compose stack** (`deploy/loadtest/compose.yaml`, Pub/Sub emulator).
6. **Event contracts:** every event type checked against a schema in CI, so no service can publish what another can't read.
7. **Architecture decision records:** one short page per big decision (events over direct calls, GKE over Cloud Run, Claude only, the score set by code, no staging), in `docs/adr/`.
8. **Shared rate budget** (above).
9. **Argo CD** (above): after the demo, from 2026-11-21.

**Decided (2026-11-17):** Trivy blocks on our backend images only (built: `HOW_IT_WORKS.md` §9); CodeQL and GitHub's own attestations are skipped (they need a paid plan on a private repository; signing, SBOMs and SLSA provenance are built instead).

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
