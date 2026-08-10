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

## Services created

| Service | Where | Logic | Metrics |
|---|---|---|---|
| **UXI Sensor** | each sensor host | offline → CRIT, online-but-not-testing → WARN | `uxi_active_issues` |
| **UXI Issues** | each sensor host | worst issue severity (INFO/WARNING → WARN, ERROR/CRITICAL → CRIT); issues listed in details | `uxi_issues_info/warning/error/critical` |
| **UXI Metrics** | each sensor host | OK unless levels configured | DHCP/DNS/association/EAP/auth latency, channel utilisation, RSSI, RX/TX data rates |
| **UXI Fleet** | plugin host | any sensor offline → WARN | totals: online/offline/issues |

Sensors that are offline (or not testing) produce no `uxi_metrics` section and
so get no **UXI Metrics** service — expected, since they aren't running tests.

## Install

Everything below was performed and verified on a live site. Substitute your
site name for `cmk`.

### 1. Prerequisites
```bash
python3 -c "import requests" || sudo pip3 install requests
# boto3 is ONLY needed if you use AWS Secrets Manager for credentials
```

### 2. Deploy the collector
```bash
sudo git clone https://github.com/jtklabs/cape.git /opt/cape
```

### 3. Credentials — pick ONE

**(a) No AWS access** (typical for on-prem sites). Create a root-only env file;
the agent plugin sources it automatically:
```bash
sudo mkdir -p /etc/cape && sudo chmod 700 /etc/cape
sudo tee /etc/cape/uxi.env >/dev/null <<'EOF'
UXI_CLIENT_ID=your-client-id
UXI_CLIENT_SECRET=your-client-secret
EOF
sudo chmod 600 /etc/cape/uxi.env && sudo chown root:root /etc/cape/uxi.env
```

**(b) AWS Secrets Manager.** Leave `/etc/cape/uxi.env` absent and set these in
the agent plugin wrapper instead:
```bash
export UXI_SECRET_ID="test_secrets"    # may be a shared multi-app secret
export AWS_REGION="us-east-1"
```
The secret is a JSON key/value map; keys default to `uxi_client_id` /
`uxi_client_secret` (falling back to `client_id` / `client_secret`).
The agent runs as **root**, so root needs AWS credentials.

### 4. Install the agent plugin
```bash
sudo mkdir -p /usr/lib/check_mk_agent/plugins/300
sudo cp /opt/cape/checkmk/agent_plugin/uxi_piggyback /usr/lib/check_mk_agent/plugins/300/
sudo chmod +x /usr/lib/check_mk_agent/plugins/300/uxi_piggyback
```
`300` = a 5-minute cache interval, which keeps UXI API usage modest (the
collector makes ~51 status calls plus one query per metric). Verify:
```bash
sudo /usr/lib/check_mk_agent/plugins/300/uxi_piggyback | head -20
```
You should see `<<<uxi_fleet...>>>` followed by `<<<<uxi-...>>>>` blocks.
Empty output means credentials failed — rerun without `2>/dev/null` to see why.

### 5. Install the check plugins
```bash
sudo -u cmk mkdir -p /omd/sites/cmk/local/lib/python3/cmk_addons/plugins/uxi/agent_based
sudo -u cmk cp /opt/cape/checkmk/cmk_addons_plugins/uxi/agent_based/uxi.py \
    /omd/sites/cmk/local/lib/python3/cmk_addons/plugins/uxi/agent_based/
sudo -iu cmk cmk -L | grep '^uxi_'      # expect: uxi_fleet uxi_issues uxi_metrics uxi_sensor
```

### 6. Create the hosts (DCD)
```bash
sudo -iu cmk python3 /opt/cape/checkmk/setup_dcd.py --site cmk --source-host checkmk
sudo -iu cmk cmk -O
sudo -iu cmk omd restart dcd
```
`--source-host` is the Checkmk host whose agent carries the piggyback data —
i.e. the host you installed the agent plugin on.

`setup_dcd.py` is idempotent and creates three things: the WATO folder
`UXI Sensors`, a DCD piggyback connection that auto-creates/removes `uxi-*`
hosts, and rules suppressing host+service notifications for that folder so a
first rollout can't page anyone. **Remove that suppression once you've triaged
the fleet** — delete the `UXI-NOTIFY-SUPPRESSION` block from
`etc/check_mk/conf.d/wato/rules.mk`. Pass `--no-suppress-notifications` to skip
it entirely.

### 7. Verify
```bash
sudo -iu cmk cmk --list-hosts | grep -c '^uxi-'
sudo -iu cmk bash -lc 'printf "GET services\nFilter: host_name ~ ^uxi-\nColumns: description state\n" | lq' \
  | sort | uniq -c
```
Hosts appear within ~60s of the DCD restart. If services are missing, run
discovery explicitly:
```bash
sudo -iu cmk cmk -II $(sudo -iu cmk cmk --list-hosts | grep '^uxi-' | tr '\n' ' ')
sudo -iu cmk cmk -O
```

## Uninstall
```bash
sudo -iu cmk python3 /opt/cape/checkmk/uninstall.py --site cmk --dry-run   # preview
sudo -iu cmk python3 /opt/cape/checkmk/uninstall.py --site cmk
sudo -iu cmk cmk -O && sudo -iu cmk omd restart dcd
```
That removes the DCD connection, the folder and its hosts, and the
notification rules. Then remove the files and data:
```bash
sudo rm -f /usr/lib/check_mk_agent/plugins/300/uxi_piggyback
sudo rm -rf /etc/cape /opt/cape
sudo rm -rf /omd/sites/cmk/local/lib/python3/cmk_addons/plugins/uxi
sudo rm -f /var/lib/check_mk_agent/cache/plugins_uxi_piggyback.cache
sudo sh -c 'rm -rf /omd/sites/cmk/tmp/check_mk/piggyback/uxi-*'
sudo sh -c 'rm -rf /omd/sites/cmk/var/check_mk/rrd/uxi-*'
```

## Troubleshooting

**Changes to the collector don't show up.** Piggyback output is cached in
*three* places. Clear all of them:
```bash
sudo rm -f /var/lib/check_mk_agent/cache/plugins_uxi_piggyback.cache   # agent plugin cache
sudo rm -f /omd/sites/cmk/tmp/check_mk/cache/checkmk                   # site fetch cache
sudo sh -c 'rm -rf /omd/sites/cmk/tmp/check_mk/piggyback/uxi-*'        # piggyback spool
```
Then trigger the agent and wait for the async plugin to finish before
re-fetching (it takes ~60s for 51 sensors):
```bash
sudo check_mk_agent >/dev/null; sleep 60; sudo -iu cmk cmk -n checkmk >/dev/null
```

**Globs under the site tmp directory silently match nothing.** A non-root shell
can't read those directories to expand `uxi-*`, so `sudo rm -rf .../uxi-*`
becomes a literal no-op. Always wrap: `sudo sh -c 'rm -rf .../uxi-*'`.

**`Check_MK Discovery` shows WARN right after rollout.** Stale — it clears on
its next scheduled run. Confirm with `cmk --check-discovery <host>`.

**`omd restart dcd` says "site does not exist".** Run it as the site user
(`sudo -iu cmk omd restart dcd`), not as root.

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
