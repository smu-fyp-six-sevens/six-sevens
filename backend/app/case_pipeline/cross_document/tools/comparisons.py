"""Tools that check whether documents agree with each other."""

from __future__ import annotations

import re
from typing import Any, Callable

from langchain_core.tools import tool
from pydantic import BaseModel

from ....identifiers import alnum, individual_names_match, uen_key
from ....fraud_detection.numerical_validation.common import as_number
from ....fraud_detection.shared.field_values import decode_field_value
from ..bias import is_protected_field
from ..evidence import mask_identifiers
from ..identity_pairs import compare_pair
from .common import ToolKit, statement_period, unavailable

_MAX_ITEMS = 8
_IDENTIFIER_FIELD_RE = re.compile(r"uen|nric|fin|passport|(?<![a-z])id(?![a-z])|_id|id_|number|registration|reg_?no|tax|tin", re.IGNORECASE)


class FieldRef(BaseModel):
    document: str
    field: str


def build_comparison_tools(kit: ToolKit) -> list:
    context = kit.context

    @tool
    async def compare_periods(document_a: str, document_b: str, is_refutation_probe: bool = False) -> str:
        """Compare two statements' periods: overlap, gap, and whether the closing balance of the earlier one equals the opening balance of the later one."""
        a, err_a = kit.doc(document_a)
        b, err_b = kit.doc(document_b)
        args = {"document_a": document_a, "document_b": document_b}
        if a is None or b is None:
            return kit.log("compare_periods", args, err_a or err_b or {}, is_refutation_probe)
        (a_start, a_end), (b_start, b_end) = statement_period(a), statement_period(b)
        if a_start is None or a_end is None or b_start is None or b_end is None:
            return kit.log("compare_periods", args, unavailable("A statement period is missing on one of the documents."),
                           is_refutation_probe)
        flags, notes = [], []
        for view, (start, end) in ((a, (a_start, a_end)), (b, (b_start, b_end))):
            if end < start:
                flags.append("CHRONOLOGICAL_IMPOSSIBILITY")
                notes.append(f"{view.key} ends before it starts.")
        first, second = ((a, a_start, a_end), (b, b_start, b_end))
        if (b_start, b_end) < (a_start, a_end):
            first, second = second, first
        overlap_days = (min(first[2], second[2]) - max(first[1], second[1])).days + 1
        gap_days = (second[1] - first[2]).days - 1
        if overlap_days > 0:
            flags.append("PERIOD_OVERLAP")
            notes.append(f"The periods overlap by {overlap_days} day(s).")
        closing = as_number(decode_field_value(first[0].fields.get("closing_balance")))
        opening = as_number(decode_field_value(second[0].fields.get("opening_balance")))
        continuous = None
        if overlap_days <= 0 and gap_days <= 3 and closing is not None and opening is not None:
            continuous = abs(closing - opening) < 0.01
            if not continuous:
                flags.append("BALANCE_DISCONTINUITY")
                notes.append(f"{first[0].key} closes at {closing:,.2f} but {second[0].key} opens at {opening:,.2f}.")
        refs = [context.ledger.issue(v.key, "statement_period", f"{s} to {e}", source="compare_periods",
                                     breach_flag=flags[0] if flags else None) for v, s, e in (first, second)]
        return kit.log("compare_periods", args, {
            "available": True, "overlap_days": max(overlap_days, 0), "gap_days": max(gap_days, 0),
            "balances_continuous": continuous, "breach_flags": flags, "notes": notes, "refs": refs,
        }, is_refutation_probe, refs=refs)

    def _compare(
        tool_name: str, items: list[FieldRef], same: Callable[[Any, Any], bool], flag: str, probe: bool,
        normalise: Callable[[Any], Any] = lambda v: v,
    ) -> str:
        values, refs, missing = [], [], []
        for item in items[:_MAX_ITEMS]:
            view = context.resolve(item.document)
            # A personal attribute is never compared, whatever the model asks for.
            raw = decode_field_value(view.fields.get(item.field)) if view and not is_protected_field(item.field) else None
            if view is None or raw is None or str(raw).strip() == "":
                missing.append({"document": item.document, "field": item.field})
                continue
            ref = context.ledger.issue(view.key, item.field, raw, source=tool_name)
            values.append((view.key, item.field, raw, ref))
            refs.append(ref)
        args = {"items": [i.model_dump() for i in items]}
        if len(values) < 2:
            return kit.log(tool_name, args, {**unavailable("Fewer than two of the named fields have a value."),
                                             "missing": missing}, probe)
        mismatches = [
            {"between": [values[i][3], values[j][3]]}
            for i in range(len(values)) for j in range(i + 1, len(values))
            if not same(values[i][2], values[j][2])
        ]
        flags = [flag] if mismatches else []
        for key, name, raw, _ in values if flags else []:
            # Reissued carrying the flag, so a verdict citing them may reach High.
            context.ledger.issue(key, name, raw, source=tool_name, breach_flag=flag)
        return kit.log(tool_name, args, {
            "available": True, "same": not mismatches, "mismatches": mismatches, "breach_flags": flags,
            "values": [{"document": k, "field": f, "value": mask_identifiers(str(normalise(v)))[:160], "ref": r}
                       for k, f, v, r in values],
            "missing": missing,
        }, probe, refs=refs)

    @tool
    async def compare_identifiers(items: list[FieldRef], is_refutation_probe: bool = False) -> str:
        """Whether an identifier (UEN, NRIC or passport number, registration number) is the same across documents. items: [{document, field}]."""
        def clean(value: Any) -> str:
            return uen_key(value) or alnum(str(value))

        # A name or address differing is a variance, not a conflicting identity; those have their own tools.
        not_identifiers = [i.field for i in items if not _IDENTIFIER_FIELD_RE.search(i.field)]
        if not_identifiers:
            return kit.log("compare_identifiers", {"items": [i.model_dump() for i in items]}, unavailable(
                f"{', '.join(not_identifiers)} is not an identifier field. Use company_name_consistency_tool, "
                "address_consistency_tool or compare_people for names and addresses."), is_refutation_probe)
        return _compare("compare_identifiers", items, lambda a, b: clean(a) == clean(b),
                        "IDENTIFIER_CONFLICT", is_refutation_probe, normalise=clean)

    @tool
    async def compare_people(items: list[FieldRef], is_refutation_probe: bool = False) -> str:
        """Whether an individual's name on several documents is the same person. items: [{document, field}]."""
        return _compare("compare_people", items, individual_names_match, "PERSON_MISMATCH", is_refutation_probe)

    @tool
    async def compare_linked_people(
        document_a: str, document_b: str, check: str = "id", is_refutation_probe: bool = False,
    ) -> str:
        """For two documents that name the same individual (see 'Documents naming the same individual'), report whether that person's ID number (check "id") or name (check "name") differs between them. Use one call per check."""
        a, err_a = kit.doc(document_a)
        b, err_b = kit.doc(document_b)
        wanted = "individual_name" if check == "name" else "individual_id"
        args = {"document_a": document_a, "document_b": document_b, "check": "name" if check == "name" else "id"}
        if a is None or b is None:
            return kit.log("compare_linked_people", args, err_a or err_b or {}, is_refutation_probe)
        comparison = compare_pair(a, b)
        compared = comparison.names_compared if wanted == "individual_name" else comparison.ids_compared
        if not comparison.linked or not compared:
            return kit.log("compare_linked_people", args, unavailable(
                "No individual is named on both documents." if not comparison.linked
                else f"Both documents do not give the linked individual's {'name' if check == 'name' else 'ID number'}."
            ), is_refutation_probe)
        linked = comparison.linked
        found = [m for m in comparison.mismatches if m.check == wanted]
        mismatches, refs, flags = [], [], set()
        for item in found:
            flag = "IDENTIFIER_CONFLICT" if item.check == "individual_id" else "PERSON_MISMATCH"
            flags.add(flag)
            pair_refs = [
                context.ledger.issue(a.key, item.left_field, item.left_value, source="compare_linked_people", breach_flag=flag),
                context.ledger.issue(b.key, item.right_field, item.right_value, source="compare_linked_people", breach_flag=flag),
            ]
            refs += pair_refs
            mismatches.append({
                "check": item.check, "refs": pair_refs,
                "values": [mask_identifiers(item.left_value)[:160], mask_identifiers(item.right_value)[:160]],
            })
        return kit.log("compare_linked_people", args, {
            "available": True, "linked_people": linked, "same": not mismatches, "mismatches": mismatches,
            "breach_flags": sorted(flags), "refs": refs,
        }, is_refutation_probe, refs=refs)

    return [compare_periods, compare_identifiers, compare_people, compare_linked_people]
