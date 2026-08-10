# Checkmk integration (piggyback)

`python -m cape checkmk` prints Checkmk agent output in which every UXI sensor
becomes its own **piggyback host**:

```
<<<uxi_fleet:sep(0)>>>                 <- lands on the host running the plugin
{"total": 51, "online": 35, "offline": 16, "active_issues": 40, ...}
<<<<uxi-Louisville>>>>                 <- piggyback block, one per sensor
<<<uxi_sensor:sep(0)>>>
{"name": "Louisville", "serial": "CNP3KYT0B5", "isOnline": true, ...}
<<<uxi_issues:sep(0)>>>
{"code": "HIGH_DHCP_RESPONSE_TIME", "severity": "INFO", ...}
<<<uxi_metrics:sep(0)>>>
{"dhcp_elapsed_time": 18925.8, "dns_elapsed_time": 76.5, "mean_rssi": -62.6, ...}
<<<<>>>>
```

Verified end-to-end on **Checkmk 2.5.0p8**. The check plugins use the
agent_based **v2** API, which exists from **2.3** onward, so 2.3/2.4 should
work — but only 2.5 has been tested. Checkmk **2.2** uses the v1 API under
`local/lib/check_mk/base/plugins/agent_based/` and would need import changes.

## Layout

All files live on the `/data` disk; every path Checkmk expects is a **symlink**
into it. Verified working — Checkmk discovers symlinked check plugins and
executes symlinked agent plugins.

| Checkmk expects | Symlink target on `/data` |
|---|---|
| `/etc/cape/uxi.env` | `/data/cape/etc/uxi.env` |
| `/usr/lib/check_mk_agent/plugins/300/uxi_piggyback` | `/data/cape/repo/checkmk/agent_plugin/uxi_piggyback` |
| `/omd/sites/$SITE/local/lib/python3/cmk_addons/plugins/uxi/agent_based/uxi.py` | `/data/cape/repo/checkmk/cmk_addons_plugins/uxi/agent_based/uxi.py` |

The code is never copied — the symlinks point straight at the git checkout, so
`git pull` updates both plugins in place.

Repo layout, for reference (`/data/cape/repo` is the clone root):
```
/data/cape/repo/
├── cape/                                   # the collector package
└── checkmk/
    ├── agent_plugin/uxi_piggyback          # -> agent plugins dir
    ├── cmk_addons_plugins/uxi/agent_based/uxi.py   # -> site plugins dir
    ├── setup_dcd.py
    └── uninstall.py
```

## Services created

| Service | Where | Logic | Metrics |
|---|---|---|---|
| **UXI Sensor** | each sensor host | offline → CRIT, online-but-not-testing → WARN | `uxi_active_issues` |
| **UXI Issues** | each sensor host | worst issue severity (INFO/WARNING → WARN, ERROR/CRITICAL → CRIT); issues listed in details | `uxi_issues_info/warning/error/critical` |
| **UXI Metrics** | each sensor host | OK unless levels configured | DHCP/DNS/association/EAP/auth latency, channel utilisation, RSSI, RX/TX data rates |
| **UXI Fleet** | plugin host | any sensor offline → WARN | totals: online/offline/issues |

Offline (or non-testing) sensors emit no `uxi_metrics` section and so get no
**UXI Metrics** service — expected, since they aren't running tests.

## Install

### 0. Set these two values first

Every command below uses them, so export them once in the shell you're
installing from.

```bash
# Your OMD site name — `omd sites` lists them (the SITE column)
SITE=your_site_name

# The host, AS NAMED IN CHECKMK, whose agent will carry the UXI data.
# Usually the Checkmk server monitoring itself. `cmk --list-hosts` shows names.
AGENT_HOST=your_agent_host
```

Confirm they're right before continuing — a wrong `SITE` writes into a
non-existent path, and a wrong `AGENT_HOST` makes DCD ignore the piggyback data:
```bash
sudo omd sites
sudo -iu "$SITE" cmk --list-hosts | grep -x "$AGENT_HOST" && echo "AGENT_HOST OK"
```

### 1. Prerequisites
```bash
python3 -c "import requests" || sudo pip3 install requests
# boto3 is ONLY needed if you use AWS Secrets Manager instead of the env file
```

