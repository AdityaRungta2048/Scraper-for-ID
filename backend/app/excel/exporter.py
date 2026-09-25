"""ExcelExporter: writes results into a COPY of the ORIGINAL workbook, by original row number.

It never inserts, deletes, appends or sorts rows; it only sets the destination and
remarks cells of the rows it was told to write (and, if the sheet had no remarks
column, one "remarks" header cell).
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell

from app.excel.importer import ColumnMap


class ExportError(RuntimeError):
    pass


@dataclass(frozen=True)
class CellWrite:
    original_row: int
    write_destination: bool
    destination: str | None
    write_remarks: bool
    remarks: str | None


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
    if columns.remarks is None and columns.remarks_new_col is not None:
        expected[(columns.header_row, columns.remarks_new_col)] = "remarks"
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
    if columns.remarks is None and columns.remarks_new_col is not None:
        # style the added header like the neighbouring header cell
        from copy import copy

        src = ws.cell(row=columns.header_row, column=columns.remarks_new_col - 1)
        dst = ws.cell(row=columns.header_row, column=columns.remarks_new_col)
        if src.has_style:
            dst._style = copy(src._style)

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
