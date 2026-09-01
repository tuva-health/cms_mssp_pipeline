"""Generic linear ECS stage sequencer.

Runs an ordered *stage plan* of ECS tasks, gating each stage with the four
convergence primitives and halting the whole sequence on any gate or stage
failure. The engine is client-neutral: the plan, cluster, taskdef families,
lease backend, readiness observations, identity expectations, and accepted
output sets are all inputs -- never literals baked into this module.

Per stage, strictly in order:

1. Acquire (first stage) or refresh (subsequent stages) the run **lease**
   (`lease.py`) -- a second concurrent run is rejected.
2. Evaluate the stage's named **readiness** policy (`readiness.py`) against the
   observed gate values from the injected readiness source -- halt if blocked.
3. Verify the target **image / task identity** (`release_identity.py`) against
   the identity the ECS client resolves for the taskdef family -- exact digest
   / exact revision, never a mutable tag.
4. Launch the ECS task (``run_task``) and **wait** for it to stop; a non-zero
   exit halts the sequence.
5. Where the stage declares one, verify the **output contract**
   (`output_contract.py`) against the produced outputs from the injected output
   source.

The lease is released at the end -- on success and on failure alike.

Seams (all injected, so the engine is fully unit-testable with no cloud):

* ``ecs`` -- an :class:`EcsClient` (resolve identity, run-task, wait). A real
  boto3 adapter (:class:`BotoEcsClient`) is provided for production; tests use a
  fake.
* ``lease`` -- a :class:`LeaseBackend`. The in-memory reference store from
  `lease.py` in tests; a real backend (DynamoDB / S3 conditional-put) is
  supplied by the A overlay.
* ``readiness_source`` / ``output_source`` -- callables that read the world
  (SSM, warehouse, ...) and hand observed / produced values to the gates. Both
  are A-class policy; tests supply fakes.
"""

from __future__ import annotations

import argparse
import importlib
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping, Protocol, Sequence

from mssp_pipeline.lease import LeaseError, LeaseOwnershipLost
from mssp_pipeline.output_contract import AcceptedOutputContract, verify_outputs
from mssp_pipeline.readiness import ReadinessPolicy, evaluate
from mssp_pipeline.release_identity import verify_image, verify_task_revision

# ---------------------------------------------------------------------------
# ECS client seam
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskIdentity:
    """The exact identity the ECS control plane resolves for a taskdef family:
    a pinned task-definition revision ARN and the container image it runs."""

    task_definition_arn: str
    image: str


@dataclass(frozen=True)
class TaskResult:
    """The terminal state of a launched ECS task."""

    task_arn: str
    exit_code: int
    stopped_reason: str | None = None


class EcsClient(Protocol):
    """The slice of ECS the sequencer needs. A real boto3 adapter implements it
    in production; tests supply a fake. No other module reaches ECS directly."""

    def describe_task_definition(self, family: str) -> TaskIdentity: ...

    def run_task(self, *, cluster: str, task_definition: str) -> str: ...

    def wait_for_stopped(self, *, cluster: str, task_arn: str) -> TaskResult: ...


# ---------------------------------------------------------------------------
# Lease backend seam
# ---------------------------------------------------------------------------


class LeaseBackend(Protocol):
    """The conditional-lease operations the sequencer needs. Satisfied by
    ``lease.InMemoryLeaseStore`` (tests) and by the A overlay's real backend."""

    def acquire(self, name: str, *, owner: str, now: int, ttl: int): ...

    def refresh(
        self,
        name: str,
        *,
        owner: str,
        now: int,
        ttl: int,
        fencing_token: int | None = ...,
    ): ...

    def release(self, name: str, *, owner: str) -> None: ...


# ---------------------------------------------------------------------------
# Stage plan schema (declarative input; neutral types)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Stage:
    """One ordered step of a plan.

    * ``taskdef_family`` -- the ECS taskdef family to run for this stage.
    * ``readiness`` -- the named-gate policy that must pass before launch
      (an empty policy is vacuously ready).
    * ``expected_image`` / ``expected_task_revision`` -- optional exact-identity
      expectations checked against what the ECS client resolves for the family.
    * ``output_contract`` -- an optional accepted-output contract verified after
      the task succeeds.
    """

    name: str
    taskdef_family: str
    readiness: ReadinessPolicy = field(default_factory=lambda: ReadinessPolicy({}))
    expected_image: str | None = None
    expected_task_revision: str | None = None
    output_contract: AcceptedOutputContract | None = None


@dataclass(frozen=True)
class StagePlan:
    """An ordered sequence of stages. Names must be non-empty and unique."""

    stages: tuple[Stage, ...]

    def __post_init__(self) -> None:
        if not self.stages:
            raise ValueError("a stage plan must have at least one stage")
        seen: set[str] = set()
        for stage in self.stages:
            if not stage.name or not stage.name.strip():
                raise ValueError("stage name must be non-empty")
            if stage.name in seen:
                raise ValueError(f"duplicate stage name {stage.name!r}")
            seen.add(stage.name)


