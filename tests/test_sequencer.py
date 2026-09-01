"""Generic linear ECS stage sequencer (unit, no cloud).

The sequencer runs an ordered stage plan of ECS tasks, gating each stage with
the four convergence primitives (lease, readiness, release-identity,
output-contract). These tests drive it against a fake ECS client and the
in-memory lease store -- no boto3, no cloud, no credentials.
"""

from __future__ import annotations

import pytest

from mssp_pipeline.lease import InMemoryLeaseStore
from mssp_pipeline.output_contract import AcceptedOutputContract
from mssp_pipeline.readiness import ReadinessPolicy
from mssp_pipeline.lease import LeaseUnavailable
from mssp_pipeline.sequencer import (
    LaunchError,
    SequencerConfig,
    Sequencer,
    Stage,
    StagePlan,
    TaskIdentity,
    TaskResult,
)

# --- synthetic, client-neutral identities -----------------------------------

ACCOUNT = "123456789012"
REGION = "us-east-1"


def _arn(family: str, revision: int) -> str:
    return f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/{family}:{revision}"


def _image(family: str) -> str:
    # A deterministic 64-hex digest derived from the family name (no real registry).
    digest = (family.encode().hex() + "0" * 64)[:64]
    return f"registry.example/{family}@sha256:{digest}"


class FakeEcsClient:
    """In-memory ECS stand-in: resolves families to identities, records
    run-task calls in order, and returns configured exit codes."""

    def __init__(
        self,
        identities: dict[str, TaskIdentity],
        exit_codes: dict[str, int] | None = None,
        launch_failures: tuple[str, ...] = (),
    ) -> None:
        self._identities = identities
        self._exit_codes = exit_codes or {}
        self._launch_failures = frozenset(launch_failures)
        self.describe_calls: list[str] = []
        self.run_order: list[str] = []
        self._running: dict[str, str] = {}  # task_arn -> family

    def describe_task_definition(self, family: str) -> TaskIdentity:
        self.describe_calls.append(family)
        return self._identities[family]

    def run_task(self, *, cluster: str, task_definition: str) -> str:
        # Map the resolved revision ARN back to its family for bookkeeping.
        family = next(
            f for f, ident in self._identities.items()
            if ident.task_definition_arn == task_definition
        )
        if family in self._launch_failures:
            # Mirror a real ECS run-task rejection (capacity, subnet, ...): the
            # task never starts, so it is NOT appended to run_order.
            raise LaunchError(f"run-task failed for {family}: CapacityUnavailable")
        self.run_order.append(family)
        task_arn = f"arn:aws:ecs:{REGION}:{ACCOUNT}:task/{cluster}/{family}-run"
        self._running[task_arn] = family
        return task_arn

    def wait_for_stopped(self, *, cluster: str, task_arn: str) -> TaskResult:
        family = self._running[task_arn]
        code = self._exit_codes.get(family, 0)
        return TaskResult(
            task_arn=task_arn,
            exit_code=code,
            stopped_reason=None if code == 0 else f"{family} exited {code}",
        )


def _identities(*families: str) -> dict[str, TaskIdentity]:
    return {
        f: TaskIdentity(task_definition_arn=_arn(f, 1), image=_image(f))
        for f in families
    }


def _config() -> SequencerConfig:
    return SequencerConfig(
        cluster="test-cluster",
        lease_name="run-lease",
        owner="exec-1",
        lease_ttl=300,
    )


def _ready(_stage: Stage) -> dict[str, str]:
    """Readiness source that reports every gate satisfied."""
    return {}


def _stage(name: str, taskdef_family: str | None = None, **kwargs) -> Stage:
    """Build a Stage that satisfies the fail-closed identity rule by default.

    Unless the caller declares an identity expectation explicitly, pin the
    stage to the exact image the fake resolves for its family, so plans built
    in tests are valid without repeating the digest everywhere.
    """
    family = taskdef_family if taskdef_family is not None else name
    if "expected_image" not in kwargs and "expected_task_revision" not in kwargs:
        kwargs["expected_image"] = _image(family)
    return Stage(name=name, taskdef_family=family, **kwargs)


class Clock:
    def __init__(self) -> None:
        self.t = 0

    def __call__(self) -> int:
        self.t += 1
        return self.t


# --- ordered success ---------------------------------------------------------


