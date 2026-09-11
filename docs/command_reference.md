# SAP Cluster Health Check - Command Reference

This document lists all health check commands executed on the cluster nodes and evaluates their potential impact on the cluster, HANA, or SAP configuration.

- **Expert-Designed**: The commands and analytical methods are based on recommendations from human cluster experts rather than being AI-generated. (Claude and existing documentation were referenced solely to evaluate additional command parameters.)

- **Production-Safe**: The health check commands do not modify the cluster, HANA, or SAP configurations and can safely be run on live production systems.

- **Remote Processing Only**: The only extraction and processing tasks occur on separate remote systems designed to unpack and analyze the generated sosreport files.

---

## Analysis Result

> **None of the health check commands modify the cluster, HANA, or SAP configuration.**
>
> All commands are purely read-only and serve exclusively for diagnostics and status queries.
> No `pcs resource move`, `pcs cluster stop`, `hdbnsutil -sr_register`,
> `crm resource cleanup`, or similar modifying commands are used.

---

## Commands by Category

### 1. Cluster Status & Pacemaker

| Command | Description | Modifies System? |
|---------|-------------|:----------------:|
| `crm_mon -1` | Shows the current cluster status (one-shot) | **No** |
| `crm_mon -1 -r` | Cluster status including inactive resources | **No** |
| `crm_mon -1 -f` | Cluster status including fail counts | **No** |
| `crm_mon -A1` | Cluster status including node attributes | **No** |
| `crm_node -l` | Lists all cluster nodes | **No** |
| `pcs status` | Shows the cluster status | **No** |
| `pcs status nodes` | Shows the status of all nodes | **No** |
| `pcs status resources` | Shows the status of all resources | **No** |
| `pcs status --full` | Full cluster status | **No** |
| `pcs property` | Shows cluster properties | **No** |
| `pcs property show` | Shows cluster properties (alternative syntax) | **No** |
| `pcs property config` | Shows cluster property configuration | **No** |
| `pcs resource config` | Shows resource configuration | **No** |
| `pcs resource show` | Shows resources | **No** |
| `pcs resource defaults` | Shows default values for resources | **No** |
| `pcs resource op defaults` | Shows default values for resource operations | **No** |
| `pcs constraint location show` | Shows location constraints | **No** |
| `pcs constraint location config` | Shows location constraint configuration | **No** |
| `pcs constraint location show --full` | Shows constraints including resource-discovery | **No** |
| `pcs alert config` | Shows alert configuration | **No** |
| `pcs stonith status` | Shows STONITH status | **No** |
| `pcs stonith config` | Shows STONITH configuration | **No** |
| `pcs -f {cib.xml} ...` | Offline query against CIB XML file (SOSreport) | **No** |

### 2. Corosync / Quorum

| Command | Description | Modifies System? |
|---------|-------------|:----------------:|
| `corosync-quorumtool -s` | Shows quorum status | **No** |
| `cat /etc/corosync/corosync.conf` | Reads Corosync configuration | **No** |

### 3. SAP HANA System Replication

| Command | Description | Modifies System? |
|---------|-------------|:----------------:|
| `SAPHanaSR-showAttr` | Shows HANA SR attributes (replication status) | **No** |
| `su - <sid>adm -c 'hdbnsutil -sr_state'` | Shows SR status as <sid>adm user | **No** |
| `su - <sid>adm -c 'HDB info'` | Shows HANA instance information | **No** |

### 4. SAP HANA Installation & Configuration

| Command | Description | Modifies System? |
|---------|-------------|:----------------:|
| `ls -d /usr/sap/*/HDB[0-9][0-9]` | Checks whether HANA instances are installed | **No** |
| `grep ... /etc/passwd` | Searches for <sid>adm users | **No** |
| `cat /hana/shared/*/global/hdb/custom/config/global.ini` | Reads HANA global.ini (HADR hooks) | **No** |
| `cat /usr/sap/*/SYS/global/hdb/custom/config/global.ini` | Reads HANA global.ini (alternative path) | **No** |
| `grep -r 'Autostart' /usr/sap/*/SYS/profile/*_HDB*` | Checks HANA autostart setting in profiles | **No** |
| `grep 'Autostart' /sapmnt/*/profile/*` | Checks autostart in sapmnt profiles | **No** |
| `ls /usr/share/sap-hana-ha/HanaSR.py ...` | Checks whether HADR hook scripts are present | **No** |
| `su - <sid>adm -c 'which hdbnsutil'` | Checks whether hdbnsutil is available | **No** |

### 5. Systemd Services

| Command | Description | Modifies System? |
|---------|-------------|:----------------:|
| `systemctl is-active pacemaker` | Checks whether Pacemaker is running | **No** |
| `systemctl is-active corosync` | Checks whether Corosync is running | **No** |
| `systemctl is-active sapinit` | Checks whether SAP init service is running | **No** |
| `systemctl is-active saphostagent` | Checks whether SAP Host Agent is running | **No** |
| `systemctl show pacemaker --property=ActiveEnterTimestampMonotonic` | Reads Pacemaker start time | **No** |
| `/usr/sap/hostctrl/exe/saphostexec -status` | Checks SAP Host Agent status | **No** |

### 6. Package Queries

| Command | Description | Modifies System? |
|---------|-------------|:----------------:|
| `rpm -q pacemaker` | Checks installed Pacemaker version | **No** |
| `rpm -q corosync` | Checks installed Corosync version | **No** |
| `rpm -q resource-agents-sap-hana` | Checks SAP HANA resource agent | **No** |
| `rpm -q resource-agents-sap-hana-scaleout` | Checks scaleout resource agent | **No** |
| `rpm -q sap-hana-ha` | Checks sap-hana-ha package | **No** |
| `rpm -q SAPHanaSR` | Checks SAPHanaSR package | **No** |
| `rpm -qa \| grep -E 'sap-hana-ha\|resource-agents'` | Lists all SAP HA-related packages | **No** |

