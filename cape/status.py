"""Pull live sensor health + active issues from the UXI API.

Uses GET /sensors/{id}/status — pull-only, no external feeds required.
(Endpoint verified live; it is not documented in the API reference or the
official pyhpeuxi SDK, so treat it as subject to change.)

Each status response contains:
    isOnline / isTesting   — current sensor health
    issues[]               — active issues with code, severity, status,
                             timestamp, and full network/group/test context

This gives fleet health and alerting data. It does NOT expose historical
time-series test metrics — as of 2026-07 those have no pull API; they are
only available via push integrations (not permitted here) or the Aruba
Central integration.
"""
from __future__ import annotations

import logging

from .client import UXIClient

log = logging.getLogger(__name__)


def collect_statuses(
    client: UXIClient, sensors: list[dict] | None = None
) -> tuple[list[dict], list[dict]]:
    """Sweep every sensor's /status. Returns (statuses, active_issues).

    statuses: one row per sensor — identity + isOnline/isTesting/issue count.
    active_issues: flattened issue records (issue fields + sensor identity).
    """
    if sensors is None:
        sensors = client.get_all("/sensors")

    statuses: list[dict] = []
    issues: list[dict] = []
    for s in sensors:
        ident = {
            "sensorId": s.get("id"),
            "sensorName": s.get("name"),
            "sensorSerial": s.get("serial"),
            "groupName": s.get("groupName"),
        }
        try:
            st = client.get_one(f"/sensors/{s['id']}/status")
        except Exception as e:  # noqa: BLE001 — keep sweeping the rest of the fleet
            log.error("status failed for %s (%s): %s", s.get("name"), s.get("id"), e)
            statuses.append({**ident, "error": str(e)})
            continue

        sensor_issues = st.get("issues") or []
        statuses.append({
            **ident,
            "isOnline": st.get("isOnline"),
            "isTesting": st.get("isTesting"),
            "activeIssueCount": len(sensor_issues),
        })
        for issue in sensor_issues:
            flat = {k: v for k, v in issue.items() if k != "context"}
            flat.update(issue.get("context") or {})
            # context carries its own sensor fields; ident fills any gaps
            for k, v in ident.items():
                flat.setdefault(k, v)
            issues.append(flat)

    online = sum(1 for x in statuses if x.get("isOnline"))
    log.info("Status sweep: %d sensors, %d online, %d active issue(s)",
             len(statuses), online, len(issues))
    return statuses, issues
