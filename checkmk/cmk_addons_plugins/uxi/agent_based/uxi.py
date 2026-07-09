"""Checkmk check plugins for Aruba UXI sensors (piggyback data).

Install on the Checkmk site (2.3+ / agent_based v2 API):
    /omd/sites/<site>/local/lib/python3/cmk_addons/plugins/uxi/agent_based/uxi.py

Sections produced by `python -m cape checkmk`:
    uxi_fleet   — fleet totals, on the host running the agent plugin
    uxi_sensor  — per-sensor identity + live status (piggyback)
    uxi_issues  — per-sensor active issues, one JSON line each (piggyback)
"""
from __future__ import annotations

import json
from collections import Counter

from cmk.agent_based.v2 import (
    AgentSection,
    CheckPlugin,
    CheckResult,
    DiscoveryResult,
    Metric,
    Result,
    Service,
    State,
    StringTable,
)


def _parse_json_lines(string_table: StringTable) -> list[dict]:
    records = []
    for line in string_table:
        if not line:
            continue
        try:
            records.append(json.loads(line[0]))
        except (json.JSONDecodeError, IndexError):
            continue
    return records


# --- uxi_sensor ------------------------------------------------------------
def parse_uxi_sensor(string_table: StringTable) -> dict | None:
    records = _parse_json_lines(string_table)
    return records[0] if records else None


agent_section_uxi_sensor = AgentSection(name="uxi_sensor", parse_function=parse_uxi_sensor)


def discover_uxi_sensor(section: dict | None) -> DiscoveryResult:
    if section:
        yield Service()


def check_uxi_sensor(section: dict | None) -> CheckResult:
    if not section:
        return
    if section.get("statusError"):
        yield Result(state=State.UNKNOWN,
                     summary=f"Status query failed: {section['statusError']}")
        return

    online = section.get("isOnline")
    testing = section.get("isTesting")
    if online is True:
        yield Result(state=State.OK, summary="Online")
    elif online is False:
        yield Result(state=State.CRIT, summary="Offline")
    else:
        yield Result(state=State.UNKNOWN, summary="Online state unknown")

    if online and testing is False:
        yield Result(state=State.WARN, summary="Not testing")
    elif testing:
        yield Result(state=State.OK, summary="Testing")

    n_issues = int(section.get("activeIssueCount") or 0)
    yield Metric("uxi_active_issues", n_issues)
    yield Result(state=State.OK, summary=f"Active issues: {n_issues}")

    details = [f"{k}: {section.get(k)}" for k in
               ("serial", "modelNumber", "groupName", "addressNote",
                "ethernetMacAddress", "wifiMacAddress") if section.get(k)]
    if details:
        yield Result(state=State.OK, notice="Sensor info", details="\n".join(details))


check_plugin_uxi_sensor = CheckPlugin(
    name="uxi_sensor",
    service_name="UXI Sensor",
    discovery_function=discover_uxi_sensor,
    check_function=check_uxi_sensor,
)


# --- uxi_issues ------------------------------------------------------------
agent_section_uxi_issues = AgentSection(name="uxi_issues", parse_function=_parse_json_lines)

_SEVERITY_STATE = {
    "INFO": State.WARN,      # an INFO issue is still a confirmed problem
    "WARNING": State.WARN,
    "ERROR": State.CRIT,
    "CRITICAL": State.CRIT,
}


def discover_uxi_issues(section: list[dict]) -> DiscoveryResult:
    # Discover on every sensor that has the section, even when currently
    # issue-free — so the service exists and alerts when issues appear.
    yield Service()


def check_uxi_issues(params: dict, section: list[dict]) -> CheckResult:
    severity_map = {**_SEVERITY_STATE,
                    **{k.upper(): State(v) for k, v in
                       (params.get("severity_states") or {}).items()}}

    by_severity = Counter((i.get("severity") or "UNKNOWN").upper() for i in section)
    for sev in ("INFO", "WARNING", "ERROR", "CRITICAL"):
        yield Metric(f"uxi_issues_{sev.lower()}", by_severity.get(sev, 0))

    if not section:
        yield Result(state=State.OK, summary="No active issues")
        return

    worst = State.OK
    for issue in section:
        sev = (issue.get("severity") or "UNKNOWN").upper()
        state = severity_map.get(sev, State.WARN)
        worst = State.worst(worst, state)

    summary = ", ".join(f"{n}x {sev}" for sev, n in by_severity.most_common())
    yield Result(state=worst, summary=f"{len(section)} active issue(s): {summary}")
    for issue in section:
        yield Result(
            state=State.OK,
            notice=(f"{issue.get('severity')}: {issue.get('code')} "
                    f"[{issue.get('networkName') or issue.get('serviceTestName') or '-'}] "
                    f"since {issue.get('timestamp')}"),
        )


check_plugin_uxi_issues = CheckPlugin(
    name="uxi_issues",
    service_name="UXI Issues",
    discovery_function=discover_uxi_issues,
    check_function=check_uxi_issues,
    check_default_parameters={},
    check_ruleset_name="uxi_issues",
)


# --- uxi_fleet -------------------------------------------------------------
agent_section_uxi_fleet = AgentSection(name="uxi_fleet", parse_function=parse_uxi_sensor)


def discover_uxi_fleet(section: dict | None) -> DiscoveryResult:
    if section:
        yield Service()


def check_uxi_fleet(section: dict | None) -> CheckResult:
    if not section:
        return
    total = section.get("total", 0)
    online = section.get("online", 0)
    offline = section.get("offline", 0)
    issues = section.get("active_issues", 0)
    errors = section.get("status_errors", 0)

    state = State.OK
    if offline:
        state = State.WARN
    yield Result(state=state,
                 summary=f"{online}/{total} sensors online, "
                         f"{offline} offline, {issues} active issues")
    if errors:
        yield Result(state=State.WARN, summary=f"{errors} status query errors")
    yield Metric("uxi_sensors_total", total)
    yield Metric("uxi_sensors_online", online)
    yield Metric("uxi_sensors_offline", offline)
    yield Metric("uxi_active_issues", issues)


check_plugin_uxi_fleet = CheckPlugin(
    name="uxi_fleet",
    service_name="UXI Fleet",
    discovery_function=discover_uxi_fleet,
    check_function=check_uxi_fleet,
)
