# LangGraph migration plan (branch `langgraph-test`)

**Goal:** replace the Dify Classifier chatflow, Extractor workflow, the three classifier tool workflows and `orchestrator.py` with LangGraph
inside the FastAPI backend, then build the fraud Detection team natively on it. DSL files: `backend/app/dify/`.
This branch is an experiment: it will not go to prod, and the deployed cloud keeps running Dify until the workflow is run manually.

## 0. Status

**Phases 0-4's core are built and wired live** (`backend/app/langgraph/`) — `queue_service.py` hard-switched to it, so
every upload on this branch now runs through LangGraph, not Dify. See `LANGGRAPH_TESTING_SETUP.md` for how to run/test it.

- **Classify architecture changed from a deterministic router to a mostly agent-owned one** (deliberate departure from
  Dify, not a port): only `rule_classify` runs deterministically. `llm_classify` and `vision_llm` are both tools the
  Sonnet agent calls itself — but not in whatever order: `llm_classify_tool` is only offered on the agent's first turn,
  `vision_llm` only from the second turn onward (code-enforced, not just prompted — vision sends the whole document
  image, easily the most expensive call in this pipeline). Within that ordering the agent still genuinely decides
  whether to call either at all. See section 2/3.
- Rule classifier, vision prompt, and both classify parse steps were diffed against the exact Dify YAML source rather
  than eyeballed. `rule_classify`, `llm_classify_tool` and `vision_llm` all return the same `{top_candidates (top 3),
  notes}` shape.
- Prompt caching (section 5) is done ahead of schedule: `cache_control` on every static prompt prefix, plus `cache_gate.py`
  so concurrent workers don't all race the same cache write (the "run a batch's first document alone" mitigation).
- A dev-only trace (`trace.py` + `GET /documents/{id}/langgraph-trace` + a debug panel on the document details page) shows
  which tools each document's agent actually called and what each returned — not part of the original plan, added for
  testing. `classification_reasoning` is intentionally left out of it for now (planned, not yet wired in).
- Model IDs confirmed against the account's live `/v1/models` list (not guessed): `claude-sonnet-4-6` and
  `claude-haiku-4-5-20251001` — see `config.py`'s `LANGGRAPH_SONNET_MODEL`/`LANGGRAPH_HAIKU_MODEL` defaults.
