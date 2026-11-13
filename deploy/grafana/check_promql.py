"""
Every PromQL query in the Grafana dashboards, written as a Prometheus rules file so `promtool check rules` can parse
them all (CI: ci-cd.yml, "Infrastructure checks"). Grafana's own variables ($__range, $__rate_interval, ...) are
replaced with a fixed range first, since Prometheus doesn't know them. From the repo root:

    python deploy/grafana/check_promql.py > /tmp/dashboard-rules.yml
"""

import json
import re
import sys
from pathlib import Path

DASHBOARDS = Path(__file__).parent / "dashboards"
_RANGE_VARIABLES = re.compile(r"\$__(?:range|rate_interval|interval)\b|\$\{__(?:range|rate_interval|interval)\}")
_OTHER_VARIABLES = re.compile(r"\$\{\w+(?::\w+)?\}|\$\w+")


def queries(node, found: list[tuple[str, str]], where: str) -> None:
    if isinstance(node, dict):
        if isinstance(node.get("expr"), str) and node["expr"].strip():
            found.append((where, node["expr"]))
        title = node.get("title")
        for value in node.values():
            queries(value, found, f"{where} / {title}" if isinstance(title, str) else where)
    elif isinstance(node, list):
        for value in node:
            queries(value, found, where)


def main() -> int:
    found: list[tuple[str, str]] = []
    for path in sorted(DASHBOARDS.glob("*.json")):
        queries(json.loads(path.read_text(encoding="utf-8")), found, path.name)
    if not found:
        print("No PromQL queries found in the dashboards", file=sys.stderr)
        return 1
    lines = ["groups:", "  - name: dashboard-queries", "    rules:"]
    for index, (where, expr) in enumerate(found):
        plain = _OTHER_VARIABLES.sub("x", _RANGE_VARIABLES.sub("5m", expr))
        # The comment says which panel a parse error is in; ASCII so any console can print it.
        label = where.encode("ascii", "replace").decode()
        lines += [f"      # {label}", f"      - record: dashboard_query_{index}", f"        expr: {json.dumps(plain)}"]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
