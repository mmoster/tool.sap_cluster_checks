# Test Sequence: SAP HANA Pacemaker Cluster Health Check

The tool executes checks in **5 sequential steps**. Within each step, checks are organized into **phases** - checks within the same phase run in **parallel**, while phases execute **sequentially** (because later phases may depend on results from earlier ones).

## Step 1: Access Discovery

Discovers and validates access to cluster nodes.
Methods: local, SSH, Ansible inventory, or SOSreport archives.
Auto-discovers all cluster members from a single seed node.

No health checks - establishes connectivity for Steps 2-4.

## Step 2: Cluster Configuration

### Phase 1 (parallel)

| #  | Check ID                | Severity | Scope           | Description                                               |
|----|-------------------------|----------|-----------------|-----------------------------------------------------------|
|  1 | CHK_CLUSTER_TYPE        | INFO     | cluster         | Detect Scale-Up vs Scale-Out configuration                |
|  2 | CHK_NODE_STATUS         | CRITICAL | cluster         | Verify all cluster nodes are online                       |
|  3 | CHK_CLUSTER_QUORUM      | CRITICAL | cluster         | Verify cluster has quorum                                 |
|  4 | CHK_CLUSTER_READY       | WARNING  | cluster         | Check cluster is fully started (not in transition)        |
|  5 | CHK_CIB_TIME_SYNC       | WARNING  | cluster         | Verify CIB is synchronized across nodes                  |
|  6 | CHK_PACKAGE_CONSISTENCY | WARNING  | all_nodes_equal | Verify package versions consistent across nodes           |
|  7 | CHK_SETUP_VALIDATION    | CRITICAL | cluster         | Validate against SAP HANA HA best practices               |
|  8 | CHK_CLONE_CONFIG        | CRITICAL | cluster         | Validate SAPHana clone/promotable resource configuration  |
|  9 | CHK_QUORUM_CONFIG       | CRITICAL | all_nodes_equal | Validate quorum configuration (Scale-Up only)             |

> **Note:** CHK_QUORUM_CONFIG only runs for Scale-Up clusters.

## Step 3: Pacemaker/Corosync

### Phase 1 (parallel)

| #  | Check ID                | Severity | Scope   | Description                                               |
|----|-------------------------|----------|---------|-----------------------------------------------------------|
| 10 | CHK_STONITH_CONFIG      | CRITICAL | cluster | Verify STONITH/fencing is enabled and configured          |
| 11 | CHK_RESOURCE_STATUS     | CRITICAL | cluster | Verify SAP HANA cluster resources are running             |
| 12 | CHK_RESOURCE_FAILURES   | WARNING  | cluster | Detect failed resource operations in cluster history      |
| 13 | CHK_ALERT_FENCING       | WARNING  | cluster | Validate SAPHanaSR alert and fencing configuration        |
| 14 | CHK_MAJORITY_MAKER      | CRITICAL | cluster | Validate majority maker configuration (Scale-Out only)    |

> **Note:** CHK_MAJORITY_MAKER only runs for Scale-Out clusters.

### Phase 2 (parallel) - gate: hana_resource_running

Skipped if HANA resource is not running (stopped/disabled/unmanaged).

| #  | Check ID                | Severity | Scope   | Description                                               |
|----|-------------------------|----------|---------|-----------------------------------------------------------|
| 15 | CHK_PROMOTED_ROLES      | CRITICAL | cluster | Verify exactly one promoted and one unpromoted in cluster |

## Step 4: SAP-Specific

### Phase 1 (parallel)

| #  | Check ID                | Severity | Scope    | Description                                               |
|----|-------------------------|----------|----------|-----------------------------------------------------------|
| 16 | CHK_HANA_INSTALLED      | INFO     | per_node | Detect HANA installation, SID, instance, sidadm, status   |

### Phase 2 (parallel) - gate: hana_installed

Skipped entirely if no HANA installation was detected in Phase 1.

| #  | Check ID                | Severity | Scope    | Description                                               |
|----|-------------------------|----------|----------|-----------------------------------------------------------|
| 17 | CHK_HANA_SR_STATUS      | CRITICAL | cluster  | Verify HANA System Replication status is healthy          |
| 18 | CHK_REPLICATION_MODE    | WARNING  | cluster  | Verify replication mode is sync or syncmem                |
| 19 | CHK_HADR_HOOKS          | CRITICAL | per_node | Validate HA/DR provider hooks configuration               |
| 20 | CHK_HANA_AUTOSTART      | WARNING  | per_node | Validate HANA autostart is disabled                       |
| 21 | CHK_SYSTEMD_SAP         | WARNING  | per_node | Validate SAP Host Agent and systemd integration           |
| 22 | CHK_SITE_ROLES          | CRITICAL | cluster  | Verify site roles consistency (one primary, one secondary)|

> **Notes:**
> - CHK_HANA_SR_STATUS - additional gate: `hana_resource_running`
> - CHK_HADR_HOOKS - additional gate: `not_legacy_scaleup`; runs on HANA nodes only
> - CHK_SITE_ROLES - additional gate: `hana_resource_running`

## Step 5: Health Check Report

Generates summary of all check results.
Outputs: YAML summary + optional PDF report (requires fpdf2).
Verbose mode (`-v`) includes all checks, not just failures.

## Execution Model

- **Steps** execute sequentially (1 → 2 → 3 → 4 → 5)
- **Phases** within a step execute sequentially (phase 1 → phase 2) - this allows phase 2 to use results from phase 1 (e.g., `CHK_RESOURCE_STATUS` must run before `CHK_PROMOTED_ROLES`)
- **Checks** within a phase execute in **parallel** (multithreaded) for performance
- **Gates** control conditional execution: if a gate evaluates to false, all checks behind it are skipped with an explanatory message
- **Topology filters** skip checks not applicable to the detected cluster type (e.g., `CHK_MAJORITY_MAKER` is skipped for Scale-Up)

## Read-Only Commands Used

All checks use **read-only** commands - no cluster or SAP configuration is ever modified:

| Command | Purpose |
|---------|---------|
| `crm_mon -1` | Cluster status snapshot |
| `crm_node -l` | List cluster nodes |
| `pcs status` | Pacemaker status |
| `pcs property` | Cluster properties |
| `pcs resource config` | Resource configuration |
| `pcs constraint location` | Location constraints |
| `pcs alert config` | Alert configuration |
| `pcs stonith config` | STONITH configuration |
| `pcs resource defaults` | Resource defaults |
| `pcs resource op defaults` | Operation defaults |
| `corosync-quorumtool -s` | Quorum status |
| `cat /etc/corosync/corosync.conf` | Corosync configuration (read) |
| `rpm -q` | Package version queries |
| `systemctl is-active` | Service status check |
| `systemctl show` | Service property query |
| `grep` / `cat` | File content reads |
| `SAPHanaSR-showAttr` | SAP HANA SR attributes (read-only) |
| `hdbnsutil -sr_state` | HANA replication state (read-only) |
| `saphostexec -status` | SAP Host Agent status |
