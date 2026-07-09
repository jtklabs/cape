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


def fetch_credentials(secret_id: str, region_name: str | None = None) -> Credentials:
    """Load {"client_id", "client_secret"} from a Secrets Manager secret.

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
            f"Secret '{secret_id}' is not valid JSON; expected "
            '{"client_id": "...", "client_secret": "..."}'
        ) from e

    try:
        creds = Credentials(client_id=data["client_id"], client_secret=data["client_secret"])
    except KeyError as e:
        raise RuntimeError(
            f"Secret '{secret_id}' is missing required key {e}; "
            'expected {"client_id": "...", "client_secret": "..."}'
        ) from e

    log.info("Loaded UXI credentials from secret '%s'", secret_id)
    return creds
