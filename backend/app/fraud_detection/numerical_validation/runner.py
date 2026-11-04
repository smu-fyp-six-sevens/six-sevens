"""Runs the numerical checks from role bindings.

A check declares the roles it reads (the @check decorator in registry.py). A binding says which document
field fills a role; row roles also need a row index. Roles that aren't bound fall back
to a field with the same name. A check whose roles can't be filled is skipped with the
reason, never run on guessed data.

Which checks apply to a document type comes from each check's `applies`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ...config import settings
from ..shared.field_catalogue import array_rows, row_label
from .casts import CastLine, identity_finding, profit_cast_finding, section_total_finding
from .common import _apply_needs_review, _as_number
from .models import NumericalFinding, NumericalValidationResult, SkippedCheck
from .registry import Check, checks_for
from .statements import _benford_unavailable, _run_benford_pair

@dataclass(frozen=True)
class Binding:
    role: str
    field: str
    row: int | None = None


# Row roles: one row of an income statement, found by row index. The check looks the row
# up by label, so the runner hands it a row carrying the first alias as its label.
ROW_ROLES: dict[str, tuple[str, ...]] = {
    "depreciation_line": ("depreciation of property, plant and equipment", "depreciation", "depreciation expense"),
    "profit_before_tax_line": ("profit before taxation", "profit before tax"),
    "tax_expense_line": ("tax expense",),
    "finance_costs_line": ("finance costs",),
    "cost_of_sales_line": ("cost of sales",),
}
# Statement casts, run on rows bound one by one (income_line, expense_line, <section>_row).
INCOME_LINE_ROLES = ("income_line", "expense_line")
# Rows of the income statement that the cast needs too, bound for their own checks: all count as costs.
COST_LINE_ROLES = ("cost_of_sales_line", "depreciation_line", "finance_costs_line", "tax_expense_line")
BALANCE_SECTIONS = ("current_assets", "non_current_assets", "current_liabilities", "non_current_liabilities", "equity")
LINE_SET_TYPES = frozenset({"source_of_wealth"})


@dataclass
class RunOutcome:
    findings: list[NumericalFinding] = field(default_factory=list)
    skipped: list[SkippedCheck] = field(default_factory=list)
    breakdown: list[dict] = field(default_factory=list)
    unassigned: list[dict] = field(default_factory=list)
    applied: list[dict] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    benford: Any = None
    benford_excluding_fees: Any = None


def _check_title(fn: Any) -> str:
    doc = (fn.__doc__ or "").strip().split("\n")[0]
    return doc[:140] if doc else fn.__name__


def _applicable(document_type: str | None, group: str = "numerical") -> list[Check]:
    return checks_for(document_type, group)


def _resolve_plain(
    role: str, bound: dict[str, str], values: dict[str, Any], applied: list[dict] | None = None,
) -> str | None:
    """The field behind a role; `applied` records a specialist binding that was actually read."""
    if role in bound:
        if applied is not None:
            applied.append({"role": role, "field": bound[role]})
        return bound[role]
    if role in values:
        return role
    return None


def _run_checks(document_type: str | None, values: dict[str, Any], confidences: dict[str, float],
                plain: dict[str, str], rows: dict[str, tuple[str, int]], outcome: RunOutcome,
                group: str = "numerical") -> None:
    today = date.today()
    row_dicts = {name: array_rows(values[name]) or [] for name in {f for f, _ in rows.values()}}
    for entry in _applicable(document_type, group):
        kwargs: dict[str, Any] = {}
        missing: list[str] = []
        rename: dict[str, str] = {}
        for param in entry.params:
            if param.kind == "today":
                kwargs[param.name] = today
                continue
            if param.kind == "fields":
                kwargs[param.name] = dict(values)
                continue
            if param.kind == "row":
                value = None
                if param.name in rows:
                    name, index = rows[param.name]
                    value = row_dicts[name][index]
                    outcome.applied.append({"role": param.name, "field": name, "row": index})
                kwargs[param.name] = value
                if param.required and value is None:
                    missing.append(param.name)
                continue
            resolved = _resolve_plain(param.name, plain, values)
            value = values[resolved] if resolved is not None else None
            if resolved is not None:
                rename[param.name] = resolved
                outcome.applied.append({"role": param.name, "field": resolved})
            if param.numeric and value is not None:
                value = _as_number(value)
            kwargs[param.name] = value
            if param.required and (value is None or value == "" or value == []):
                missing.append(param.name)
        if missing:
            outcome.skipped.append(SkippedCheck(
                check_code=entry.code, reason=f"Required field(s) not extracted: {', '.join(missing)}",
            ))
            continue
        findings, skip = entry.fn(**kwargs)
        for finding in findings:
            if finding.field in rename:
                finding.field = rename[finding.field]
        outcome.findings.extend(findings)
        if skip is not None:
            outcome.skipped.append(skip.model_copy(update={"check_code": skip.check_code or entry.code}))
    outcome.findings[:] = _apply_needs_review(outcome.findings, confidences)


def _low_confidence(confidences: dict[str, float], names: list[str]) -> bool:
    threshold = settings.EXTRACTION_CONFIDENCE_THRESHOLD
    return any(confidences.get(name, 1.0) < threshold for name in names)


INCOME_TOTAL_WORDS = ("total", "gross profit", "profit from operations", "profit before", "profit for the year")
SECTION_TOTAL_WORDS = ("total", "end of year")


def _is_total(row: dict, words: tuple[str, ...]) -> bool:
    label = row_label(row).lower()
    return any(word in label for word in words)


def _income_lines_from_labels(name: str, values: dict[str, Any]) -> list[tuple[str, str, int]]:
    """Every income statement line except the totals. Amounts are signed (costs negative), so the sign gives the role."""
    lines = []
    for index, row in enumerate(array_rows(values[name]) or []):
        amount = _as_number(row.get("amount"))
        if amount is None or _is_total(row, INCOME_TOTAL_WORDS):
            continue
        lines.append(("income_line" if amount > 0 else "expense_line", name, index))
    return lines


def _statement_casts(values: dict[str, Any], plain: dict[str, str], bound_rows: list[tuple[str, str, int]],
                     confidences: dict[str, float], outcome: RunOutcome) -> None:
    """Statement casts: the income statement (all its non-total lines) and balance sheet sections (the rows the binder names)."""
    income_name = _resolve_plain("income_statement_items", plain, values, outcome.applied)
    if income_name is not None and array_rows(values[income_name]):
        lines = _income_lines_from_labels(income_name, values)
    else:
        lines = [(role, name, index) for role, name, index in bound_rows if role in INCOME_LINE_ROLES + COST_LINE_ROLES]
    _income_statement(values, plain, lines, confidences, outcome)
    sections = {section: [] for section in BALANCE_SECTIONS}
    for role, name, index in bound_rows:
        for section in BALANCE_SECTIONS:
            if role == f"{section}_row":
                sections[section].append((name, index))
    _balance_sheet(values, plain, sections, confidences, outcome)


def _income_statement(values: dict[str, Any], plain: dict[str, str], lines: list[tuple[str, str, int]],
                      confidences: dict[str, float], outcome: RunOutcome) -> None:
    if not lines:
        if _resolve_plain("revenue", plain, values) and _resolve_plain("profit_for_the_year", plain, values):
            outcome.skipped.append(SkippedCheck(
                check_code="FS.IS_CAST", reason="no income statement lines bound (income_line / expense_line)",
            ))
        return
    arrays = {name: array_rows(values[name]) or [] for _role, name, _index in lines}
    referenced = set()
    entries = []
    for role, name, index in lines:
        if not 0 <= index < len(arrays[name]):
            outcome.issues.append(f"row {index} is out of range for {name}")
            continue
        if (name, index) in referenced:
            continue  # one row bound to two roles (e.g. depreciation) is cast once
        referenced.add((name, index))
        entries.append((role, name, index))
    for name, rows in arrays.items():
        for index, row in enumerate(rows):
            if (name, index) not in referenced:
                outcome.unassigned.append({"field": name, "row": index, "label": row_label(row)})

    revenue = _resolve_plain("revenue", plain, values, outcome.applied)
    profit = _resolve_plain("profit_for_the_year", plain, values, outcome.applied)
    years = []
    if revenue and profit:
        years.append(("current year", revenue, profit, "amount"))
    prior_revenue = _resolve_plain("revenue_prior_year", plain, values, outcome.applied)
    prior_profit = _resolve_plain("profit_for_the_year_prior_year", plain, values, outcome.applied)
    if prior_revenue and prior_profit:
        years.append(("prior year", prior_revenue, prior_profit, "amount_prior_year"))
    if not years:
        outcome.skipped.append(SkippedCheck(check_code="FS.IS_CAST", reason="Required field(s) not extracted: revenue, profit_for_the_year"))
        return
    arrays_used = [name for name in arrays]

    for year, revenue_name, profit_name, column in years:
        revenue_value = _as_number(values[revenue_name])
        profit_value = _as_number(values[profit_name])
        if revenue_value is None or profit_value is None:
            outcome.skipped.append(SkippedCheck(check_code="FS.IS_CAST",
                                                reason=f"{year}: revenue or profit is not a number"))
            continue
        cast_lines, missing = [], None
        for role, name, index in entries:
            row = arrays[name][index]
            amount = _as_number(row.get(column))
            if amount is None:
                missing = row_label(row)
                break
            cast_lines.append(CastLine(label=row_label(row), role="income" if role == "income_line" else "expense",
                                       amount=amount))
        if missing is not None:
            outcome.skipped.append(SkippedCheck(check_code="FS.IS_CAST",
                                                reason=f"{year}: no {column} for '{missing}'"))
            continue
        finding = profit_cast_finding(
            year, revenue_value, profit_value, cast_lines, profit_field=profit_name,
            needs_review=_low_confidence(confidences, [revenue_name, profit_name, *arrays_used]),
        )
        if finding:
            outcome.findings.append(finding)
        outcome.breakdown.append({
            "year": year, "revenue": revenue_value, "stated_profit": profit_value,
            "expected_profit": round(revenue_value + sum(line.effect for line in cast_lines), 2),
            "lines": [{"label": line.label, "role": line.role, "effect": round(line.effect, 2)}
                      for line in cast_lines],
        })


def _balance_sheet(values: dict[str, Any], plain: dict[str, str], sections: dict[str, list[tuple[str, int]]],
                   confidences: dict[str, float], outcome: RunOutcome) -> None:
    referenced = set()
    arrays: dict[str, list[dict]] = {}
    for section, bound in sections.items():
        for name, index in bound:
            arrays.setdefault(name, array_rows(values[name]) or [])
            if not 0 <= index < len(arrays[name]):
                outcome.issues.append(f"row {index} is out of range for {name}")
                continue
            referenced.add((name, index))
    for name, rows in arrays.items():
        for index, row in enumerate(rows):
            if (name, index) not in referenced:
                outcome.unassigned.append({"field": name, "row": index, "label": row_label(row)})

    for section in BALANCE_SECTIONS:
        bound = sections[section]
        if not bound:
            continue
        total_name = _resolve_plain(f"total_{section}", plain, values, outcome.applied)
        if total_name is None:
            outcome.skipped.append(SkippedCheck(check_code="FS.BS_CAST",
                                                reason=f"{section}: no total field bound (total_{section})"))
            continue
        stated = _as_number(values[total_name])
        amounts = []
        for name, index in bound:
            if 0 <= index < len(arrays[name]) and not _is_total(arrays[name][index], SECTION_TOTAL_WORDS):
                amounts.append(_as_number(arrays[name][index].get("amount")))
        if stated is None or any(a is None for a in amounts) or not amounts:
            outcome.skipped.append(SkippedCheck(check_code="FS.BS_CAST",
                                                reason=f"{section}: rows or total are not numbers"))
            continue
        finding = section_total_finding(
            section.replace("_", " "), total_name, amounts, stated,
            needs_review=_low_confidence(confidences, [total_name, *(n for n, _i in bound)]),
        )
        if finding:
            outcome.findings.append(finding)
        outcome.breakdown.append({"section": section, "stated_total": stated, "sum_of_rows": round(sum(amounts), 2)})

    for year, names in (
        ("current year", ("total_assets", "total_liabilities", "total_equity")),
        ("prior year", ("total_assets_prior_year", "total_liabilities_prior_year", "total_equity_prior_year")),
    ):
        resolved_names = [name for name in (_resolve_plain(name, plain, values, outcome.applied) for name in names) if name is not None]
        if len(resolved_names) != len(names):
            continue
        numbers = [_as_number(values[name]) for name in resolved_names]
        numeric_values = [n for n in numbers if n is not None]
        if len(numeric_values) != len(numbers):
            continue
        assets, liabilities, equity = numeric_values
        finding = identity_finding(
            year, resolved_names[0], assets, liabilities, equity,
            needs_review=_low_confidence(confidences, resolved_names),
        )
        if finding:
            outcome.findings.append(finding)
        outcome.breakdown.append({"identity": year, "total_assets": assets,
                                  "total_liabilities": liabilities, "total_equity": equity})


def _shared_field_conflicts(
    bindings: list[Binding], values: dict[str, Any], outcome: RunOutcome, check_roles: set[str],
) -> set[tuple[str, str]]:
    """A plain field can hold one value, so bind it to one role. Where a field is bound to several roles, keep the
    role named after the field (or the first one). Returns the (role, field) pairs to drop.

    A field named after a role the checks read fills that role by name (_resolve_plain) unless a binding points
    the role elsewhere, so it counts as bound to it: an opening_balance field must not also be bound as the
    ledger_balance of a statement that prints no closing balance."""
    redirected = {b.role for b in bindings if b.row is None and b.role != b.field}
    roles_by_field: dict[str, list[str]] = {  # list fields have their own rule (see run_numerical_checks)
        name: [name] for name, value in values.items()
        if name in check_roles and name not in redirected and array_rows(value) is None
    }
    for binding in bindings:
        if binding.row is None and binding.field in values and binding.role not in roles_by_field.setdefault(binding.field, []):
            roles_by_field[binding.field].append(binding.role)
    dropped: set[tuple[str, str]] = set()
    for field_name, roles in roles_by_field.items():
        if len(roles) < 2:
            continue
        keep = field_name if field_name in roles else roles[0]
        for role in roles:
            if role != keep:
                dropped.add((role, field_name))
                outcome.issues.append(f"'{role}' not bound to '{field_name}': that field is already bound to '{keep}'")
    return dropped


def run_numerical_checks(
    document_type: str | None, fields: list[Any], bindings: list[Binding], group: str = "numerical",
) -> RunOutcome:
    """fields are ExtractedField-like objects. Returns findings, skips and what was bound.

    group picks which registered checks run: "numerical" (also the statement casts and Benford) or
    "external" (lookups in static reference tables)."""
    values = {f.field_name: f.field_value for f in fields}
    confidences = {f.field_name: f.confidence for f in fields if f.confidence is not None}
    outcome = RunOutcome()

    plain: dict[str, str] = {}
    rows: dict[str, tuple[str, int]] = {}
    bound_rows: list[tuple[str, str, int]] = []
    check_roles = {role for entry in _applicable(document_type, group) for role in entry.roles}
    dropped = _shared_field_conflicts(bindings, values, outcome, check_roles)
    for binding in bindings:
        if binding.field not in values:
            outcome.issues.append(f"'{binding.field}' is not a field in this document")
            continue
        if (binding.role, binding.field) in dropped:
            continue
        if binding.row is None:
            if array_rows(values[binding.field]) is not None and binding.role != binding.field:
                outcome.issues.append(f"'{binding.role}' not bound to '{binding.field}': a list field only binds to its own name")
                continue
            plain[binding.role] = binding.field
            continue
        if array_rows(values[binding.field]) is None:
            outcome.issues.append(f"'{binding.field}' is not an array field")
            continue
        if binding.role in ROW_ROLES:
            rows[binding.role] = (binding.field, binding.row)
        elif binding.role in INCOME_LINE_ROLES or any(
            binding.role == f"{s}_row" for s in BALANCE_SECTIONS
        ):
            bound_rows.append((binding.role, binding.field, binding.row))
        else:
            outcome.issues.append(f"'{binding.role}' is not a row role")

    _run_checks(document_type, values, confidences, plain, rows, outcome, group)
    if group != "numerical":
        return outcome
    if document_type in LINE_SET_TYPES:
        _statement_casts(values, plain, bound_rows, confidences, outcome)
    if document_type == "bank_statement":
        name = _resolve_plain("transactions", plain, values, outcome.applied)
        if name is not None:
            outcome.benford, outcome.benford_excluding_fees, benford_findings = _run_benford_pair(values[name])
            outcome.findings.extend(benford_findings)
    return outcome


def benford_summary(document_type: str | None, outcome: RunOutcome) -> Any:
    """The Benford analysis to report, with the same fallback reason as the reference path."""
    if outcome.benford is not None:
        return outcome.benford
    reason = (
        "Benford's Law only runs on bank statement transactions" if document_type != "bank_statement"
        else "No transactions field extracted"
    )
    return _benford_unavailable(reason)


def render_check_roles(document_type: str | None, fields: list[Any], group: str = "numerical") -> str:
    """The checks of this group that apply to this document type, and the roles each one reads."""
    values = {f.field_name for f in fields}
    label = "Numerical" if group == "numerical" else "External reference"
    lines = [f"{label} checks for this document type, and the roles each one reads:"]
    for entry in _applicable(document_type, group):
        title = _check_title(entry.fn)
        if entry.scan_all:
            lines.append(f"- {title} (reads every field; no roles to bind)")
            continue
        roles = [f"{p.name} (matched)" if p.name in values else f"{p.name} (bind)"
                 for p in entry.params if p.kind == "field"]
        roles += [f"{p.name} (row index; bind)" for p in entry.params if p.kind == "row"]
        if not roles:
            continue
        lines.append(f"- {title}: " + ", ".join(roles))
    if group == "numerical" and document_type in LINE_SET_TYPES:
        lines.append(
            "- Statement casts: bind each income statement line as income_line or expense_line with its row "
            "index (leave out subtotals; unbound rows are reported as unassigned); bind revenue, "
            "profit_for_the_year (and the _prior_year versions for the prior year). Bind each balance sheet row "
            "as <section>_row with its row index, e.g. equity_row, and the section total as total_<section>. "
            "Also bind total_assets, total_liabilities and total_equity (and _prior_year) for the identity."
        )
    return "\n".join(lines)


def finding_dict(finding: NumericalFinding) -> dict:
    return {
        "check_code": finding.check_code,
        "severity_hint": finding.severity,
        "field": finding.field,
        "stated": finding.stated,
        "expected": finding.expected,
        "needs_review": finding.needs_review,
        "summary": finding.summary,
    }


def build_result(document_id: str, document_type: str | None, fields: list[Any], outcome: RunOutcome,
                 binding_error: str | None = None) -> NumericalValidationResult:
    """The endpoint response for one run."""
    values_analysed = sum(
        1 for f in fields
        if isinstance(f.field_value, (int, float)) or (isinstance(f.field_value, str) and _as_number(f.field_value) is not None)
    )
    unable = not fields
    return NumericalValidationResult(
        document_id=document_id,
        document_type=document_type,
        values_analysed=values_analysed,
        findings=outcome.findings,
        skipped_checks=outcome.skipped,
        benford=benford_summary(document_type, outcome),
        benford_excluding_fees=outcome.benford_excluding_fees,
        unable_to_assess=unable,
        unable_to_assess_reason="No numerical values could be extracted from this document" if unable else None,
        breakdown=outcome.breakdown,
        unassigned=outcome.unassigned,
        # The same role (e.g. "transactions") is read by many checks, so outcome.applied has one
        # entry per check that used it — de-duplicated here, once per distinct role/field/row.
        bindings_applied=list({tuple(sorted(b.items())): b for b in outcome.applied}.values()),
        binding_issues=outcome.issues,
        binding_error=binding_error,
    )
