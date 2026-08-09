"""Static configuration: regional endpoints and the resource registry.

The Aruba UXI (formerly Cape Networks) API is reached in two steps:
  1. OAuth2 client-credentials token exchange against HPE GreenLake SSO.
  2. REST calls against the regional UXI API base.

Credentials live in AWS Secrets Manager as a JSON blob:
    {"client_id": "...", "client_secret": "..."}
"""
from __future__ import annotations

from dataclasses import dataclass, field

# HPE GreenLake SSO token endpoint (same for all regions).
TOKEN_URL = "https://sso.common.cloud.hpe.com/as/token.oauth2"

# Regional API bases. Pick the one matching the service region you chose when
# creating the Personal API Client in GreenLake.
API_BASES = {
    "us-west": "https://api.capenetworks.com/networking-uxi/v1alpha1",
    "eu-central": "https://api.eu.capenetworks.com/networking-uxi/v1alpha1",
}
DEFAULT_REGION = "us-west"

# The dashboard's own backend. Undocumented, but it accepts the SAME OAuth
# bearer token as the public API (verified live) and is the only pull-based
# source of test-result time series — the public v1alpha1 API exposes none.
# See cape/metrics.py.
DASHBOARD_BASES = {
    "us-west": "https://dashboard.capenetworks.com/api",
    "eu-central": "https://dashboard.eu.capenetworks.com/api",
}

# Stay below the documented 5 requests/sec per-customer limit.
MIN_REQUEST_INTERVAL = 0.25  # seconds between requests


@dataclass(frozen=True)
class Resource:
    """A single collectable API resource.

    name:     CLI/file identifier.
    path:     path appended to the API base.
    category: groups resources so you can collect a whole category at once.
    note:     'confirmed' endpoints are documented; 'verify' are best-guess
              paths you should confirm against your tenant's API reference.
    """
    name: str
    path: str
    category: str
    note: str = "confirmed"
    params: dict = field(default_factory=dict)


# Registry — the single source of truth for what can be collected.
# Categories: inventory | config
#
# Every path below is verified live against the v1alpha1 API (2026-07).
# v1alpha1 is the onboarding/config surface only: time-series metrics and
# test RESULTS are NOT exposed here — UXI delivers those via webhooks
# (https://help.capenetworks.com -> "Getting Started With Webhooks") or the
# Aruba Central integration. If HPE ships a results API later, add a row here
# and everything (CLI, pagination, writers) picks it up automatically.
RESOURCES: list[Resource] = [
    # --- inventory: the physical/logical fleet ---
    Resource("sensors", "/sensors", "inventory"),
    Resource("agents", "/agents", "inventory"),
    Resource("groups", "/groups", "inventory"),

    # --- config: how the fleet is set up ---
    Resource("service-tests", "/service-tests", "config"),
    Resource("wired-networks", "/wired-networks", "config"),
    Resource("wireless-networks", "/wireless-networks", "config"),
    Resource("network-group-assignments", "/network-group-assignments", "config"),
    Resource("sensor-group-assignments", "/sensor-group-assignments", "config"),
    Resource("agent-group-assignments", "/agent-group-assignments", "config"),
    Resource("service-test-group-assignments", "/service-test-group-assignments", "config"),
]


def resources_by_category(category: str) -> list[Resource]:
    return [r for r in RESOURCES if r.category == category]


def get_resource(name: str) -> Resource | None:
    return next((r for r in RESOURCES if r.name == name), None)
