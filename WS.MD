# Case Status Push: WebSocket + Supabase Realtime

## Context

`routers/ws.py` (branch `feature/websocket-case-status-v2`) replaced the old
per-document client-side poll loop in `UploadPage.tsx` with one WebSocket connection per
case — a real improvement, but it moved the polling rather than removing it: the backend
now polls Supabase in a `while True` loop, once per open connection, only sending when
the snapshot changed. Two problems follow:

1. **No dedup even within one process.** Three tabs open on the same case, landing on
   the same replica, run three independent polling loops for identical data. The
   multiplier is "connections to a case," not "replicas."
2. **Token exposure.** Browsers' native `WebSocket` can't set an `Authorization` header,
   so the bearer token travels as `?token=...`. Cloud Run's default access logging
   captures full request URLs, so the shared 7-day credential (`TOKEN_TTL_SECONDS`)
   lands in plaintext in GCP logs on every connection. It's also only checked once, at
   connect — never re-validated for the life of the socket.

This plan replaces backend-polls-Supabase with backend-subscribes-to-Supabase-Realtime,
keeping WebSocket to the frontend as-is (SSE was considered and rejected — the
`refresh()` nudge already uses the connection bidirectionally, not worth a second
endpoint just to change transport). It fixes the token exposure gap in the same pass,
since it touches the same connect path anyway.

**Non-goal:** implementing the 5-service decoupled split from `CLAUDE.md`. This is
written so that migration won't require touching this code again (see below), but the
actual service split is separate, future work.

## Target architecture

```
Browser <--WebSocket--> Backend <--Realtime subscription--> Supabase
 (per tab, per case)     (one subscription per PROCESS,
                          not per case, not per connection)
```

### 1. Realtime listener (new, singleton per process)

Started in `main.py`'s `lifespan` (same place `document_queue.start()` runs), torn down
on shutdown. Subscribes to `postgres_changes` on `documents` (and `cases`, if
`submission_finalized` needs to push too). Uses Supabase's **async** client
(`create_async_client`) — new to this codebase, `supabase_client.py` currently only
builds the sync client used via `asyncio.to_thread` everywhere else. Uses the
**secret key** (Supabase's current replacement for the legacy `service_role` key —
bypasses RLS, appropriate for a backend-only, trusted-context subscription never exposed
to the browser) — new `SUPABASE_SECRET_KEY` env var, server-side only, alongside the
existing `SUPABASE_PUBLISHABLE_KEY`.

On every change event, the callback: reads `case_id` directly off the changed row
(already in the payload — no extra query), builds the snapshot the same way
`_build_snapshot`/`case_status.build_summary` do today (already a bulk fetch — one
`list_documents(case_id=...)` query for every document in the case, not per-changed-row —
so a 10-document case still costs one query per snapshot, not ten), and calls
`broadcaster.publish(case_id, snapshot)`.

**Debounce per case_id before building a snapshot.** A bulk upload (this app supports
uploading several files to one case at once) inserts N rows in quick succession —
Realtime fires N separate events within milliseconds, and without debouncing, the
callback above would rebuild and push N near-identical snapshots back to back. The same
burst happens again later in a case's life whenever several of its documents finish
classification/extraction close together, not just at upload time. Fix: instead of
building+publishing immediately on each event, reset a short per-case_id delayed task
(~100ms) on every event for that case_id; only when the delay elapses with no further
events does it actually run `_build_snapshot` + `publish`, once. Lives in the listener,
not the broadcaster — the broadcaster stays a dumb, always-immediate fan-out primitive;
only the listener decides *when* it's worth calling `publish()`. Trade-off: up to ~100ms
added latency on a status update, in exchange for collapsing a 10-document burst into one
query and one push instead of ten.

### 2. In-memory broadcaster (new, one per process)

The only piece touching both layers:

```python
def subscribe(case_id: str) -> asyncio.Queue: ...
def unsubscribe(case_id: str, queue: asyncio.Queue) -> None: ...
def publish(case_id: str, snapshot: dict) -> None: ...
```

Backed by `dict[str, set[asyncio.Queue]]`. Only the Realtime listener's callback may
call `publish()` — see "What to explicitly avoid" below.

**No lock needed, confirmed.** `realtime-py` (what `supabase`'s async client uses,
v2.31.0) runs natively on `asyncio`, not threads — started from `lifespan`, it shares
FastAPI's own event loop, so the Realtime callback and every route handler run on the
same thread. As long as `subscribe`/`unsubscribe`/`publish` stay plain synchronous
functions (no `await` inside — there's no I/O in touching a dict/set), asyncio's
cooperative scheduling makes them atomic with respect to each other automatically; no
`asyncio.Lock()`/`threading.Lock()` needed. Still iterate over a **copy** of the
per-case set in `publish()` (`for q in list(self._subs.get(case_id, ())):`) — cheap,
and forecloses `RuntimeError: Set changed size during iteration` even if a future edit
adds an `await` to that loop.

### 3. WebSocket route (rework of `routers/ws.py`)

Stops polling. On every `accept()` — first connect **and** every reconnect, no
special-casing — send one immediate snapshot before relying on Realtime, then:
`queue = broadcaster.subscribe(case_id)`, and race `queue.get()` against
`websocket.receive_text()` (needed for both the `refresh()` nudge and disconnect
detection — a bare `await queue.get()` wouldn't notice a client leaving). On disconnect,
`broadcaster.unsubscribe(case_id, queue)` in a `finally`. `refresh()` becomes a direct
"build and send one snapshot now" trigger, independent of the Realtime path — useful
right after an upload, before the new row's insert has necessarily propagated through
Realtime.

`ACTIVE_POLL_INTERVAL_SECONDS`/`IDLE_POLL_INTERVAL_SECONDS`/`_poll_interval_for` are
deleted — nothing left to throttle.

**Why the always-send-on-connect matters:** it's the fix for the missed-event race
condition, not an optimization to skip. Pure event-driven push has a gap polling never
had — if a connection drops (browser hiccup, or the backend's own Realtime subscription
blipping) and reconnects, whatever changed during that gap is never replayed, since the
broadcaster only pushes to queues that are currently subscribed. Sending a fresh
snapshot on every `accept()`, unconditionally, closes that gap for free — the frontend
has no notion of "this is a reconnect" (`caseSocket.ts` just calls `connect()` again),
so as long as the backend never special-cases it, every reconnect self-heals.

### 4. Auth fix: short-lived WebSocket ticket (bundled, not deferred)

Replace the long-lived bearer token in the query string with a single-use ticket:
- New `POST /ws/tickets` (behind normal `require_auth` — the client sends its usual
  `Authorization` header, no new auth mechanism). Returns a random opaque UUID, valid
  **10 seconds**, single-use, stored server-side in-memory.
- `caseSocket.ts` mints one before opening the socket, connects with `?ticket=...`
  instead of `?token=...`. The master bearer credential never touches a WS URL or a
  Cloud Run access log — only a 10-second, single-use, low-value ticket does, and it's
  already consumed/expired even if that log entry leaked.
- `routers/ws.py` validates and immediately deletes the ticket on use, so a captured one
  can't be replayed within its own window.
- Every reconnect (see above) mints a **fresh** ticket — tickets are single-use, so
  reusing the original would just fail like an expired one.

## Why this survives scaling to N replicas and the eventual 5-service split unchanged

The broadcaster only knows about connections its own process is holding — deliberately.
No cross-instance coordination is needed because Supabase Realtime already provides it:
every subscribed instance receives every change independently (fan-out, not a
competing-consumer queue like a Pub/Sub pull subscription). 1 replica or N replicas is
the same code, just N independent listener+broadcaster pairs, each acting only on the
case_ids its own local connections care about.

It also slots into the planned split with no rework: this whole router + listener +
broadcaster trio belongs in the **Query Service** (read-only, dashboard-facing, per
`CLAUDE.md`) — it never calls the Per-Document Orchestrator or any other service, only
reads Supabase, consistent with "communication only via Pub/Sub events or shared
Supabase state, never direct function calls between services."

**What to explicitly avoid:** never have orchestration code (`orchestrator.py` today, or
a separately-deployed Per-Document Orchestrator later) call `broadcaster.publish()`
directly. That's the shortcut that works now and breaks the moment orchestration runs in
a different process — which is the plan. Always go through the database: orchestration
writes a row, Realtime notices it, *that* calls `publish()`. Nothing else may.

## Supabase free-tier fit

- **Realtime connections (200 cap):** one subscription per replica, not per case or
  connection — scales with replica count, nowhere near this cap at real scale.
- **Realtime messages (2M/month):** fan-out means volume scales as *(DB changes) ×
  (active replica subscriptions)* — a non-issue at 1-3 replicas, worth revisiting only
  at genuinely high replica counts under heavy load.
- **DB connections (60 direct / 200 pooler):** unrelated to Realtime (rides Postgres's
  logical replication, not a counted connection) — pre-existing consideration for the
  sync client's own query load, unaffected by this change either way.
- **7-day inactivity auto-pause:** unrelated to this design; a demo-day note (hit the
  API once to wake the project after a break).

## Files touched

**Backend**
- `supabase_client.py` — add the async client alongside the existing sync one, for
  Realtime only; existing sync call sites untouched.
- `realtime_listener.py` (new) — subscription + callback (case_id extraction, snapshot
  build, `broadcaster.publish`).
- `case_broadcaster.py` (new) — the `subscribe`/`unsubscribe`/`publish` registry.
- `routers/ws.py` — remove the poll loop; subscribe to the broadcaster; race
  `queue.get()` against `receive_text()`; add `POST /ws/tickets`.
- `auth.py` — short-lived ticket issue/verify functions alongside the bearer-token ones.
- `main.py` — start/stop the Realtime listener in `lifespan`.
- `config.py` — new `SUPABASE_SECRET_KEY` setting.
- `tests/test_ws.py` — replace poll-interval tests with broadcaster/publish tests; keep
  token-rejection and snapshot-on-connect tests, adapted to the ticket flow.
- New test coverage for the broadcaster and/or listener callback in isolation.

**Frontend**
- `caseSocket.ts` — call `POST /ws/tickets` before connecting; `?ticket=...` instead of
  `?token=...`; `refresh()` becomes a direct snapshot trigger.
- Wherever API calls live (`uploadService.ts`) — add the ticket-fetching call.

**Supabase project**
- Add `documents` (and `cases`, if used) to the `supabase_realtime` publication.
- Provision `SUPABASE_SECRET_KEY` as a backend-only secret.

## Verification

- Two tabs, same case, same running process — a status change pushes to both without
  either issuing a new poll request.
- Old poll-interval code path is gone; no test still asserts on it.
- **Reconnect check:** drop a connection, change the case's state while it's down, let
  it reconnect — the next snapshot reflects the change immediately, not after a second
  trigger.
- **Bulk-upload check:** upload 10+ files to one case at once (real supported path, see
  `MAX_BULK_FILES`) — confirm the debounce collapses the resulting insert burst into one
  query/push rather than ten, the frontend updates smoothly (no rapid-fire re-render
  flashing), and the snapshot correctly reflects all N documents' progress together
  (e.g. "7 of 10 processed"), not just whichever row's change last triggered a push.
- Expired/already-used/never-issued tickets are all rejected the same way an invalid
  token is today (`close(code=1008)`).
- Bearer token never appears in the WS connection URL — only the ticket does — and
  reconnects mint a fresh ticket rather than reusing one.
- Full backend suite green, including reworked `test_ws.py` and new broadcaster tests.