def test_ordered_success_runs_every_stage_in_order() -> None:
    ecs = FakeEcsClient(_identities("download", "raw", "dbt"))
    store = InMemoryLeaseStore()
    plan = StagePlan(
        stages=(
            _stage(name="download", taskdef_family="download"),
            _stage(name="raw", taskdef_family="raw"),
            _stage(name="dbt", taskdef_family="dbt"),
        )
    )
    seq = Sequencer(
        ecs=ecs,
        lease=store,
        config=_config(),
        readiness_source=lambda s: {g: seq_expected(s, g) for g in s.readiness.gates},
        clock=Clock(),
    )
    result = seq.run(plan)

    assert result.ok is True
    assert [o.stage for o in result.outcomes] == ["download", "raw", "dbt"]
    assert all(o.ok for o in result.outcomes)
    # Stages launched strictly in plan order.
    assert ecs.run_order == ["download", "raw", "dbt"]


def seq_expected(stage: Stage, gate: str) -> str:
    return stage.readiness.expected(gate)


def test_lease_is_released_after_a_successful_run() -> None:
    ecs = FakeEcsClient(_identities("download"))
    store = InMemoryLeaseStore()
    plan = StagePlan(stages=(_stage(name="download", taskdef_family="download"),))
    seq = Sequencer(
        ecs=ecs, lease=store, config=_config(), readiness_source=_ready, clock=Clock()
    )
    result = seq.run(plan)

    assert result.ok is True
    # Lease freed: a different owner can acquire immediately.
    assert store.get("run-lease") is None


# --- mid-sequence stage failure halts the rest -------------------------------


def test_mid_sequence_task_failure_halts_and_skips_later_stages() -> None:
    ecs = FakeEcsClient(
        _identities("download", "raw", "dbt"),
        exit_codes={"raw": 3},  # second stage's task exits non-zero
    )
    store = InMemoryLeaseStore()
    plan = StagePlan(
        stages=(
            _stage(name="download", taskdef_family="download"),
            _stage(name="raw", taskdef_family="raw"),
            _stage(name="dbt", taskdef_family="dbt"),
        )
    )
    seq = Sequencer(
        ecs=ecs, lease=store, config=_config(), readiness_source=_ready, clock=Clock()
    )
    result = seq.run(plan)

    assert result.ok is False
    # First stage ran and passed; the failing stage is recorded; the third
    # stage never launched.
    assert [o.stage for o in result.outcomes] == ["download", "raw"]
    assert result.outcomes[0].ok is True
    assert result.outcomes[1].ok is False
    assert result.outcomes[1].gate == "task"
    assert ecs.run_order == ["download", "raw"]  # dbt not launched
    # Lease released even though the run failed.
    assert store.get("run-lease") is None


# --- each gate blocks --------------------------------------------------------


def test_readiness_gate_blocks_before_launch() -> None:
    ecs = FakeEcsClient(_identities("raw"))
    store = InMemoryLeaseStore()
    plan = StagePlan(
        stages=(
            _stage(
                name="raw",
                taskdef_family="raw",
                readiness=ReadinessPolicy({"bootstrap": "true"}),
            ),
        )
    )
    # Source reports the gate NOT satisfied.
    seq = Sequencer(
        ecs=ecs,
        lease=store,
        config=_config(),
        readiness_source=lambda s: {"bootstrap": "false"},
        clock=Clock(),
    )
    result = seq.run(plan)

    assert result.ok is False
    assert result.outcomes[-1].gate == "readiness"
    assert "bootstrap" in result.outcomes[-1].detail
    # Readiness fails before the task launches.
    assert ecs.run_order == []
    assert store.get("run-lease") is None


def test_image_identity_gate_blocks_on_digest_mismatch() -> None:
    ecs = FakeEcsClient(_identities("raw"))  # resolves to _image("raw")
    store = InMemoryLeaseStore()
    other_digest = "b" * 64
    plan = StagePlan(
        stages=(
            Stage(
                name="raw",
                taskdef_family="raw",
                expected_image=f"registry.example/raw@sha256:{other_digest}",
            ),
        )
    )
    seq = Sequencer(
        ecs=ecs, lease=store, config=_config(), readiness_source=_ready, clock=Clock()
    )
    result = seq.run(plan)

    assert result.ok is False
    assert result.outcomes[-1].gate == "image-identity"
    assert ecs.run_order == []  # never launched
    assert store.get("run-lease") is None


