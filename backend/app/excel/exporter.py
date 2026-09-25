"""ExcelExporter: writes results into a COPY of the ORIGINAL workbook, by original row number.

It never inserts, deletes, appends or sorts rows; it only sets the destination and
remarks cells of the rows it was told to write (and, if the sheet had no remarks
column, one "remarks" header cell).
"""

from __future__ import annotations

import os
import tempfile
from copy import copy
from dataclasses import dataclass
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from app.excel.importer import ColumnMap

LINK_FONT = Font(color="0563C1", underline="single")


class ExportError(RuntimeError):
    pass


@dataclass(frozen=True)
class CellWrite:
    original_row: int
    write_destination: bool
    destination: str | None
    write_remarks: bool
    remarks: str | None
    # {"twitch": "<links text>", "kick": ...}; None = leave the link cells untouched
    links: dict[str, str | None] | None = None


def first_url(text: str | None) -> str | None:
    """The primary link of a link cell (first line) — becomes the cell's hyperlink."""
    if not text:
        return None
    first = text.splitlines()[0].split(" ")[0]
    return first if first.startswith("https://") else None


def link_cells(columns: ColumnMap) -> set[int]:
    return {c for c in (columns.twitch_link, columns.kick_link) if c is not None}


def output_filename(original_name: str) -> str:
    """streamers.xlsx -> streamers_processed.xlsx (keeps .xlsm for macro workbooks)."""
    p = Path(original_name)
    suffix = p.suffix.lower() if p.suffix.lower() in {".xlsx", ".xlsm"} else ".xlsx"
    return f"{p.stem}_processed{suffix}"


def expected_cell_values(
    writes: list[CellWrite], columns: ColumnMap, source_platform: str
) -> dict[tuple[int, int], str | None]:
    """(row, col) -> exact value the output must contain. Used by exporter AND verifier."""
    dest_col = columns.dest_col(source_platform)
    rem_col = columns.remarks_col
    expected: dict[tuple[int, int], str | None] = {}
    seen_rows: set[int] = set()
    for w in writes:
        if w.original_row in seen_rows:
            raise ExportError(f"duplicate write for row {w.original_row}")
        if w.original_row <= columns.header_row:
            raise ExportError(f"refusing to write into header area (row {w.original_row})")
        seen_rows.add(w.original_row)
        if w.write_destination:
            expected[(w.original_row, dest_col)] = w.destination
        if w.write_remarks:
            expected[(w.original_row, rem_col)] = w.remarks
        if w.links is not None:
            for platform, text in w.links.items():
                col = columns.link_col(platform)
                if col is not None:
                    expected[(w.original_row, col)] = text
    if columns.remarks is None and columns.remarks_new_col is not None:
        expected[(columns.header_row, columns.remarks_new_col)] = "remarks"
    for col_key, header in (columns.new_headers or {}).items():
        expected[(columns.header_row, int(col_key))] = header
    return expected


def export_processed(
    original_path: Path,
    output_path: Path,
    columns: ColumnMap,
    source_platform: str,
    writes: list[CellWrite],
) -> dict[tuple[int, int], str | None]:
    expected = expected_cell_values(writes, columns, source_platform)
    keep_vba = original_path.suffix.lower() == ".xlsm"
    wb = load_workbook(original_path, keep_vba=keep_vba, keep_links=True, rich_text=True)
    if columns.sheet_name not in wb.sheetnames:
        raise ExportError(f"worksheet '{columns.sheet_name}' missing from the original workbook")
    ws = wb[columns.sheet_name]
    for (row, col), value in expected.items():
        cell = ws.cell(row=row, column=col)
        if isinstance(cell, MergedCell):
            raise ExportError(f"cell {cell.coordinate} is inside a merged range; cannot write safely")
        if isinstance(value, str) and value.startswith("="):
            raise ExportError(f"refusing to write formula-like value into {cell.coordinate}")
        cell.value = value
        if col in link_cells(columns) and row > columns.header_row:
            url = first_url(value)
            cell.hyperlink = url  # None removes a stale link
            if url:
                cell.font = LINK_FONT
                cell.alignment = Alignment(wrap_text=True, vertical="top")
    added = [columns.remarks_new_col] if columns.remarks is None and columns.remarks_new_col else []
    added += [int(c) for c in (columns.new_headers or {})]
    for col in added:
        # style added header cells like the existing header; give link columns room
        src = ws.cell(row=columns.header_row, column=columns.id_kick)
        dst = ws.cell(row=columns.header_row, column=col)
        if src.has_style:
            dst._style = copy(src._style)
        letter = get_column_letter(col)
        if col in link_cells(columns) and letter not in ws.column_dimensions:
            ws.column_dimensions[letter].width = 48

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=output_path.parent, suffix=output_path.suffix)
    os.close(fd)
    try:
        wb.save(tmp)
        os.replace(tmp, output_path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return expected
