from dataclasses import dataclass


@dataclass(frozen=True)
class SheetDef:
    """Definition of one sectioned data sheet inside an MSSP xlsx workbook.

    Every BNMRK / AEXPU / QEXPU data sheet shares the same grammar: a block of
    title rows, a single header row, then alternating section and data rows,
    optionally followed by a notes/footnote block. One SheetDef describes one
    such sheet and the output table it is unpivoted into.

    sheet_pattern is a SQL LIKE pattern evaluated with ESCAPE '\\', so a literal
    underscore in a sheet name must be written '\\_'. That matters: the AEXPU and
    QEXPU sheets are named 'Table_1-Aggregate_EU_Report', and an unescaped '_'
    is LIKE's single-character wildcard.
    """

    table_name: str                     # e.g. "BNMRK_TABLE_1"
    sheet_pattern: str                  # SQL LIKE pattern, e.g. "Table 1 - Historical%"
    header_row_hint: int | None = None  # 1-based override when auto-detection fails
    include_notes: bool = False         # keep footnote/notes rows instead of dropping them

    # Key/value sheets (the "Parameters" tab) carry no column-header row at all:
    # column A is the key, column B the value. Setting this skips header detection
    # entirely, starts data at row 1, and names the columns from B onward. Columns
    # beyond the supplied labels are excluded. COLUMN_GROUP_LABEL is always NULL.
    synthetic_column_labels: tuple[str, ...] | None = None
