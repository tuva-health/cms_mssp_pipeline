"""Distributed lease backends for the sequencer.

The engine (`sequencer.py`) drives an injected :class:`LeaseBackend`; `lease.py`
ships the conditional-lease *semantics* and an in-memory reference store, and
defers the real distributed backend to an overlay. This module is that backend,
built generically (client-neutral) alongside ``BotoEcsClient``:
:class:`DynamoDbLeaseBackend` implements the same three predicates against a
DynamoDB table via conditional writes.

Each predicate maps to a DynamoDB ``ConditionExpression`` that is logically
identical to the in-memory store's Python check:

* **acquire** -- put-if-absent OR the recorded lease has expired::

      attribute_not_exists(#name) OR #expires_at <= :now

* **refresh** -- (free OR expired OR you own the live lease) AND, when a fencing
  token is supplied, the new token strictly advances the recorded one::

      (attribute_not_exists(#name) OR #expires_at <= :now OR #owner = :owner)
      AND
      (attribute_not_exists(#name) OR attribute_not_exists(#fence)
       OR #fence < :token)

  The fencing clause is dropped entirely when no token is supplied, mirroring
  ``InMemoryLeaseStore.refresh`` (which only checks fencing when a token is
  passed). Expiry is decided logically by ``#expires_at <= :now`` -- DynamoDB's
  native TTL is best-effort sweeping only and is never relied on for
  correctness.

* **release** -- you still own the record::

      attribute_exists(#name) AND #owner = :owner

A rejected conditional write (``ConditionalCheckFailedException``) is translated
to the same error types the engine already catches: :class:`LeaseUnavailable`
for acquire/refresh contention and takeover races, :class:`LeaseOwnershipLost`
for a release of a lease the caller no longer owns.
"""

from __future__ import annotations

from mssp_pipeline.lease import (
    Lease,
    LeaseOwnershipLost,
    LeaseUnavailable,
)

# ``owner`` and ``name`` are DynamoDB reserved words, so every attribute is
# aliased through ExpressionAttributeNames. DynamoDB rejects any alias that is
# declared but unused in the expression, so each operation passes only the
# subset of aliases its own ConditionExpression references.
_NAME_ALIASES = {
    "#name": "lease_name",
    "#owner": "owner",
    "#expires_at": "expires_at",
    "#fence": "fencing_token",
}


def _names(*aliases: str) -> dict:
    return {alias: _NAME_ALIASES[alias] for alias in aliases}


class DynamoDbLeaseBackend:
    """A :class:`~mssp_pipeline.sequencer.LeaseBackend` backed by a DynamoDB
    lock table, mirroring ``InMemoryLeaseStore``'s predicates via conditional
    writes.

    The table has a ``lease_name`` string hash key; one item per lease holds
    ``owner``, ``acquired_at``, ``expires_at`` and (once stamped) an integer
    ``fencing_token``. ``expires_at`` doubles as the table's TTL attribute.

    boto3 is imported lazily in ``__init__`` (guarded, like ``BotoEcsClient``)
    so importing this module never pulls boto3 into a unit path that does not
    construct the backend. ``endpoint_url`` points the client at a local/moto
    DynamoDB in tests.
    """

    def __init__(
        self, table_name: str, region: str, *, endpoint_url: str | None = None
    ) -> None:
        import boto3

        self._table_name = table_name
        self._client = boto3.client(
            "dynamodb", region_name=region, endpoint_url=endpoint_url
        )

    # -- helpers ---------------------------------------------------------

    @property
    def _conditional_check_failed(self):
        return self._client.exceptions.ConditionalCheckFailedException

    def _put(
        self,
        name: str,
        *,
        owner: str,
        now: int,
        ttl: int,
        fencing_token: int | None,
        condition: str,
        names: dict,
        values: dict,
    ) -> Lease:
        expires_at = now + ttl
        item = {
            "lease_name": {"S": name},
            "owner": {"S": owner},
            "acquired_at": {"N": str(now)},
            "expires_at": {"N": str(expires_at)},
        }
        if fencing_token is not None:
            item["fencing_token"] = {"N": str(fencing_token)}
        self._client.put_item(
            TableName=self._table_name,
            Item=item,
            ConditionExpression=condition,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
        return Lease(
            name=name,
            owner=owner,
            acquired_at=now,
            expires_at=expires_at,
            fencing_token=fencing_token,
        )

    # -- LeaseBackend protocol ------------------------------------------

    def acquire(self, name: str, *, owner: str, now: int, ttl: int) -> Lease:
        try:
            return self._put(
                name,
                owner=owner,
                now=now,
                ttl=ttl,
                fencing_token=None,
                condition="attribute_not_exists(#name) OR #expires_at <= :now",
                names=_names("#name", "#expires_at"),
                values={":now": {"N": str(now)}},
            )
        except self._conditional_check_failed:
            raise LeaseUnavailable(
                f"lease {name!r} is held by another owner and has not expired"
            ) from None

    def refresh(
        self,
        name: str,
        *,
        owner: str,
        now: int,
        ttl: int,
        fencing_token: int | None = None,
    ) -> Lease:
        # Free OR expired OR you own the still-live lease.
        condition = (
            "(attribute_not_exists(#name) OR #expires_at <= :now "
            "OR #owner = :owner)"
        )
        values: dict = {":now": {"N": str(now)}, ":owner": {"S": owner}}
        names = _names("#name", "#expires_at", "#owner")
        # Only enforce fencing when a token is supplied (matches the in-memory
        # store): the new token must strictly advance a recorded one.
        if fencing_token is not None:
            condition += (
                " AND (attribute_not_exists(#name) "
                "OR attribute_not_exists(#fence) OR #fence < :token)"
            )
            values[":token"] = {"N": str(fencing_token)}
            names = _names("#name", "#expires_at", "#owner", "#fence")
        try:
            return self._put(
                name,
                owner=owner,
                now=now,
                ttl=ttl,
                fencing_token=fencing_token,
                condition=condition,
                names=names,
                values=values,
            )
        except self._conditional_check_failed:
            raise LeaseUnavailable(
                f"lease {name!r} refresh rejected: held by another owner "
                f"or fencing token {fencing_token} does not advance"
            ) from None

    def release(self, name: str, *, owner: str) -> None:
        try:
            self._client.delete_item(
                TableName=self._table_name,
                Key={"lease_name": {"S": name}},
                ConditionExpression="attribute_exists(#name) AND #owner = :owner",
                ExpressionAttributeNames=_names("#name", "#owner"),
                ExpressionAttributeValues={":owner": {"S": owner}},
            )
        except self._conditional_check_failed:
            raise LeaseOwnershipLost(
                f"lease {name!r} is not owned by {owner!r}"
            ) from None
