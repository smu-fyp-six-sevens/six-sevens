"""
Shows what officer feedback the agents would see for a check, without calling a model (no credits).

Reads finding_feedback the way a specialist's first turn does (feedback_repository.similar, then detection/feedback.render)
and prints the exact prompt block and the ids a finding would record as applied. Read-only: it writes nothing.

    python scripts/preview_feedback.py --check metadata_tool --case-id <a case id other than the feedback's own>
    python scripts/preview_feedback.py --check metadata_tool --case-id x --doc-type payslip
    python scripts/preview_feedback.py --cross-document --case-id <a case id other than the feedback's own>

--cross-document shows what the case analysis (planner and judge) would be shown, for the categories the model decides.

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
    parser.add_argument("--check", help="the detection check (tool) name, e.g. metadata_tool")
    parser.add_argument("--cross-document", action="store_true", help="preview the case analysis's feedback instead")
    parser.add_argument("--case-id", required=True, help="the case the document is in; its own feedback is left out")
    parser.add_argument("--doc-type", default=None, help="the document's type, so feedback on the same type comes first")
    args = parser.parse_args()
    if not args.cross_document and not args.check:
        parser.error("give --check, or --cross-document")

    # The lookup treats a failure as "no feedback" so a review never stops; here that would read as nothing to learn from.
    try:
        service_supabase_client().table("finding_feedback").select("id").limit(1).execute()
    except Exception as error:
        print(f"Can't read finding_feedback ({str(error)[:120]}). Run its CREATE TABLE from backend/sql/schema.sql first.")
        return 1

    if args.cross_document:
        by_category = await cross_document_feedback.load_feedback(args.case_id)
        block = cross_document_feedback.render(by_category)
        if not block:
            print("No feedback from other cases for the case analysis: its prompts would have no feedback section.")
            return 0
        print(block)
        print("\nA finding of each kind would record as applied:")
        for category, ids in cross_document_feedback.ids_by_category(by_category).items():
            print(f"  {category}: {', '.join(ids)}")
        return 0

    shown = await detection_feedback.feedback_for([args.check], args.case_id, args.doc_type)
    block = detection_feedback.render(shown)
    if not block:
        print(f"No feedback for {args.check} from other cases: the specialist's prompt would have no feedback section.")
        return 0
    print(block)
    print("\nA finding of this check would record as applied:")
    for feedback_id in detection_feedback.ids_by_tool(shown)[args.check]:
        print(f"  {feedback_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
