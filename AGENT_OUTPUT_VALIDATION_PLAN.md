# Plan: validate agent and tool output

Status: built 2026-10-16, uncommitted. Checked with ruff, pyright, `import app.main` and the new tests run outside pytest; the existing suite and the regression tests were not run (see "What was built").

**Goal:** every model reply and tool result is checked against an expected shape before anything uses it. A bad one is repaired, replaced by a safe default, or recorded as "could not assess". It never crashes a run, and it never counts as "clean".

## Where we were

Several call sites already do this well. They are the pattern to copy.

| Site | What it does well |
|---|---|
| `case_pipeline/cross_document/agents.py` | Forced tool call (structured output), text fallback, `salvage_list` for cut-off replies, per-item `isinstance` checks, hard call budget |
| `cross_document/verify.py` | Citations, severity and category are checked in code; anything else is discarded |
| `risk/summary.py` | Grounded: cited ids must exist and no foreign risk level; falls back to a deterministic summary |
| `classify_support.validate_agent_output` + `classify_graph.repair_node` | Never raises; one cheap re-ask; then manual review |
| `langgraph/pipeline.py`, `detection.run_detection` | A crash is recorded; the document goes to FAILED or back to `approved` and is retryable |

The gaps, from reading every call site:

### 1. No single contract or parser
- Four parsers: `parse_extraction_output`, `llm_classify._extract_json`, `validate_agent_output`, `llm_output.parse_json_object`. They differ on `<think>` blocks, code fences and prose around the JSON.
- Output is read with `.get()` on unvalidated dicts. There are no models for what a model is expected to return.

### 2. Known crash paths
| Where | Failure |
|---|---|
| `extract.parse_extraction_output` | Calls `json.loads` on the whole reply. No brace-span fallback, so JSON with a sentence around it fails extraction. If the JSON is a list or string, `parsed.get` raises `AttributeError`. |
| `extract.parse_extraction_output` | `escalation` is passed through unchecked, whatever type it is. |
| `specialists._collect_node` | `json.loads(...)` may give a non-dict, so `result.get` raises. `finding["id"]` raises `KeyError`. One bad tool result fails the whole specialist, then the whole detection run. |
| `detection/tools._with_ids` | Uses `assert` to enforce the tool contract. It disappears under `python -O`, and when it fires it is reported as a generic "Tool error". |
| `risk/outlook.propose_requests` | The Haiku call is not inside a `try`. A provider error fails the **whole case analysis**, although "no evidence requests" is a fine result. |
| `detection/graph._ask_lead` | An API error or unparseable reply in `lead_plan_node` fails the whole run, although `_normalise_plan` already has a default brief for every specialist. |
| Truncation | Only the case pipeline handles `stop_reason == "max_tokens"`. Classifier, extraction, specialists and lead get a half-JSON reply and treat it as garbage. |

### 3. Silent degradation
- A failed parse is logged but not counted anywhere. There is no way to ask "how often does the specialist reply fail to parse".
- Stored detection reports (JSON in the trace) have no version. Code that reads them back indexes keys such as `r["specialist"]` and `f["id"]`, so a shape change across deploys breaks case snapshots.

### 4. No tests with malformed output
Nothing feeds a call site a bad reply. The "never raises" claims in docstrings are untested.

## Principles

1. **Nothing crosses a boundary untyped.** Model replies and tool results are validated by a pydantic model before they reach a repository, a score or an API response.
2. **A bad output stays where it landed.** It is repaired, defaulted or marked unassessed at that node. It does not raise past it.
3. **Fail closed on risk.** A check that could not run is `unable_to_assess`, never "passed". The summary already follows this rule (it may not call an unrun check clear); this extends it to every site.
4. **Every degradation is recorded** in the trace and counted, so it can be seen and trended.
5. **Prefer forced tool calls to parsing text.** The case pipeline already does this and is the most robust path.

## Design

### One module: `app/llm_io.py` (replaced `langgraph/llm_output.py`)

```
parse_reply(text)  -> dict | None        one extractor: strips <think>, fences, prose around the object
salvage_list(...)                         kept, for cut-off replies
validate(Model, data) -> Checked[Model]   lenient pydantic validation, never raises
```

`Checked` carries `value`, `status` and `issues`:

| status | meaning |
|---|---|
| `ok` | valid as returned |
| `degraded` | partly usable; the fields that didn't fit were dropped so their defaults apply |
| `failed` | nothing usable (not an object, or a required field is missing); the caller applies its own fallback |

Validators are **lenient on shape, strict on meaning**: coerce and default for shape (a string where a list belongs, a number out of range), but never invent content. Verifier-style checks (citations, grounding) stay in code as they are.

### Contract models (`app/llm_contracts.py`)

One model per reply the system asks for:

`ClassifierAgentReply`, `LlmClassifierReply`, `VisionReply`, `ExtractionReply`, `LeadPlan`, `LeadReview`, `SpecialistAssessment`, `Hypotheses`, `Verdicts`, `EvidenceRequests`, `SummaryReply`.