def test_task_revision_gate_blocks_on_mutable_expectation() -> None:
    ecs = FakeEcsClient(_identities("raw"))
    store = InMemoryLeaseStore()
    plan = StagePlan(
        stages=(
            Stage(
                name="raw",
                taskdef_family="raw",
                # A bare family (no :revision) is mutable -> rejected.
                expected_task_revision=(
                    f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/raw"
                ),
            ),
        )
    )
    seq = Sequencer(
        ecs=ecs, lease=store, config=_config(), readiness_source=_ready, clock=Clock()
    )
    result = seq.run(plan)

    assert result.ok is False
    assert result.outcomes[-1].gate == "image-identity"
    assert ecs.run_order == []


def test_output_contract_gate_blocks_after_a_successful_task() -> None:
    ecs = FakeEcsClient(_identities("raw"))
    store = InMemoryLeaseStore()
    contract = AcceptedOutputContract(
        {"raw_dev": {"database": "DB_DEV", "schema": "RAW"}}
    )
    plan = StagePlan(
        stages=(
            _stage(name="raw", taskdef_family="raw", output_contract=contract),
        )
    )
    # Output source produces the wrong placement -> a contract violation.
    seq = Sequencer(
        ecs=ecs,
        lease=store,
        config=_config(),
        readiness_source=_ready,
        output_source=lambda s: {"raw_dev": {"database": "WRONG", "schema": "RAW"}},
        clock=Clock(),
    )
    result = seq.run(plan)

    assert result.ok is False
    assert result.outcomes[-1].gate == "output-contract"
    # The task did run (contract is a post-run check).
    assert ecs.run_order == ["raw"]
    assert store.get("run-lease") is None


def test_output_contract_passes_when_produced_matches() -> None:
    ecs = FakeEcsClient(_identities("raw"))
    store = InMemoryLeaseStore()
    placement = {"database": "DB_DEV", "schema": "RAW"}
    contract = AcceptedOutputContract({"raw_dev": placement})
    plan = StagePlan(
        stages=(_stage(name="raw", taskdef_family="raw", output_contract=contract),)
    )
    seq = Sequencer(
        ecs=ecs,
        lease=store,
        config=_config(),
        readiness_source=_ready,
        output_source=lambda s: {"raw_dev": dict(placement)},
        clock=Clock(),
    )
    result = seq.run(plan)
    assert result.ok is True


# --- lease concurrency rejection ---------------------------------------------


def test_second_concurrent_run_is_rejected() -> None:
    ecs = FakeEcsClient(_identities("download"))
    store = InMemoryLeaseStore()
    # Another executor already holds a live lease on the same name.
    store.acquire("run-lease", owner="other-exec", now=0, ttl=10_000)

    plan = StagePlan(stages=(_stage(name="download", taskdef_family="download"),))
    seq = Sequencer(
        ecs=ecs, lease=store, config=_config(), readiness_source=_ready, clock=Clock()
    )
    result = seq.run(plan)

    assert result.ok is False
    assert result.outcomes[-1].gate == "lease"
    # Nothing launched, and the other owner's lease is untouched.
    assert ecs.run_order == []
    held = store.get("run-lease")
    assert held is not None and held.owner == "other-exec"


def test_lease_is_refreshed_between_stages_with_advancing_fencing_token() -> None:
    ecs = FakeEcsClient(_identities("download", "raw"))
    store = InMemoryLeaseStore()
    plan = StagePlan(
        stages=(
            _stage(name="download", taskdef_family="download"),
            _stage(name="raw", taskdef_family="raw"),
        )
    )
    seq = Sequencer(
        ecs=ecs, lease=store, config=_config(), readiness_source=_ready, clock=Clock()
    )
    result = seq.run(plan)
    assert result.ok is True
    # Two stages: acquire then one refresh. Both released at the end.
    assert store.get("run-lease") is None


# --- plan schema validation --------------------------------------------------


def test_empty_plan_is_rejected() -> None:
    with pytest.raises(ValueError):
        StagePlan(stages=())


def test_duplicate_stage_name_is_rejected() -> None:
    with pytest.raises(ValueError):
        StagePlan(
            stages=(
                _stage(name="raw", taskdef_family="raw"),
                _stage(name="raw", taskdef_family="raw2"),
            )
        )


