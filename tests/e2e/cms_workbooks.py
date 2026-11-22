"""CMS-consistent synthetic BNMRK / AEXPU / QEXPU workbooks for the e2e run (TUVA-165).

The e2e delivery used to carry the unit tests' minimal workbooks: one made-up
section per sheet, a literal ``Benchmark`` column, ACO ``T0000``. They load,
but cms_mssp_connector's benchmark models reject them, so nothing past the
expenditure model could be proven against pipeline output.

This module writes the workbooks the connector's own integration seeds
describe instead. ``fixtures/workbooks/<table>.csv`` are those seeds, copied
unchanged from cms_mssp_connector ``integration_tests/seeds/workbooks/``,
which ``cclf-synth generate-mssp`` builds: the cell-grain rows mssp-process
writes for a CMS-consistent synthetic PY2025 delivery for ACO ``A0000`` (the
ACO of the connector's ALR seeds and its ``mssp_aco_agreement`` row). The
labels follow the delivered CMS workbooks' grammar; every value is generated.

``render_delivery`` turns those rows back into xlsx files that mssp-process
parses into exactly the same rows (``test_cms_workbooks.py`` holds it to
that), at the seeds' own paths re-rooted under a file store. So the pipeline's
e2e output and the connector's seeds are the same data, and the harness
(cms_mssp_connector ``scripts/dbt-pipeline-e2e``) builds the benchmark facts
on it as cleanly as the connector's CI does on the seeds.

To refresh the fixtures, copy the connector's seeds again when they change.
Never edit them by hand: they come from the generator.

Rendering, per sheet (see ``SectionedSheetProcessor`` for the grammar):

- the header row (two rows when a column carries a ``COLUMN_GROUP_LABEL``)
  directly above the first section or data row, and a title in row 1 when
  the header does not start there;
- every data row at its own ``ROW_NUM``, its label in column A and its values
  in the columns its ``COLUMN_LABEL`` names;
- the section rows the parser needs to reach each data row's
  ``GROUP_LABEL`` / ``SECTION_CODE`` / ``SECTION_LABEL``, in the blank rows
  just above it: an unbracketed heading for a group, ``[X] label`` for a
  lettered section. A data row that is its own lettered section is written
  ``[X] label`` with its values.

The ``Parameters`` sheet has no header row: its row 1 is a heading too.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import openpyxl

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "workbooks"

# The one place the e2e delivery's ACO is set. It must be the ACO the
# fixtures (and the connector's ALR seeds) carry; load_cells() checks.
E2E_ACO_ID = "A0000"

# Where cclf-synth roots the fixture paths.
FIXTURE_STORE = "/synthetic/store/"

# table -> sheet name, as CMS names the sheets (the processors match on the
# prefix: see the *_file_defs.py modules).
SHEET_NAME = {
    "bnmrk_table_1": "Table 1 - Historical Benchmark",
    "bnmrk_table_1a": "Table 1A - Regional Adjustment",
    "bnmrk_table_1b": "Table 1B - Prior Savings Adj",
    "bnmrk_table_1c": "Table 1C - Health Equity Adj",
    "bnmrk_table_2": "Table 2 - Trend Factor",
    "bnmrk_table_3": "Table 3 - Truncation",
    "bnmrk_table_4": "Table 4 - Renormalization",
    "bnmrk_table_5": "Table 5 - Reg Adj Weight",
    "bnmrk_table_6": "Table 6 - ACPT",
    "bnmrk_parameters": "Parameters",
    "aexpu_table_1": "Table_1-Aggregate_EU_Report",
    "aexpu_table_1a": "Table_1A-EU_Excluding_COVID",
    "aexpu_table_3": "Table_3-SNF_Report",
    "aexpu_table_4": "Table_4-Exp_Truncation",
    "aexpu_table_4a": "Table_4A-Ex_Trc_Excluding_COVID",
    "aexpu_parameters": "Parameters",
    "qexpu_table_1": "Table_1-Aggregate_EU_Report",
    "qexpu_table_2": "Table_2-Regional_Expenditures",
    "qexpu_table_3": "Table_3-SNF_Report",
    "qexpu_parameters": "Parameters",
}

# Sheets every CMS workbook carries ahead of its tables; no processor reads them.
FRONT_MATTER = {
    "Cover": [["Synthetic workbook: every value is generated."]],
    "TOC": [["Table of Contents"]],
    "Glossary": [["Term", "Definition"]],
}

Column = Tuple[Optional[str], str]  # (COLUMN_GROUP_LABEL, COLUMN_LABEL)
State = Tuple[Optional[str], Optional[str], Optional[str]]  # (group, code, section)


@dataclass
class DataRow:
    row_num: int
    group: Optional[str]
    code: Optional[str]
    section: Optional[str]
    label: str
    values: Dict[Column, Optional[str]] = field(default_factory=dict)

    @property
    def state(self) -> State:
        return (self.group, self.code, self.section)


@dataclass
class Sheet:
    table: str
    rows: Dict[int, DataRow] = field(default_factory=dict)
    columns: List[Column] = field(default_factory=list)


def _blank(value: str) -> Optional[str]:
    return value if value != "" else None


def load_cells(fixtures: Path = FIXTURES) -> Dict[str, List[Sheet]]:
    """{store-relative workbook path: [Sheet, ...]} from the fixture CSVs."""
    workbooks: Dict[str, Dict[str, Sheet]] = defaultdict(dict)
    for table in SHEET_NAME:
        with open(fixtures / f"{table}.csv", newline="", encoding="utf-8") as fh:
            for rec in csv.DictReader(fh):
                if rec["aco_id"] != E2E_ACO_ID:
                    raise ValueError(f"{table}.csv carries ACO {rec['aco_id']}, not {E2E_ACO_ID}")
                path = rec["file_path"]
                if not path.startswith(FIXTURE_STORE):
                    raise ValueError(f"{table}.csv: {path} is not under {FIXTURE_STORE}")
                rel = path[len(FIXTURE_STORE):]
                sheet = workbooks[rel].setdefault(table, Sheet(table))
                row_num = int(rec["row_num"])
                row = DataRow(
                    row_num,
                    _blank(rec["group_label"]),
                    _blank(rec["section_code"]),
                    _blank(rec["section_label"]),
                    rec["row_label"],
                )
                existing = sheet.rows.setdefault(row_num, row)
                if (existing.state, existing.label) != (row.state, row.label):
                    raise ValueError(f"{rel} {table} row {row_num}: labels disagree across cells")
                column = (_blank(rec["column_group_label"]), rec["column_label"])
                if column not in sheet.columns:
                    sheet.columns.append(column)
                value = _blank(rec["value_text"])
                if existing.values.get(column, value) != value:
                    raise ValueError(f"{rel} {table} row {row_num} {column}: two values")
                existing.values[column] = value
    return {rel: list(sheets.values()) for rel, sheets in workbooks.items()}


def _ordered_columns(columns: Iterable[Column]) -> List[Column]:
    """First-appearance order, each column group kept contiguous."""
    order: List[Column] = []
    for column in columns:
        if column in order:
            continue
        group = column[0]
        if group is not None and any(c[0] == group for c in order):
            last = max(i for i, c in enumerate(order) if c[0] == group)
            order.insert(last + 1, column)
        else:
            order.append(column)
    return order


def _section_label(code: str, section: str) -> str:
    return f"[{code}]" if section == code else f"[{code}] {section}"


def render_sheet(sheet: Sheet) -> List[list]:
    """The sheet's rows, 1-based row N at index N-1, as openpyxl appends them."""
    parameters = sheet.table.endswith("_parameters")
    columns = _ordered_columns(sheet.columns)
    width = 1 + len(columns)
    grid: Dict[int, list] = {}

    def line(label: str, values: Optional[Dict[Column, Optional[str]]] = None) -> list:
        cells: list = [label] + [None] * len(columns)
        for i, column in enumerate(columns, start=1):
            if values is not None:
                cells[i] = values.get(column)
        return cells

    # The parser's (GROUP_LABEL, SECTION_CODE, SECTION_LABEL) so far.
    state: State = (None, None, None)
    floor = 0  # the last row already used
    if parameters:
        # No header row: row 1 is read as data, and a leading blank row would
        # be dropped (shifting every ROW_NUM), so it carries a heading. An
        # unbracketed heading sets the group and the section to its label.
        heading = "Parameters"
        grid[1] = line(heading)
        state, floor = (heading, None, heading), 1

    first_used: Optional[int] = None
    for row_num in sorted(sheet.rows):
        row = sheet.rows[row_num]
        group, code, section = row.state
        events: List[str] = []
        current = state
        if group != current[0]:
            if group is None:
                raise ValueError(f"{sheet.table} row {row_num}: no way back to an empty group")
            events.append(group)
            current = (group, None, group)
        own_section = code is not None and row.label == section
        if own_section:
            label = _section_label(code, section)
        else:
            label = row.label
            if code is not None:
                if current[1:] != (code, section):
                    events.append(_section_label(code, section))
            elif current != (group, None, section):
                if section != group:
                    raise ValueError(f"{sheet.table} row {row_num}: section {section!r} has no code")
                events.append(group)
        start = row_num - len(events)
        if start <= floor:
            raise ValueError(
                f"{sheet.table} row {row_num}: no room above it for the section rows {events}"
            )
        for offset, event in enumerate(events):
            grid[start + offset] = line(event)
        grid[row_num] = line(label, row.values)
        if first_used is None:
            first_used = start
        state = (group, code, section)
        floor = row_num

    if not parameters:
        if first_used is None:
            raise ValueError(f"{sheet.table}: no data rows")
        grouped = any(group is not None for group, _ in columns)
        header_row = first_used - (2 if grouped else 1)
        if header_row < 1:
            raise ValueError(f"{sheet.table}: no room for the header above row {first_used}")
        primary: list = ["Calculation"] + [None] * len(columns)
        carried: Optional[str] = None
        for i, (group, label) in enumerate(columns, start=1):
            if group is None:
                primary[i], carried = label, None
            elif group != carried:
                primary[i], carried = group, group
        grid[header_row] = primary
        if grouped:
            grid[header_row + 1] = [None] + [label if group else None for group, label in columns]
        if header_row > 1:
            grid[1] = [f"{sheet.table} (synthetic)"] + [None] * len(columns)

    last = max(grid)
    return [grid.get(n, [None] * width) for n in range(1, last + 1)]


def sheet_rows(sheets: List[Sheet]) -> Dict[str, List[list]]:
    """{sheet name: rows} for one workbook, front matter first."""
    rows = dict(FRONT_MATTER)
    for sheet in sheets:
        rows[SHEET_NAME[sheet.table]] = render_sheet(sheet)
    return rows


def write_workbook(path: Path, sheets: List[Sheet]) -> Path:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheet_rows(sheets).items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def render_delivery(
    store: Path, workbooks: Optional[Dict[str, List[Sheet]]] = None
) -> List[Path]:
    """Write every fixture workbook under ``store`` at its own relative path."""
    if workbooks is None:
        workbooks = load_cells()
    return [write_workbook(store / rel, sheets) for rel, sheets in sorted(workbooks.items())]
