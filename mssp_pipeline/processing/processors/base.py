from abc import ABC, abstractmethod
from typing import List, Any, Tuple


class FileProcessor(ABC):
    """
    Template Method base class for file processors.

    Subclasses implement _get_file_definitions(), _get_table_name(),
    _list_source_file_paths(), and _build_query() to define how files are
    discovered and transformed. The run() method orchestrates iteration and
    export without knowing which backend is in use.

    Incremental filtering is done at the processor level before any source
    file content is read: _list_source_file_paths() enumerates available
    FILE_PATH values using glob metadata only, then run() subtracts already-
    loaded paths (fetched from the exporter) and passes only new source paths
    to _build_query(). Exporters receive a pre-filtered query and simply write
    everything they get.
    """

    def __init__(self, session, exporter, config):
        self.session = session
        self.exporter = exporter
        self.config = config

    def run(self) -> None:
        conn = self.session.connection
        for file_def in self._get_file_definitions():
            table_name = self._get_table_name(file_def)
            try:
                print(f"Processing {table_name}...")
                source_info = self._list_source_file_paths(file_def)
                if not source_info:
                    print(f"  No source files found for {table_name}. Skipping.")
                    continue
                if not self.config.FULL_REFRESH:
                    total_found = len(source_info)
                    existing = set(
                        self.exporter.get_existing_file_paths(table_name, conn)
                    )
                    source_info = [
                        (fp, sp) for fp, sp in source_info if fp not in existing
                    ]
                    if not source_info:
                        print(
                            f"  {total_found} source file(s) found, 0 new. Skipping."
                        )
                        continue
                source_paths = [sp for _, sp in source_info]
                query = self._build_query(file_def, source_paths)
                self.exporter.export(query, table_name, conn)
                print(f"✅ Successfully wrote {table_name}")
            except Exception as e:
                print(f"❌ Error processing {table_name}: {e}")

    @abstractmethod
    def _get_file_definitions(self) -> List[Any]:
        """Return the ordered list of file definition objects to process."""
        ...

    @abstractmethod
    def _list_source_file_paths(self, file_def) -> List[Tuple[str, str]]:
        """Return (file_path, source_path) pairs for every source file matching file_def.

        file_path:   the value that will appear in the FILE_PATH metadata column —
                     used for incremental dedup comparison against the destination.
        source_path: the actual readable path passed to _build_query() (zipfs reference,
                     direct file path, etc.).

        Only path-level metadata is gathered here — no file content is read.
        Returns an empty list when no matching files are found.
        """
        ...

    @abstractmethod
    def _build_query(self, file_def, source_paths: List[str]) -> str:
        """Return the DuckDB SELECT query string for this file definition.

        source_paths: list of source_path values to include, already filtered to
                      only new (not yet loaded) files.
        """
        ...

    @abstractmethod
    def _get_table_name(self, file_def) -> str:
        """Return the logical table name for this file definition."""
        ...

    def _metadata_columns(self, file_date_expr: str) -> str:
        """
        Returns the standard metadata SELECT fragment appended to every query.

        file_date_expr is the SQL expression for FILE_DATE — it differs by source:
          - CCLF: parsed from filename  try_strptime('20' || regexp_extract(...), ...)::DATE
          - MSSP: today()
        """
        return (
            f"replace(replace(filename, '!', ''), 'zip://', '') AS FILE_PATH,\n"
            f"            regexp_extract(replace(filename, 'zip://', ''), '^(.*)/', 1) AS DIRECTORY_NAME,\n"
            f"            regexp_extract(filename, '([^/]+)$', 1) AS FILE_NAME,\n"
            f"            {file_date_expr} AS FILE_DATE"
        )