def test_stage_without_any_image_identity_expectation_is_rejected() -> None:
    # Fail-closed: a stage that pins neither an image digest nor a task revision
    # would launch an unverified (possibly mutable-tag) image -> plan is invalid.
    with pytest.raises(ValueError, match="image-identity"):
        StagePlan(stages=(Stage(name="raw", taskdef_family="raw"),))


def test_stage_with_only_a_task_revision_expectation_is_valid() -> None:
    # Declaring just one of the two identity expectations is enough.
    plan = StagePlan(
        stages=(
            Stage(
                name="raw",
                taskdef_family="raw",
                expected_task_revision=_arn("raw", 1),
            ),
        )
    )
    assert len(plan.stages) == 1


# --- fencing token: monotonic across runs ------------------------------------


class SpyLeaseBackend:
    """A lease backend that records the fencing tokens passed to refresh,
    delegating the actual conditional semantics to the real in-memory store."""

    def __init__(self) -> None:
        self._inner = InMemoryLeaseStore()
        self.refresh_tokens: list[int | None] = []

    def acquire(self, name, *, owner, now, ttl):
        return self._inner.acquire(name, owner=owner, now=now, ttl=ttl)

    def refresh(self, name, *, owner, now, ttl, fencing_token=None):
        self.refresh_tokens.append(fencing_token)
        return self._inner.refresh(
            name, owner=owner, now=now, ttl=ttl, fencing_token=fencing_token
        )

    def release(self, name, *, owner):
        return self._inner.release(name, owner=owner)

    def get(self, name):
        return self._inner.get(name)


def _tokens_for_run_acquired_at(epoch: int) -> list[int | None]:
    ecs = FakeEcsClient(_identities("download", "raw"))
    spy = SpyLeaseBackend()
    plan = StagePlan(
        stages=(
            _stage(name="download", taskdef_family="download"),
            _stage(name="raw", taskdef_family="raw"),
        )
    )
    seq = Sequencer(
        ecs=ecs,
        lease=spy,
        config=_config(),
        readiness_source=_ready,
        clock=lambda: epoch,  # a run's acquisition epoch is fixed
    )
    assert seq.run(plan).ok is True
    return spy.refresh_tokens


def test_fencing_token_base_is_the_epoch_and_monotonic_across_runs() -> None:
    early = _tokens_for_run_acquired_at(100)
    late = _tokens_for_run_acquired_at(500)

    # Within a run the token strictly advances (the lease primitive requires it).
    assert all(a < b for a, b in zip(early, early[1:]))
    assert all(a < b for a, b in zip(late, late[1:]))
    # The base is the acquisition epoch, not a per-run-restarting stage index.
    assert min(early) == 100
    assert min(late) == 500
    # Cross-run: every token of the later run exceeds every token of the earlier
    # run -> a stale redrive can never out-fence a fresher run. (A stage-index
    # token would give both runs the same values and fail this.)
    assert max(early) < min(late)


def test_stale_run_cannot_retake_after_a_newer_run_acquired() -> None:
    # The property the sequencer's epoch-based token guarantees, shown against
    # the real lease store: a run that started earlier (smaller epoch base)
    # loses a re-take to one that started later, even as the same logical owner
    # (a redrive of the same job).
    store = InMemoryLeaseStore()
    owner = "mssp-run"

    # Stale run acquired at epoch 100 and progressed a couple of stages.
    store.acquire("run-lease", owner=owner, now=100, ttl=50)
    store.refresh("run-lease", owner=owner, now=100, ttl=50, fencing_token=100)
    store.refresh("run-lease", owner=owner, now=110, ttl=50, fencing_token=101)

    # It hangs; the lease expires; a fresher run (epoch 500) takes over + stamps.
    store.refresh("run-lease", owner=owner, now=500, ttl=50, fencing_token=500)

    # The stale run wakes and tries to refresh for its next stage -- its token is
    # still based on epoch 100, so it is fenced out.
    with pytest.raises(LeaseUnavailable):
        store.refresh("run-lease", owner=owner, now=520, ttl=50, fencing_token=102)


# --- launch failure halts cleanly --------------------------------------------


