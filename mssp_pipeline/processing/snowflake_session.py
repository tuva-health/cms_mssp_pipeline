"""The Snowflake session seam: the one place a connection is opened from a
:class:`~mssp_pipeline.processing.config_defs.SnowflakeConfig`.

Shared by the Snowflake exporter and by any other Snowflake reader (for
example the sequencer's information_schema output source), so there is a
single credential path: key-pair auth from ``rsa_key_path``, unlocked with
``rsa_key_passphrase`` or the OS keyring. Imports the connector at module
level; callers that must stay free of the ``snowflake`` extra import this
module lazily (as ``build_exporter`` does for the exporter).
"""

from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from snowflake import connector

from mssp_pipeline.processing.config_defs import SnowflakeConfig


def snowflake_connection(sf_config: SnowflakeConfig, *, private_key=None):
    """Open a Snowflake session for ``sf_config``.

    ``private_key`` may be passed pre-loaded (the exporter caches it across
    its many short sessions); otherwise it is loaded from the config's key
    path via :func:`load_private_key`.
    """
    return connector.connect(
        user=sf_config.username,
        account=sf_config.account,
        schema=sf_config.schema,
        database=sf_config.database,
        warehouse=sf_config.warehouse,
        private_key=private_key if private_key is not None else load_private_key(sf_config),
        role=sf_config.role,
    )


def load_private_key(sf_config: SnowflakeConfig):
    """Load the RSA private key at ``sf_config.rsa_key_path``.

    An unencrypted PEM loads directly. An encrypted PEM is unlocked with
    ``sf_config.rsa_key_passphrase`` when set, else with the passphrase stored
    in the OS keyring under service ``SNOWFLAKE`` / the config's username.
    """
    with open(sf_config.rsa_key_path, "rb") as f:
        key_data = f.read()
    try:
        return serialization.load_pem_private_key(key_data, password=None)
    except TypeError as e:
        if "password" not in str(e).lower():
            raise
        passphrase = sf_config.rsa_key_passphrase or _keyring_passphrase(sf_config.username)
        return serialization.load_pem_private_key(
            key_data, password=passphrase.rstrip().encode()
        )


def _keyring_passphrase(username: str) -> str:
    import keyring

    return keyring.get_password("SNOWFLAKE", username)