### 2. Lay out /data
Every parent must be traversable by the site user (`755`), or check-plugin
discovery silently finds nothing.
```bash
sudo mkdir -p /data/cape/etc
sudo git clone https://github.com/jtklabs/cape.git /data/cape/repo
sudo chmod 755 /data /data/cape
```

### 3. Credentials
`600 root:root` applies to the **target** file, not the symlink. `CAPE_DIR` is
how the agent plugin locates the code, so the wrapper needs no edits.
```bash
sudo tee /data/cape/etc/uxi.env >/dev/null <<'EOF'
UXI_CLIENT_ID=your-client-id
UXI_CLIENT_SECRET=your-client-secret
CAPE_DIR=/data/cape/repo
EOF
sudo chmod 600 /data/cape/etc/uxi.env
sudo chown root:root /data/cape/etc/uxi.env
```

*Using AWS Secrets Manager instead?* Put `UXI_SECRET_ID=...` and `AWS_REGION=...`
in that file rather than the id/secret. The secret is a JSON key/value map;
keys default to `uxi_client_id` / `uxi_client_secret` (falling back to
`client_id` / `client_secret`). The agent runs as **root**, so root needs AWS
credentials.

### 4. Symlink 1 — credentials
```bash
sudo mkdir -p /etc/cape && sudo chmod 700 /etc/cape
sudo ln -sfn /data/cape/etc/uxi.env /etc/cape/uxi.env
```

### 5. Symlink 2 — agent plugin
`300` = a 5-minute cache interval, which keeps UXI API usage modest (~51 status
calls plus one query per metric per run).
```bash
sudo mkdir -p /usr/lib/check_mk_agent/plugins/300
sudo chmod +x /data/cape/repo/checkmk/agent_plugin/uxi_piggyback
sudo ln -sfn /data/cape/repo/checkmk/agent_plugin/uxi_piggyback \
  /usr/lib/check_mk_agent/plugins/300/uxi_piggyback
```

### 6. Symlink 3 — check plugins
```bash
sudo -u "$SITE" mkdir -p "/omd/sites/$SITE/local/lib/python3/cmk_addons/plugins/uxi/agent_based"
sudo ln -sfn /data/cape/repo/checkmk/cmk_addons_plugins/uxi/agent_based/uxi.py \
  "/omd/sites/$SITE/local/lib/python3/cmk_addons/plugins/uxi/agent_based/uxi.py"
```

### 7. Checkpoint — verify before creating hosts
```bash
sudo /usr/lib/check_mk_agent/plugins/300/uxi_piggyback | head -5
sudo -iu "$SITE" cmk -L | grep '^uxi_'
```
Expect piggyback blocks from the first, and all four plugins
(`uxi_fleet uxi_issues uxi_metrics uxi_sensor`) from the second.

**Stop here if either is empty** — creating hosts first only hides the cause.
No piggyback output means credentials failed (rerun the plugin without
`2>/dev/null` to see the error). No plugins listed means the site user cannot
traverse `/data`; recheck the `755` in step 2.

### 8. Create the hosts (DCD)
```bash
sudo -iu "$SITE" python3 /data/cape/repo/checkmk/setup_dcd.py \
  --site "$SITE" --source-host "$AGENT_HOST"
sudo -iu "$SITE" cmk -O
sudo -iu "$SITE" omd restart dcd
```

`setup_dcd.py` is idempotent and creates three things: the WATO folder
`UXI Sensors`, a DCD piggyback connection that auto-creates/removes `uxi-*`
hosts, and rules suppressing host+service notifications for that folder so a
first rollout can't page anyone. **Remove that suppression once you've triaged
the fleet** — delete the `UXI-NOTIFY-SUPPRESSION` block from
`/omd/sites/$SITE/etc/check_mk/conf.d/wato/rules.mk`. Pass
`--no-suppress-notifications` to skip it entirely.

### 9. Verify
```bash
sudo -iu "$SITE" cmk --list-hosts | grep -c '^uxi-'
sudo -iu "$SITE" bash -lc 'printf "GET services\nFilter: host_name ~ ^uxi-\nColumns: description state\n" | lq' \
  | sort | uniq -c
```
Hosts appear within ~60s of the DCD restart. If services are missing, run
discovery explicitly:
```bash
sudo -iu "$SITE" cmk -II $(sudo -iu "$SITE" cmk --list-hosts | grep '^uxi-' | tr '\n' ' ')
sudo -iu "$SITE" cmk -O
```

