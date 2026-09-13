#!/usr/bin/env python3
"""Export the company Notice of Assessment HTML template to PDF."""

from __future__ import annotations

import argparse
import base64
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
HTML_PATH = ROOT / "noa.html"
CSS_PATH = ROOT / "noa.css"
LOGO_PATH = ROOT / "iras-logo.png"
DEFAULT_OUT = ROOT / "output" / "noa_harbourview_trading.pdf"

DEFAULTS = {
    "TAX_REF": "201834567K",
    "NOA_DATE": "18 Apr 2025",
    "YEAR_OF_ASSESSMENT": "2025",
    "ADDRESSEE_LINES": (
        '        <div class="name">HARBOURVIEW TRADING PTE. LTD.</div>\n'
        "        <div>10 ANSON ROAD</div>\n"
        "        <div>#18-12 INTERNATIONAL PLAZA</div>\n"
        "        <div>SINGAPORE 079903</div>"
    ),
    "ECI": "428,000",
    "EXEMPT_FIRST": "7,500",
    "EXEMPT_NEXT": "95,000",
    "ECI_AFTER": "325,500",
    "TAX_AMT": "55,335.00",
    "REBATE_AMT": "27,667.50",
    "PAYABLE_AMT": "27,667.50",
    "DISCHARGE_AMT": "27,667.50",
    "PREV_DATE": "05 Mar 2025",
    "TRACKING": "810879239-0-7",
}


def apply_placeholders(html: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        html = html.replace("{{" + key + "}}", value)
    leftover = [part for part in html.split("{{")[1:] if "}}" in part]
    if leftover:
        names = ", ".join(part.split("}}", 1)[0] for part in leftover[:8])
        raise SystemExit(f"Unfilled NOA placeholders: {names}")
    return html


def find_chrome() -> Path:
    candidates = [
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    ]
    for path in candidates:
        if path.exists():
            return path
    raise SystemExit("Chrome, Chromium, or Edge is required to export PDFs.")


def bundled_html() -> str:
    html = HTML_PATH.read_text(encoding="utf-8")
    css = CSS_PATH.read_text(encoding="utf-8")
    html = html.replace(
        '<link rel="stylesheet" href="noa.css">',
        f"<style>{css}</style>",
    )
    logo_b64 = base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")
    html = html.replace(
        'src="iras-logo.png"',
        f'src="data:image/png;base64,{logo_b64}"',
    )
    return html


def html_to_pdf(html: str, pdf_path: Path) -> None:
    chrome = find_chrome()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        html_path = Path(tmp) / "noa.html"
        html_path.write_text(html, encoding="utf-8")
        result = subprocess.run(
            [
                str(chrome),
                "--headless",
                "--disable-gpu",
                "--no-pdf-header-footer",
                f"--print-to-pdf={pdf_path.resolve()}",
                html_path.resolve().as_uri(),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
        )
        if result.returncode != 0 or not pdf_path.exists() or pdf_path.stat().st_size < 1000:
            raise SystemExit(
                "Failed to write PDF with Chrome.\n"
                + (result.stderr or result.stdout or f"exit {result.returncode}")
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export the company NOA as a PDF.")
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="PDF path (default: ./output/noa_harbourview_trading.pdf)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    for path in (HTML_PATH, CSS_PATH, LOGO_PATH):
        if not path.exists():
            raise SystemExit(f"Missing {path.name}")
    html_to_pdf(apply_placeholders(bundled_html(), DEFAULTS), args.out)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
