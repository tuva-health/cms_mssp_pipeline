"""DynamoDbLeaseBackend adapter contract.

Exercises the real conditional-write adapter against an in-process fake
DynamoDB (moto) -- no live AWS, no credentials. Mirrors the predicates and
assertions of ``tests/test_lease.py`` (the in-memory reference store) so the
adapter is a drop-in for the sequencer's injected ``LeaseBackend``.
"""

from __future__ import annotations

import pytest

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

from mssp_pipeline.lease import LeaseOwnershipLost, LeaseUnavailable
from mssp_pipeline.lease_backends import DynamoDbLeaseBackend

TABLE = "mssp-lease-locks"
REGION = "us-east-1"


def _create_table() -> None:
    client = boto3.client("dynamodb", region_name=REGION)
    client.create_table(
        TableName=TABLE,
        AttributeDefinitions=[{"AttributeName": "lease_name", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "lease_name", "KeyType": "HASH"}],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture
def backend():
    with moto.mock_aws():
        _create_table()
        yield DynamoDbLeaseBackend(TABLE, REGION)


def test_acquire_on_free_name_succeeds(backend) -> None:
    lease = backend.acquire("job", owner="exec-1", now=0, ttl=100)
    assert lease.name == "job"
    assert lease.owner == "exec-1"
    assert lease.expires_at == 100


def test_acquire_rejects_a_live_lease_held_by_another(backend) -> None:
    backend.acquire("job", owner="exec-1", now=0, ttl=100)
    with pytest.raises(LeaseUnavailable):
        backend.acquire("job", owner="exec-2", now=50, ttl=100)


def test_acquire_succeeds_after_expiry(backend) -> None:
    backend.acquire("job", owner="exec-1", now=0, ttl=100)
    lease = backend.acquire("job", owner="exec-2", now=101, ttl=100)
    assert lease.owner == "exec-2"
    assert lease.expires_at == 201


def test_refresh_by_owner_extends_expiry(backend) -> None:
    backend.acquire("job", owner="exec-1", now=0, ttl=100)
    lease = backend.refresh("job", owner="exec-1", now=50, ttl=100)
    assert lease.expires_at == 150


def test_refresh_by_non_owner_on_live_lease_is_rejected(backend) -> None:
    backend.acquire("job", owner="exec-1", now=0, ttl=100)
    with pytest.raises(LeaseUnavailable):
        backend.refresh("job", owner="exec-2", now=50, ttl=100)


def test_refresh_takes_over_an_expired_lease(backend) -> None:
    backend.acquire("job", owner="exec-1", now=0, ttl=100)
    lease = backend.refresh("job", owner="exec-2", now=200, ttl=100)
    assert lease.owner == "exec-2"


def test_release_by_owner_frees_the_lease(backend) -> None:
    backend.acquire("job", owner="exec-1", now=0, ttl=100)
    backend.release("job", owner="exec-1")
    # Now free: a different owner can acquire immediately.
    assert backend.acquire("job", owner="exec-2", now=1, ttl=100).owner == "exec-2"


def test_release_by_non_owner_is_rejected(backend) -> None:
    backend.acquire("job", owner="exec-1", now=0, ttl=100)
    with pytest.raises(LeaseOwnershipLost):
        backend.release("job", owner="exec-2")


def test_release_of_absent_lease_is_rejected(backend) -> None:
    with pytest.raises(LeaseOwnershipLost):
        backend.release("job", owner="exec-1")


def test_fencing_token_rejects_a_stale_refresh(backend) -> None:
    backend.acquire("job", owner="exec-1", now=0, ttl=100)
    backend.refresh("job", owner="exec-1", now=10, ttl=100, fencing_token=5)
    # A lower/equal token is stale and must be rejected even by the owner.
    with pytest.raises(LeaseUnavailable):
        backend.refresh("job", owner="exec-1", now=20, ttl=100, fencing_token=5)
    # A higher token advances.
    lease = backend.refresh("job", owner="exec-1", now=30, ttl=100, fencing_token=6)
    assert lease.fencing_token == 6


def test_takeover_after_expiry_requires_a_newer_fencing_token(backend) -> None:
    # A stale run cannot out-fence a fresher one even once the lease expires:
    # the fencing check advances regardless of the ownership/expiry clause.
    backend.acquire("job", owner="exec-1", now=0, ttl=100)
    backend.refresh("job", owner="exec-1", now=10, ttl=100, fencing_token=10)
    with pytest.raises(LeaseUnavailable):
        backend.refresh("job", owner="exec-2", now=200, ttl=100, fencing_token=9)
    # The newer token wins the takeover.
    lease = backend.refresh("job", owner="exec-2", now=200, ttl=100, fencing_token=11)
    assert lease.owner == "exec-2"
    assert lease.fencing_token == 11
