# Checkmk integration (piggyback)

`python -m cape checkmk` prints Checkmk agent output where every UXI sensor
becomes its own **piggyback host**:

```
<<<uxi_fleet:sep(0)>>>                 <- lands on the host running the plugin
{"total": 51, "online": 36, "offline": 15, "active_issues": 49, ...}
<<<<uxi-Louisville>>>>                 <- piggyback block, one per sensor
<<<uxi_sensor:sep(0)>>>
{"name": "Louisville", "serial": "CNP3KYT0B5", "isOnline": true, ...}
<<<uxi_issues:sep(0)>>>
{"code": "HIGH_DHCP_RESPONSE_TIME", "severity": "INFO", ...}
<<<<>>>>
```

## Services created

| Service | Where | Logic | Metrics |
|---|---|---|---|
| **UXI Sensor** | each sensor host | offline → CRIT, online-but-not-testing → WARN | `uxi_active_issues` |
| **UXI Issues** | each sensor host | worst issue severity (default INFO/WARNING → WARN, ERROR/CRITICAL → CRIT); issue list in details | `uxi_issues_info/warning/error/critical` |
| **UXI Fleet** | plugin host | any sensor offline → WARN | totals: online/offline/issues |

## Install

### 1. Collector (agent plugin) — on the Checkmk server (or any agented host)
```bash
sudo mkdir -p /opt/cape && sudo cp -r <repo>/cape /opt/cape/
sudo pip3 install requests boto3       # or a venv; adjust PYTHON in the wrapper
sudo mkdir -p /usr/lib/check_mk_agent/plugins/300
sudo cp <repo>/checkmk/agent_plugin/uxi_piggyback /usr/lib/check_mk_agent/plugins/300/
sudo chmod +x /usr/lib/check_mk_agent/plugins/300/uxi_piggyback
# edit the env vars in the wrapper (secret id, regions, CAPE_DIR)
```
The `300` directory = 5-minute execution interval (cached between agent polls),
which keeps UXI API usage modest. Verify with:
```bash
sudo /usr/lib/check_mk_agent/plugins/300/uxi_piggyback | head -20
```

### 2. Check plugins — on the Checkmk site (2.3+, agent_based v2)
```bash
sudo -u <site> mkdir -p /omd/sites/<site>/local/lib/python3/cmk_addons/plugins/uxi/agent_based
sudo -u <site> cp <repo>/checkmk/cmk_addons_plugins/uxi/agent_based/uxi.py \
    /omd/sites/<site>/local/lib/python3/cmk_addons/plugins/uxi/agent_based/
# validate:
sudo -u <site> cmk --debug --detect-plugins=uxi_sensor -L | grep uxi
```
(Checkmk 2.2 uses the v1 API under
`local/lib/check_mk/base/plugins/agent_based/` — the plugin needs import
changes for 2.2; targeted here: 2.3/2.4.)

### 3. Create the sensor hosts
Either **Dynamic host management** (recommended): Setup → Hosts → Dynamic host
management → add a **piggyback connector** — sensor hosts (`uxi-<name>`) are
created/removed automatically. Set "no IP" / piggyback-only defaults.
Or create hosts manually with names matching the piggyback names
(`python -m cape checkmk | grep '<<<<'` to list them). Hosts must have
**no agent / no IP** (piggyback data only).

### 4. Discover services
Run service discovery on the new hosts (DCD does this automatically), then
activate changes. Sensor data is refreshed each time the plugin runs;
piggyback data older than the Checkmk default validity goes stale → services
turn UNKNOWN, which catches a broken collector.

## Host naming
Default: sanitized sensor name with `uxi-` prefix (`uxi-Floor-24-CapeSensor-1`).
Prefer serials (stable across renames): `--host-field serial`. Name collisions
get the serial appended automatically.

## Notes
- All timestamps/issue context come from the UXI status endpoint; historical
  time-series metrics are not available by pull (see main README), so graphs
  build up in Checkmk from each 5-minute sweep — issue counts, online state,
  fleet totals.
- The `UXI Issues` severity→state mapping is overridable via the
  `uxi_issues` ruleset (`severity_states`, e.g. `{"INFO": 0}` to keep INFO OK).
