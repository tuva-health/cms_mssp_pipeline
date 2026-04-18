from types import SimpleNamespace

from mssp_pipeline.processing.processors.base import FileProcessor


class DummyProcessor(FileProcessor):
    def __init__(self, session, exporter, config, source_info):
        super().__init__(session, exporter, config)
        self._source_info = source_info

    def _get_file_definitions(self):
        return [SimpleNamespace(name="dummy")]

    def _get_table_name(self, file_def):
        return "dummy_table"

    def _list_source_file_paths(self, file_def):
        return list(self._source_info)

    def _build_query(self, file_def, source_paths):
        return "|".join(source_paths)


class RecordingExporter:
    def __init__(self, existing_paths=None, full_refresh=False):
        self.existing_paths = existing_paths or []
        self.full_refresh = full_refresh
        self.calls = []

    def export(self, query, table_name, duckdb_connection):
        self.calls.append((query, table_name, self.full_refresh))

    def get_existing_file_paths(self, table_name, duckdb_connection):
        return list(self.existing_paths)


def test_batches_queries_by_default_batch_size():
    processor = DummyProcessor(
        SimpleNamespace(connection=object()),
        RecordingExporter(full_refresh=False),
        SimpleNamespace(FULL_REFRESH=False, PROCESS_BATCH_SIZE_DEFAULT=2),
        [("fp1", "sp1"), ("fp2", "sp2"), ("fp3", "sp3")],
    )

    processor.run()

    assert processor.exporter.calls == [
        ("sp1|sp2", "dummy_table", False),
        ("sp3", "dummy_table", False),
    ]


def test_full_refresh_only_applies_to_first_batch():
    processor = DummyProcessor(
        SimpleNamespace(connection=object()),
        RecordingExporter(full_refresh=True),
        SimpleNamespace(FULL_REFRESH=True, PROCESS_BATCH_SIZE_DEFAULT=2),
        [("fp1", "sp1"), ("fp2", "sp2"), ("fp3", "sp3")],
    )

    processor.run()

    assert processor.exporter.calls == [
        ("sp1|sp2", "dummy_table", True),
        ("sp3", "dummy_table", False),
    ]


def test_incremental_filtering_happens_before_batching():
    processor = DummyProcessor(
        SimpleNamespace(connection=object()),
        RecordingExporter(existing_paths=["fp2"], full_refresh=False),
        SimpleNamespace(FULL_REFRESH=False, PROCESS_BATCH_SIZE_DEFAULT=2),
        [("fp1", "sp1"), ("fp2", "sp2"), ("fp3", "sp3"), ("fp4", "sp4")],
    )

    processor.run()

    assert processor.exporter.calls == [
        ("sp1|sp3", "dummy_table", False),
        ("sp4", "dummy_table", False),
    ]