def test_launch_failure_halts_cleanly_and_skips_later_stages() -> None:
    ecs = FakeEcsClient(
        _identities("download", "raw", "dbt"),
        launch_failures=("raw",),  # run-task rejects the second stage's launch
    )
    store = InMemoryLeaseStore()
    plan = StagePlan(
        stages=(
            _stage(name="download", taskdef_family="download"),
            _stage(name="raw", taskdef_family="raw"),
            _stage(name="dbt", taskdef_family="dbt"),
        )
    )
    seq = Sequencer(
        ecs=ecs, lease=store, config=_config(), readiness_source=_ready, clock=Clock()
    )
    result = seq.run(plan)

    # Clean halt (no traceback escapes), reported as the launch gate.
    assert result.ok is False
    assert [o.stage for o in result.outcomes] == ["download", "raw"]
    assert result.outcomes[-1].gate == "launch"
    assert "raw" in result.outcomes[-1].detail
    assert "launch" in result.summary().lower()
    # The first stage launched; the failing launch is not counted as launched;
    # the third stage never launches.
    assert ecs.run_order == ["download"]
    # Lease released despite the failure.
    assert store.get("run-lease") is None


def test_launch_error_is_raised_by_the_boto_adapter_on_ecs_failures() -> None:
    from mssp_pipeline.sequencer import BotoEcsClient

    class StubBoto:
        def run_task(self, **kwargs):
            return {"tasks": [], "failures": [{"reason": "CapacityUnavailable"}]}

    adapter = BotoEcsClient(client=StubBoto())
    with pytest.raises(LaunchError):
        adapter.run_task(cluster="c", task_definition=_arn("raw", 1))


# --- CLI ---------------------------------------------------------------------


def test_cli_runs_a_provider_plan_and_exits_zero_on_success(monkeypatch) -> None:
    import sys
    import types

    from mssp_pipeline.sequencer import SequencerJob, sequence_main

    def _make_job() -> SequencerJob:
        ecs = FakeEcsClient(_identities("download"))
        store = InMemoryLeaseStore()
        plan = StagePlan(stages=(_stage(name="download", taskdef_family="download"),))
        seq = Sequencer(
            ecs=ecs,
            lease=store,
            config=_config(),
            readiness_source=_ready,
            clock=Clock(),
        )
        return SequencerJob(sequencer=seq, plan=plan)

    module = types.ModuleType("fake_plan_provider")
    module.make_job = _make_job
    monkeypatch.setitem(sys.modules, "fake_plan_provider", module)

    rc = sequence_main(["--plan-provider", "fake_plan_provider:make_job"])
    assert rc == 0


def test_cli_exits_non_zero_when_the_sequence_halts(monkeypatch) -> None:
    import sys
    import types

    from mssp_pipeline.sequencer import SequencerJob, sequence_main

    def _make_job() -> SequencerJob:
        ecs = FakeEcsClient(_identities("raw"), exit_codes={"raw": 2})
        store = InMemoryLeaseStore()
        plan = StagePlan(stages=(_stage(name="raw", taskdef_family="raw"),))
        seq = Sequencer(
            ecs=ecs,
            lease=store,
            config=_config(),
            readiness_source=_ready,
            clock=Clock(),
        )
        return SequencerJob(sequencer=seq, plan=plan)

    module = types.ModuleType("fake_plan_provider_fail")
    module.make_job = _make_job
    monkeypatch.setitem(sys.modules, "fake_plan_provider_fail", module)

    rc = sequence_main(["--plan-provider", "fake_plan_provider_fail:make_job"])
    assert rc == 1


# --- boto3 adapter request shaping (stubbed client; no real AWS) --------------


