"""
Writes the success-metric labels into a copy of the document mastersheet.

From backend/:
    python -m scripts.metrics.label_mastersheet "<mastersheet.xlsx>" "<labelled copy.xlsx>"

What it changes, and nothing else:
  - Case Registry gains an "Expected Risk Level" column (Low / Medium / High), from the rubric in
    build_dataset.expected_level; "Fraud Case? (Y/N)" is set to match (Y for Medium and High).
  - Every document the fraud manifest forges (scripts/metrics/frauds.py) is marked "Fraud Injected? = Y" with its
    Detection Dimension, and given a Fraud Description if it had none.
  - "Cross--Document" is corrected to "Cross-Document" and "AI" to "AI-Generated".

The workbook's XML is edited in place, not re-saved through a spreadsheet library: a re-save drops the cached
results of its formulas (the generators read those, not the formulas) and its dropdown lists. Row fills are not
touched, so a newly marked fraud row is not coloured orange; do that in Excel if you want it.
"""

from __future__ import annotations

import re
import sys
import warnings
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import openpyxl
from openpyxl.utils import column_index_from_string, get_column_letter

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from . import ai_document, frauds  # noqa: E402
from .build_dataset import RISK_COLUMN, expected_level  # noqa: E402

warnings.filterwarnings("ignore", message="Data Validation extension is not supported")

AI_DESCRIPTION = (
    "AI-generated proof of address: a picture of the holder's NRIC carrying an image generator's traces (no "
    "sensor noise, a 2px upsampling pattern, 1024x1024, a generator named as the PDF's creator). Built by "
    "scripts/metrics/ai_document.py; simulated traces, not real generator output."
)


def _edits(source: Path) -> dict[str, dict[str, str]]:
    """sheet name -> {cell reference: new text}."""
    wb = openpyxl.load_workbook(str(source), data_only=True)
    edits: dict[str, dict[str, str]] = {}

    def put(sheet: str, column: int, row: int, text: str) -> None:
        edits.setdefault(sheet, {})[f"{get_column_letter(column)}{row}"] = text

    registry = wb["Case Registry"]
    header = [c.value for c in registry[1]]
    risk_column = header.index(RISK_COLUMN) + 1 if RISK_COLUMN in header else max(
        i for i, value in enumerate(header, start=1) if value is not None) + 1
    fraud_column = header.index("Fraud Case? (Y/N)") + 1
    put("Case Registry", risk_column, 1, RISK_COLUMN)
    for row in range(2, registry.max_row + 1):
        case_id = registry.cell(row, 1).value
        if not case_id or not registry.cell(row, header.index("Company Name") + 1).value:
            continue
        level = expected_level(str(case_id))
        put("Case Registry", risk_column, row, level)
        wanted = "N" if level == "Low" else "Y"
        if str(registry.cell(row, fraud_column).value or "").strip().upper() != wanted:
            put("Case Registry", fraud_column, row, wanted)

    forged = {(f.case_id, f.filename): f for f in frauds.FRAUDS}
    for sheet in wb.worksheets:
        header = [c.value for c in sheet[1]]
        if "Filename" not in header or "Fraud Injected? (Y/N)" not in header:
            continue
        columns = {str(name).strip(): i for i, name in enumerate(header, start=1) if name is not None}
        case_column = columns.get("Case ID") or columns.get("Case ID (auto)")
        for row in range(2, sheet.max_row + 1):
            filename = sheet.cell(row, columns["Filename"]).value
            if not filename:
                continue
            case_id = str(sheet.cell(row, case_column).value)
            dimension = str(sheet.cell(row, columns["Detection Dimension"]).value or "").strip()
            if dimension == "AI" and case_id == ai_document.CASE_ID:
                put(sheet.title, columns["Detection Dimension"], row, "AI-Generated")
                put(sheet.title, columns["Fraud Description"], row, AI_DESCRIPTION)
            elif "Cross--Document" in dimension:
                put(sheet.title, columns["Detection Dimension"], row, dimension.replace("Cross--Document", "Cross-Document"))
            fraud = forged.get((case_id, str(filename)))
            if fraud is None:
                continue
            if str(sheet.cell(row, columns["Fraud Injected? (Y/N)"]).value or "").strip().upper() != "Y":
                put(sheet.title, columns["Fraud Injected? (Y/N)"], row, "Y")
            if not dimension:
                put(sheet.title, columns["Detection Dimension"], row,
                    "; ".join(dict.fromkeys(step.dimension for step in fraud.steps)))
            if not sheet.cell(row, columns["Fraud Description"]).value:
                put(sheet.title, columns["Fraud Description"], row, " ".join(step.note for step in fraud.steps))
    return edits


