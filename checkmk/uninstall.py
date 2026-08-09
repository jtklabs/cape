#!/usr/bin/env python3
"""Remove the UXI integration's site-level config from a Checkmk site.

Undoes what setup_dcd.py created, plus the hosts DCD generated:
  1. The DCD piggyback connection (restores dcd.d/wato/global.mk to empty).
  2. The WATO folder and every uxi-* host in it.
  3. The UXI-NOTIFY-SUPPRESSION block appended to conf.d/wato/rules.mk.

Run as the site user:
    sudo -iu <site> python3 /opt/cape/checkmk/uninstall.py

Then activate changes (`cmk -O`) and restart dcd. Root-level files
(/usr/lib/check_mk_agent/plugins/300/uxi_piggyback, /etc/cape, /opt/cape,
the check plugins, RRDs and piggyback spool) are removed separately — see
README-checkmk.md or the commands printed at the end.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil

MARKER = "UXI-NOTIFY-SUPPRESSION"


def remove_dcd(root: str, dry: bool) -> None:
    path = f"{root}/etc/check_mk/dcd.d/wato/global.mk"
    if not os.path.exists(path):
        print(f"dcd config absent: {path}")
        return
    body = open(path).read()
    if "uxi_piggyback" not in body:
        print("dcd config has no uxi_piggyback connection, leaving untouched")
        return
    if dry:
        print(f"[dry-run] would reset {path}")
        return
    # The file contained only the WATO header before setup_dcd.py wrote to it.
    with open(path, "w") as f:
        f.write("# Created by WATO\n\n")
    print(f"removed DCD connection: {path}")


def remove_folder(root: str, folder: str, dry: bool) -> int:
    path = f"{root}/etc/check_mk/conf.d/wato/{folder}"
    if not os.path.isdir(path):
        print(f"folder absent: {path}")
        return 0
    n = 0
    hosts_mk = f"{path}/hosts.mk"
    if os.path.exists(hosts_mk):
        n = len(re.findall(r"'uxi-[^']*'", open(hosts_mk).read()))
    if dry:
        print(f"[dry-run] would delete {path} ({n} host entries)")
        return n
    shutil.rmtree(path)
    print(f"deleted folder and hosts: {path} (~{n} host entries)")
    return n


def remove_notification_rules(root: str, dry: bool) -> None:
    path = f"{root}/etc/check_mk/conf.d/wato/rules.mk"
    if not os.path.exists(path):
        print("rules.mk absent")
        return
    body = open(path).read()
    if MARKER not in body:
        print("no notification suppression block found")
        return
    # Strip from the opening marker comment through the closing one.
    pattern = re.compile(
        r"\n*# --- " + MARKER + r".*?# --- end " + MARKER + r" ---\n?",
        re.DOTALL,
    )
    new = pattern.sub("\n", body)
    if new == body:
        print(f"WARNING: found '{MARKER}' but could not match the block; "
              "remove it by hand")
        return
    if dry:
        print(f"[dry-run] would strip {MARKER} block from rules.mk")
        return
    with open(path, "w") as f:
        f.write(new)
    print("removed notification suppression block from rules.mk")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default=os.environ.get("OMD_SITE", "cmk"))
    ap.add_argument("--folder", default="uxi")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = f"/omd/sites/{args.site}"
    if not os.path.isdir(root):
        raise SystemExit(f"site not found: {root}")

    # DCD first, so it cannot recreate hosts while we delete them.
    remove_dcd(root, args.dry_run)
    remove_folder(root, args.folder, args.dry_run)
    remove_notification_rules(root, args.dry_run)

    print("\nNext: `cmk -O` to apply, and `omd restart dcd`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
