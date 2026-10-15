"""
The company-name and address consistency tools, moved here from the
per-document detection agent: comparing a document with the rest of its
case is cross-document work, not something one document's scan can judge.
"""

from __future__ import annotations

from typing import Optional

from langchain_core.tools import tool

from ....models import DocumentRecord
from ..address_matching import find_address_mismatches
from ..company_identity import check_company_name_consistency
from ..evidence import DocView, mask_identifiers
from .common import ToolKit, unavailable


def build_consistency_tools(kit: ToolKit) -> list:
    context = kit.context

    def _record(view: DocView) -> Optional[DocumentRecord]:
        return next((r for r in context.records if r.id == view.id), None)

    @tool
    async def company_name_consistency_tool(document: str, is_refutation_probe: bool = False) -> str:
        """Compare one document's company name with the company name on every other document in the case. Names that differ only by legal suffix ("Pte Ltd" vs "Pte. Ltd."), letter case or "&"/"AND" match; any other difference is reported with both values."""
        view, error = kit.doc(document)
        args = {"document": document}
        if view is None:
            return kit.log("company_name_consistency_tool", args, error or {}, is_refutation_probe)
        record = _record(view)
        if record is None:
            return kit.log("company_name_consistency_tool", args, unavailable("Document not found."),
                           is_refutation_probe, view.key)
        result = check_company_name_consistency(record, context.records)
        if result.skipped_reason:
            return kit.log("company_name_consistency_tool", args, unavailable(result.skipped_reason),
                           is_refutation_probe, view.key)
        flag = "NAME_MISMATCH" if result.discrepancies else None
        refs = [context.ledger.issue(view.key, "company_name", result.company_name,
                                     source="company_name_consistency_tool", breach_flag=flag)]
        discrepancies = []
        for item in result.discrepancies:
            for document_id in item.document_ids:
                other = context.by_id(document_id)
                if other is not None:
                    refs.append(context.ledger.issue(other.key, "company_name", item.conflicting,
                                                     source="company_name_consistency_tool", breach_flag=flag))
            discrepancies.append({"stated": mask_identifiers(item.stated),
                                  "conflicting": mask_identifiers(item.conflicting),
                                  "filenames": item.filenames, "summary": item.summary})
        return kit.log("company_name_consistency_tool", args, {
            "available": True, "same": not discrepancies, "compared_documents": len(result.compared_documents),
            "discrepancies": discrepancies, "breach_flags": [flag] if flag else [], "refs": refs,
        }, is_refutation_probe, view.key, refs)

    @tool
    async def address_consistency_tool(document: str, is_refutation_probe: bool = False) -> str:
        """Compare one document's personal and company addresses with the other addresses extracted in the case. Ignores case, punctuation and common abbreviations (Blk/Block, Rd/Road); different postal codes are reported with both values."""
        view, error = kit.doc(document)
        args = {"document": document}
        if view is None:
            return kit.log("address_consistency_tool", args, error or {}, is_refutation_probe)
        mismatches = [
            m for m in find_address_mismatches(r for r in context.records if r.extracted_fields)
            if view.id in (m.first.document_id, m.second.document_id)
        ]
        refs, found = [], []
        for mismatch in mismatches:
            source, other = ((mismatch.first, mismatch.second) if mismatch.first.document_id == view.id
                             else (mismatch.second, mismatch.first))
            other_view = context.by_id(other.document_id)
            refs.append(context.ledger.issue(view.key, source.field_name, source.value,
                                             source="address_consistency_tool", breach_flag="ADDRESS_MISMATCH"))
            if other_view is not None:
                refs.append(context.ledger.issue(other_view.key, other.field_name, other.value,
                                                 source="address_consistency_tool", breach_flag="ADDRESS_MISMATCH"))
            found.append({"field": source.field_name, "stated": mask_identifiers(str(source.value)),
                          "conflicting": mask_identifiers(str(other.value)),
                          "filenames": [source.filename, other.filename]})
        return kit.log("address_consistency_tool", args, {
            "available": True, "same": not found, "mismatches": found,
            "breach_flags": ["ADDRESS_MISMATCH"] if found else [], "refs": refs,
        }, is_refutation_probe, view.key, refs)

    return [company_name_consistency_tool, address_consistency_tool]
