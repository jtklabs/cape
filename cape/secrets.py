"""Fetch UXI API credentials from AWS Secrets Manager."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Credentials:
    client_id: str
    client_secret: str


# Key names tried (in order) when none are configured explicitly. Supports a
# shared secret holding many app credentials alongside the UXI ones.
_DEFAULT_ID_KEYS = ("uxi_client_id", "client_id")
_DEFAULT_SECRET_KEYS = ("uxi_client_secret", "client_secret")


def fetch_credentials(
    secret_id: str,
    region_name: str | None = None,
    client_id_key: str | None = None,
    client_secret_key: str | None = None,
) -> Credentials:
    """Load UXI credentials from a Secrets Manager secret (JSON key/value map).

    The secret may be shared with other applications; only the two UXI keys
    are read. Key names default to uxi_client_id/uxi_client_secret (falling
    back to client_id/client_secret) and can be overridden explicitly.

    secret_id:   name or ARN of the secret.
    region_name: AWS region of the secret (falls back to the usual
                 boto3 resolution: AWS_REGION / profile / instance metadata).
    """
    import boto3  # deferred so `--help`/`list` work without the AWS SDK
    from botocore.exceptions import BotoCoreError, ClientError

    client = boto3.client("secretsmanager", region_name=region_name)
    try:
        resp = client.get_secret_value(SecretId=secret_id)
    except (BotoCoreError, ClientError) as e:
        raise RuntimeError(f"Could not read secret '{secret_id}': {e}") from e

    raw = resp.get("SecretString")
    if raw is None:  # binary secret — decode it
        raw = resp["SecretBinary"].decode("utf-8")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Secret '{secret_id}' is not valid JSON; expected a key/value "
            "map containing the UXI client id and secret"
        ) from e

    id_keys = (client_id_key,) if client_id_key else _DEFAULT_ID_KEYS
    secret_keys = (client_secret_key,) if client_secret_key else _DEFAULT_SECRET_KEYS
    client_id = next((data[k] for k in id_keys if data.get(k)), None)
    client_secret = next((data[k] for k in secret_keys if data.get(k)), None)
    if not client_id or not client_secret:
        raise RuntimeError(
            f"Secret '{secret_id}' has no UXI credentials under keys "
            f"{list(id_keys)} / {list(secret_keys)}. "
            f"Keys present in the secret: {sorted(data)}"
        )

    log.info("Loaded UXI credentials from secret '%s'", secret_id)
    return Credentials(client_id=client_id, client_secret=client_secret)
