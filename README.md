# cape — Aruba UXI (Cape Networks) sensor data collector

Authenticates to the HPE Aruba UXI API (OAuth2 client-credentials via GreenLake
SSO) and collects inventory, configuration, and test/metric data. Credentials
are pulled from AWS Secrets Manager so nothing sensitive lives on disk.

## Setup

```bash
pip install -r requirements.txt
```

### 1. Create API credentials (one-time)
In your **HPE GreenLake Workspace** → **Manage Workspace** → **Personal API
clients** → **Create Personal API Client**. Pick the **User Experience Insight**
service for your region. Save the **Client ID** and **Client Secret**.

### 2. Store them in AWS Secrets Manager
The secret is a JSON key/value map and **may be shared with other
applications** — only the two UXI keys are read. Default key names are
`uxi_client_id` / `uxi_client_secret` (with `client_id` / `client_secret`
as fallback):

```json
{
  "some_other_app_key": "...",
  "uxi_client_id": "YOUR_ID",
  "uxi_client_secret": "YOUR_SECRET"
}
```

Using different key names? Point at them with `--client-id-key` /
`--client-secret-key` (env: `UXI_CLIENT_ID_KEY` / `UXI_CLIENT_SECRET_KEY`).

The IAM role/user running this needs `secretsmanager:GetSecretValue` on that secret.

### 3. Configure
```bash
export UXI_SECRET_ID="uxi/api-credentials"   # secret name or ARN
export AWS_REGION="us-east-1"                 # region of the secret
export UXI_REGION="us-west"                   # UXI API region: us-west | eu-central
```

## Usage

```bash
# See what can be collected
python -m cape list

# Just the sensor inventory (printed to stdout)
python -m cape inventory

# A single resource
python -m cape collect --resource sensors

# Whole categories, written to ./out
python -m cape inventory --out ./out
python -m cape config    --out ./out --format csv

# Service tests + which groups they apply to
python -m cape tests --out ./out

# Everything (all REST resources)
python -m cape all --out ./out

# Live fleet health + active issues (pull-only)
python -m cape status --out ./out

# Checkmk piggyback output (one block per sensor) — see checkmk/README-checkmk.md
python -m cape checkmk --host-prefix uxi-
```

Each resource is written to `<out>/<resource>.json` (or `.csv`). Without `--out`,
a compact summary table prints to stdout.

## Architecture

| File           | Responsibility                                              |
|----------------|-------------------------------------------------------------|
| `config.py`    | Endpoints + the **resource registry** (add/edit endpoints)  |
| `secrets.py`   | Fetch credentials from AWS Secrets Manager                  |
| `client.py`    | OAuth token + token refresh, rate limiting, pagination     |
| `collector.py` | Generic collect loop + JSON/CSV writers                    |
| `__main__.py`  | CLI: subcommands, resource selection, output               |

**Adding or fixing an endpoint** is a one-line edit in `config.py`'s `RESOURCES`
list — the CLI, pagination, and writers pick it up automatically.

## Health & issues — `status` (pull-only, no external feeds)
`python -m cape status` sweeps `GET /sensors/{id}/status` for every sensor
(rate-limited, ~13s for 50 sensors) and produces:

- **`sensor-status`** — one row per sensor: `isOnline`, `isTesting`, active
  issue count. Catches offline/unreachable sensors.
- **`issues`** — every active issue, flattened: code
  (e.g. `NO_CONNECTIVITY`, `DHCP_NO_ROUTERS`, `ETHERNET_8021X_AUTHENTICATION_FAILED`,
  `HIGH_DHCP_RESPONSE_TIME`), severity, confirmation status, timestamp, and
  the sensor/network/group/service-test context.

Run it on a schedule (cron) to build your own health history — each sweep is a
point-in-time snapshot. Note: this endpoint is verified working but is not in
the published API reference or official SDK, so treat it as subject to change.

## Historical test-result metrics — not pullable (as of 2026-07)
The REST API has **no pull endpoint for raw time-series test results**
(latency/throughput per test over time). Verified live (all candidate paths
404) and against the official [pyhpeuxi SDK](https://github.com/aruba/pyhpeuxi),
which wraps the exact same endpoint set. HPE only offers that data via push
integrations (webhooks / S3 / BigQuery / Splunk — **not permitted in this
environment**) or through the **Aruba Central integration** (Central's own API
is pull-based — an option if this org runs Central). If HPE ships a results
pull API, add a row to `RESOURCES` in `config.py`.

## Pagination note
The API returns `{"items", "count", "next"}`; the next-page token must be sent
back as the `next` query parameter (`cursor` is silently ignored — verified
live). `client.py` handles this and guards against non-advancing tokens.