The same models become the `input_schema` of the forced tool calls, so prompt, schema and validator can't drift apart.

### Tool results: one `ToolResult` model

Every detection tool returns the same dict today (`_result`). Make it a model: `tool`, `unable_to_assess`, `nothing_to_check`, `reason`, `findings: list[ToolFinding]`, `truncated`. `ToolFinding` allows extras and always ends up with an `id` and a `summary`: a finding without a summary gets a placeholder instead of raising, because dropping it would let a flagged document read as clean. `ToolNode` already turns a tool exception into an error message, so no wrapper was needed; `_collect_node` reads each tool message through `ToolResult` and marks an unreadable result, or findings that did not validate, as `unable_to_assess`.

The case-pipeline evidence tools already return `{available, reason, ...}` with refs. Give them the same treatment: a small `EvidenceResult` model with `available` and `reason` required.

### Fallback policy per site

| Site | On `failed` / call error |
|---|---|
| Classifier agent | Existing repair re-ask, then `unrecognised` and manual review (keep) |
| LLM classifier, vision | Empty candidates plus a note (keep); the agent then relies on the rule classifier |
| Extraction | Fail the document with the real reason, or "cut off at the token limit" when `stop_reason == "max_tokens"`; never via an uncaught `AttributeError` |
| Lead plan | Default brief for every specialist and carry on |
| Lead review | No summary, no second look; carry on |
| Specialist final reply | Findings stay unassessed (not approved), `uncertain=True` (keep); also retry once if cut off |
| Hypotheses / verdicts | Empty list (keep) |
| Evidence requests | Empty list; the analysis completes without an outlook |
| Summary | Deterministic summary (keep) |

Transient provider errors (rate limit, 5xx, timeout) are already retried twice by the Anthropic client (`ChatAnthropic.max_retries` defaults to 2), so no extra retry layer was added.

## What was built

| # | Phase | Result |
|---|---|---|
| 1 | Known crash paths | Done: extraction (non-object JSON, prose around JSON, bad `escalation`), `_collect_node`, `_with_ids`, `propose_requests`, `lead_plan_node` / `lead_review_node` |
| 2 | `llm_io.py` | Done: one parser; `parse_extraction_output`, `llm_classify` and `validate_agent_output` use it. Also fixes `str(response.content)` for block-list replies, which produced Python-repr text that no JSON parser could read |
| 3 | Contract models | Done for the lead plan and review, the specialist's closing reply, evidence requests and the summary. Not done for hypotheses and verdicts: they were already guarded item by item and `verify.py` checks every field |
| 4 | `ToolResult` / `ToolFinding` | Done, as described above |
| 5 | Truncation and transient errors | Specialist replies cut off mid-reply keep the assessments that finished; evidence requests do the same; extraction says it was cut off. No larger-limit retry (see decisions). Transient errors are covered by the client's own retries |
| 6 | Forced tool calls | Evidence requests only (`submit_requests`, with a text fallback). Extraction stays text JSON by decision. The lead, specialist and summary calls stay text JSON behind the validator |
| 7 | Observability | Partly: every non-`ok` result is logged with its site, and the detection trace records each specialist's `output_status`. Not added to the `perf` log line |
| 8 | Stored reports | Done: `schema_version` on new detection reports and `read_report()` for every read of one. No migration |
| 9 | Tests | `tests/test_output_validation.py` (54 cases); one existing test and the c028 report fixture updated |

Not run: the existing test suite, including the detection regression tests (the c028 fixture gained `"schema_version": 1` by hand, so check that test first). The new tests were run with a small script outside pytest, and the specialist and extraction tests in `test_company_identity.py`, `test_langgraph.py` and `test_detection_tools.py` passed the same way.

## Hardening added after review (2026-10-16)

| Concern | What was done | Limit |
|---|---|---|
| Numbers made up by coercion | A test fails if a contract gains a numeric field (only the two counters on `ToolResult` are allowed), and tests check that a tool finding's own values (`"N/A"`, `0`, `None`) pass through unchanged and that extraction keeps a missing amount as `None` | The extraction value path was already untouched |
| Retry cascades | `run_context.py`: one `RunContext` per run, shared by parallel nodes and threads. A detection run may make `DETECTION_MAX_MODEL_CALLS` (30) calls; past it the lead and specialists stop asking, findings found so far stay unapproved, and the verdict cannot be "clean" (an "unable to assess: run limit" entry is added). The case analysis has its own ceiling of 6. `LLM_MAX_RETRIES` is now explicit (1), and a request has a time limit | Calls are counted, not tokens. The client's own retries are not counted, only bounded by `LLM_MAX_RETRIES` |
| Repaired output not traceable | `validate(..., raw=...)` records every non-`ok` result (site, issues, the raw reply, what was used) in the run context. Detection writes it to the trace as a `run_audit` step with the call count; the case analysis writes it to its run trace. Silent list drops now make a result `degraded` | The raw reply is capped at 4,000 characters. Sites that don't pass `raw` (binder, classifier) are not recorded |
| Hanging tools | `tool_timeout.guard_tool` limits every detection tool (`TOOL_TIMEOUT_SECONDS`, 60; `SLOW_TOOL_TIMEOUT_SECONDS`, 120 for the model and registry tools). A timeout becomes `unable_to_assess`. The case-analysis evidence tools have the same limit | A running thread can't be stopped, and there is one CPU worker, so a hung CPU check blocks later ones until it returns. The limit includes queueing, so 5 to 10 seconds would fire spuriously. Ending one for good needs a separate process or service |

