"""Generic collection engine + output writers (JSON / CSV)."""
from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

from .client import UXIClient
from .config import Resource, get_resource, resources_by_category, RESOURCES

log = logging.getLogger(__name__)


def collect_resource(client: UXIClient, resource: Resource) -> list[dict]:
    """Pull all records for one resource. Returns [] on error (logged)."""
    log.info("Collecting '%s' (%s)", resource.name, resource.path)
    try:
        records = client.get_all(resource.path, params=resource.params or None)
    except Exception as e:  # noqa: BLE001 — one bad endpoint shouldn't abort the run
        level = log.warning if resource.note == "verify" else log.error
        level("  '%s' failed: %s", resource.name, e)
        return []
    log.info("  '%s': %d record(s)", resource.name, len(records))
    return records


def select_resources(
    *, category: str | None = None, names: list[str] | None = None
) -> list[Resource]:
    """Resolve a category and/or an explicit name list into Resource objects."""
    if names:
        chosen = []
        for n in names:
            r = get_resource(n)
            if r is None:
                raise ValueError(f"Unknown resource '{n}'. Known: {[x.name for x in RESOURCES]}")
            chosen.append(r)
        return chosen
    if category and category != "all":
        return resources_by_category(category)
    return list(RESOURCES)


# --- writers -------------------------------------------------------------
def write_json(records: list[dict], path: Path) -> None:
    path.write_text(json.dumps(records, indent=2, default=str))


def write_csv(records: list[dict], path: Path) -> None:
    if not records:
        path.write_text("")
        return
    # Union of keys across records, preserving first-seen order.
    fields: list[str] = []
    for rec in records:
        for k in rec:
            if k not in fields:
                fields.append(k)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for rec in records:
            # Flatten nested values to JSON strings so CSV stays valid.
            w.writerow({k: (v if isinstance(v, (str, int, float, bool)) or v is None
                            else json.dumps(v, default=str)) for k, v in rec.items()})


def write_output(records: list[dict], resource_name: str, out_dir: Path, fmt: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{resource_name}.{fmt}"
    (write_csv if fmt == "csv" else write_json)(records, path)
    return path
