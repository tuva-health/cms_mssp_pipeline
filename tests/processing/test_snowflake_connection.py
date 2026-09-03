"""The Snowflake session seam (``processing/snowflake_session.py``): one
place opens a connection from a SnowflakeConfig, shared by the exporter and by
any other Snowflake reader (for example the sequencer's information_schema
output source)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip(
    "snowflake.connector",
    reason="requires the snowflake extra; production imports it lazily in build_exporter",
)

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from mssp_pipeline.processing import snowflake_session  # noqa: E402


def _config(**overrides) -> SimpleNamespace:
    base = dict(
        username="user",
        account="acct",
        schema="RAW_DATA",
        database="DB",
        warehouse="WH",
        role="ROLE",
        rsa_key_path="/tmp/key.p8",
        rsa_key_passphrase="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _write_pem(tmp_path, encryption):
    """Write a fresh RSA key as PKCS8 PEM; return (path, key)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path / "key.p8"
    path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption)
    )
    return path, key


def test_snowflake_connection_opens_a_session_from_the_config() -> None:
    fake_conn = MagicMock()
    with patch.object(snowflake_session, "load_private_key", return_value=b"key") as load, \
         patch.object(snowflake_session.connector, "connect", return_value=fake_conn) as connect:
        conn = snowflake_session.snowflake_connection(_config())

    assert conn is fake_conn
    load.assert_called_once()
    connect.assert_called_once_with(
        user="user",
        account="acct",
        schema="RAW_DATA",
        database="DB",
        warehouse="WH",
        private_key=b"key",
        role="ROLE",
    )


def test_load_private_key_reads_an_unencrypted_pem(tmp_path) -> None:
    path, key = _write_pem(tmp_path, serialization.NoEncryption())

    loaded = snowflake_session.load_private_key(_config(rsa_key_path=str(path)))

    assert loaded.public_key().public_numbers() == key.public_key().public_numbers()


def test_load_private_key_uses_the_configured_passphrase_for_an_encrypted_pem(tmp_path) -> None:
    path, key = _write_pem(tmp_path, serialization.BestAvailableEncryption(b"secret"))

    loaded = snowflake_session.load_private_key(
        _config(rsa_key_path=str(path), rsa_key_passphrase="secret\n")
    )

    assert loaded.public_key().public_numbers() == key.public_key().public_numbers()