@dataclass(frozen=True)
class SequencerConfig:
    """Run-level configuration -- all client-supplied inputs."""

    cluster: str
    lease_name: str
    owner: str
    lease_ttl: int


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------

# The gate/step that failed, for a blocked stage.
GATE_LEASE = "lease"
GATE_READINESS = "readiness"
GATE_IMAGE_IDENTITY = "image-identity"
GATE_TASK = "task"
GATE_OUTPUT_CONTRACT = "output-contract"


@dataclass(frozen=True)
class StageOutcome:
    """The result of attempting one stage."""

    stage: str
    ok: bool
    gate: str | None  # None on success; else the gate/step that halted the run
    detail: str


@dataclass(frozen=True)
class SequenceResult:
    """The result of running a whole plan (halts at the first failure)."""

    ok: bool
    outcomes: tuple[StageOutcome, ...]

    def summary(self) -> str:
        lines = []
        for outcome in self.outcomes:
            if outcome.ok:
                lines.append(f"[ok] {outcome.stage}")
            else:
                lines.append(f"[FAIL] {outcome.stage} ({outcome.gate}): {outcome.detail}")
        header = "sequence succeeded" if self.ok else "sequence HALTED"
        return "\n".join([header, *lines])


ReadinessSource = Callable[[Stage], Mapping[str, str]]
OutputSource = Callable[[Stage], Mapping[str, Mapping[str, str]]]


def _epoch_seconds() -> int:
    # Wall-clock (epoch) is intentional: a lease TTL is compared across separate
    # orchestrator processes/hosts, where time.monotonic() would be meaningless.
    return int(time.time())


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


class Sequencer:
    """Drives a stage plan: ``run-task -> wait -> gate -> next``, holding a run
    lease across the whole sequence and halting on the first failure."""

    def __init__(
        self,
        *,
        ecs: EcsClient,
        lease: LeaseBackend,
        config: SequencerConfig,
        readiness_source: ReadinessSource,
        output_source: OutputSource | None = None,
        clock: Callable[[], int] = _epoch_seconds,
    ) -> None:
        self._ecs = ecs
        self._lease = lease
        self._config = config
        self._readiness_source = readiness_source
        self._output_source = output_source
        self._clock = clock

    def run(self, plan: StagePlan) -> SequenceResult:
        cfg = self._config
        outcomes: list[StageOutcome] = []
        acquired = False
        try:
            for index, stage in enumerate(plan.stages):
                # 1. lease: acquire on the first stage, refresh thereafter.
                try:
                    if not acquired:
                        self._lease.acquire(
                            cfg.lease_name,
                            owner=cfg.owner,
                            now=self._clock(),
                            ttl=cfg.lease_ttl,
                        )
                        acquired = True
                    else:
                        self._lease.refresh(
                            cfg.lease_name,
                            owner=cfg.owner,
                            now=self._clock(),
                            ttl=cfg.lease_ttl,
                            fencing_token=index,
                        )
                except LeaseError as exc:
                    outcomes.append(
                        StageOutcome(stage.name, False, GATE_LEASE, str(exc))
                    )
                    return SequenceResult(False, tuple(outcomes))

                # 2. readiness policy.
                observed = self._readiness_source(stage)
                readiness = evaluate(stage.readiness, observed)
                if not readiness.ready:
                    outcomes.append(
                        StageOutcome(
                            stage.name, False, GATE_READINESS, readiness.summary()
                        )
                    )
                    return SequenceResult(False, tuple(outcomes))

                # 3. exact image / task identity.
                identity = self._ecs.describe_task_definition(stage.taskdef_family)
                try:
                    if stage.expected_image is not None:
                        verify_image(identity.image, stage.expected_image)
                    if stage.expected_task_revision is not None:
                        verify_task_revision(
                            identity.task_definition_arn, stage.expected_task_revision
                        )
                except ValueError as exc:
                    outcomes.append(
                        StageOutcome(stage.name, False, GATE_IMAGE_IDENTITY, str(exc))
                    )
                    return SequenceResult(False, tuple(outcomes))

                # 4. launch + wait.
                task_arn = self._ecs.run_task(
                    cluster=cfg.cluster,
                    task_definition=identity.task_definition_arn,
                )
                task_result = self._ecs.wait_for_stopped(
                    cluster=cfg.cluster, task_arn=task_arn
                )
                if task_result.exit_code != 0:
                    reason = task_result.stopped_reason or "no reason reported"
                    outcomes.append(
                        StageOutcome(
                            stage.name,
                            False,
                            GATE_TASK,
                            f"task exited {task_result.exit_code}: {reason}",
                        )
                    )
                    return SequenceResult(False, tuple(outcomes))

                # 5. output contract (only when the stage declares one).
                if stage.output_contract is not None:
                    produced = (
                        self._output_source(stage)
                        if self._output_source is not None
                        else {}
                    )
                    violations = verify_outputs(stage.output_contract, produced)
                    if violations:
                        outcomes.append(
                            StageOutcome(
                                stage.name,
                                False,
                                GATE_OUTPUT_CONTRACT,
                                "; ".join(violations),
                            )
                        )
                        return SequenceResult(False, tuple(outcomes))

                outcomes.append(StageOutcome(stage.name, True, None, "succeeded"))

            return SequenceResult(True, tuple(outcomes))
        finally:
            # Release the lease we hold -- on success and on failure alike.
            # Best-effort: if it expired and was taken over, ownership is already
            # gone and there is nothing to release.
            if acquired:
                try:
                    self._lease.release(cfg.lease_name, owner=cfg.owner)
                except LeaseOwnershipLost:
                    pass