def _sheet_paths(archive: zipfile.ZipFile) -> dict[str, str]:
    workbook = archive.read("xl/workbook.xml").decode("utf-8")
    relations = archive.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    targets = {}
    for tag in re.findall(r"<Relationship\b[^>]*>", relations):
        rid, target = re.search(r'Id="([^"]+)"', tag), re.search(r'Target="([^"]+)"', tag)
        if rid and target:
            targets[rid.group(1)] = target.group(1)
    paths = {}
    for tag in re.findall(r"<sheet\b[^>]*>", workbook):
        name, rid = re.search(r'name="([^"]+)"', tag), re.search(r'r:id="([^"]+)"', tag)
        if name and rid:
            target = targets[rid.group(1)]
            paths[name.group(1).replace("&amp;", "&")] = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
    return paths


def _split(reference: str) -> tuple[int, int]:
    letters, digits = re.match(r"([A-Z]+)(\d+)", reference).groups()
    return column_index_from_string(letters), int(digits)


def _cell(reference: str, content: str, styled_like: str) -> str:
    """An inline-text cell, keeping the style of the cell it replaces (or sits beside)."""
    style = re.search(r'\bs="(\d+)"', styled_like.split(">", 1)[0])
    attribute = f' s="{style.group(1)}"' if style else ""
    return f'<c r="{reference}"{attribute} t="inlineStr">{content}</c>'


def _set_cell(xml: str, reference: str, text: str) -> str:
    column, row = _split(reference)
    content = f'<is><t xml:space="preserve">{escape(text)}</t></is>'
    row_match = re.search(rf'<row\b[^>]*\br="{row}"[^>]*?(/>|>(.*?)</row>)', xml, re.S)
    if row_match is None:
        raise ValueError(f"row {row} is not in the sheet")
    row_xml = row_match.group(0)
    if row_xml.endswith("/>"):
        row_xml = row_xml[:-2] + "></row>"
    cell = re.search(rf'<c\b[^>]*\br="{reference}"[^>]*?(/>|>.*?</c>)', row_xml, re.S)
    if cell:
        new = _cell(reference, content, cell.group(0))
        row_xml = row_xml[: cell.start()] + new + row_xml[cell.end():]
    else:
        # A new cell takes the look of the one to its left, and goes in column order.
        cells = list(re.finditer(r'<c\b[^>]*\br="([A-Z]+)\d+"[^>]*?(?:/>|>.*?</c>)', row_xml, re.S))
        before = [c for c in cells if column_index_from_string(c.group(1)) < column]
        new = _cell(reference, content, before[-1].group(0) if before else "")
        at = before[-1].end() if before else row_xml.index(">") + 1
        row_xml = row_xml[:at] + new + row_xml[at:]
    xml = xml[: row_match.start()] + row_xml + xml[row_match.end():]

    dimension = re.search(r'<dimension ref="([A-Z]+)(\d+):([A-Z]+)(\d+)"/>', xml)
    if dimension and column > column_index_from_string(dimension.group(3)):
        xml = xml.replace(dimension.group(0),
                          f'<dimension ref="{dimension.group(1)}{dimension.group(2)}:{get_column_letter(column)}{dimension.group(4)}"/>')
    return xml


def label(source: Path, destination: Path) -> dict[str, dict[str, str]]:
    edits = _edits(source)
    with zipfile.ZipFile(source) as archive:
        paths = _sheet_paths(archive)
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as out:
            for item in archive.infolist():
                data = archive.read(item.filename)
                sheet = next((name for name, path in paths.items() if path == item.filename and name in edits), None)
                if sheet:
                    xml = data.decode("utf-8")
                    for reference, text in edits[sheet].items():
                        xml = _set_cell(xml, reference, text)
                    data = xml.encode("utf-8")
                out.writestr(item, data)
    return edits


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    source, destination = Path(sys.argv[1]), Path(sys.argv[2])
    if source.resolve() == destination.resolve():
        raise SystemExit("Write the labelled copy to a different file; the original is left as it is.")
    edits = label(source, destination)
    for sheet, cells in edits.items():
        print(f"{sheet}: {len(cells)} cell(s)")
    print("Wrote", destination)


if __name__ == "__main__":
    main()
