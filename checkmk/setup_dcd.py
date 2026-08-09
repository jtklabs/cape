#!/usr/bin/env python3
"""Provision the Checkmk side of the UXI integration.

Creates, idempotently:
  1. A WATO folder "UXI Sensors" (/wato/uxi/) to hold the sensor hosts.
  2. A DCD piggyback connection that auto-creates/removes hosts named uxi-*
     from the uxi_piggyback agent plugin's output.
  3. Rules suppressing host+service notifications for that folder, so the
     initial rollout cannot page anyone while offline sensors are triaged.

Run as the site user on the Checkmk server:

    sudo -iu <site> python3 /opt/cape/checkmk/setup_dcd.py

Then activate changes (UI, or `cmk -O` + `omd restart dcd`).

Remove the notification suppression once you have triaged the fleet: delete
the two blocks marked UXI-NOTIFY-SUPPRESSION from conf.d/wato/rules.mk, or
disable the corresponding rules in the UI.
"""
from __future__ import annotations

import argparse
import os
import pprint
import time
import uuid

MARKER = "UXI-NOTIFY-SUPPRESSION"


def site_root(site: str) -> str:
    return f"/omd/sites/{site}"


def make_folder(root: str, folder_name: str, title: str) -> str:
    path = f"{root}/etc/check_mk/conf.d/wato/{folder_name}"
    os.makedirs(path, exist_ok=True)
    wato_file = f"{path}/.wato"
    if os.path.exists(wato_file):
        print(f"folder already exists, leaving as-is: {path}")
    else:
        now = time.time()
        spec = {
            "__id": uuid.uuid4().hex,
            "title": title,
            "attributes": {
                "meta_data": {"created_at": now, "created_by": None, "updated_at": now}
            },
            "num_hosts": 0,
            "lock": False,
            "lock_subfolders": False,
        }
        with open(wato_file, "w") as f:
            f.write(repr(spec) + "\n")
        print(f"created folder: {path}")
    hosts_mk = f"{path}/hosts.mk"
    if not os.path.exists(hosts_mk):
        with open(hosts_mk, "w") as f:
            f.write("# Created by HostStorage\n\nall_hosts += []\n")
    return path


def write_dcd(root: str, site: str, folder_name: str, source_host: str,
              prefix: str, interval: int) -> None:
    """Write the DCD piggyback connection.

    Schema per cmk.gui...dcd._typedefs._spec_types:
      DCDConnectionSpec{site, connector: (name, cfg), title?, disabled?}
      PiggybackConnectorSpec{interval, creation_rules, discover_on_creation,
                             no_deletion_time_after_init, max_cache_age,
                             validity_period, source_filters?}
      HostCreationRuleSpec{create_folder_path, host_attributes, delete_hosts,
                           host_filters?}
    """
    path = f"{root}/etc/check_mk/dcd.d/wato/global.mk"
    conn = {
        "uxi_piggyback": {
            "title": "UXI Sensors (piggyback)",
            "comment": "Auto-creates hosts from the uxi_piggyback agent plugin.",
            "disabled": False,
            "site": site,
            "connector": (
                "piggyback",
                {
                    # Only trust piggyback data coming from our collector host.
                    "source_filters": [source_host],
                    "interval": interval,
                    "creation_rules": [
                        {
                            "create_folder_path": folder_name,
                            # Piggyback-only hosts: no agent, no SNMP, no IP.
                            "host_attributes": [
                                ("tag_agent", "no-agent"),
                                ("tag_snmp_ds", "no-snmp"),
                                ("tag_address_family", "no-ip"),
                                ("tag_piggyback", "piggyback"),
                            ],
                            "delete_hosts": True,
                            "host_filters": [f"^{prefix}"],
                        }
                    ],
                    "discover_on_creation": True,
                    # Grace period before newly-vanished hosts may be deleted.
                    "no_deletion_time_after_init": 600,
                    "max_cache_age": 900,
                    "validity_period": 900,
                },
            ),
        }
    }
    with open(path, "w") as f:
        f.write("# Created by WATO\n\ndcd_connections = ")
        f.write(pprint.pformat(conn))
        f.write("\n")
    print(f"wrote DCD connection: {path}")


def suppress_notifications(root: str, folder_name: str) -> None:
    path = f"{root}/etc/check_mk/conf.d/wato/rules.mk"
    if MARKER in open(path).read():
        print("notification suppression already present, skipping")
        return
    cond = f"/wato/{folder_name}/"
    desc = f"{MARKER}: notifications off during UXI rollout"
    block = f"""

# --- {MARKER} (remove this block to re-enable notifications) ---
extra_host_conf.setdefault('notifications_enabled', [])

extra_host_conf['notifications_enabled'] = [
{{'id': '{uuid.uuid4()}', 'value': '0', 'condition': {{'host_folder': '{cond}'}}, 'options': {{'description': '{desc}'}}}},
] + extra_host_conf['notifications_enabled']

extra_service_conf.setdefault('notifications_enabled', [])

extra_service_conf['notifications_enabled'] = [
{{'id': '{uuid.uuid4()}', 'value': '0', 'condition': {{'host_folder': '{cond}'}}, 'options': {{'description': '{desc}'}}}},
] + extra_service_conf['notifications_enabled']
# --- end {MARKER} ---
"""
    with open(path, "a") as f:
        f.write(block)
    print(f"appended notification suppression for {cond}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default=os.environ.get("OMD_SITE", "cmk"))
    ap.add_argument("--folder", default="uxi")
    ap.add_argument("--title", default="UXI Sensors")
    ap.add_argument("--source-host", default="checkmk",
                    help="Host whose agent carries the piggyback data.")
    ap.add_argument("--prefix", default="uxi-", help="Host name prefix to match.")
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--no-suppress-notifications", action="store_true")
    args = ap.parse_args()

    root = site_root(args.site)
    if not os.path.isdir(root):
        raise SystemExit(f"site not found: {root}")

    make_folder(root, args.folder, args.title)
    write_dcd(root, args.site, args.folder, args.source_host, args.prefix, args.interval)
    if not args.no_suppress_notifications:
        suppress_notifications(root, args.folder)

    print("\nDone. Next: activate changes, then `omd restart dcd`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