## Updating
```bash
sudo git -C /data/cape/repo pull
sudo -iu "$SITE" cmk -O      # only needed for check-plugin changes
```
The symlinks point at the checkout, so both plugins update in place. Collector
changes also need the caches cleared — see Troubleshooting.

## Uninstall
```bash
sudo -iu "$SITE" python3 /data/cape/repo/checkmk/uninstall.py --site "$SITE" --dry-run
sudo -iu "$SITE" python3 /data/cape/repo/checkmk/uninstall.py --site "$SITE"
sudo -iu "$SITE" cmk -O && sudo -iu "$SITE" omd restart dcd
```
That removes the DCD connection, the folder and its hosts, and the notification
rules. Then the symlinks, data and caches:
```bash
sudo rm -f /usr/lib/check_mk_agent/plugins/300/uxi_piggyback
sudo rm -rf /etc/cape
sudo rm -rf "/omd/sites/$SITE/local/lib/python3/cmk_addons/plugins/uxi"
sudo rm -f /var/lib/check_mk_agent/cache/plugins_uxi_piggyback.cache
sudo sh -c "rm -rf /omd/sites/$SITE/tmp/check_mk/piggyback/uxi-*"
sudo sh -c "rm -rf /omd/sites/$SITE/var/check_mk/rrd/uxi-*"
sudo rm -rf /data/cape        # the actual files, incl. your credentials
```
The first commands remove only symlinks — `/data/cape` holds the real
credentials file, so don't skip that last line.

## Troubleshooting

**Changes to the collector don't show up.** Piggyback output is cached in
*three* places. Clear all of them:
```bash
sudo rm -f /var/lib/check_mk_agent/cache/plugins_uxi_piggyback.cache    # agent plugin cache
sudo rm -f "/omd/sites/$SITE/tmp/check_mk/cache/$AGENT_HOST"            # site fetch cache
sudo sh -c "rm -rf /omd/sites/$SITE/tmp/check_mk/piggyback/uxi-*"       # piggyback spool
```
Then trigger the agent and wait for the async plugin to finish before
re-fetching (~60s for 51 sensors):
```bash
sudo check_mk_agent >/dev/null; sleep 60
sudo -iu "$SITE" cmk -n "$AGENT_HOST" >/dev/null
```

**`cmk -L` lists no uxi plugins.** The site user cannot traverse `/data`:
```bash
sudo -u "$SITE" test -r "/omd/sites/$SITE/local/lib/python3/cmk_addons/plugins/uxi/agent_based/uxi.py" \
  && echo readable || echo "blocked — check 755 on /data and /data/cape"
```

**Globs under the site tmp directory silently match nothing.** A non-root shell
can't read those directories to expand `uxi-*`, so `sudo rm -rf .../uxi-*`
becomes a literal no-op. Always wrap: `sudo sh -c "rm -rf .../uxi-*"`.

**`Check_MK Discovery` shows WARN right after rollout.** Stale — it clears on
its next scheduled run. Confirm with `cmk --check-discovery <host>`.

**`omd restart dcd` says "site does not exist".** Run it as the site user
(`sudo -iu "$SITE" omd restart dcd`), not as root.

## Host naming
Default: sanitized sensor name with a `uxi-` prefix
(`uxi-Floor-24-CapeSensor-1`); runs of separators are collapsed. Use
`--host-field serial` for names stable across renames — **decide before first
rollout**, because renaming a Checkmk host loses its historical data. Name
collisions get the serial appended automatically.

## Notes
- The **UXI Issues** severity→state mapping is overridable via the `uxi_issues`
  ruleset (`severity_states`, e.g. `{"INFO": 0}` to keep INFO OK).
- **UXI Metrics** supports upper-bound levels per metric via the `uxi_metrics`
  ruleset, e.g. `{"dhcp_elapsed_time": (1000, 5000)}` to alert on slow DHCP.
- Metrics come from an **undocumented** dashboard endpoint (see
  `cape/metrics.py`). Each metric degrades independently, so a vendor-side
  change shows up as missing graphs rather than a broken collector.
