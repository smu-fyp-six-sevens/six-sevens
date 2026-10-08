"""Build a curated reference layout from genuine copies of one form version.

Text that every copy has at the same place becomes a label (text and
position). Everything else becomes a value slot (position only), so no
applicant's details are written to the reference. Use copies that differ in
their values, from the same issuer and form version.

python backend/scripts/build_layout_reference.py \\
    --type notice_of_assessment --name iras_noa_2025 copy1.pdf copy2.pdf

Writes app/fraud_detection/layout_anomalies/references/<type>/<name>.json.
Review the file and commit it; the check picks it up for that document type.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.fraud_detection.layout_anomalies.extraction import (  # noqa: E402
    _extract_blocks,
    _open_document,
)
from app.fraud_detection.layout_anomalies.reference import (  # noqa: E402
    REFERENCES_DIR,
    build_reference,
)
from app.models import DocumentType  # noqa: E402

# Fewer labels than this cannot tell this form from another one.
_MIN_LABELS = 5


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--type", required=True, choices=[t.value for t in DocumentType if t != DocumentType.UNRECOGNISED])
    parser.add_argument("--name", required=True, help="Issuer and form version, e.g. iras_noa_2025")
    parser.add_argument("copies", nargs="+", type=Path, help="Two or more genuine copies")
    args = parser.parse_args()

    if len(args.copies) < 2:
        parser.error("give at least two genuine copies, so labels can be told apart from values")
    copies = []
    for path in args.copies:
        doc = _open_document(path.read_bytes())
        try:
            copies.append(_extract_blocks(doc))
        finally:
            doc.close()

    reference = build_reference(args.name, args.type, copies)
    if len(reference.labels) < _MIN_LABELS:
        print(
            f"Only {len(reference.labels)} labels are shared by every copy. Check the copies are "
            "the same form version and have a text layer.",
            file=sys.stderr,
        )
        return 1

    out = REFERENCES_DIR / args.type / f"{args.name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(reference.model_dump_json(indent=1) + "\n")
    print(f"{out}: {len(reference.labels)} labels, {len(reference.slots)} value slots")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
