"""The binding argument both field-driven tools take from the specialist."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ....fraud_detection.numerical_validation.runner import Binding


class BindingArg(BaseModel):
    """One check input: which extracted field fills which role."""

    role: str = Field(description="A role from the check menu in your instructions, exactly as written.")
    field: str = Field(description="An extracted field name, exactly as listed in the field list.")
    row: int | None = Field(
        default=None, description="For a row role only: the row's index in that list field, exactly as listed."
    )


def to_bindings(args: list[BindingArg] | None) -> list[Binding]:
    return [Binding(role=a.role, field=a.field, row=a.row) for a in args or []]
