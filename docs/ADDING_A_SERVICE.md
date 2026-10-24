# Adding a service

What to change when you add a new Cloud Run service, so it gets built, deployed, secured and monitored like the other eight. Skipping a step usually fails loudly on the next deploy (a 403, a 404, or a service that never receives events), and the deploy rolls back. Still, it's quicker to follow the list.

**Order matters:** make the Terraform change and run `terraform apply` *before* pushing the code. Every push to `main` deploys automatically, and the deploy only swaps images: it can't create a service that Terraform hasn't made yet.

## A new backend role

For example, another worker that reacts to events. It runs the same backend code, started with a different `ROLE`.

1. **Code** (`backend/app`)
   - Add the role to `ALL_ROLES` in `config.py`.
   - Give it its handlers: a `HANDLERS` dict in its module under `workers/`, plus one line in `_HANDLERS` in `main.py`.
   - Say which events it receives: an entry in `SUBSCRIPTIONS` in `events/types.py`. Add any new event type names there too.
2. **Subscription file:** run `python -m app.events.types` from `backend/`. It rewrites `deploy/terraform/subscriptions.json`, which Terraform reads. Pub/Sub filters max out at 256 bytes; the command raises an error if the new filter is too long. CI also checks that the file matches the code.
3. **Dockerfile:** add a stage to `backend/Dockerfile`:
   ```dockerfile
   FROM runtime AS my-worker
   ENV ROLE=my-worker
   ```
   Use `FROM with-land-use` instead if it runs detection checks that need the zoning index.
4. **Terraform:** add one entry to `local.roles` in `deploy/terraform/main.tf`, copying a similar role:
   - CPU, memory, concurrency, timeout, max instances;
   - `public` (reachable through Kong) or private (only Pub/Sub may call it);
   - `publishes` if it sends events;
   - the secrets it needs, and any extra env.

   Its service account, permissions, secret mounts, metrics sidecar and push subscription are all created from that entry. Then run `terraform apply`. A new subscription is created; changed filters replace their subscriptions.
5. **Deploy workflow:** add the role to `BACKEND_ROLES` at the top of `.github/workflows/deploy.yml`. That one list drives the build, the revision record, the rollout (every role except api goes in the first wave) and the rollback.
6. **Optional:**
   - A compose service for local runs (`deploy/compose/`) and a scrape target in `deploy/compose/prometheus.yml`.
   - Kong routes in `deploy/kong/kong.yml.tmpl`, only if officers call it directly.
   - The Grafana dashboards filter by service name patterns rather than a list, so a new role appears on them without changes.

## A new non-backend container

For example a tool like Grafana or Kong, with its own image.

1. **Its own Dockerfile** (non-root `USER`, a `HEALTHCHECK`: Checkov fails the build without them).
2. **Terraform**
   - Its own `google_cloud_run_v2_service`; copy the Grafana block in `observability.tf`.
   - Add its name to the two lists of non-backend services: `main.tf` (the `url` map, line 4) and `iam.tf` (service accounts, line 4).
   - Run `terraform apply`.
3. **Deploy workflow:** add `name=path/to/Dockerfile` to `OTHER_SERVICES` in `deploy.yml`.
   - If it has no sidecar (a single container, like the frontend), also add its name to `SINGLE_CONTAINER`.
   - If its build needs arguments, add a case next to the frontend's in the build step.
4. **Post-deploy check:** if it's user-facing, add an `expect` line to the gateway check in `deploy.yml`, so a broken release rolls back.

## Afterwards

- **Docs:** add the service to the services table in `CLOUD_ARCHITECTURE_PLAN.md` (section 2) and `docs/architecture/PRESENTATION_GUIDE.md` (section 3). The diagrams in `docs/architecture/` are generated, so ask for them to be regenerated rather than editing by hand.
- **Check the first deploy** in the Actions tab. The gateway check runs after the rollout, and a failure rolls every service back to what was serving before.