def test_boto_adapter_shapes_requests_against_a_stub_client() -> None:
    from mssp_pipeline.sequencer import BotoEcsClient

    class StubWaiter:
        def __init__(self) -> None:
            self.waited: list[dict] = []

        def wait(self, **kwargs) -> None:
            self.waited.append(kwargs)

    class StubBoto:
        def __init__(self) -> None:
            self.run_kwargs: dict | None = None
            self.waiter = StubWaiter()

        def describe_task_definition(self, taskDefinition):  # noqa: N803 (boto casing)
            return {
                "taskDefinition": {
                    "taskDefinitionArn": _arn("download", 5),
                    "containerDefinitions": [{"image": _image("download")}],
                }
            }

        def run_task(self, **kwargs):
            self.run_kwargs = kwargs
            return {"tasks": [{"taskArn": "arn:task/xyz"}], "failures": []}

        def get_waiter(self, name):
            assert name == "tasks_stopped"
            return self.waiter

        def describe_tasks(self, cluster, tasks):
            return {"tasks": [{"containers": [{"exitCode": 0}], "stoppedReason": None}]}

    stub = StubBoto()
    adapter = BotoEcsClient(
        client=stub,
        network_configuration={"awsvpcConfiguration": {"subnets": ["subnet-1"]}},
    )

    identity = adapter.describe_task_definition("download")
    assert identity.task_definition_arn == _arn("download", 5)

    task_arn = adapter.run_task(cluster="c", task_definition=identity.task_definition_arn)
    assert task_arn == "arn:task/xyz"
    assert stub.run_kwargs["cluster"] == "c"
    assert stub.run_kwargs["taskDefinition"] == _arn("download", 5)
    assert stub.run_kwargs["networkConfiguration"] == {
        "awsvpcConfiguration": {"subnets": ["subnet-1"]}
    }

    result = adapter.wait_for_stopped(cluster="c", task_arn=task_arn)
    assert result.exit_code == 0
    # The waiter is polled on a generous budget, not boto's 10-min default, so a
    # long-running stage (download / raw / dbt) is not cut off while still live.
    assert stub.waiter.waited[0]["WaiterConfig"] == {"Delay": 15, "MaxAttempts": 480}


def test_wait_timeout_is_configurable(monkeypatch) -> None:
    from mssp_pipeline.sequencer import BotoEcsClient

    class StubWaiter:
        def __init__(self) -> None:
            self.waited: list[dict] = []

        def wait(self, **kwargs) -> None:
            self.waited.append(kwargs)

    class StubBoto:
        def __init__(self) -> None:
            self.waiter = StubWaiter()

        def get_waiter(self, name):
            return self.waiter

        def describe_tasks(self, cluster, tasks):
            return {"tasks": [{"containers": [{"exitCode": 0}], "stoppedReason": None}]}

    # Explicit constructor values win.
    stub = StubBoto()
    BotoEcsClient(client=stub, wait_delay_seconds=30, wait_max_attempts=200).wait_for_stopped(
        cluster="c", task_arn="a"
    )
    assert stub.waiter.waited[0]["WaiterConfig"] == {"Delay": 30, "MaxAttempts": 200}

    # Otherwise the env contract tunes it.
    monkeypatch.setenv("MSSP_TASK_WAIT_DELAY_SECONDS", "20")
    monkeypatch.setenv("MSSP_TASK_WAIT_MAX_ATTEMPTS", "360")
    stub2 = StubBoto()
    BotoEcsClient(client=stub2).wait_for_stopped(cluster="c", task_arn="a")
    assert stub2.waiter.waited[0]["WaiterConfig"] == {"Delay": 20, "MaxAttempts": 360}


def test_wait_for_stopped_is_not_masked_by_a_passing_sibling_container() -> None:
    """A failed workload must surface regardless of container order in the ECS
    response; a sibling gate container that exited 0 must not mask it."""
    from mssp_pipeline.sequencer import BotoEcsClient

    def _boto(containers):
        class StubWaiter:
            def wait(self, **kwargs) -> None:
                return None

        class StubBoto:
            def __init__(self) -> None:
                self.waiter = StubWaiter()

            def get_waiter(self, name):
                return self.waiter

            def describe_tasks(self, cluster, tasks):
                return {"tasks": [{"containers": containers, "stoppedReason": "r"}]}

        return BotoEcsClient(client=StubBoto())

    gate_ok_workload_fail = [
        {"name": "readiness-gates", "exitCode": 0},
        {"name": "workload", "exitCode": 127},
    ]
    # Both orderings must report the failure.
    assert _boto(gate_ok_workload_fail).wait_for_stopped(cluster="c", task_arn="a").exit_code == 127
    assert _boto(list(reversed(gate_ok_workload_fail))).wait_for_stopped(cluster="c", task_arn="a").exit_code == 127
    # A missing exit code (killed / never started) is a failure.
    assert _boto(
        [{"name": "readiness-gates", "exitCode": 0}, {"name": "workload", "exitCode": None}]
    ).wait_for_stopped(cluster="c", task_arn="a").exit_code == 1
    # Every container exited 0 -> success.
    assert _boto(
        [{"name": "readiness-gates", "exitCode": 0}, {"name": "workload", "exitCode": 0}]
    ).wait_for_stopped(cluster="c", task_arn="a").exit_code == 0
