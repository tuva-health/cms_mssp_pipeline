"""Conditional lease with ownership and expiry.

A lease is a single named record with an owner, an expiry, and an optional
monotonic fencing token. Three conditional operations mirror the operational
DynamoDB lock, expressed as pure predicates so they can be reasoned about and
tested without a backend:

* **acquire** succeeds iff the name is free or the existing lease has expired.
* **refresh** succeeds iff the name is free, you already own it, or it has
  expired -- and, when a fencing token is supplied, iff the new token strictly
  advances the recorded one (a stale redrive cannot re-take the lease).
* **release** succeeds iff you still own the lease.

The concrete backend (DynamoDB conditional writes, its table name, the lease
name, TTL duration, and the owner-id source) is A-class policy. This module
ships the semantics and an in-memory reference store; overlays implement the
same predicates against their backend.
"""

from __future__ import annotations

from dataclasses import dataclass


class LeaseError(RuntimeError):
    """Base class for lease conflicts."""


class LeaseUnavailable(LeaseError):
    """Acquire/refresh rejected: a live lease is held by someone else, or a
    fencing token did not advance."""


class LeaseOwnershipLost(LeaseError):
    """Release rejected: the caller no longer owns the lease."""


@dataclass(frozen=True)
class Lease:
    name: str
    owner: str
    acquired_at: int
    expires_at: int
    fencing_token: int | None = None

    def is_expired(self, now: int) -> bool:
        return self.expires_at <= now


class InMemoryLeaseStore:
    """Reference lease store implementing the conditional semantics in memory."""

    def __init__(self) -> None:
        self._leases: dict[str, Lease] = {}

    def get(self, name: str) -> Lease | None:
        return self._leases.get(name)

    def acquire(self, name: str, *, owner: str, now: int, ttl: int) -> Lease:
        current = self._leases.get(name)
        if current is not None and not current.is_expired(now):
            raise LeaseUnavailable(
                f"lease {name!r} is held by {current.owner!r} until {current.expires_at}"
            )
        return self._write(name, owner=owner, now=now, ttl=ttl, fencing_token=None)

    def refresh(
        self,
        name: str,
        *,
        owner: str,
        now: int,
        ttl: int,
        fencing_token: int | None = None,
    ) -> Lease:
        current = self._leases.get(name)
        if current is not None and not current.is_expired(now):
            if current.owner != owner:
                raise LeaseUnavailable(
                    f"lease {name!r} is held by {current.owner!r}, not {owner!r}"
                )
        if fencing_token is not None and current is not None:
            recorded = current.fencing_token
            if recorded is not None and fencing_token <= recorded:
                raise LeaseUnavailable(
                    f"fencing token {fencing_token} does not advance {recorded}"
                )
        return self._write(
            name, owner=owner, now=now, ttl=ttl, fencing_token=fencing_token
        )

    def release(self, name: str, *, owner: str) -> None:
        current = self._leases.get(name)
        if current is None or current.owner != owner:
            raise LeaseOwnershipLost(
                f"lease {name!r} is not owned by {owner!r}"
            )
        del self._leases[name]

    def _write(
        self,
        name: str,
        *,
        owner: str,
        now: int,
        ttl: int,
        fencing_token: int | None,
    ) -> Lease:
        lease = Lease(
            name=name,
            owner=owner,
            acquired_at=now,
            expires_at=now + ttl,
            fencing_token=fencing_token,
        )
        self._leases[name] = lease
        return lease
