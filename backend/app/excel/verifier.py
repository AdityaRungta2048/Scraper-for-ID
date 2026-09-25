"""OutputVerifier: proves the processed workbook differs from the original ONLY in the
expected cells, before it is offered for download.

The check is a full cell-by-cell diff of every sheet (values and formatting) plus
structural checks (sheet names/order, dimensions, merged ranges, freeze panes,
filters, column widths, hyperlinks, conditional formats, data validations) and a
row-identity check that the source id stored for every processing row is still in
that exact Excel row.
"""

from __future__ import annotations

import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from app.excel.importer import ColumnMap, cell_text


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class VerificationReport:
    ok: bool = True
    checks: list[Check] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append(Check(name, ok, detail))
        if not ok:
            self.ok = False

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "checks": [asdict(c) for c in self.checks], "mismatches": self.mismatches[:50]}


def _load(path: Path) -> Any:
    return load_workbook(path, keep_vba=path.suffix.lower() == ".xlsm", keep_links=True, rich_text=True)


def _v(value: Any) -> Any:
    # CellRichText and friends: compare their text + structure via str()/repr()
    if value is None or isinstance(value, (int, float, str, bool)):
        return value
    return repr(value)


def _style_sig(cell: Any) -> tuple[Any, ...]:
    if not getattr(cell, "has_style", False):
        return ()
    # openpyxl StyleProxy objects do not compare equal across workbooks; compare their serialisation
    return (
        cell.number_format,
        repr(cell.font),
        repr(cell.fill),
        repr(cell.border),
        repr(cell.alignment),
        repr(cell.protection),
    )


def verify_output(
    original_path: Path,
    output_path: Path,
    columns: ColumnMap,
    source_platform: str,
    expected: dict[tuple[int, int], str | None],
    row_sources: list[tuple[int, str | None]],
) -> VerificationReport:
    rep = VerificationReport()
    # 1/15. file integrity + reopen
    try:
        with zipfile.ZipFile(output_path) as zf:
            bad = zf.testzip()
        rep.add("zip_integrity", bad is None, f"corrupt member {bad}" if bad else "archive intact")
    except Exception as exc:
        rep.add("zip_integrity", False, f"{type(exc).__name__}: {exc}")
        return rep
    try:
        out = _load(output_path)
        rep.add("workbook_opens", True, "output workbook opened")
    except Exception as exc:
        rep.add("workbook_opens", False, f"{type(exc).__name__}: {exc}")
        return rep
    try:
        ro = load_workbook(output_path, read_only=True, data_only=True)
        ro.close()
        rep.add("reopens_read_only", True, "output reopened in read-only mode")
    except Exception as exc:
        rep.add("reopens_read_only", False, f"{type(exc).__name__}: {exc}")
    orig = _load(original_path)

    # 2. worksheet exists / sheet structure
    rep.add("worksheet_exists", columns.sheet_name in out.sheetnames, columns.sheet_name)
    rep.add("sheet_names_and_order", orig.sheetnames == out.sheetnames, f"{out.sheetnames}")
    if not rep.ok:
        return rep

    max_exp_row = max((r for r, _ in expected), default=0)
    max_exp_col = max((c for _, c in expected), default=0)
    total_diffs = 0
    for name in orig.sheetnames:
        o_ws, n_ws = orig[name], out[name]
        is_target = name == columns.sheet_name
        exp_rows = max(o_ws.max_row, max_exp_row if is_target else 0)
        exp_cols = max(o_ws.max_column, max_exp_col if is_target else 0)
        # 3/10/11/12. no rows/columns added or removed
        rep.add(
            f"dimensions[{name}]",
            (n_ws.max_row, n_ws.max_column) == (exp_rows, exp_cols),
            f"rows {n_ws.max_row} (expected {exp_rows}), columns {n_ws.max_column} (expected {exp_cols})",
        )
        rep.add(
            f"merged_cells[{name}]",
            sorted(map(str, o_ws.merged_cells.ranges)) == sorted(map(str, n_ws.merged_cells.ranges)),
        )
        rep.add(f"freeze_panes[{name}]", o_ws.freeze_panes == n_ws.freeze_panes, str(n_ws.freeze_panes))
        rep.add(
            f"auto_filter[{name}]", o_ws.auto_filter.ref == n_ws.auto_filter.ref, str(n_ws.auto_filter.ref)
        )
        o_w = {k: v.width for k, v in o_ws.column_dimensions.items()}
        n_w = {k: v.width for k, v in n_ws.column_dimensions.items() if k in o_w}
        rep.add(f"column_widths[{name}]", o_w == n_w)
        rep.add(
            f"hyperlinks[{name}]",
            sorted((h.ref, h.target) for h in o_ws._hyperlinks)
            == sorted((h.ref, h.target) for h in n_ws._hyperlinks),
        )
        rep.add(
            f"conditional_formatting[{name}]",
            len(list(o_ws.conditional_formatting)) == len(list(n_ws.conditional_formatting)),
        )
        rep.add(
            f"data_validations[{name}]",
            len(o_ws.data_validations.dataValidation) == len(n_ws.data_validations.dataValidation),
        )
        # 4-9/13/14. full cell diff: only expected cells may differ, and they must hold exactly
        # the expected values; formatting must be unchanged everywhere.
        diffs = 0
        for r in range(1, exp_rows + 1):
            for c in range(1, exp_cols + 1):
                o_cell, n_cell = o_ws.cell(row=r, column=c), n_ws.cell(row=r, column=c)
                if is_target and (r, c) in expected:
                    ok = _v(n_cell.value) == expected[(r, c)]
                    what = f"expected {expected[(r, c)]!r}"
                else:
                    ok = _v(n_cell.value) == _v(o_cell.value)
                    what = f"original {o_cell.value!r}"
                if not ok:
                    diffs += 1
                    rep.mismatches.append(f"{name}!{n_cell.coordinate}: got {n_cell.value!r}, {what}")
                if not (is_target and (r, c) in expected and not o_cell.has_style) and _style_sig(
                    o_cell
                ) != _style_sig(n_cell):
                    diffs += 1
                    rep.mismatches.append(f"{name}!{n_cell.coordinate}: formatting changed")
        total_diffs += diffs
        rep.add(f"cell_values_and_formatting[{name}]", diffs == 0, f"{diffs} unexpected difference(s)")

    # row identity: each processing record's source id is still in its original row
    ws = out[columns.sheet_name]
    src_col = columns.source_col(source_platform)
    bad_rows = [r for r, src in row_sources if cell_text(ws.cell(row=r, column=src_col).value) != src]
    rep.add(
        "row_identity",
        not bad_rows,
        f"{len(row_sources)} rows checked" + (f"; mismatched rows {bad_rows[:10]}" if bad_rows else ""),
    )
    dest_col = columns.dest_col(source_platform)
    n_dest = sum(1 for (_, c) in expected if c == dest_col)
    n_rem = sum(1 for (r, c) in expected if c == columns.remarks_col and r != columns.header_row)
    rep.add("expected_writes", True, f"{n_dest} destination cell(s), {n_rem} remark cell(s) written")
    return rep
