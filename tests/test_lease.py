"""Conditional lease with ownership + expiry (synthetic).

Reproduces the conditional-write semantics of the operational DynamoDB lease
without any backend: acquire iff free-or-expired, refresh iff owner-or-expired
(with optional monotonic fencing), release iff still owner.
"""

from __future__ import annotations

import pytest

from mssp_pipeline.lease import (
    InMemoryLeaseStore,
    LeaseOwnershipLost,
    LeaseUnavailable,
)


def test_acquire_on_free_name_succeeds() -> None:
    store = InMemoryLeaseStore()
    lease = store.acquire("job", owner="exec-1", now=0, ttl=100)
    assert lease.name == "job"
    assert lease.owner == "exec-1"
    assert lease.expires_at == 100


def test_acquire_rejects_a_live_lease_held_by_another() -> None:
    store = InMemoryLeaseStore()
    store.acquire("job", owner="exec-1", now=0, ttl=100)
    with pytest.raises(LeaseUnavailable):
        store.acquire("job", owner="exec-2", now=50, ttl=100)


def test_acquire_succeeds_after_expiry() -> None:
    store = InMemoryLeaseStore()
    store.acquire("job", owner="exec-1", now=0, ttl=100)
    lease = store.acquire("job", owner="exec-2", now=101, ttl=100)
    assert lease.owner == "exec-2"
    assert lease.expires_at == 201


def test_refresh_by_owner_extends_expiry() -> None:
    store = InMemoryLeaseStore()
    store.acquire("job", owner="exec-1", now=0, ttl=100)
    lease = store.refresh("job", owner="exec-1", now=50, ttl=100)
    assert lease.expires_at == 150


def test_refresh_by_non_owner_on_live_lease_is_rejected() -> None:
    store = InMemoryLeaseStore()
    store.acquire("job", owner="exec-1", now=0, ttl=100)
    with pytest.raises(LeaseUnavailable):
        store.refresh("job", owner="exec-2", now=50, ttl=100)


def test_refresh_takes_over_an_expired_lease() -> None:
    store = InMemoryLeaseStore()
    store.acquire("job", owner="exec-1", now=0, ttl=100)
    lease = store.refresh("job", owner="exec-2", now=200, ttl=100)
    assert lease.owner == "exec-2"


def test_release_by_owner_frees_the_lease() -> None:
    store = InMemoryLeaseStore()
    store.acquire("job", owner="exec-1", now=0, ttl=100)
    store.release("job", owner="exec-1")
    # Now free: a different owner can acquire immediately.
    assert store.acquire("job", owner="exec-2", now=1, ttl=100).owner == "exec-2"


def test_release_by_non_owner_is_rejected() -> None:
    store = InMemoryLeaseStore()
    store.acquire("job", owner="exec-1", now=0, ttl=100)
    with pytest.raises(LeaseOwnershipLost):
        store.release("job", owner="exec-2")


def test_release_of_absent_lease_is_rejected() -> None:
    store = InMemoryLeaseStore()
    with pytest.raises(LeaseOwnershipLost):
        store.release("job", owner="exec-1")


def test_fencing_token_rejects_a_stale_refresh() -> None:
    store = InMemoryLeaseStore()
    store.acquire("job", owner="exec-1", now=0, ttl=100)
    store.refresh("job", owner="exec-1", now=10, ttl=100, fencing_token=5)
    # A lower/equal token is stale and must be rejected even by the owner.
    with pytest.raises(LeaseUnavailable):
        store.refresh("job", owner="exec-1", now=20, ttl=100, fencing_token=5)
    # A higher token advances.
    lease = store.refresh("job", owner="exec-1", now=30, ttl=100, fencing_token=6)
    assert lease.fencing_token == 6