- **The reclassify loop is built** (Phase 4's core): a bounded classify<->extract cycle
  (`settings.MAX_RECLASSIFICATION_ATTEMPTS`), reusing the Sonnet agent as a conversation continuation (not a fresh
  rule re-guess) when Extraction objects to the classified type — see section 3.
- **A "certain" rule tier is a hint, not a bypass**: `rule_classify` can find a decisive, type-unique textual signal
  (verified against every generator under `backend/doc_gen_scripts/`, not guessed) and surfaces it to the agent as
  `certain_type`, but the agent still decides whether that's enough — see section 2.
- **Classification reasoning is captured but deliberately not persisted yet**: every classify path produces a
  `reasoning` string (previously parsed out of the LLM's JSON and silently dropped on both the Dify and langgraph
  paths), and it now shows up in the langgraph debug trace — but `document_repository.record_classification` accepts
  the field without it being wired to Supabase from either path for now, so `documents.classification_reasoning` stays
  unused until that's turned on deliberately.
- **Rerun-extraction is built for this path too**: the manual "correct document type" endpoint now calls this graph's
  own extraction, not Dify's — and only actually re-runs Extraction when the officer changed the type; confirming an
  unchanged type just marks it officer-confirmed (`document_repository.record_classification`, no re-extraction).
- **Not yet built:** Postgres checkpointing/pruning — see section 6.

## 1. Decisions

| Topic | Decision |
|---|---|
| Cutover | Hard switch on this branch. No feature flag. Dify keeps running in the cloud in the background. |
| Order | Classify + Extract first (phases 0-4), Detection after (4b). Goal is to judge whether LangGraph fits before investing more. |
| `llm_classify` model | Haiku 4.5 instead of DeepSeek (one fewer vendor, shares the Anthropic cache). |
| HITL | Non-blocking. No long-lived `interrupt()` runs (see section 4). |
| Database | Supabase **free tier only** (500 MB). Design below keeps checkpoints tiny. |
| Detection team | Lead + 3 specialists: **Layout & Rendering**, **Content Validation**, **Digital Authenticity**. The lead decides which to call per document. |

## 2. What we are replacing

| Dify piece | Really is | LangGraph |
|---|---|---|
| Classifier chatflow (Code 3, Agent 3, Validate Output) | One Sonnet agent that collates two pre-computed results and may call vision once | Not a port — see below: the agent now owns routing itself, calling `llm_classify`/`vision_llm` as real tools rather than being handed pre-computed results |
| `rule_classify_v3` | Python regex code node, no LLM | Same function, port verbatim, **plus** a langgraph-only "certain" tier (decisive, type-unique phrases verified against `doc_gen_scripts/`) surfaced to the agent as a hint |
| `LLM classification v2` | DeepSeek call + parsing, always runs | Now `llm_classify_tool` — Haiku, structured output, called by the agent only when it decides to |
| `vision_classify_v3` | Logs into our own backend, downloads the file, calls Sonnet vision | A tool reading storage directly (drops the login/download hops), called by the agent only when it decides to |
| Extractor workflow | Single Sonnet call | One node (`build_extraction_prompt` stays) |
| `orchestrator.py` | Ingest, classify, extract, bounded reclassify loop, persist, status writes | Graph with a conditional edge for the loop; repository writes stay inside nodes |
| Dify `conversation_id` | Memory for the reclassify turn | Message history in graph state |
| `DocumentQueue` (4 workers) | Concurrency | Unchanged |

Only DeepSeek/Anthropic/Supabase are real remote APIs; the rule classifier is local.
**Deliberately not a 1:1 port here:** Dify's Agent 3 was handed two already-computed results and could only optionally
call vision. This graph only pre-computes `rule_result` deterministically; `llm_classify` and `vision_llm` are both real
tools the agent calls itself, zero or more of either — routing is the agent's decision, not a hardcoded gate, which is
the whole point of calling this "agentic" at all (see LANGGRAPH_MIGRATION_PLAN.md discussion history: a deterministic
router that skips the agent whenever sources agree is efficient but stops feeling agentic).

## 3. Document graph (classify + extract)

```
ingest -> classify_subgraph -> finalise_classification -> check_text_available -> extract -+-> mark_analyzed
                                        ^                                                   |
                                        +-- record_escalation -> reclassify (agent only) <--+   (max MAX_RECLASSIFICATION_ATTEMPTS,
                                                                                                  else -> mark_unresolved_escalation -> fail)

classify_subgraph:  rule_classify (deterministic) -> agent (Sonnet, tools: llm_classify_tool, vision_llm) -> [tools -> agent]* -> validate -> repair
```

Built as-is in `graph.py`/`classify_graph.py`/`nodes.py`. The agent decides itself which of its two tools to call, if
any — rule_result (including a `certain_type` hint when one fired) is the only thing computed for it in advance. The
reclassify branch re-enters at the agent directly (never rule_classify again) and continues the same `agent_messages`
conversation — matching Dify's `conversation_id` continuation. Since the agent now always runs at least once per
document, `agent_messages` is never empty by the time a reclassify could happen, so this is always a continuation, not
a fresh seed.

- **State (typed, small, PII-free):** ids, filename, storage path, `text_hash`, capped tool results, classification, extraction summary
  (confidence + escalation), `attempts`, agent `messages`, `failure_reason`. Text (`text_cache.py`, run-scoped, keyed by `document_id`)
  and extracted fields are not in state — never checkpointed once Phase 4 turns checkpointing on.
- `pipeline.process_document` mirrors `orchestrator.process_document`'s signature and "always ends ANALYZED or FAILED, never raises"
  contract, so `queue_service`'s worker loop needed only a one-line swap (done) and the routers are unchanged.
- **Loops (all capped):** classify<->extract cycle (Phase 4, not yet), ReAct tool loop inside the classify agent (`LANGGRAPH_RECURSION_LIMIT`,
  built), lead<->specialists (section 4, not yet). Each ends in manual review or "unable to assess", never an unbounded retry.

## 4. Approval gate and Detection team

Document flow: `queued -> classifying -> extracting -> pending_review -> detecting -> analysed`.
`pending_review` replaces the old overloaded `analysed` (extracted, awaiting officer approval); `analysed` now only means fully done (detected too).
Detection depends on approval, so it is a **second graph triggered by the approval endpoint**, not a pause inside the first graph — human-in-the-loop
with no `interrupt()`, no long-lived run, and no change to stuck-detection or queue draining. Nothing follows detection this sprint, so `analysed`
is terminal for now; the per-case pipeline (waits for all documents) is next sprint.

Consequences to handle:
- Frontend/status-formatting (`formatDocumentStatus`, `TERMINAL_DOCUMENT_STATUSES`) and `monitoring._STUCK_ELIGIBLE_STATUSES` need `pending_review` and `detecting` added, same pattern as the earlier `extracting` addition.
- New approve endpoint moves `pending_review -> detecting` and kicks off the detect graph.
- A detection failure must not undo extraction: it gets its own failure state and can be re-run alone without re-extracting.

```
detect(document_id)
  lead (decides which specialists, and why) -> Send in parallel -> specialists -> lead synthesises -> findings
```

- **Default team per document type (built).** The lead's prompt carries an expected/optional team for each document type (income/financial documents: all three; corporate and identity documents: Content + Digital, Layout optional; unrecognised: none). It's guidance, not routing: the lead may deviate with a reason, and any deviation is recorded (`plan_deviation` in the report, `detection_deviation` in the trace). Layout is dropped from the expected set for non-PDF files.
- **The lead is the agent.** From document type and content it chooses which specialists to run and records a reason for each one it skips
  (auditable, and part of the report). Each specialist likewise chooses which of its own tools to call. A fixed doc-type table is only a prompt hint, not the router.
- **Specialists (proposed tool mapping):**
  - Layout & Rendering: `font_consistency_v2` (fonts, sizes, row deviations), later misalignment/spacing.
  - Content Validation: `spelling_anomaly`, plus date-consistency and bank-statement running-balance (numerical) checks — built.
  - Digital Authenticity: metadata (ExifTool) checks — built; AI-generated/image manipulation checks not yet.
- **Isolation:** each specialist is a subgraph with private messages; only a compact structured finding goes back to the lead.
- **Tool results are ground truth.** Findings are merged deterministically; the LLM adds severity and rationale but cannot delete a finding.
  "Unable to assess" is its own result, never read as a pass. Documents are untrusted, so text inside one must not be able to suppress a result.
- **Tools are local and parameter-bound:** bind `document_id` from state, fetch bytes/fields inside the tool (reuse helpers in `routers/detection.py`), return size-capped results.
  `font_consistency_v2` is CPU-bound (run via `asyncio.to_thread`, bounded pool). `spelling_anomaly` re-ingests text; pass it in instead.
- **Cost:** up to 1 lead + 3 specialist runs per document. Haiku for specialists, Sonnet for the lead; measure before adding more.
- **Evals:** `injection/pdf.py` makes forged documents with known ground truth, so specialists can be scored on precision/recall. Keep the synthetic-to-real caveat from CLAUDE.md.

## 5. Cross-cutting design

**Persistence (free tier).** Checkpoints are for crash recovery and short-term debugging, not history.
- Keep PII and text out of state. `raw_text` lives in a run-scoped in-memory cache filled by `ingest`; re-ingest on a miss (RapidOCR is local and free). Later nodes reload extracted fields from Postgres.
- Only the parent graph checkpoints; classify and specialist subgraphs use `checkpointer=False`.
- Delete a document's thread when it finishes (`adelete_thread`); keep failed threads for a few days. The checkpoint tables have no `created_at` column, so sweep by `thread_id` (= `document_id`) in app code.
- `langgraph-checkpoint-postgres` with psycopg3 `AsyncConnectionPool`, transaction pooler (6543), `prepare_threshold=None` (confirmed), small pool. Run `.setup()` once via a session connection. Own `langgraph` schema, not in the Realtime publication.
- If churn hurts the case DB, point `LANGGRAPH_DATABASE_URL` at a second free Postgres (another Supabase project or Neon). Not SQLite (ephemeral disk) or hosted Redis (missing modules). `MemorySaver` for tests.
- Phase 0 measures bytes per document; target steady state well under ~50 MB. Plan a keep-alive for the free-tier inactivity pause before the November demo.
- Time travel works for in-flight and failed threads, enough to replace the hand-written `rerun_extraction` for "fix type and re-extract".

**Tools over the internet (Anthropic, Supabase).** Keys only from env. Each tool returns a small validated Pydantic model (top 3 candidates, capped strings), never raw provider JSON.
Timeouts and bounded retries on 429/5xx, none on 4xx. A failed tool returns "treat as absent" instead of raising; `ToolNode(handle_tool_errors=True)` as the outer net (confirmed).

**Files and parameter binding.** Pass references (`document_id`, `storage_path`), never bytes; tools fetch inside. Use `InjectedState` so `document_id`, `filename`, text etc. never appear in the tool schema
the model sees (the Dify `tool_configurations` equivalent); test that the schema is clean. The agent never sees raw text. Tool definitions are cached prefix and cost tokens, so keep them short and static.

**Repair node.** After the agent: deterministic cleanup first (strip `<think>` and code fences, reuse `_parse_extraction_output`), then one Haiku re-ask, then manual review. Prefer structured output over parsing prose.

**Models.** Sonnet: classifier agent (only when needed), extraction, vision, detection lead. Haiku 4.5: llm_classify, repair, specialists. Names come from settings.

**Prompt caching (high priority) — built.** Static first, document text last, `cache_control` on the static block only; the classify agent's
system message is split into a static block (cached) and a per-document inputs block (not) so concatenating them can't break the cache.
Extraction: per-type schema first (`cache_control` there), `raw_text` last (13 possible prefixes, one per document type).
Classifier and extractor prompts differ, so they only share a cache via an identical leading block on the same model.
`cache_gate.py` solves "parallel identical requests can all miss": the first call for a given static prompt (`llm_classify`,
`classify_agent`, `vision_llm`, `extract:{document_type}`) runs alone and warms the cache; concurrent siblings wait for it, then
proceed warm. Once a key's been served once, `run()` never blocks for it again. **Not yet built:** recording cache read/creation
tokens into Prometheus (needs Phase 6's observability work first). Never vary tool lists per request (invalidates the cache); prune
tools statically only.

**Other cost controls.** Router skips the agent when sources agree; vision at most once; whitespace/length capping of text sent to classification; `recursion_limit` and a tool-call cap.

**Concurrency.** Keep the in-process queue (4 workers). Add shared per-provider limiters (semaphore or LangChain rate limiter, start at 3-4 for Anthropic). Detection fans out up to 3x, so limits must be global, not per graph.
LangGraph `max_concurrency` only bounds fan-out within one run. Redis or a broker only when running multiple instances (Cloud Pub/Sub per CLAUDE.md); not now.

**Guardrails and PII.** Outputs keep PII (it is the product). We control where it goes: masked logs and traces (ids, lengths, scores only), PII-free checkpoints, no LangSmith in prod, agent tools read-only.
Treat uploaded documents as untrusted (prompt injection): delimited data blocks, bound tools. Encryption of checkpoints is optional defence in depth; LangGraph may not ship an encrypted saver, so wrap the serializer with Fernet/AES-GCM if needed.

**Evals.** Golden set from `documents_generated/` (classification accuracy, per-field extraction, escalation rate, tokens and cost per doc, cache hit rate), run nightly; a mocked subset on every PR.

**Observability.** OpenTelemetry as the primary (vendor neutral, Cloud Trace, PII masking). LangSmith dev-only. Prometheus/Grafana complement traces: traces explain one run; metrics show the system
(queue depth, latency per node, failures, tokens/cost, cache hit ratio).

**Conversational state and learning.** Reclassify memory becomes checkpointed `messages`. The LangGraph Store (learning from officer corrections) is the very last phase.

**CI/CD and deploy.** Tests use fake chat models and `MemorySaver`, no network; existing 100% coverage bar applies. Same container and `deploy.yml`, LangGraph as a library (no LangGraph Platform).
New secrets: `ANTHROPIC_API_KEY` (new to this backend; today the keys live in Dify), `LANGGRAPH_DATABASE_URL`, optional encryption key. Cloud Run settings unchanged.

## 6. Phases

0. ✅ **Spike:** install `langgraph`, `langchain-anthropic`, `langgraph-checkpoint-postgres`, `psycopg[binary,pool]`; one Sonnet node with `cache_control`.
   (Checkpoint-size measurement deferred to Phase 4, when checkpointing actually turns on.)
1. ✅ **Skeleton:** typed state (`state.py`), deterministic nodes ported from `orchestrator.py` (`ingest`, `finalise_classification`,
   `check_text_available`, `extract`, `mark_analyzed`, `fail` — `nodes.py`). Unit tests written (`test_langgraph.py`), not yet run
   against installed deps.
2. ✅ **Classifier:** rule code ported and diffed against source (`rule_classify.py`), Haiku node (`llm_classify.py`), parameter-bound
   vision tool returning top-3 (`vision_tool.py`), deterministic router (`router.py`), Sonnet agent + validate/repair
   (`classify_graph.py`, `classify_support.py`).
3. ✅ **Extractor:** node plus parse (`extract.py`, reuses `orchestrator._parse_extraction_output`). Repair node for extraction not
   built — only the classifier side has one so far.
4. 🟡 **Loop + persistence:** reclassify cycle ✅ done. Postgres checkpointer with pruning, `rerun_extraction` on this path: not yet.
   *Checkpoint to judge whether LangGraph fits before continuing.*
4b. 🟡 **Detection:** the agent module is built (`backend/app/langgraph/detection/`): lead investigator, three specialists (layout & rendering, content validation incl. numerical checks, digital authenticity), tools wrapped, parallel dispatch, one code-enforced second look, deterministic findings merge, report stored on the langgraph trace. Triggered from a dev endpoint (`POST /documents/{id}/detection/run`) with no DB changes. Still to do after UAT: approval endpoint/marker, `pending_review`/`detecting` statuses, persisting findings, wiring detection to approval.
5. 🟡 **Concurrency and caching:** cache breakpoints + `cache_gate.py` done (see section 5) — ahead of schedule, since testing needed
   it. Per-provider rate limiters and cache-hit metrics still not built.
6. 🟡 **Observability:** OpenTelemetry tracing is wired (`backend/app/telemetry.py`) — off by default (`OTEL_ENABLED`), FastAPI auto-instrumented, manual spans on every model-call boundary (classify agent, extraction, detection lead/specialists) with token-usage attributes, exports to Google Cloud Trace when `GOOGLE_CLOUD_PROJECT` is set, else prints to console. Setup steps (env vars, GCP credentials) are in `LANGGRAPH_TESTING_SETUP.md` section 6. Guardrails, evals, CI still not built. (The dev-only trace panel is not this — it's a testing aid, not OTel/Prometheus.)
7. ⬜ **Learning/Store** last. `orchestrator.py`, `dify_client.py` and their tests are already deleted on this branch (their shared helpers moved into `langgraph/nodes.py` and `langgraph/extract.py`); the Dify DSL files, env keys and deploy config are removed when this branch is adopted.

## 7. Settled and deferred

**Settled:** `raw_text` stays out of checkpoints. Reproducibility comes from re-ingesting the stored file (RapidOCR is deterministic) and keeping
`text_hash` in state to detect drift, so no extra text storage is needed while experimenting.

**Deferred until we build it** (experimentation stage, no need to decide now):
- Exact tool-to-specialist mapping, and where ExifTool metadata and AI-generated checks live today.
- Approval marker: new column on `documents` or an existing field.
- Version checks: checkpoint-on-exit durability, encrypted serializer availability, `autocommit=True` + `dict_row` for the Postgres saver.
