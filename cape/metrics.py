"""Pull UXI test-result time series (the actual measurements).

The public v1alpha1 API exposes no test results. The dashboard's own backend
does, via a structured-query endpoint over what is evidently an InfluxDB-style
store, and it accepts the SAME OAuth client-credentials bearer token as the
public API (verified live) — so this stays fully pull-based, with no webhooks
or push destinations.

    POST {dashboard_base}/test-results/actions/structured-query
    {
      "measurements": ["dhcp"],
      "selectors": ["MEAN(elapsed_time) as elapsed_time"],
      "groups": ["time(600s)", "sensor_uid"],
      "filters": {"sensors": null, "networks": null, "services": null},
      "where": [],
      "time_greater_than": "now() - 3600s"
    }

Grouping by `sensor_uid` returns every sensor in one request, keyed by the same
UUIDs as /sensors — so one query per measurement covers the whole fleet.
(`sensor_name` and `sensor_serial` also work; `sensor_id`/`serial` 500.)

CAVEAT: this endpoint is undocumented and unversioned. It may change without
notice. Every metric below degrades independently — a failed query yields no
data for that metric rather than breaking the run.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .client import UXIClient

log = logging.getLogger(__name__)

QUERY_PATH = "/test-results/actions/structured-query"
GROUP_TAG = "sensor_uid"


@dataclass(frozen=True)
class Metric:
    """One collectable measurement.

    key:         short name used in output and as the Checkmk metric name.
    measurement: InfluxDB measurement.
    selector:    aggregation expression; its `as <alias>` must equal `key`.
    unit:        for display/annotation only.
    """
    key: str
    measurement: str
    selector: str
    unit: str = ""
    description: str = ""


# Verified live against the dashboard backend. Selectors mirror what the UXI
# dashboard itself issues for its own charts.
METRICS: list[Metric] = [
    Metric("dhcp_elapsed_time", "dhcp", "MEAN(elapsed_time) as dhcp_elapsed_time",
           "ms", "DHCP lease acquisition time"),
    Metric("dns_elapsed_time", "dns", "MEAN(elapsed_time) as dns_elapsed_time",
           "ms", "DNS resolution time"),
    Metric("assoc_elapsed_time", "ap_association",
           "MEAN(elapsed_time) as assoc_elapsed_time", "ms", "Wi-Fi association time"),
    Metric("eap_time", "ieee8021x_auth", "MEAN(eap_time) as eap_time",
           "ms", "802.1X EAP authentication time"),
    Metric("auth_elapsed_time", "ieee8021x_auth",
           "MEAN(elapsed_time) as auth_elapsed_time", "ms", "802.1X total auth time"),
    Metric("rssi", "wifi_link", "MEAN(rssi) as rssi", "dBm", "Wi-Fi signal strength"),
    Metric("channel_utilisation", "wifi_data",
           "SUM(channel_busy)/SUM(channel_active)*100 as channel_utilisation",
           "%", "Channel utilisation"),
    Metric("mean_rssi", "mcs", "MEAN(mean_rssi) as mean_rssi", "dBm", "MCS mean RSSI"),
    Metric("rx_datarate", "mcs",
           "MEAN(receive_mean_datarate) as rx_datarate", "bits/s", "Receive data rate"),
    Metric("tx_datarate", "mcs",
           "MEAN(transfer_mean_datarate) as tx_datarate", "bits/s", "Transmit data rate"),
]


def build_query(metric: Metric, bin_seconds: int, window_seconds: int,
                group_by_sensor: bool = True) -> dict:
    groups = [f"time({bin_seconds}s)"]
    if group_by_sensor:
        groups.append(GROUP_TAG)
    return {
        "where": [],
        "filters": {"sensors": None, "networks": None, "services": None},
        "groups": groups,
        "name": f"cape_{metric.key}",
        "measurements": [metric.measurement],
        "selectors": [metric.selector],
        "time_greater_than": f"now() - {window_seconds}s",
    }


def _latest_from_series(series: list) -> float | None:
    """Newest non-null value from [[timestamp, value], ...]."""
    for point in reversed(series or []):
        if isinstance(point, (list, tuple)) and len(point) >= 2 and point[1] is not None:
            try:
                return float(point[1])
            except (TypeError, ValueError):
                return None
    return None


def collect_metrics(
    client: UXIClient,
    *,
    window_seconds: int = 3600,
    bin_seconds: int = 600,
    metrics: list[Metric] | None = None,
) -> dict[str, dict[str, float]]:
    """Fetch all metrics for the whole fleet.

    Returns {sensor_uid: {metric_key: latest_value}}. One request per metric.
    """
    chosen = metrics if metrics is not None else METRICS
    by_sensor: dict[str, dict[str, float]] = {}

    for m in chosen:
        try:
            payload = client.post_dashboard(QUERY_PATH,
                                            build_query(m, bin_seconds, window_seconds))
        except Exception as e:  # noqa: BLE001 — one metric must not break the run
            log.warning("metric '%s' query failed: %s", m.key, e)
            continue

        values = payload.get("values")
        if not isinstance(values, dict):
            # Ungrouped result (no sensor breakdown) — not usable per-host.
            log.debug("metric '%s': no per-sensor grouping in response", m.key)
            continue

        n = 0
        for sensor_uid, series in values.items():
            latest = _latest_from_series(series)
            if latest is None:
                continue
            by_sensor.setdefault(sensor_uid, {})[m.key] = latest
            n += 1
        log.info("metric '%s': %d sensor(s)", m.key, n)

    log.info("metrics collected for %d sensor(s)", len(by_sensor))
    return by_sensor
