"""
Shows what officer feedback the agents would see, without calling a model (no credits).

Reads finding_feedback the way a specialist does (feedback_repository.counts, through detection/feedback.load) and
prints, per check code, the block a tool result would carry and the ids a finding of that code would record as shown.
Read-only: it writes nothing.

    python scripts/preview_feedback.py --check numerical_checks_tool --case-id <a case id other than the feedback's own>
    python scripts/preview_feedback.py --cross-document --case-id <a case id other than the feedback's own>

--check is a tool name; every code it has feedback for is shown, then the tool's own (code-less findings and missed
issues). --cross-document shows what the case analysis (planner and judge) would be shown, for the categories the
model decides.

Feedback from the case you pass is left out (an agent only learns from other cases), so use any id the feedback didn't
come from. To try it with test rows, run backend/sql/dev/seed_feedback_test.sql first.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.case_pipeline.cross_document import feedback as cross_document_feedback  # noqa: E402
from app.langgraph.detection import feedback as detection_feedback  # noqa: E402
from app.supabase_client import service_supabase_client  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", help="the detection tool name, e.g. numerical_checks_tool")
    parser.add_argument("--cross-document", action="store_true", help="preview the case analysis's feedback instead")
    parser.add_argument("--case-id", required=True, help="the case the document is in; its own feedback is left out")
    args = parser.parse_args()
    if not args.cross_document and not args.check:
        parser.error("give --check, or --cross-document")

    # The lookup treats a failure as "no feedback" so a review never stops; here that would read as nothing to learn from.
    try:
        service_supabase_client().rpc(
            "feedback_counts", {"p_scope": "document", "p_check_names": [], "p_exclude_case_id": ""},
        ).execute()
    except Exception as error:
        print(f"Can't read feedback_counts ({str(error)[:120]}). Run finding_feedback's migration first (schema.sql).")
        return 1

    if args.cross_document:
        by_category = await cross_document_feedback.load_feedback(args.case_id)
        block = cross_document_feedback.render(by_category)
        if not block:
            print("No feedback from other cases for the case analysis: its prompts would have no feedback section.")
            return 0
        print(block)
        print("\nA finding of each kind would record as shown:")
        for category, ids in cross_document_feedback.ids_by_category(by_category).items():
            print(f"  {category}: {', '.join(ids)}")
        return 0

    loaded = await detection_feedback.load([args.check], args.case_id)
    if not loaded:
        print(f"No feedback for {args.check} from other cases: its results would carry no feedback.")
        return 0
    # As if one result fired every code there is feedback for.
    block, ids = detection_feedback.for_result(loaded, [*(k for k in loaded if k != args.check), args.check], [])
    print(block)
    print("\nA finding of each code would record as shown:")
    for key, shown in ids.items():
        print(f"  {key}: {', '.join(shown)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
