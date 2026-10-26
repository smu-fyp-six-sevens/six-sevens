"""The binding argument both field-driven tools take from the specialist."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ....fraud_detection.numerical_validation.runner import Binding


class BindingArg(BaseModel):
    """One check input: which extracted field fills which role."""

    # Both are required in practice but typed optional, so one incomplete entry is skipped
    # (see binding_problems) instead of the schema rejecting the whole call.
    role: str | None = Field(default=None, description="Required. A role from the check menu in your instructions, exactly as written.")
    field: str | None = Field(default=None, description="Required. An extracted field name, exactly as listed in the field list.")
    row: int | None = Field(
        default=None, description="For a row role only: the row's index in that list field, exactly as listed."
    )


def to_bindings(args: list[BindingArg] | None) -> list[Binding]:
    return [Binding(role=a.role, field=a.field, row=a.row) for a in args or [] if a.role and a.field]


def binding_problems(args: list[BindingArg] | None) -> list[str]:
    """One message per entry that was left out for lacking a role or a field."""
    return [
        f"binding {index} was skipped: it has no {'role' if not a.role else 'field'}"
        + (f" (row {a.row})" if a.row is not None else "")
        for index, a in enumerate(args or [])
        if not (a.role and a.field)
    ]
