# Adding a service

What to change so a new Cloud Run service is built, deployed, secured and monitored like the others. A missed step usually fails the next deploy (403, 404, or no events arrive) and it rolls back.

**Order:** change Terraform and run `terraform apply` *before* pushing. A push to `main` deploys, but the deploy only swaps images: it can't create a service Terraform hasn't made.

## A new backend role

Same backend code, a different `ROLE` (e.g. another event worker).

1. **Code** (`backend/app`): add the role to `ALL_ROLES` in `config.py`; a `HANDLERS` dict in its `workers/` module plus one line in `_HANDLERS` in `main.py`; its events in `SUBSCRIPTIONS` (and any new event names) in `events/types.py`.
2. **Subscriptions:** run `python -m app.events.types` from `backend/` to rewrite `deploy/terraform/subscriptions.json`. It errors if a filter passes Pub/Sub's 256-byte limit; CI checks the file matches.
3. **Dockerfile:** a stage in `backend/Dockerfile`: `FROM runtime AS my-worker` + `ENV ROLE=my-worker` (`FROM with-land-use` if it needs the zoning index).
4. **Terraform:** one entry in `local.roles` (`deploy/terraform/main.tf`), copied from a similar role: CPU, memory, concurrency, timeout, max instances; `public` or private; `publishes`; secrets and env. Everything else (account, permissions, secrets, metrics sidecar, subscription) comes from it. Then `terraform apply` (changed filters replace their subscriptions).
5. **Deploy workflow:** add it to `BACKEND_ROLES` in `.github/workflows/deploy.yml`.
6. **Optional:** a compose service and Prometheus target (`deploy/compose/`); Kong routes (`deploy/kong/kong.yml.tmpl`) only if officers call it. Grafana picks it up by name pattern.

## A new non-backend container

E.g. a tool like Grafana or Kong.

1. **Dockerfile** with a non-root `USER` and a `HEALTHCHECK` (Checkov requires both).
2. **Terraform:** its own `google_cloud_run_v2_service` (copy Grafana's in `observability.tf`); add its name to the non-backend lists in `main.tf` (the `url` map) and `iam.tf` (service accounts); `terraform apply`.
3. **Deploy workflow:** `name=path/to/Dockerfile` in `OTHER_SERVICES`; also `SINGLE_CONTAINER` if it has no sidecar; a build case if it needs build arguments.
4. **Post-deploy check:** if user-facing, an `expect` line in the gateway check, so a broken release rolls back.

## Afterwards

- Add it to the role table in `deploy/terraform/main.tf`, the services summary in `HOW_IT_WORKS.md` §9, and `docs/architecture/PRESENTATION_GUIDE.md` §3, and to `docs/architecture/build_diagrams.py` (rerun it; don't edit the `.drawio`).
- Watch the first deploy in the Actions tab.
