"""Tools that read what the documents say."""

from __future__ import annotations

import json
import re

from langchain_core.tools import tool

from ....fraud_detection.shared.field_values import decode_field_value
from ..bias import is_protected_field
from ..evidence import mask_identifiers
from .common import MAX_ROWS, ToolKit, unavailable

_PROFILE_KEYS: dict[str, tuple[str, ...]] = {
    "declared_activity": (
        "business_activit", "principal_activit", "nature_of_business", "ssic", "business_description",
    ),
    "incorporation_date": ("incorporation_date", "date_of_incorporation"),
    "share_capital": ("share_capital", "paid_up", "issued_capital"),
    "registered_address": ("registered_address", "business_address", "company_address"),
    "directors": ("director",),
}


def build_reading_tools(kit: ToolKit) -> list:
    context = kit.context

    @tool
    async def list_documents(is_refutation_probe: bool = False) -> str:
        """List the case's approved documents: the name to use for each, its type, and how many detection findings it has."""
        result = {"available": True, "documents": [
            # No file name: it can hold a person's name or a protected word (bias.py).
            {"document": v.key, "type": v.document_type, "detection_findings": len(v.findings)}
            for v in context.docs.values()
        ]}
        return kit.log("list_documents", {}, result, is_refutation_probe)

    @tool
    async def get_fields(document: str, names: list[str], is_refutation_probe: bool = False) -> str:
        """Read named extracted fields of one document. Each value comes with a ref id to cite."""
        view, error = kit.doc(document)
        if view is None:
            return kit.log("get_fields", {"document": document}, error or unavailable("Unknown document"),
                           is_refutation_probe)
        out, refs = [], []
        for name in names[:20]:
            if is_protected_field(name):
                out.append({"field": name, "available": False, "reason": "A personal attribute; never used."})
                continue
            if name not in view.fields:
                out.append({"field": name, "available": False})
                continue
            value = decode_field_value(view.fields[name])
            shown = value[:MAX_ROWS] if isinstance(value, list) else value
            ref = context.ledger.issue(view.key, name, value, source="get_fields")
            refs.append(ref)
            out.append({"field": name, "value": mask_identifiers(json.dumps(shown, default=str))[:600], "ref": ref})
        return kit.log("get_fields", {"document": document, "names": names}, {"available": True, "fields": out},
                       is_refutation_probe, view.key, refs)

    @tool
    async def get_business_profile(is_refutation_probe: bool = False) -> str:
        """The declared business profile (activity, incorporation date, share capital, registered address, directors) gathered across documents. Where documents disagree, every value is returned with its source."""
        profile: dict[str, list[dict]] = {}
        refs: list[str] = []
        for attribute, keys in _PROFILE_KEYS.items():
            found = []
            for view in context.docs.values():
                for name, value in view.fields.items():
                    if is_protected_field(name):
                        continue
                    if any(k in name.lower() for k in keys) and str(decode_field_value(value) or "").strip():
                        ref = context.ledger.issue(view.key, name, value, source="get_business_profile")
                        refs.append(ref)
                        found.append({"document": view.key, "field": name,
                                      "value": mask_identifiers(str(decode_field_value(value)))[:200], "ref": ref})
            if found:
                profile[attribute] = found
        disagreements = [
            attribute for attribute, items in profile.items()
            if attribute != "directors" and len({re.sub(r"\W+", "", i["value"].lower()) for i in items}) > 1
        ]
        result: dict = {"available": bool(profile), "profile": profile, "documents_disagree_on": disagreements}
        if not profile:
            result["reason"] = "No profile fields were found on any document."
        return kit.log("get_business_profile", {}, result, is_refutation_probe, refs=refs)

    return [list_documents, get_fields, get_business_profile]
