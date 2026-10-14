"""The field-driven checks: numerical and external validation, and the binder the officer endpoints use.

Both tools take a hidden, injected document_type (the agent never sees or sets it), and the type alone decides
which checks run. The specialist fills in `bindings`, saying which extracted field holds each check input.
Where there is no tool loop to ask (the officer's endpoints), binder.py makes one LLM call for them instead.
"""

from .external import external_validation_tool
from .numerical import numerical_checks_tool

__all__ = ["external_validation_tool", "numerical_checks_tool"]
