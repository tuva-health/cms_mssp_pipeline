"""Named-policy readiness evaluator (synthetic policies).

The core ships the policy type and the evaluation rule. Which gates exist and
which backend supplies their values is A-class policy; these tests use synthetic
gates and in-memory values.
"""

from __future__ import annotations

import pytest

from mssp_pipeline.readiness import ReadinessPolicy, evaluate


def test_all_gates_true_is_ready() -> None:
    policy = ReadinessPolicy({"bootstrap": "true", "whitelist": "true"})
    result = evaluate(policy, {"bootstrap": "true", "whitelist": "true"})
    assert result.ready is True
    assert result.blocked == ()


def test_a_false_gate_blocks_with_reason() -> None:
    policy = ReadinessPolicy({"bootstrap": "true", "whitelist": "true"})
    result = evaluate(policy, {"bootstrap": "true", "whitelist": "false"})
    assert result.ready is False
    assert [b.gate for b in result.blocked] == ["whitelist"]
    assert "whitelist" in result.blocked[0].reason
    assert "false" in result.blocked[0].reason


def test_missing_observation_blocks() -> None:
    policy = ReadinessPolicy({"bootstrap": "true"})
    result = evaluate(policy, {})
    assert result.ready is False
    assert result.blocked[0].gate == "bootstrap"
    assert result.blocked[0].observed is None


def test_all_failing_gates_are_reported_not_short_circuited() -> None:
    policy = ReadinessPolicy({"a": "true", "b": "true", "c": "true"})
    result = evaluate(policy, {"a": "false", "b": "true", "c": "false"})
    assert [b.gate for b in result.blocked] == ["a", "c"]


def test_gate_order_is_preserved() -> None:
    policy = ReadinessPolicy({"first": "1", "second": "2", "third": "3"})
    assert policy.gates == ("first", "second", "third")


def test_non_default_expected_value() -> None:
    # The expected value is per-gate policy, not hardcoded to "true".
    policy = ReadinessPolicy({"mode": "production"})
    assert evaluate(policy, {"mode": "production"}).ready is True
    assert evaluate(policy, {"mode": "candidate"}).ready is False


def test_empty_policy_is_vacuously_ready() -> None:
    assert evaluate(ReadinessPolicy({}), {}).ready is True


def test_policy_rejects_blank_gate_name() -> None:
    with pytest.raises(ValueError):
        ReadinessPolicy({"": "true"})


def test_cli_confirms_when_env_gates_true(monkeypatch) -> None:
    from mssp_pipeline.readiness import main

    monkeypatch.setenv("MSSP_READINESS_BOOTSTRAP", "true")
    monkeypatch.setenv("MSSP_READINESS_WHITELIST", "true")
    assert main(["bootstrap", "whitelist"]) == 0


def test_cli_blocks_when_a_gate_is_not_true(monkeypatch) -> None:
    from mssp_pipeline.readiness import main

    monkeypatch.setenv("MSSP_READINESS_BOOTSTRAP", "true")
    monkeypatch.setenv("MSSP_READINESS_WHITELIST", "false")
    assert main(["bootstrap", "whitelist"]) == 1


def test_cli_usage_error_without_gates() -> None:
    from mssp_pipeline.readiness import main

    assert main([]) == 64
