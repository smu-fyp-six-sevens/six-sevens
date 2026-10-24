# Validating model replies and tool results

Every model reply and tool result is checked against an expected shape before anything uses it. A bad one is repaired, replaced by a safe default, or recorded as "could not assess". It never crashes a run and never counts as "clean". Built 2026-10-16; this is how it works now and where it stops.

## Principles

1. **Nothing crosses a boundary untyped.** A pydantic model validates every reply and tool result before it reaches a repository, a score or an API response.
2. **A bad output stays where it landed.** It is repaired, defaulted or marked unassessed at that node, never raised past it.
3. **Fail closed on risk.** A check that could not run is `unable_to_assess`, never "passed". The summary may not call an unrun check clear.
4. **Every degradation is recorded** in the trace, so it can be seen and trended.
5. **Prefer forced tool calls to parsing text** for small decision outputs (the plan, the judge, evidence requests). Extraction stays text JSON: large per-type schemas as tool definitions would add prompt tokens and latency.

## The pieces

- **`app/llm_io.py`**: one parser (`parse_json_object`: strips `<think>` blocks and fences and prose around the object), `forced_args` for forced tool calls, `was_cut_off`, `salvage_list` / `salvage_entries` for cut-off replies, and `validate(Model, data)`, which never raises and returns `Checked` with `value`, `status` and `issues`. Status is `ok`, `degraded` (partly usable; fields that didn't fit were dropped so their defaults apply) or `failed` (nothing usable; the caller applies its own fallback). Validators are lenient on shape (coerce, default) and strict on meaning (never invent content). `message_text` reads block-list replies correctly.
- **`app/llm_contracts.py`**: one model per reply the system asks for: `ClassifierReply` (the classifier agent, the LLM classifier and vision), `ExtractionReply`, `LeadPlan`, `LeadReview`, `SpecialistAssessment`, `EvidenceRequests`, `SummaryReply`. They double as the `input_schema` of forced tool calls. Hypotheses and verdicts are guarded item by item and checked in `verify.py`. A test fails if a contract gains a numeric field (only the two counters on `ToolResult` are allowed), so coercion can't make up a number.
- **`ToolResult` / `ToolFinding`**: every detection tool returns `{tool, unable_to_assess, nothing_to_check, reason, findings, truncated}`. A finding always ends up with an `id` and a `summary`; one without a summary gets a placeholder rather than being dropped, because dropping it could let a flagged document read as clean. `_collect_node` reads each tool message through `ToolResult` and marks an unreadable result, or findings that did not validate, `unable_to_assess`.
- **Stored reports** carry a `schema_version`, and every read goes through `read_report()` (no migration of old rows).

## Fallback per site

| Site | On a failed or unreadable reply |
|---|---|
| Classifier agent | One cheap re-ask, then `unrecognised` and manual review. Each field is read on its own: an unreadable `confidence` takes 0 and manual review, the rest is kept |
| LLM classifier, vision | Empty candidates plus a note; the agent relies on the rule classifier |
| Extraction | See below |
| Lead plan | Default brief for every specialist; carry on |
| Lead review | No summary, no second look; carry on |
| Specialist final reply | Findings stay unassessed (not approved), `uncertain=True`; assessments that finished before a cut-off are kept |
| Hypotheses / verdicts | Empty list |
| Evidence requests | Empty list; the analysis completes without an outlook (forced tool call with a text fallback) |
| Summary | Deterministic summary |

Transient provider errors are retried by the Anthropic client (`LLM_MAX_RETRIES`, 1); no extra retry layer.

## Extraction

- An unreadable reply sets `output_error` and leaves `escalation` empty, so `_decide_after_extract` fails the document (retryable by the officer) without reclassifying it. It is retried once first, showing the model its own reply and asking for only the JSON; not for a cut-off reply, which would be cut off again. The retry counts against the call ceiling.
- `extracted_fields` is required (`{}` is a valid, empty extraction). A reply that is a list, a string or prose around JSON no longer raises.
- A cut-off reply keeps the complete fields (`salvage_entries`); the document fails with "cut off at the token limit; N complete field(s) were kept" and the officer sees the partial fields. There is no larger-limit retry (it doubles cost for a reply that is often cut again).
- Nothing is written when there is no usable output, so a failed rerun after a type change doesn't wipe the stored fields.
- Field confidence is clamped to 0–1; anything that isn't a number is unknown (`None`), never a score.
- **Toss-back for a low-confidence guess.** A classification below the confidence threshold is extracted with the generic schema, which can't object to the guess on its own. The generic extraction now gets the classifier's guess as its own block after the cached prefix (first pass only) and may say "this isn't a list of loans". Checked live on `C067_BudgetProposal.pdf`.

## Bounds

| Concern | Control |
|---|---|
| Runaway model calls | `run_context.py`: one `RunContext` per run, shared by parallel nodes and threads. A document run may make `DOCUMENT_MAX_MODEL_CALLS` (24) calls; a detection run `DETECTION_MAX_MODEL_CALLS` (30); the case analysis has its own ceiling of 6. Past a detection limit the lead and specialists stop asking, findings so far stay unapproved, and the verdict cannot be "clean" (an "unable to assess: run limit" entry is added). Calls are counted, not tokens; the client's own retries are bounded by `LLM_MAX_RETRIES`, not counted |
| Repaired output not traceable | `validate(..., raw=...)` records every non-`ok` result (site, issues, the raw reply capped at 4,000 characters, what was used) in the run context; it is written to the trace as a `run_audit` step (detection, document runs) or to the run trace (case analysis). Sites that don't pass `raw` (the binder, the classifier) are not recorded |
| Hanging tools | `detection/tool_timeout.guard_tool` limits every tool (`TOOL_TIMEOUT_SECONDS` 60; `SLOW_TOOL_TIMEOUT_SECONDS` 120 for the model and registry tools); a timeout becomes `unable_to_assess`. The case-analysis evidence tools have the same limit |

The tool limit counts working time only; a job waiting for a free CPU worker has its own, longer cap (`CPU_QUEUE_TIMEOUT_SECONDS`). A running job can't be stopped (a thread locally, a process when deployed, `cpu_pool.py`), so a hung CPU check holds its worker until it returns; a process that crashes is replaced with a fresh pool.

## Not covered

- Nothing checks that an extracted value has the type its schema expects (a text value in a number field).
- The AI-provenance and other checks that can't run (no exiftool) report `unable_to_assess`; that is by design.
- Verifying that the model's content is *right* (citations, grounding, severity caps) is `verify.py` and the summary check, not this layer.
- The HTTP API is validated by pydantic response models on the backend; the frontend does not validate at runtime.
