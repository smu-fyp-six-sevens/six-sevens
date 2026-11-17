# Load tests

Many officers at once against the real API, events and workers, with Supabase and Claude replaced, so a run costs
nothing and touches no real data (`stand_ins.py`):

- **Database and files:** in memory (`backend/tests/support/fake_supabase.py`, with the schema's defaults).
- **Claude:** each run waits as long as a real one (`LOADTEST_DOC_SECONDS` 20, `LOADTEST_DETECTION_SECONDS` 40,
  `LOADTEST_ANALYSIS_SECONDS` 30), then writes a plain result: an Other document, a clean report, a finished
  analysis. Set them from a real run's timings (the case's Claude Cost tab).
- **Realtime:** off; the app's own WebSocket pushes still go out.

`run.py` plays each officer: create a case, upload documents, approve each once it's analysed, wait for detection
and the case analysis. It prints uploads a minute and p50 / p95 / max seconds per stage, and refuses any URL but
localhost.

## Run it

On the laptop, one process, events in process (no Docker). From `backend/`:

```
python ..\deploy\loadtest\stand_ins.py
```

then, in another terminal from the repo root:

```
python deploy\loadtest\run.py --cases 20 --documents 3 --ramp 60
```

With Pub/Sub delivery (the emulator, pulled as on GKE), from the repo root:

```
docker compose -f deploy/loadtest/compose.yaml up --build
python deploy\loadtest\run.py --cases 20 --documents 3 --ramp 60
```

## What it shows, and what it doesn't

- How much one process takes before stages queue: `DETECTION_CONCURRENCY` (2) runs at once, so detection is the
  first to back up. Raise `--cases` until "approve -> detected" p95 grows past one run's time.
- Not the cloud's scaling: KEDA adds pods on GKE from the subscription backlog. For that, the presentation's live
  run (synthetic documents on production) with the Workloads page and Grafana's Pipeline dashboard open.
- Not Claude's own limits or Supabase's: both are replaced.

Results go in `HOW_IT_WORKS.md` §9, "Load tests".
