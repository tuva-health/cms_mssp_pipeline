"""Output-set / placement verification from an accepted-output contract.

An accepted-output contract declares exactly which outputs a run may produce and
the placement coordinates each must land at (for example a warehouse
database/schema, or a bucket/prefix). Verification is an *exact* comparison:

* the produced output-name set must equal the accepted set -- no omissions, no
  extras;
* each produced output's coordinate keys must equal the contracted keys, with
  equal values -- no drift, no extra coordinate;
* no forbidden field (e.g. an inlined secret) may appear.

The contract contents -- which outputs, which coordinates, which values -- are
A-class policy. This module ships the contract type and the verification rule;
tests use a synthetic contract.
"""

from __future__ import annotations

from typing import Iterable, Mapping


class AcceptedOutputContract:
    """An accepted set of outputs, each with its expected placement coordinates."""

    def __init__(
        self,
        outputs: Mapping[str, Mapping[str, str]],
        *,
        forbidden_fields: Iterable[str] = (),
    ):
        self._outputs = {name: dict(coords) for name, coords in outputs.items()}
        self._forbidden = frozenset(forbidden_fields)

    @property
    def accepted(self) -> frozenset[str]:
        return frozenset(self._outputs)

    @property
    def forbidden_fields(self) -> frozenset[str]:
        return self._forbidden

    def placement(self, name: str) -> dict[str, str]:
        return dict(self._outputs[name])


def verify_outputs(
    contract: AcceptedOutputContract,
    produced: Mapping[str, Mapping[str, str]],
) -> list[str]:
    """Return the list of contract violations (empty means the run conforms)."""
    violations: list[str] = []

    produced_names = frozenset(produced)
    for missing in sorted(contract.accepted - produced_names):
        violations.append(f"output {missing!r} is missing from the produced set")
    for unexpected in sorted(produced_names - contract.accepted):
        violations.append(f"output {unexpected!r} is unexpected (not in the contract)")

    for name in sorted(contract.accepted & produced_names):
        expected = contract.placement(name)
        actual = dict(produced[name])

        for field in sorted(contract.forbidden_fields & frozenset(actual)):
            violations.append(f"output {name!r} contains forbidden field {field!r}")

        expected_keys = frozenset(expected)
        actual_keys = frozenset(actual) - contract.forbidden_fields
        for missing in sorted(expected_keys - actual_keys):
            violations.append(
                f"output {name!r} is missing placement coordinate {missing!r}"
            )
        for extra in sorted(actual_keys - expected_keys):
            violations.append(
                f"output {name!r} has unexpected coordinate {extra!r}"
            )
        for key in sorted(expected_keys & actual_keys):
            if actual[key] != expected[key]:
                violations.append(
                    f"output {name!r} coordinate {key!r} is {actual[key]!r}, "
                    f"expected {expected[key]!r}"
                )

    return violations
