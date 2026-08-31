"""Named-policy readiness evaluator.

A run is "ready" when every named gate declared by a policy resolves to its
expected value. This module owns the *policy type* and the *evaluation rule*
only. Which gates exist, what they expect, and where their observed values come
from (SSM, a file, a database, ...) is A-class policy supplied by a downstream
overlay -- the overlay reads the backend and hands the observed values here.

    policy = ReadinessPolicy({"bootstrap": "true", "whitelist": "true"})
    result = evaluate(policy, read_gate_values())
    if not result.ready:
        raise RuntimeError(result.summary())
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class BlockedGate:
    """A gate whose observed value did not meet the policy."""

    gate: str
    expected: str
    observed: str | None

    @property
    def reason(self) -> str:
        seen = "missing" if self.observed is None else repr(self.observed)
        return f"gate {self.gate!r} expected {self.expected!r} but observed {seen}"


@dataclass(frozen=True)
class ReadinessResult:
    """Outcome of evaluating a policy against observed gate values."""

    ready: bool
    blocked: tuple[BlockedGate, ...]

    def summary(self) -> str:
        if self.ready:
            return "readiness confirmed"
        return "readiness blocked: " + "; ".join(b.reason for b in self.blocked)


class ReadinessPolicy:
    """An ordered mapping of gate name -> expected value."""

    def __init__(self, gates: Mapping[str, str]):
        built: dict[str, str] = {}
        for name, expected in gates.items():
            if not name or not name.strip():
                raise ValueError("readiness gate name must be non-empty")
            built[name] = expected
        self._gates = built

    @property
    def gates(self) -> tuple[str, ...]:
        return tuple(self._gates)

    def expected(self, gate: str) -> str:
        return self._gates[gate]


def evaluate(
    policy: ReadinessPolicy, observed: Mapping[str, str]
) -> ReadinessResult:
    """Evaluate ``policy`` against ``observed`` gate values.

    Every gate must be present and equal to its expected value. All failing
    gates are reported (not short-circuited) so an operator sees the full set of
    reasons at once, in policy order.
    """
    blocked: list[BlockedGate] = []
    for gate in policy.gates:
        expected = policy.expected(gate)
        value = observed.get(gate)
        if value != expected:
            blocked.append(BlockedGate(gate=gate, expected=expected, observed=value))
    return ReadinessResult(ready=not blocked, blocked=tuple(blocked))


# --- Backend-agnostic CLI ---------------------------------------------------
#
# `python -m mssp_pipeline.readiness <gate> [<gate> ...]` evaluates the named
# gates, each expected to be "true", against values read from the environment
# (gate "bootstrap" -> MSSP_READINESS_BOOTSTRAP). How those values get into the
# environment -- SSM, a secret, a file -- is the overlay's concern; the gate
# names and the expected value keep this a named-policy evaluation.

_ENV_PREFIX = "MSSP_READINESS_"
_EXPECTED = "true"


def _env_key(gate: str) -> str:
    return _ENV_PREFIX + gate.upper().replace("-", "_")


def main(argv: Sequence[str] | None = None) -> int:
    gates = list(sys.argv[1:] if argv is None else argv)
    if not gates:
        print(
            "usage: python -m mssp_pipeline.readiness <gate> [<gate> ...]",
            file=sys.stderr,
        )
        return 64
    policy = ReadinessPolicy({gate: _EXPECTED for gate in gates})
    observed = {
        gate: os.environ[_env_key(gate)]
        for gate in gates
        if _env_key(gate) in os.environ
    }
    result = evaluate(policy, observed)
    if result.ready:
        print("readiness confirmed: " + ", ".join(gates))
        return 0
    print(result.summary(), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
