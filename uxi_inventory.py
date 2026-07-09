#!/usr/bin/env python3
"""
Authenticate to the Aruba UXI (formerly Cape Networks) API and collect a full
inventory of all sensors.

Usage:
    export UXI_CLIENT_ID="your-client-id"
    export UXI_CLIENT_SECRET="your-client-secret"
    python3 uxi_inventory.py                # pretty table to stdout
    python3 uxi_inventory.py --json out.json # also dump raw JSON

Credentials come from an HPE GreenLake "Personal API Client"
(Manage Workspace -> Personal API clients -> Create Personal API Client).
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

TOKEN_URL = "https://sso.common.cloud.hpe.com/as/token.oauth2"
API_BASE = "https://api.capenetworks.com/networking-uxi/v1alpha1"


def get_token(client_id: str, client_secret: str) -> str:
    """Exchange client credentials for a bearer token (valid ~2 hours)."""
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.load(resp)["access_token"]
    except urllib.error.HTTPError as e:
        sys.exit(f"Auth failed ({e.code}): {e.read().decode()}")


def get_all_sensors(token: str) -> list:
    """Page through the sensors endpoint (cursor-based pagination, 5 req/s limit)."""
    sensors = []
    cursor = None
    while True:
        url = f"{API_BASE}/sensors"
        if cursor:
            url += "?" + urllib.parse.urlencode({"cursor": cursor})
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as e:
            sys.exit(f"API error ({e.code}): {e.read().decode()}")
        sensors.extend(payload.get("items", []))
        cursor = payload.get("next")
        if not cursor:
            break
        time.sleep(0.25)  # stay under the 5 req/sec rate limit
    return sensors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", metavar="FILE", help="write raw sensor JSON to FILE")
    args = ap.parse_args()

    client_id = os.environ.get("UXI_CLIENT_ID")
    client_secret = os.environ.get("UXI_CLIENT_SECRET")
    if not client_id or not client_secret:
        sys.exit("Set UXI_CLIENT_ID and UXI_CLIENT_SECRET environment variables.")

    token = get_token(client_id, client_secret)
    sensors = get_all_sensors(token)

    print(f"\nFound {len(sensors)} sensor(s)\n")
    hdr = f"{'NAME':<28} {'SERIAL':<16} {'MODEL':<12} {'STATUS':<10} ID"
    print(hdr)
    print("-" * len(hdr))
    for s in sensors:
        print(f"{(s.get('name') or '-'):<28} "
              f"{(s.get('serial') or s.get('serialNumber') or '-'):<16} "
              f"{(s.get('modelNumber') or s.get('model') or '-'):<12} "
              f"{(s.get('status') or '-'):<10} "
              f"{s.get('id') or '-'}")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(sensors, f, indent=2)
        print(f"\nRaw JSON written to {args.json}")


if __name__ == "__main__":
    main()
