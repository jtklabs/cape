"""Emit Checkmk piggyback output for UXI sensors.

Run via `python -m cape checkmk` (typically as a Checkmk agent plugin on the
Checkmk server, see checkmk/README-checkmk.md). Output structure:

    <<<uxi_fleet:sep(0)>>>          # on the host running the plugin
    {"total": 51, "online": 36, ...}
    <<<<uxi-louisville>>>>          # one piggyback block per sensor
    <<<uxi_sensor:sep(0)>>>
    {identity + status json}
    <<<uxi_issues:sep(0)>>>
    {issue json}                    # one line per active issue
    <<<<>>>>

Piggyback host names are derived from the sensor name (sanitized) or serial
(--host-field), optionally prefixed (--host-prefix). Colliding names get the
serial appended. Use Checkmk's Dynamic host management (piggyback connector)
to auto-create the hosts, or create them manually with matching names.
"""
from __future__ import annotations

import json
import re

from .client import UXIClient
from .status import collect_statuses

_INVALID = re.compile(r"[^A-Za-z0-9._-]+")

# Keep only identity/context fields that are useful on the sensor host;
# status fields are merged in separately.
_SENSOR_FIELDS = (
    "id", "serial", "name", "groupName", "groupPath", "modelNumber",
    "wifiMacAddress", "ethernetMacAddress", "addressNote", "notes",
    "longitude", "latitude", "pcapMode",
)


def hostname_for(sensor: dict, field: str = "name", prefix: str = "") -> str:
    raw = (sensor.get(field) or sensor.get("serial") or sensor.get("id") or "unknown")
    clean = _INVALID.sub("-", str(raw))
    # Collapse runs of separators so "1550 - 2nd Floor" becomes
    # "1550-2nd-Floor" rather than "1550---2nd-Floor". Host renames lose
    # historical data in Checkmk, so names must be stable and tidy up front.
    clean = re.sub(r"-{2,}", "-", clean).strip("-.")
    return f"{prefix}{clean}" if clean else f"{prefix}{sensor.get('id', 'unknown')}"


def build_piggyback(
    client: UXIClient, *, host_field: str = "name", host_prefix: str = "uxi-"
) -> str:
    """Collect inventory + status and render Checkmk piggyback output."""
    sensors = client.get_all("/sensors")
    statuses, issues = collect_statuses(client, sensors=sensors)

    by_id_status = {s["sensorId"]: s for s in statuses}
    issues_by_sensor: dict[str, list[dict]] = {}
    for i in issues:
        issues_by_sensor.setdefault(i.get("sensorId"), []).append(i)

    # Resolve host names, deduplicating collisions with the serial.
    names: dict[str, str] = {}
    seen: dict[str, str] = {}
    for s in sensors:
        h = hostname_for(s, host_field, host_prefix)
        if h in seen.values():
            h = f"{h}-{s.get('serial', s['id'])}"
        names[s["id"]] = seen[s["id"]] = h

    out: list[str] = []
    # Fleet summary lands on the host that runs the plugin.
    online = sum(1 for s in statuses if s.get("isOnline"))
    out.append("<<<uxi_fleet:sep(0)>>>")
    out.append(json.dumps({
        "total": len(sensors),
        "online": online,
        "offline": sum(1 for s in statuses if s.get("isOnline") is False),
        "status_errors": sum(1 for s in statuses if "error" in s),
        "active_issues": len(issues),
    }, sort_keys=True))

    for s in sensors:
        st = by_id_status.get(s["id"], {})
        record = {k: s.get(k) for k in _SENSOR_FIELDS}
        record.update({
            "isOnline": st.get("isOnline"),
            "isTesting": st.get("isTesting"),
            "activeIssueCount": st.get("activeIssueCount", 0),
            "statusError": st.get("error"),
        })
        out.append(f"<<<<{names[s['id']]}>>>>")
        out.append("<<<uxi_sensor:sep(0)>>>")
        out.append(json.dumps(record, sort_keys=True, default=str))
        sensor_issues = issues_by_sensor.get(s["id"], [])
        out.append("<<<uxi_issues:sep(0)>>>")
        for issue in sensor_issues:
            out.append(json.dumps(issue, sort_keys=True, default=str))
        out.append("<<<<>>>>")

    return "\n".join(out) + "\n"
