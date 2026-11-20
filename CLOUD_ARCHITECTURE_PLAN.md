# Cloud architecture: what's left

Open cloud work only: delete an item once it's done, and this file once it's empty. Updated 2026-11-20. The cloud as built: `HOW_IT_WORKS.md` §9. Operations: `deploy/terraform/README.md`.

**Now:** the app runs on GKE at `sixsevens.me`; the workers scale from zero. Grafana and Prometheus scraping are off to save credits (`values-prod.yaml`, and their checks removed from `deploy/clouddeploy/skaffold.yaml`); undo both to bring them back.

| # | Step | Who | When |
|---|---|---|---|
| 1 | Remove the push delivery code (`CODEBASE_AUDIT.md` B1) | Me | Any time |
| 2 | Argo CD (below), only if the project goes on: it adds about 0.85 vCPU | Both | On hold |
| 3 | Delete the cluster | You | Before the credits run out (around mid-December) |

## To do

- **Argo CD.** The files are ready: `deploy/argocd/values.yaml` (smallest install) and `deploy/argocd/application.yaml` (the chart at `main`, automated sync off at first). Open decision: replace Cloud Deploy, or keep both (Argo CD watching, Cloud Deploy releasing); the steps below replace it, so this can wait until step 7.
  1. Binary Authorization: allow `quay.io/argoproj/**` and `public.ecr.aws/docker/library/redis*` (`supply_chain.tf`, beside KEDA's); apply.
  2. A read-only GitHub deploy key, registered in Argo CD; the repository is private.
  3. `deploy/helm/six-sevens/values-gitops.yaml`: `projectId`, `supabaseUrl`, `gateway.address`, which `deploy.yml` passes as `--set` today.
  4. CI writes the signed digests to `values-images.yaml` and commits it with `[skip ci]` (the gke job needs `contents: write`). Cloud Deploy keeps releasing meanwhile.
  5. Install, apply the Application, sync by hand once and check it shows *Synced* with nothing to change.
  6. Move the verify step to an Argo CD `PostSync` hook Job (the same checks as `skaffold.yaml`), and rollback to `argocd app rollback six-sevens`.
  7. Turn automated sync on, and remove the Cloud Deploy release from `deploy.yml` (keeping build, scan and sign).
- **Kong behaviour test in CI** (routes, CORS, rate limits, WebSocket upgrade): CI only checks the config parses.
- **Load test on the compose stack** (`deploy/loadtest/compose.yaml`, with the Pub/Sub emulator).
- **Event contracts:** every event type checked against a schema in CI, so no service can publish what another can't read.