### 7. Sudoers & Permissions

| Command | Description | Modifies System? |
|---------|-------------|:----------------:|
| `cat /etc/sudoers.d/20-saphana` | Reads SAP sudoers configuration | **No** |
| `cat /etc/sudoers.d/*sap*` | Reads SAP-related sudoers files | **No** |
| `cat /etc/sudoers.d/*hana*` | Reads HANA-related sudoers files | **No** |

### 8. System Information

| Command | Description | Modifies System? |
|---------|-------------|:----------------:|
| `cat /etc/redhat-release` | Reads OS version | **No** |
| `cat /proc/uptime` | Reads system uptime | **No** |
| `ps -ef \| grep hdb...` | Checks running HANA processes | **No** |

### 9. Access Discovery

| Command | Description | Modifies System? |
|---------|-------------|:----------------:|
| `ssh -o BatchMode=yes -o ConnectTimeout=10 <user>@<node> '<cmd>'` | Executes commands remotely via SSH | **No** ¹ |
| `ansible <node> -m shell -a '<cmd>'` | Executes commands remotely via Ansible | **No** ¹ |
| `ansible-inventory --list --yaml` | Reads Ansible inventory | **No** |

¹ *SSH/Ansible calls are transport wrappers only. The actual commands executed are the read-only queries listed above.*

---

## Local Operations (only on the machine running the health check)

These operations do **not** affect the cluster, HANA, or SAP, but only the local environment:

| Command | Description | What is modified? |
|---------|-------------|-------------------|
| `tar x[Jzj]f <archive>` | Extracts SOSreport archives | Local directories are created |
| `git fetch --quiet` | Checks for tool updates | Updates local git references |
| `git pull` | Updates the health check tool | Updates the tool code itself (only after user confirmation) |
| `xdg-open / open` | Opens PDF report in viewer | Launches a local application |

---

## Health Checks Overview

| Check ID | Description | Main Commands |
|----------|-------------|---------------|
| CHK_CLUSTER_READY | Checks whether the cluster is fully started | `crm_mon -1`, `systemctl show pacemaker` |
| CHK_CLUSTER_QUORUM | Checks the quorum status | `crm_mon -1`, `corosync-quorumtool -s` |
| CHK_CLUSTER_TYPE | Detects scale-up vs. scale-out cluster | `crm_node -l`, `pcs resource`, `hdbnsutil -sr_state` |
| CHK_NODE_STATUS | Checks the status of all cluster nodes | `crm_mon -1 -r`, `pcs status nodes` |
| CHK_RESOURCE_STATUS | Checks the status of all cluster resources | `crm_mon -1 -r`, `pcs status resources` |
| CHK_RESOURCE_FAILURES | Checks for resource errors and fail counts | `crm_mon -1 -f`, `pcs status --full` |
| CHK_STONITH_CONFIG | Checks STONITH/fencing configuration | `pcs property`, `pcs stonith config` |
| CHK_ALERT_FENCING | Checks alert and fencing configuration | `pcs alert config`, `pcs stonith status` |
| CHK_QUORUM_CONFIG | Checks Corosync quorum settings | `cat /etc/corosync/corosync.conf` |
| CHK_CLONE_CONFIG | Checks clone/promotable resource configuration | `pcs resource config` |
| CHK_SETUP_VALIDATION | Validates cluster properties and defaults | `pcs property show`, `pcs resource defaults` |
| CHK_CIB_TIME_SYNC | Checks CIB timestamps and synchronization | `crm_mon -1` |
| CHK_HANA_INSTALLED | Checks whether SAP HANA is installed and active | `ls /usr/sap/*/HDB*`, `HDB info`, `ps -ef` |
| CHK_HANA_SR_STATUS | Checks the HANA System Replication status | `pcs status`, `SAPHanaSR-showAttr` |
| CHK_HANA_AUTOSTART | Checks the HANA autostart setting | `grep 'Autostart' .../profile/*` |
| CHK_HADR_HOOKS | Checks HADR provider hook configuration | `cat global.ini`, `cat sudoers.d/*`, `rpm -q` |
| CHK_REPLICATION_MODE | Checks the SR replication mode (sync/async) | `SAPHanaSR-showAttr`, `crm_mon -A1` |
| CHK_PROMOTED_ROLES | Checks promoted/unpromoted role assignment | `crm_mon -1`, `SAPHanaSR-showAttr` |
| CHK_SITE_ROLES | Checks site roles (primary/secondary) | `pcs status`, `crm_mon -1` |
| CHK_MAJORITY_MAKER | Checks majority maker configuration (scale-out) | `crm_node -l`, `pcs constraint location` |
| CHK_PACKAGE_CONSISTENCY | Checks package versions across all nodes | `rpm -q pacemaker corosync ...` |
| CHK_SYSTEMD_SAP | Checks SAP-related systemd services | `systemctl is-active sapinit/saphostagent` |

---

## Conclusion

The SAP Cluster Health Check is a **purely diagnostic tool**. All commands executed on the
cluster nodes are **exclusively read-only**. No modifications are made to any of the following:

- **Pacemaker/Corosync configuration** -- no `pcs resource move/ban/cleanup/delete`, no `pcs cluster stop/destroy`
- **HANA System Replication** -- no `hdbnsutil -sr_register/takeover/enable`, no `HDB stop/start`
- **SAP configuration** -- no profile changes, no service restarts
- **STONITH/Fencing** -- no fencing actions, no `pcs stonith enable/disable`
- **Systemd services** -- no `systemctl start/stop/restart`

The tool can be safely used on production systems.
