"""Numerical checks on a document's extracted fields.

Layout:
* models.py            result types returned to the API and the agents
* registry.py          the @check decorator and the registered checks (with the roles each reads)
* common.py            shared helpers, reference tables (empty until real tables exist), the check context
* identity_company.py  general identifier checks (every type) plus NRIC, passport, company and FATCA checks
* statements.py        bank statement, source of wealth and notice of assessment checks
* records.py           payslip, credit report, loan list and proof of address checks
* runner.py            applies bindings to the registered checks and builds the result

Callers use run_numerical_checks with bindings (see runner.py). Each check only sees the fields
its roles name; a check whose roles aren't filled is skipped with the reason.
"""

# Importing the check modules registers their checks (general checks first).
from . import identity_company, records, statements  # noqa: F401
from .models import BenfordAnalysis, NumericalFinding, NumericalValidationResult, SkippedCheck
from .registry import CHECKS, checks_for
from .runner import (
    ROW_ROLES,
    Binding,
    RunOutcome,
    benford_summary,
    build_result,
    finding_dict,
    render_check_roles,
    run_numerical_checks,
)

__all__ = [
    "BenfordAnalysis",
    "Binding",
    "CHECKS",
    "NumericalFinding",
    "NumericalValidationResult",
    "ROW_ROLES",
    "RunOutcome",
    "SkippedCheck",
    "benford_summary",
    "build_result",
    "checks_for",
    "finding_dict",
    "render_check_roles",
    "run_numerical_checks",
]
