"""Synthetic end-to-end run: delivery -> mssp-process -> DuckDB -> contract.

Exercises the pipeline as one integrated whole with no client data, no
credentials and no cloud (TUVA-70):

1. Build a synthetic CMS delivery into a local file store from the existing
   test builders: the openpyxl BNMRK / AEXPU / QEXPU workbooks and one small
   fixed-width file per CCLF type. Every value is fake.
2. Drive a staged plan through the real sequencer engine
   (`mssp_pipeline.sequencer.Sequencer`) with the in-memory lease store, a
   synthetic readiness source and a fake ECS client whose "tasks" run locally:

   ``process``      the real ``mssp-process`` console script,
                    ``MSSP_OUTPUT_TYPE=PARQUET``.
   ``load``         loads every Parquet output into a local DuckDB database
                    (schema ``raw_data``); the stage's output contract is the
                    workbook contract's accepted output set, verified through
                    the production ``InformationSchemaOutputSource``.
   ``conformance``  checks every contracted table column-for-column and
                    type-for-type against ``contracts/workbook/v1.json`` and
                    that the synthetic CCLF rows arrived.

   So lease acquire/refresh/release, readiness, exact image/revision identity,
   gate ordering and output-contract verification all run in one pass.
3. Assert the outcome. With ``--fault`` a defect is injected and the run
   passes only if the sequence halts at the stage and gate that should catch
   it -- a mutation check that the end-to-end gates have teeth.

    uv run --frozen python -m tests.e2e.synthetic_run --workdir /tmp/e2e
    uv run --frozen python -m tests.e2e.synthetic_run --workdir /tmp/e2e --fault missing-table

Exit status 0 means the expectation held (clean pass, or the fault was caught
where expected); anything else fails the CI job.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import duckdb

from mssp_pipeline.lease import InMemoryLeaseStore
from mssp_pipeline.output_contract import AcceptedOutputContract
from mssp_pipeline.output_sources import InformationSchemaOutputSource
from mssp_pipeline.processing.defs.cclf_file_defs import CCLF_FILE_DEFS
from mssp_pipeline.readiness import ReadinessPolicy
from mssp_pipeline.sequencer import (
    GATE_IMAGE_IDENTITY,
    GATE_LEASE,
    GATE_OUTPUT_CONTRACT,
    GATE_READINESS,
    GATE_TASK,
    Sequencer,
    SequencerConfig,
    SequenceResult,
    Stage,
    StagePlan,
    TaskIdentity,
    TaskResult,
)
from tests.processing.conftest import ACO_ID, make_cclf_file
from tests.processing.test_aco_workbook_processors import (
    make_bnmrk_bundle,
    make_qexpu_bundle,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPO_ROOT / "contracts" / "workbook" / "v1.json"

# Synthetic, client-neutral ECS coordinates (documentation account id).
ACCOUNT = "123456789012"
REGION = "us-east-1"
CLUSTER = "e2e-cluster"
DATABASE = "e2e"  # DuckDB catalog name == database file stem
SCHEMA = "raw_data"
CCLF_ROWS_PER_FILE = 2

# fault -> (stage, gate) where the sequence must halt for the fault to count
# as caught.
EXPECTED_HALT = {
    "uppercase-columns": ("conformance", GATE_TASK),
    "missing-table": ("load", GATE_OUTPUT_CONTRACT),
    "readiness-blocked": ("process", GATE_READINESS),
    "lease-held": ("process", GATE_LEASE),
    "image-drift": ("load", GATE_IMAGE_IDENTITY),
}


# ---------------------------------------------------------------------------
# Synthetic delivery
# ---------------------------------------------------------------------------


def build_delivery(store: Path) -> None:
    """Write the synthetic CMS delivery: workbooks plus one file per CCLF type."""
    store.mkdir(parents=True, exist_ok=True)
    make_bnmrk_bundle(store)
    make_qexpu_bundle(store)
    for file_def in CCLF_FILE_DEFS:
        first = file_def.columns[0]
        rows = [{first.name: str(n).zfill(first.width)} for n in range(1, CCLF_ROWS_PER_FILE + 1)]
        make_cclf_file(store, file_def, rows)


# ---------------------------------------------------------------------------
# Fake ECS: families resolve to synthetic digests; tasks run locally
# ---------------------------------------------------------------------------


def _arn(family: str, revision: int = 1) -> str:
    return f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/{family}:{revision}"


def _image(family: str) -> str:
    digest = (family.encode().hex() + "0" * 64)[:64]
    return f"registry.example/{family}@sha256:{digest}"


class LocalEcsClient:
    """EcsClient whose run_task executes a local callable and whose exit code
    is that callable's return value. Records launch order for the gate-order
    assertion."""

    def __init__(self, tasks: dict[str, Callable[[], int]], drifted: frozenset[str] = frozenset()):
        self._tasks = tasks
        self._identities = {
            family: TaskIdentity(
                task_definition_arn=_arn(family),
                # A drifted family resolves to a different digest than the plan pins.
                image=_image(family + "-drift") if family in drifted else _image(family),
            )
            for family in tasks
        }
        self.run_order: list[str] = []
        self._results: dict[str, int] = {}

    def describe_task_definition(self, family: str) -> TaskIdentity:
        return self._identities[family]

    def run_task(self, *, cluster: str, task_definition: str) -> str:
        family = next(f for f, i in self._identities.items() if i.task_definition_arn == task_definition)
        self.run_order.append(family)
        print(f"\n=== task {family} ===", flush=True)
        started = time.monotonic()
        code = self._tasks[family]()
        print(f"=== task {family} exited {code} ({time.monotonic() - started:.1f}s) ===", flush=True)
        task_arn = f"arn:aws:ecs:{REGION}:{ACCOUNT}:task/{cluster}/{family}"
        self._results[task_arn] = code
        return task_arn

    def wait_for_stopped(self, *, cluster: str, task_arn: str) -> TaskResult:
        code = self._results[task_arn]
        return TaskResult(task_arn, code, None if code == 0 else f"exit {code}")


class _CatalogShimConnection:
    """DuckDB has no ``<catalog>.information_schema``; rewrite the one query
    ``InformationSchemaOutputSource`` issues to filter on ``table_catalog``."""

    def __init__(self, path: Path):
        self._conn = duckdb.connect(str(path), read_only=True)

    def cursor(self):
        conn = self._conn

        class _Cursor:
            def execute(self, sql: str):
                prefix = f"FROM {DATABASE}.information_schema.tables WHERE "
                sql = sql.replace(
                    prefix,
                    f"FROM information_schema.tables WHERE table_catalog = '{DATABASE}' AND ",
                )
                self._result = conn.execute(sql)

            def fetchall(self):
                return self._result.fetchall()

            def close(self):
                pass

        return _Cursor()

    def close(self):
        self._conn.close()


# ---------------------------------------------------------------------------
# Stage tasks
# ---------------------------------------------------------------------------


@dataclass
class Workspace:
    root: Path

    @property
    def store(self) -> Path:
        return self.root / "store"

    @property
    def parquet(self) -> Path:
        return self.root / "parquet"

    @property
    def database(self) -> Path:
        return self.root / f"{DATABASE}.duckdb"


def load_contract() -> dict:
    with open(CONTRACT_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def process_task(ws: Workspace, exporter_fault: str | None) -> int:
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith(("MSSP_", "SNOWFLAKE_", "AWS_PROFILE"))
    }
    env.update(
        MSSP_ACO_ID=ACO_ID,
        MSSP_FILE_STORE=str(ws.store),
        MSSP_OUTPUT_TYPE="PARQUET",
        MSSP_OUTPUT_LOCATION=str(ws.parquet),
        MSSP_FULL_REFRESH="true",
        MSSP_TEMP_LOCATION=str(ws.root / "tmp"),
    )
    if exporter_fault:
        cmd = [sys.executable, "-m", "tests.e2e.faulty_process", exporter_fault]
    else:
        cmd = ["mssp-process"]
    return subprocess.run(cmd, env=env, cwd=REPO_ROOT).returncode


def load_task(ws: Workspace) -> int:
    files = sorted(ws.parquet.glob("*.parquet"))
    if not files:
        print("no Parquet outputs to load")
        return 1
    with duckdb.connect(str(ws.database)) as conn:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
        for path in files:
            conn.execute(
                f'CREATE OR REPLACE TABLE {SCHEMA}."{path.stem}" AS '
                "SELECT * FROM read_parquet(?)",
                [str(path)],
            )
    print(f"loaded {len(files)} Parquet output(s) into {ws.database}:{SCHEMA}")
    return 0


def conformance_violations(conn, contract: dict) -> list[str]:
    violations: list[str] = []
    for relation in contract["relations"]:
        table = relation["table"]
        declared = [(c["name"], c["type"]) for c in contract["schemas"][relation["schema"]]["columns"]]
        observed = conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position",
            [SCHEMA, table],
        ).fetchall()
        if not observed:
            violations.append(f"{table}: missing")
            continue
        if [tuple(c) for c in observed] != declared:
            violations.append(f"{table}: columns {observed} != contract {declared}")
            continue
        rows = conn.execute(f"SELECT COUNT(*) FROM {SCHEMA}.{table}").fetchone()[0]
        if not relation.get("optional", False) and rows == 0:
            violations.append(f"{table}: no rows from a delivery that carries its sheet")

    for file_def in CCLF_FILE_DEFS:
        table = file_def.table_name.lower()
        try:
            rows = conn.execute(f"SELECT COUNT(*) FROM {SCHEMA}.{table}").fetchone()[0]
        except duckdb.CatalogException:
            violations.append(f"{table}: CCLF table missing")
            continue
        # A header-bearing file (CCLF0) spends its first line on the header.
        expected = CCLF_ROWS_PER_FILE - (1 if file_def.has_header else 0)
        if rows != expected:
            violations.append(f"{table}: {rows} rows, expected {expected}")
    return violations


def conformance_task(ws: Workspace, contract: dict) -> int:
    with duckdb.connect(str(ws.database), read_only=True) as conn:
        violations = conformance_violations(conn, contract)
    for v in violations:
        print(f"[contract] {v}")
    print(f"{len(violations)} conformance violation(s)")
    return 1 if violations else 0


# ---------------------------------------------------------------------------
# Plan + run
# ---------------------------------------------------------------------------


def run(ws: Workspace, fault: str | None) -> tuple[SequenceResult, list[str]]:
    contract = load_contract()
    exporter_fault = fault if fault in ("uppercase-columns", "missing-table") else None

    ecs = LocalEcsClient(
        {
            "mssp-process": lambda: process_task(ws, exporter_fault),
            "mssp-load": lambda: load_task(ws),
            "mssp-conformance": lambda: conformance_task(ws, contract),
        },
        drifted=frozenset({"mssp-load"}) if fault == "image-drift" else frozenset(),
    )

    lease = InMemoryLeaseStore()
    config = SequencerConfig(cluster=CLUSTER, lease_name="e2e-run", owner="e2e", lease_ttl=3600)
    if fault == "lease-held":
        lease.acquire(config.lease_name, owner="concurrent-run", now=int(time.time()), ttl=3600)

    gates = {"bootstrap": "true", "whitelist": "true"}
    if fault == "readiness-blocked":
        gates["whitelist"] = "false"

    workbook_outputs = AcceptedOutputContract(
        {r["table"]: {"database": DATABASE, "schema": SCHEMA} for r in contract["relations"]}
    )
    plan = StagePlan(
        stages=(
            Stage(
                name="process",
                taskdef_family="mssp-process",
                readiness=ReadinessPolicy({"bootstrap": "true", "whitelist": "true"}),
                expected_image=_image("mssp-process"),
                expected_task_revision=_arn("mssp-process"),
            ),
            Stage(
                name="load",
                taskdef_family="mssp-load",
                expected_image=_image("mssp-load"),
                output_contract=workbook_outputs,
            ),
            Stage(
                name="conformance",
                taskdef_family="mssp-conformance",
                expected_task_revision=_arn("mssp-conformance"),
            ),
        )
    )

    sequencer = Sequencer(
        ecs=ecs,
        lease=lease,
        config=config,
        readiness_source=lambda _stage: dict(gates),
        output_source=InformationSchemaOutputSource(
            connect=lambda: _CatalogShimConnection(ws.database)
        ),
    )
    result = sequencer.run(plan)

    # The run must leave the lease free (released on success and on failure),
    # unless another owner held it all along.
    if fault != "lease-held":
        lease.acquire(config.lease_name, owner="next-run", now=int(time.time()), ttl=60)
    return result, ecs.run_order


def check(result: SequenceResult, run_order: list[str], fault: str | None) -> list[str]:
    problems: list[str] = []
    stages = ["process", "load", "conformance"]
    families = {"process": "mssp-process", "load": "mssp-load", "conformance": "mssp-conformance"}
    if fault is None:
        if not result.ok:
            problems.append("sequence halted on clean synthetic data")
        if run_order != [families[s] for s in stages]:
            problems.append(f"launch order {run_order} != {stages}")
        return problems

    stage, gate = EXPECTED_HALT[fault]
    last = result.outcomes[-1] if result.outcomes else None
    if result.ok or last is None or (last.stage, last.gate) != (stage, gate):
        got = None if last is None else (last.stage, last.gate)
        problems.append(f"fault {fault!r} not caught at {(stage, gate)}; halted at {got}")
    # Nothing after the halting stage may launch.
    launched_limit = stages.index(stage) + (1 if gate in (GATE_TASK, GATE_OUTPUT_CONTRACT) else 0)
    expected_launches = [families[s] for s in stages[:launched_limit]]
    if run_order != expected_launches:
        problems.append(f"launch order {run_order} != {expected_launches}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workdir", required=True, type=Path, help="Scratch directory (emptied first)")
    parser.add_argument(
        "--fault",
        choices=sorted(EXPECTED_HALT),
        help="Inject a defect; pass only if the sequence halts where it should",
    )
    args = parser.parse_args(argv)

    started = time.monotonic()
    ws = Workspace(args.workdir.resolve())
    shutil.rmtree(ws.root, ignore_errors=True)
    build_delivery(ws.store)

    result, run_order = run(ws, args.fault)
    print("\n" + result.summary())
    problems = check(result, run_order, args.fault)
    label = args.fault or "clean"
    elapsed = time.monotonic() - started
    if problems:
        for p in problems:
            print(f"E2E FAIL [{label}]: {p}", file=sys.stderr)
        return 1
    verdict = "caught as expected" if args.fault else "passed"
    print(f"E2E OK [{label}]: {verdict} in {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
