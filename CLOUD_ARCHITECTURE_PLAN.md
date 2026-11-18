# Cloud architecture: what's left

Open cloud work only: delete an item once it's done, and this file once it's empty. Updated 2026-11-18. The cloud as built, and why (the GKE design, Helm, releases, decisions, cost, risks): `HOW_IT_WORKS.md` §9. Operations and every problem met: `deploy/terraform/README.md`.

## Status and next steps (read this first)

**Now:** the app runs only on GKE at `sixsevens.me`; every consumer pulls its events there. Cloud Run is gone (phase 6, applied 2026-11-18).

**Next, in order:**

| # | Step | Who | When |
|---|---|---|---|
| 1 | Failure tests: the runbook is in `deploy/terraform/README.md`, "Failure tests"; paste the results and they go in `HOW_IT_WORKS.md` §9 | You run, me to write up | 2026-11-18 or 19 |
| 2 | After the demo: the backend's push code, with `CODEBASE_AUDIT.md` B1 | Me | After 2026-11-20 |
| 3 | After the demo: workers back to scaling from zero (`min` 0 in each worker's `gke` block in `roles.json`, `gen_values.py`, push) | You | 2026-11-20 or 21 |
| 4 | After step 3: Argo CD (below) | Both | From 2026-11-21 |
| 5 | Delete the cluster | You | Around 2026-12-20 |

**If a worker breaks on GKE:** roll back with Cloud Deploy (`deploy/terraform/README.md`). There's no second platform any more.

## Open decisions

1. **Argo CD's end state:** replace Cloud Deploy, or keep both (Argo CD watching, Cloud Deploy releasing). The plan below replaces it in steps, so this can wait until its step 7.

## To do

- **Failure tests** (a deliverable): run them from the runbook (`deploy/terraform/README.md`, "Failure tests"), then record the results.
- **Argo CD** (decided 2026-11-18: after the demo). *Why not before:* the cluster peaks at about 6.9 of the trial's 8 vCPUs under load, and Argo CD adds about 0.85 vCPU, so the demo's second worker could fail to start. Once the workers scale from zero again (step 3), there's room. The files are ready: `deploy/argocd/values.yaml` (smallest install, explicit requests) and `deploy/argocd/application.yaml` (the chart at `main`, automated sync off at first). Steps:
  1. Binary Authorization: allow `quay.io/argoproj/**` and `public.ecr.aws/docker/library/redis*` (`supply_chain.tf`, beside KEDA's); apply.
  2. A read-only GitHub deploy key for the repository, registered in Argo CD (`argocd repo add` or a repository secret); the repository is private.
  3. `deploy/helm/six-sevens/values-gitops.yaml` (committed): `projectId`, `supabaseUrl`, `gateway.address`, which `deploy.yml` passes as `--set` today.
  4. CI writes the signed digests to `values-images.yaml` and commits it to `main` with `[skip ci]` (the gke job needs `contents: write`; check branch protection allows the bot). Cloud Deploy keeps releasing meanwhile.
  5. Install (`helm install`, as in `values.yaml`'s header), apply the Application, sync by hand once and check it shows *Synced* with nothing to change against Cloud Deploy's release.
  6. Move the verify step to an Argo CD `PostSync` hook Job (the same `expect` checks as `deploy/clouddeploy/skaffold.yaml`), and rollback to `argocd app rollback six-sevens` or a revert of `values-images.yaml`.
  7. Turn automated sync on, and remove the Cloud Deploy release from `deploy.yml` (keeping the build, scan and sign).
- **Kong behaviour test in CI** (routes, CORS, rate limits, WebSocket upgrade): CI only checks the config parses.

## Stage 2 (after the supervisor's feedback, 2026-10-26)

**Before the final presentation (2026-11-20):** only 1 and 2. The rest after it.

1. **Live load run for the presentation:** on production, with the Workloads page and Grafana showing a second detection-worker being added (the one-process load test is done: `HOW_IT_WORKS.md` §9).
2. **Failure tests** on production (above).
3. **Load test on the compose stack** (`deploy/loadtest/compose.yaml`, Pub/Sub emulator).
4. **Event contracts:** every event type checked against a schema in CI, so no service can publish what another can't read.
5. **Argo CD** (above): after the demo, from 2026-11-21.