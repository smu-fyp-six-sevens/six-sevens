# Testing the LangGraph pipeline locally (`langgraph-test` branch)

`queue_service.py` now hard-switches every document upload to the new LangGraph pipeline
(`backend/app/langgraph/`) instead of Dify (see `LANGGRAPH_MIGRATION_PLAN.md`). This is
phases 0-3: classify (rule + Haiku + a Sonnet agent fallback) and extract (Sonnet), no
reclassify loop and no persistence/checkpointing yet. Dify itself is untouched and still
running in the cloud, just not called from this branch's upload path.

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
LANGGRAPH_SONNET_MODEL=claude-sonnet-5        # classifier agent, vision, extraction
LANGGRAPH_HAIKU_MODEL=claude-haiku-4-5-20251001   # llm_classify (was DeepSeek in Dify)
LANGGRAPH_ROUTER_AGREEMENT_THRESHOLD=0.8      # below this, the Sonnet agent gets called
LANGGRAPH_RECURSION_LIMIT=15                  # caps the classify agent's tool-call loop
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

- **route** — `fast` (rule_classify + llm_classify agreed strongly enough that the Sonnet
  agent was skipped entirely — this is the "biggest saving" the migration plan is about) or
  `agent` (Sonnet was called, optionally with the `vision_llm` tool).
- **rule_top_type / strength** and **llm_top_type / strength** — what the two cheap
  classifiers independently landed on, so you can see *why* it went fast or agent.
- **final_status** — `analyzed` or `failed`.
- A **"Full step log"** `<details>` you can expand for every step the graph took, in order,
  with a timestamp relative to when the document started (`ingest`, `rule_classify`,
  `llm_classify`, `route`, `agent_call` × N with which tools it called, `validate`,
  `classification_finalised`, `extraction`, `done`).

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

## 6. What isn't wired up yet (by design, later phases)

- **No reclassify loop.** If Extraction thinks the classified type is wrong, the document is
  routed straight to `failed` with a clear reason, instead of looping back to reclassify
  (that's Phase 4 — see `LANGGRAPH_MIGRATION_PLAN.md` section 6).
- **No checkpointing/persistence.** Nothing here is saved to Postgres as graph state; a crash
  mid-run just fails the document the normal way (same as Dify does today).
- **The manual "correct document type" action still uses Dify.** `PATCH
  /documents/{id}/document-type` calls `orchestrator.rerun_extraction`, not the langgraph
  pipeline — that's also Phase 4 scope.
- **Detection Agent isn't built at all yet** (separate, later work — see `CLAUDE.md`).

## 7. If something breaks

- `ANTHROPIC_API_KEY` missing/invalid → documents will go straight to `failed` with an
  Anthropic auth error in `failure_reason` and in the backend logs.
- Import errors on startup (`ModuleNotFoundError: langgraph` etc.) → step 1 wasn't run in the
  same virtualenv/interpreter the backend actually uses.
- A document sits at `processing`/`extracting` far longer than usual → check the backend
  terminal for exceptions; `pipeline.py`'s outer catch always marks the document `failed`
  eventually rather than leaving it stuck, but a slow Anthropic response or an unbounded
  Sonnet tool loop (capped by `LANGGRAPH_RECURSION_LIMIT`) can still make it feel stuck.