## Classifier and extractor (added 2026-10-16)

| Gap found | Fix |
|---|---|
| An unreadable extraction reply became an `escalation` with only a reason. The graph read that as "the type is wrong", so a garbled reply triggered a **reclassification** and ended as FAILED | An unreadable reply now sets `output_error` and leaves `escalation` empty. `_decide_after_extract` routes it straight to FAILED, so the document is not reclassified. It stays retryable by the officer |
| A reply that was valid JSON but had no `extracted_fields` counted as an empty extraction and the document went to `analyzed` | `extracted_fields` is required (`ExtractionReply`). `{}` is still a valid, empty extraction |
| One retry: none existed | An unreadable reply is retried once, showing the model its own reply and asking for only the JSON. Not for a cut-off reply, which would be cut off again. The retry counts against the call ceiling |
| A cut-off reply lost everything | The complete fields are kept (`salvage_entries`), the document FAILS with "cut off at the token limit; N complete field(s) were kept", and the officer sees the partial fields |
| A failed extraction wiped the fields stored before (an extraction rerun after a type change) | Nothing is written when there is no usable output |
| Field confidence above 1, below 0, a boolean, text or NaN was stored as given | Clamped to 0 to 1; anything that isn't a number is unknown (`None`), never a score |
| Classify agent: one unreadable field (`"confidence": "high"`, `"evidence": "text"`) threw away evidence and reasoning, or split a string into letters | `ClassifierReply` reads each field on its own. An unreadable one takes the safe value (confidence 0, manual review); the rest is kept |
| Classifier tools: an unparseable LLM-classifier reply was only a note | Recorded with the raw reply in the audit record |
| Document runs had no call ceiling and no audit | `DOCUMENT_MAX_MODEL_CALLS` (24) and a `run_audit` trace step with every repaired reply. A rerun loads the saved trace first, so recording the audit can't overwrite it |

**Toss-back for a low-confidence guess (found from traces, 2026-10-16).** Since 10-11 a classification below the confidence threshold is extracted with the generic schema. That prompt doesn't know the classifier's guess, so it could only suggest a known category and never say "this isn't a list of loans". `C067_BudgetProposal.pdf` was tossed back on 10-02 and 10-03 (typed schema) and not at all since. The generic extraction now gets the guess as its own block after the cached prefix (first pass only) and may object. Checked live on that document: no escalation without the hint, an escalation with it. A reply with no suggested type still reclassifies.

Not covered: the vision classifier's reply is read by the same parser as the LLM classifier, so it is covered, but nothing checks that an extracted value has the type its schema expects (a text value in a number field).

## Tests

Tests are written with the code but not run as part of this plan.

- **Corpus of bad replies**, shared by all sites: empty string, prose only, JSON in a fence, JSON with prose around it, JSON array instead of object, truncated mid-object, wrong types (`confidence: "high"`, `findings: {}`), extra keys, `<think>` prefix, `null`, very long text, non-string content blocks.
- **Per site:** feeding each corpus entry gives a valid typed value or the documented fallback, and never raises. Table-driven so a new site is one line.
- **Tool contract:** a tool returning a finding without `summary`, a non-dict, or raising becomes `unable_to_assess` and the specialist still reports on its other tools.
- **Run level:** a mocked model that returns garbage for every call still ends with the document `failed` or `approved` (retryable) and the case analysis `complete` or `failed` with a reason. Never a hung task or a document stuck in `detecting`.
- **No false clean:** a bad reply never yields a report that reads as "no issues found".

## Out of scope

- Verifying that the model's content is *right* (citations, grounding, severity caps): `verify.py` and the summary check already do this and stay as they are.
- Changing prompts or models.
- Validating the HTTP API against the frontend types. The API already uses pydantic response models; the frontend does not validate at runtime, and that is a separate item.

## Decisions

1. **Retries.** Salvage first. No larger-limit retry on `max_tokens`: it doubles cost and latency for a reply that will often be cut again. Extraction reports that it was cut off.
2. **Extraction stays text JSON** with the stronger parser. Large per-type schemas as tool definitions would add prompt tokens and latency. Forced tool calls are used only for small decision outputs (the judge, the plan, evidence requests).
3. **Stored reports are read defensively** (`read_report`), with no migration of existing rows.
