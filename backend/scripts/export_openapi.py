"""Writes the API's OpenAPI schema to frontend/openapi.json, which `npm run gen:api` turns into TypeScript types.

Run from backend/ after changing a response or request model: python scripts/export_openapi.py
--check fails instead of writing when the file is out of date (CI).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "frontend" / "openapi.json"
_REF = "#/components/schemas/"


def _refs(node: object) -> set[str]:
    if isinstance(node, dict):
        found = {node["$ref"].removeprefix(_REF)} if isinstance(node.get("$ref"), str) else set()
        return found.union(*(_refs(v) for v in node.values()))
    if isinstance(node, list):
        return set().union(*(_refs(v) for v in node))
    return set()


def _reachable(start: set[str], schemas: dict) -> set[str]:
    seen: set[str] = set()
    todo = list(start)
    while todo:
        name = todo.pop()
        if name not in seen and name in schemas:
            seen.add(name)
            todo.extend(_refs(schemas[name]))
    return seen


def build() -> dict:
    schema = app.openapi()
    schemas = schema["components"]["schemas"]
    operations = [op for methods in schema["paths"].values() for op in methods.values()]
    requests = _reachable(set().union(*(_refs(op.get("requestBody", {})) for op in operations)), schemas)
    responses = _reachable(set().union(*(_refs(op.get("responses", {})) for op in operations)), schemas)
    # A response always carries every field (no route excludes unset or None ones), so a field with a
    # default is present, not optional. Request models keep their optional fields.
    for name in responses - requests:
        properties = schemas[name].get("properties")
        if properties:
            schemas[name]["required"] = list(properties)
    return schema


def main() -> int:
    text = json.dumps(build(), indent=2, sort_keys=True) + "\n"
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print(f"{OUT.name} is out of date: run python scripts/export_openapi.py, then npm run gen:api in frontend/")
            return 1
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
