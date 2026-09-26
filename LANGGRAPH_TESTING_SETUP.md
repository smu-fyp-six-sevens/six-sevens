# Testing the LangGraph pipeline locally (`langgraph-test` branch)

`queue_service.py` now hard-switches every document upload to the new LangGraph pipeline
(`backend/app/langgraph/`) instead of Dify (see `LANGGRAPH_MIGRATION_PLAN.md`). This is
phases 0-4: classify (a deterministic rule pass, then a Sonnet agent that decides itself
whether to call the Haiku classifier and/or vision as tools), extract (Sonnet), and a bounded
classify<->extract reclassify loop when Extraction disagrees with the classified type. No
persistence/checkpointing yet. Dify itself is untouched and still running in the cloud, just
not called from this branch's upload path.

## 1. Install the new Python dependencies

```bash
cd backend
pip install -r requirements.txt
```

This adds `langgraph`, `langchain-anthropic`, `langgraph-checkpoint-postgres` (not used yet —
installed ahead of Phase 4), and `psycopg[binary,pool]` (same). Everything else in
`requirements.txt` is unchanged.

## 2. Add your Anthropic API key

Add to `backend/.env` (new variable, not used by the Dify path):

```
ANTHROPIC_API_KEY=sk-ant-...
```

Everything else already in `backend/.env` (Supabase, auth) stays as-is — the langgraph
pipeline still reads/writes the same `documents`/`cases` tables and Supabase Storage bucket.

Optional overrides, all default to sensible values (see `backend/app/config.py`):

```
LANGGRAPH_SONNET_MODEL=claude-sonnet-4-6      # classifier agent, vision, extraction
LANGGRAPH_HAIKU_MODEL=claude-haiku-4-5-20251001   # llm_classify (was DeepSeek in Dify)
LANGGRAPH_RECURSION_LIMIT=60                  # caps the classify/reclassify tool-call loops
```

## 3. Run the backend and frontend as usual

```bash
# terminal 1
cd backend
uvicorn app.main:app --reload

# terminal 2
cd frontend
npm run dev
```

Nothing else changes about how you use the app — log in, open a case, upload a document the
normal way. The upload flow itself, the document statuses (`uploaded → queued → processing →
extracting → analyzed/failed`), and the WebSocket live updates are all unchanged; only what
runs behind `processing`/`extracting` is different now (LangGraph + Claude instead of Dify).

## 4. Watch where a document gets routed

Open any document's detail page (`DocumentDetailsPage`) — there's a new **"LangGraph trace
(debug)"** panel near the bottom (below the extracted fields, above Approve/Reject). It's
intentionally plain (monospace, no styling) and shows, live while the document is processing:

- **rule_top_type / strength** and **certain_type** — what the deterministic rule pass found
  before the agent ever ran, and whether it was decisive enough to hand the agent a
  `certain_type` hint.
- **llm_top_type / strength** and **vision_top_type / strength** — only populated if the agent
  actually chose to call that tool for this document; empty means it decided not to.
- **final_status** — `analyzed` or `failed`.
- A **"Full step log"** `<details>` you can expand for every step the graph took, in order,
  with a timestamp relative to when the document started: `ingest`, `rule_classify`,
  `agent_call` × N — each entry shows `offered_tools` (what it *could* call that turn:
  `llm_classify_tool` only on the first call, `vision_llm` only from the second call onward —
  this order is enforced in code, not just prompted, since vision sends the whole document
  image and is by far the most expensive option) and `tool_calls` (what it actually called, if
  anything) — then `validate`, `classification_finalised`, `extraction`, `done`. If Extraction
  disagreed with the classified type, you'll also see `record_escalation` and `reclassify`
  before another `agent_call`/`validate`/`classification_finalised`/`extraction` cycle.
- **classification_reasoning** — the classifier's plain-language justification. Shown here
  only for now; not written to Supabase yet (deliberate, see `LANGGRAPH_MIGRATION_PLAN.md`).

This trace is **in-memory only** (`backend/app/langgraph/trace.py`) — it resets if you
restart the backend, and only exists for documents actually processed via LangGraph in the
current process. It's a debugging aid for this branch, not a persisted feature.

## 5. Prompt caching

Every static prompt (llm_classify's system prompt, the classifier agent's instructions, and
each document type's extraction schema) is sent with an Anthropic `cache_control` breakpoint,
so repeated runs of the same document type reuse the cached prefix instead of re-sending it.

Because uploads are processed by 4 concurrent workers, the first few documents of a batch
would normally all race to write the same cache entry and all miss. `cache_gate.py` prevents
that: for each distinct static prompt, exactly one call goes first and actually populates the
cache; every other concurrent call for that same prompt waits for it to finish, then proceeds
with a warm cache. You'll see this as the very first document of a batch (or of a new document
type) taking a little longer than the ones right after it — that's expected, not a bug.

## 6. Try the Detection Agent

Open a finished (`analyzed`) document and use the **Detection (dev)** panel: press *Run detection*.
A lead investigator (Sonnet) picks the specialists this document needs — starting from a default team
for the document type, and noting any deviation from it — and records why it skipped the others, the three specialists (Layout & Rendering, Content Validation, Digital Authenticity; Haiku) run their tools in parallel, the lead may ask for one second
look, and you get one combined report: verdict (`flagged` / `clean` / `inconclusive`), the
specialists consulted and skipped with reasons, and the merged findings. The report is stored on
the document's langgraph trace (so it survives restarts and shows in the `langgraph_trace`
table under `detection_report`); nothing is written to the `documents` table. For a whole
case: `GET /cases/{case_id}/detection/reports`.

## 7. What isn't wired up yet (by design, later phases)

- **No checkpointing/persistence.** Nothing here is saved to Postgres as graph state; a crash
  mid-run just fails the document the normal way (same as Dify does today).
- **`classification_reasoning` isn't saved to Supabase yet** — deliberately, see section 4 above.
- **Detection isn't wired to approval yet**: it only runs from the dev endpoint/panel, with no `pending_review`/`detecting` statuses or persisted findings (needs DB changes, deferred until after UAT).

## 8. If something breaks

- `ANTHROPIC_API_KEY` missing/invalid → documents will go straight to `failed` with an
  Anthropic auth error in `failure_reason` and in the backend logs.
- Import errors on startup (`ModuleNotFoundError: langgraph` etc.) → step 1 wasn't run in the
  same virtualenv/interpreter the backend actually uses.
- A document sits at `processing`/`extracting` far longer than usual → check the backend
  terminal for exceptions; `pipeline.py`'s outer catch always marks the document `failed`
  eventually rather than leaving it stuck, but a slow Anthropic response or an unbounded
  Sonnet tool loop (capped by `LANGGRAPH_RECURSION_LIMIT`) can still make it feel stuck.
