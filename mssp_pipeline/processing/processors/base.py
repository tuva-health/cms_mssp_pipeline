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
                total_found = len(source_info)
                if not self.config.FULL_REFRESH:
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
                batch_size = self._batch_size_for()
                batches = list(_chunked(source_info, batch_size))
                print(
                    f"  {total_found} source file(s) found, {len(source_info)} to process in "
                    f"{len(batches)} batch(es) of up to {batch_size}."
                )
                for batch_index, batch in enumerate(batches, start=1):
                    print(
                        f"  Batch {batch_index}/{len(batches)}: processing {len(batch)} source file(s)."
                    )
                    source_paths = [sp for _, sp in batch]
                    query = self._build_query(file_def, source_paths)
                    self._export_batch(query, table_name, conn, batch_index == 1)
                print(f"✅ Successfully wrote {table_name}")
            except Exception as e:
                print(f"❌ Error processing {table_name}: {e}")

    def _batch_size_for(self) -> int:
        batch_size = getattr(self.config, "PROCESS_BATCH_SIZE_DEFAULT", 25)
        overrides = {
            "CCLFProcessor": getattr(self.config, "PROCESS_BATCH_SIZE_CCLF", batch_size),
            "MSSPProcessor": getattr(self.config, "PROCESS_BATCH_SIZE_MSSP", batch_size),
            "MCQMProcessor": getattr(self.config, "PROCESS_BATCH_SIZE_MCQM", batch_size),
            "EXPUProcessor": getattr(self.config, "PROCESS_BATCH_SIZE_EXPU", batch_size),
        }
        return max(1, int(overrides.get(self.__class__.__name__, batch_size)))

    def _export_batch(self, query: str, table_name: str, conn, is_first_batch: bool) -> None:
        original_full_refresh = getattr(self.exporter, "full_refresh", None)
        if original_full_refresh is None:
            self.exporter.export(query, table_name, conn)
            return

        self.exporter.full_refresh = bool(self.config.FULL_REFRESH and is_first_batch)
        try:
            self.exporter.export(query, table_name, conn)
        finally:
            self.exporter.full_refresh = original_full_refresh

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


def _chunked(items: List[Tuple[str, str]], chunk_size: int) -> List[List[Tuple[str, str]]]:
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]
