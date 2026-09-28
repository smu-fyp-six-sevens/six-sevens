"""
LangGraph experiment (branch `langgraph-test`) — replaces the Dify
Classifier/Extractor/orchestrator with a LangGraph app inside this backend.

Not wired into queue_service or any router yet: this package is exercised
directly (see pipeline.process_document / pipeline.rerun_extraction) while
we judge fit. Dify keeps running in production regardless. See
LANGGRAPH_MIGRATION_PLAN.md at the repo root for the full plan and phases.
"""
