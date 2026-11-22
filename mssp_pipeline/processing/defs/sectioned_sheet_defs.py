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
    # Labelled cells beyond column A that make a row the header. 2 skips the
    # single-cell decorative row some sheets carry above their header; a sheet
    # with only one value column (settlement Table 3) needs 1.
    min_header_cells: int = 2
    include_notes: bool = False         # keep footnote/notes rows instead of dropping them

    # A sheet some conforming deliveries legitimately lack (BNMRK 'Table 6 -
    # ACPT', the AEXPU COVID variants, STLMT 'Table A6 - ACPT'). Such a file
    # contributes 0 rows. Every other sheet is required: a workbook without it
    # fails the table, and so the run, rather than loading 0 rows silently.
    optional: bool = False

    # Layouts of the settlement (STLMT) workbook. They take effect on a
    # processor with LAYOUT_COLUMNS (see SectionedSheetProcessor).
    #
    # heading_cells_are_labels: a lettered row heading detail rows carries
    #   sub-header text in its cells ('3-Year Average Benchmark', 'BY3'), not
    #   values, so it emits nothing (settlement Table 2).
    # blocks: the sheet is a stack of blocks, each a title row, a header row and
    #   data rows. Titles fill BLOCK_LABEL, and a header row repeated by a later
    #   block is dropped (settlement Table A2).
    # label_column_is_data: column A holds data, not row labels. It is emitted as
    #   a value column under its header label, and also keys the row as
    #   ROW_LABEL (settlement Table A4, a lookup grid).
    heading_cells_are_labels: bool = False
    blocks: bool = False
    label_column_is_data: bool = False

    # Key/value sheets (the "Parameters" tab) carry no column-header row at all:
    # column A is the key, column B the value. Setting this skips header detection
    # entirely, starts data at row 1, and names the columns from B onward. Columns
    # beyond the supplied labels are excluded. COLUMN_GROUP_LABEL is always NULL.
    synthetic_column_labels: tuple[str, ...] | None = None