# ---------------------------------------------------------------------------
# Real boto3 ECS adapter (production; never exercised by unit tests)
# ---------------------------------------------------------------------------


class BotoEcsClient:
    """boto3-backed :class:`EcsClient`.

    Network/launch configuration (subnets, security groups, launch type) is the
    client's own concern and is held here, keeping the engine cluster-agnostic.
    A boto3 ``ecs`` client may be injected for unit testing the adapter's request
    shaping without any real AWS call; in production it is created lazily so the
    engine's own unit tests never import boto3.
    """

    def __init__(
        self,
        *,
        client=None,
        region: str | None = None,
        launch_type: str = "FARGATE",
        network_configuration: dict | None = None,
    ) -> None:
        if client is None:  # pragma: no cover - exercised only against real AWS
            import boto3

            client = boto3.client("ecs", region_name=region)
        self._ecs = client
        self._launch_type = launch_type
        self._network_configuration = network_configuration

    def describe_task_definition(self, family: str) -> TaskIdentity:
        resp = self._ecs.describe_task_definition(taskDefinition=family)
        td = resp["taskDefinition"]
        return TaskIdentity(
            task_definition_arn=td["taskDefinitionArn"],
            image=td["containerDefinitions"][0]["image"],
        )

    def run_task(self, *, cluster: str, task_definition: str) -> str:
        kwargs: dict = {
            "cluster": cluster,
            "taskDefinition": task_definition,
            "launchType": self._launch_type,
            "count": 1,
        }
        if self._network_configuration is not None:
            kwargs["networkConfiguration"] = self._network_configuration
        resp = self._ecs.run_task(**kwargs)
        failures = resp.get("failures") or []
        if failures:
            raise RuntimeError(f"run-task failed: {failures}")
        return resp["tasks"][0]["taskArn"]

    def wait_for_stopped(self, *, cluster: str, task_arn: str) -> TaskResult:
        waiter = self._ecs.get_waiter("tasks_stopped")
        waiter.wait(cluster=cluster, tasks=[task_arn])
        desc = self._ecs.describe_tasks(cluster=cluster, tasks=[task_arn])
        task = desc["tasks"][0]
        containers = task.get("containers") or [{}]
        exit_code = containers[0].get("exitCode")
        return TaskResult(
            task_arn=task_arn,
            # A task that stops without an exit code (killed, failed to start)
            # is a failure, not a silent success.
            exit_code=exit_code if exit_code is not None else 1,
            stopped_reason=task.get("stoppedReason"),
        )


# ---------------------------------------------------------------------------
# CLI: mssp-sequence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SequencerJob:
    """What a plan provider returns: a fully wired sequencer and the plan to
    run. The A overlay writes the provider (concrete plan, real ECS client, real
    lease backend, real sources); this engine stays client-neutral."""

    sequencer: Sequencer
    plan: StagePlan


def _load_provider(spec: str) -> Callable[[], SequencerJob]:
    """Resolve a ``module.path:callable`` reference to a plan-provider callable."""
    if ":" not in spec:
        raise ValueError(
            f"plan provider {spec!r} must be 'module.path:callable'"
        )
    module_name, _, attr = spec.partition(":")
    module = importlib.import_module(module_name)
    provider = getattr(module, attr)
    if not callable(provider):
        raise TypeError(f"plan provider {spec!r} is not callable")
    return provider


def sequence_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mssp-sequence",
        description=(
            "Run an ordered ECS stage plan on the convergence primitives. The "
            "plan and its wiring are supplied by a client provider callable; "
            "this engine bakes in no plan or client literal."
        ),
    )
    parser.add_argument(
        "--plan-provider",
        required=True,
        help=(
            "'module.path:callable' returning a SequencerJob (a wired Sequencer "
            "plus the StagePlan to run). Supplied by the client overlay."
        ),
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    provider = _load_provider(args.plan_provider)
    job = provider()
    result = job.sequencer.run(job.plan)
    stream = None if result.ok else sys.stderr
    print(result.summary(), file=stream)
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(sequence_main())
