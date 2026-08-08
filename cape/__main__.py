"""CLI entrypoint for UXI data collection.

Examples:
    # Just the sensor inventory, printed as a table
    python -m cape inventory

    # One specific resource
    python -m cape collect --resource sensors

    # A whole category, written to ./out as CSV
    python -m cape config --out ./out --format csv

    # Everything
    python -m cape all --out ./out

    # See what can be collected
    python -m cape list
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .client import UXIClient
from .collector import collect_resource, select_resources, write_output
from .config import DEFAULT_REGION, API_BASES, RESOURCES
from .secrets import resolve_credentials

CATEGORIES = ("inventory", "config", "results")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cape", description="Collect Aruba UXI (Cape) sensor data.")
    p.add_argument("--secret-id", default=os.environ.get("UXI_SECRET_ID"),
                   help="AWS Secrets Manager secret id/ARN (env: UXI_SECRET_ID).")
    p.add_argument("--aws-region", default=os.environ.get("AWS_REGION"),
                   help="AWS region for Secrets Manager (env: AWS_REGION).")
    p.add_argument("--client-id-key", default=os.environ.get("UXI_CLIENT_ID_KEY"),
                   help="Key holding the UXI client id inside the (shared) secret "
                        "(env: UXI_CLIENT_ID_KEY; default: uxi_client_id, then client_id).")
    p.add_argument("--client-secret-key", default=os.environ.get("UXI_CLIENT_SECRET_KEY"),
                   help="Key holding the UXI client secret inside the (shared) secret "
                        "(env: UXI_CLIENT_SECRET_KEY; default: uxi_client_secret, then client_secret).")
    p.add_argument("--creds-source", choices=("auto", "env", "aws"),
                   default=os.environ.get("UXI_CREDS_SOURCE", "auto"),
                   help="Where to read UXI credentials: 'env' uses "
                        "UXI_CLIENT_ID/UXI_CLIENT_SECRET, 'aws' uses Secrets "
                        "Manager, 'auto' (default) prefers env then AWS.")
    p.add_argument("--region", default=os.environ.get("UXI_REGION", DEFAULT_REGION),
                   choices=list(API_BASES), help="UXI API region.")
    p.add_argument("--out", type=Path, default=None,
                   help="Directory to write results into. If omitted, prints to stdout.")
    p.add_argument("--format", choices=("json", "csv"), default="json")
    p.add_argument("-v", "--verbose", action="store_true")

    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="List collectable resources and exit.")
    sub.add_parser("inventory", help="Collect inventory (sensors, agents, groups).")
    sub.add_parser("config", help="Collect configuration resources.")
    sub.add_parser("tests", help="Alias: service-tests + their group assignments.")
    sub.add_parser("all", help="Collect every known REST resource.")
    c = sub.add_parser("collect", help="Collect specific resource(s) by name.")
    c.add_argument("--resource", required=True,
                   help="Comma-separated resource names (see `list`).")
    sub.add_parser("status",
                   help="Sweep live sensor health + active issues "
                        "(pull-only, GET /sensors/{id}/status).")
    k = sub.add_parser("checkmk",
                       help="Print Checkmk piggyback output (one block per "
                            "sensor) for use as an agent plugin.")
    k.add_argument("--host-field", choices=("name", "serial"), default="name",
                   help="Sensor field used as piggyback host name (default: name).")
    k.add_argument("--host-prefix", default="uxi-",
                   help="Prefix for piggyback host names (default: 'uxi-').")
    return p


# command -> (category, explicit resource names)
COMMAND_MAP = {
    "inventory": ("inventory", None),
    "config": ("config", None),
    "all": ("all", None),
    "tests": (None, ["service-tests", "service-test-group-assignments"]),
}


def cmd_list() -> int:
    width = max(len(r.name) for r in RESOURCES)
    print(f"{'RESOURCE':<{width}}  CATEGORY     NOTE      PATH")
    for r in sorted(RESOURCES, key=lambda x: (x.category, x.name)):
        print(f"{r.name:<{width}}  {r.category:<11}  {r.note:<8}  {r.path}")
    print("\nCommands: inventory | config | tests | all | "
          "collect --resource <name,...>")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.command == "list":
        return cmd_list()

    if args.command == "status":
        return cmd_status(args)

    if args.command == "checkmk":
        from .checkmk import build_piggyback
        creds = resolve_credentials(args.secret_id, region_name=args.aws_region,
                                    client_id_key=args.client_id_key,
                                    client_secret_key=args.client_secret_key,
                                    source=args.creds_source)
        client = UXIClient(creds, region=args.region)
        sys.stdout.write(build_piggyback(
            client, host_field=args.host_field, host_prefix=args.host_prefix))
        return 0

    # Resolve which resources to pull.
    if args.command == "collect":
        names = [n.strip() for n in args.resource.split(",") if n.strip()]
        resources = select_resources(names=names)
    else:
        category, names = COMMAND_MAP[args.command]
        resources = select_resources(category=category, names=names)

    creds = resolve_credentials(args.secret_id, region_name=args.aws_region,
                                client_id_key=args.client_id_key,
                                client_secret_key=args.client_secret_key,
                                source=args.creds_source)
    client = UXIClient(creds, region=args.region)

    exit_code = 0
    for res in resources:
        records = collect_resource(client, res)
        if args.out:
            path = write_output(records, res.name, args.out, args.format)
            print(f"{res.name:<32} {len(records):>6} record(s) -> {path}")
        else:
            _print_summary(res.name, records)
        if not records and res.note != "verify":
            exit_code = max(exit_code, 0)  # empty isn't necessarily an error
    return exit_code


def cmd_status(args) -> int:
    from .status import collect_statuses

    creds = resolve_credentials(args.secret_id, region_name=args.aws_region,
                                client_id_key=args.client_id_key,
                                client_secret_key=args.client_secret_key,
                                source=args.creds_source)
    client = UXIClient(creds, region=args.region)
    statuses, issues = collect_statuses(client)

    offline = [s for s in statuses if s.get("isOnline") is False]
    print(f"\nSensors: {len(statuses)} | online: "
          f"{sum(1 for s in statuses if s.get('isOnline'))} | "
          f"offline: {len(offline)} | active issues: {len(issues)}")
    for s in offline:
        print(f"  OFFLINE: {s['sensorName']} ({s['sensorSerial']}) in {s['groupName']}")
    for i in issues:
        print(f"  {i.get('severity', '?'):<8} {i.get('code'):<32} "
              f"{i.get('sensorName')} [{i.get('networkName')}] {i.get('timestamp')}")

    if args.out:
        for name, recs in (("sensor-status", statuses), ("issues", issues)):
            path = write_output(recs, name, args.out, args.format)
            print(f"{name:<20} {len(recs):>6} record(s) -> {path}")
    return 0


def _print_summary(name: str, records: list[dict]) -> None:
    print(f"\n=== {name}: {len(records)} record(s) ===")
    if not records:
        return
    # Show a compact view using the most common identifying fields.
    keys = [k for k in ("id", "name", "serial", "serialNumber", "modelNumber",
                        "status", "address") if k in records[0]]
    if not keys:
        keys = list(records[0])[:4]
    print("  " + " | ".join(keys))
    for rec in records[:50]:
        print("  " + " | ".join(str(rec.get(k, "")) for k in keys))
    if len(records) > 50:
        print(f"  ... and {len(records) - 50} more (use --out to capture all)")


if __name__ == "__main__":
    sys.exit(main())
