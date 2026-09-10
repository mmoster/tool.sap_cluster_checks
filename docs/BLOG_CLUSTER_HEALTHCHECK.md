# SAP HA Cluster Health Check: Automated Analysis for Pacemaker Clusters

Managing SAP HANA High Availability clusters on Pacemaker/Corosync is complex. A single misconfiguration — a missing STONITH device, incorrect quorum settings, or a broken HA/DR hook — can mean the difference between a seamless failover and an outage. Troubleshooting these issues typically requires deep knowledge and hours of sifting through `crm_mon` output, `corosync.conf`, and SAP-specific log files.

**sap-cluster-checks** is an open-source tool that automates this entire process. It runs 22 automated health checks against your SAP HANA Pacemaker cluster, generates PDF reports, and works with live clusters, remote SSH connections, or offline SOSreport analysis — all from a single Python script with no heavyweight dependencies.

**GitHub:** [https://github.com/mmoster/tool.sap_cluster_checks](https://github.com/mmoster/tool.sap_cluster_checks)

---

## At a Glance

- **22 built-in health checks** covering cluster configuration, Pacemaker/Corosync, and SAP-specific validations
- **Multiple access methods**: local execution, SSH, Ansible inventory, or SOSreport analysis
- **Smart auto-discovery**: provide a single node, the tool discovers the entire cluster
- **PDF reports**: auto-generated, with optional verbose mode for audits
- **Multithreaded execution**: parallel node checks and rule evaluation
- **Supports RHEL 8, 9, and 10** with SAP HANA Scale-Up and Scale-Out topologies
- **Extensible**: add custom checks as YAML rule files — no Python coding required
- **Automation-ready**: designed for cron jobs, CI/CD pipelines, and non-interactive environments

---

## Quick Start

### Installation

```bash
# Option 1: Clone with git (recommended)
git clone https://github.com/mmoster/tool.sap_cluster_checks.git
cd tool.sap_cluster_checks

# Option 2: Download without git
curl -L https://github.com/mmoster/tool.sap_cluster_checks/archive/refs/heads/main.tar.gz | tar xz
cd tool.sap_cluster_checks-main
```

**Requirements:**
- Python 3.6+ (included in RHEL 8/9/10)
- PyYAML (`pip install pyyaml` or `dnf install python3-pyyaml`)
- fpdf2 (optional, for PDF reports): `pip install fpdf2`

### First Run

```bash
# On a cluster node — the simplest way
./sap_cluster_checks.py --local

# Remote check via SSH (auto-discovers all cluster members from one node)
./sap_cluster_checks.py hana01

# Analyze SOSreports offline
./sap_cluster_checks.py -s /path/to/sosreports/
```

That's it. The tool discovers the cluster topology, runs all checks, and generates a PDF report.

---

## How It Works

### Smart Cluster Auto-Discovery

You don't need to know every node in your cluster. Provide a single seed node, and the tool automatically discovers all cluster members from the Pacemaker configuration:

```bash
# Just provide one node — the tool finds the rest
./sap_cluster_checks.py hana01

# The tool discovers hana02 (and any other members)
# and runs checks across the entire cluster
```

This works across all access methods — SSH, local execution, and SOSreport analysis. When analyzing SOSreports, it reads the `corosync.conf` inside the archive to identify all cluster members and resolves hostname aliases automatically.

### Multi-Cluster Support

When multiple clusters are discovered (e.g., from an Ansible inventory with several SAP systems), the tool prompts you to select which one to analyze. Previously discovered clusters are cached and can be reused:

```bash
# Use a previously discovered cluster
./sap_cluster_checks.py -C mycluster

# Show all discovered clusters
./sap_cluster_checks.py --show-config
```

---

## The 22 Health Checks

Every check has a severity level: **CRITICAL** (must-fix for production), **WARNING** (should investigate), or **INFO** (informational).

### Cluster Configuration Checks

| Check ID | Severity | What It Validates |
|----------|----------|-------------------|
| CHK_CLUSTER_READY | WARNING | Cluster is fully started, not in transition |
| CHK_CLUSTER_TYPE | INFO | Scale-Up vs Scale-Out configuration detection |
| CHK_NODE_STATUS | CRITICAL | All cluster nodes are online |
| CHK_CLUSTER_QUORUM | CRITICAL | Cluster has quorum |
| CHK_QUORUM_CONFIG | CRITICAL | Quorum configuration matches best practices |
| CHK_CLONE_CONFIG | CRITICAL | Clone/promotable resource configuration is correct |
| CHK_SETUP_VALIDATION | CRITICAL | SAP HANA HA best practices compliance |
| CHK_CIB_TIME_SYNC | WARNING | CIB updates are synchronized across nodes |
| CHK_PACKAGE_CONSISTENCY | WARNING | Package versions match across all nodes |

### Pacemaker/Corosync Checks

| Check ID | Severity | What It Validates |
|----------|----------|-------------------|
| CHK_STONITH_CONFIG | CRITICAL | STONITH/fencing is enabled and configured |
| CHK_RESOURCE_STATUS | CRITICAL | SAP HANA resources are running |
| CHK_RESOURCE_FAILURES | WARNING | No failed resource operations |
| CHK_ALERT_FENCING | WARNING | SAPHanaSR alert and fencing configuration |
| CHK_PROMOTED_ROLES | CRITICAL | Promoted/unpromoted role consistency |
| CHK_MAJORITY_MAKER | CRITICAL | Majority maker constraints (Scale-Out only) |

### SAP-Specific Checks

| Check ID | Severity | What It Validates |
|----------|----------|-------------------|
| CHK_HANA_INSTALLED | INFO | HANA installation, SID, instance, sidadm user, running status |
| CHK_HANA_SR_STATUS | CRITICAL | HANA System Replication is healthy |
| CHK_REPLICATION_MODE | WARNING | Replication mode is sync or syncmem |
| CHK_HADR_HOOKS | CRITICAL | HA/DR provider hooks are properly configured |
| CHK_HANA_AUTOSTART | WARNING | HANA autostart is disabled (cluster manages startup) |
| CHK_SYSTEMD_SAP | WARNING | SAP Host Agent and systemd integration |
| CHK_SITE_ROLES | CRITICAL | Site roles consistency |

---

## Usage Examples

### Example 1: Local Cluster Check

Run directly on a cluster node — the most common approach:

```bash
./sap_cluster_checks.py --local
```

The tool detects it's running on a cluster node, discovers all members from Pacemaker, and runs the full check suite.

### Example 2: Remote Check via SSH

Specify one or more hostnames. The tool checks SSH connectivity (with a 2-second TCP port pre-check to skip unreachable nodes quickly) and discovers the complete cluster from any reachable node:

```bash
# Specify nodes directly
./sap_cluster_checks.py hana01 hana02

# Or use a hosts file
./sap_cluster_checks.py -H hosts.txt

# Or filter by Ansible inventory group
./sap_cluster_checks.py -g sap_cluster
```

### Example 3: Offline SOSreport Analysis

For support engineers and consultants, or when you need to analyze a cluster state from a specific point in time:

```bash
./sap_cluster_checks.py -s /path/to/sosreports/
```

Supported formats: `.tar.xz`, `.tar.gz`, `.tar.bz2`, and plain `.tar` — all auto-extracted in parallel.

### Example 4: Complete SOSreport Collection Workflow

A single command that discovers the cluster, configures SAP-specific SOSreport extensions, creates SOSreports on all nodes in parallel, and fetches them via SCP:

```bash
# Provide any cluster node — all others are discovered
./sap_cluster_checks.py -R hana01

# Auto-configure SAP extensions without prompting
./sap_cluster_checks.py -R hana01 --configure-extensions
```

The SAP extensions add critical data that is often missing in default SOSreports:
- `SAPHanaSR-showAttr` output (essential for SR analysis)
- Cluster state snapshots (`crm_mon`, `pcs status`)
- Resource and constraint configurations

### Example 5: Interactive Mode

Don't remember where your SOSreports are? Use interactive mode to scan the directory and choose:

```bash
./sap_cluster_checks.py -u
```

```
===============================================================
 Scanning for resources...
===============================================================

---------------------------------------------------------------
 Found Resources
---------------------------------------------------------------
  SOSreports (extracted):   2
  Hosts files:              1
  Former results:           1
  PDF reports:              4

---------------------------------------------------------------
 Options
---------------------------------------------------------------
  [d] Delete former results and config, then run health check
  [c] Continue with existing configuration
  [s] Analyze sosreports in ./sosreports
  [i] Use inventory/hosts file: ./hosts.txt
  [f] Fetch SOSreports from cluster
  [n] Enter hostnames manually
  [l] Run locally (on this cluster node)
  [h] Show help and examples
  [q] Quit
---------------------------------------------------------------

  Your choice:
```

---

## Understanding the Output

### Healthy Cluster

```
Health Check Results:
  PASSED:   22  FAILED:   0  SKIPPED:   0  ERROR:   0

  ╔═══════════════════════════════════════════════════════╗
  ║            ✓  CLUSTER IS HEALTHY  ✓                   ║
  ╚═══════════════════════════════════════════════════════╝

  PDF report saved: health_check_report_mycluster_1507.pdf
```

### Problem Detected

```
  ✗ FAILED: CHK_STONITH_CONFIG
    STONITH is disabled - fencing is required for production clusters
    Severity: CRITICAL
```

### PDF Reports

The tool auto-generates a PDF report containing:
- Cluster metadata (name, nodes, cluster type, data source)
- RHEL and Pacemaker version information
- SAP HANA SID and instance detection
- Summary counts (PASSED / FAILED / SKIPPED / ERROR)
- Detailed results table with check ID, node, status, severity, and messages

Use `-v` for **verbose PDF reports** that document every single check with full details — not just failures. This is ideal for audits, compliance reviews, or handover documentation:

```bash
./sap_cluster_checks.py --local -v
```

---

## Built-In Intelligence

The tool adapts to your environment automatically:

- **RHEL & Pacemaker version detection**: Reads `/etc/redhat-release` and the installed Pacemaker RPM. Checks are tailored to RHEL 8, 9, or 10. Documentation references in the PDF report point to the correct RHEL version.
- **Cluster type detection**: Distinguishes Scale-Up from Scale-Out based on the `clone-max` value in the CIB. Checks are filtered accordingly — Scale-Out-only checks (like majority maker validation) are skipped on Scale-Up clusters.
- **Architecture detection**: Identifies whether you're running the modern ANGI resource agent (`sap-hana-ha`) or the legacy agents (`resource-agents-sap-hana`), skipping checks that don't apply.
- **HANA SID & instance discovery**: Detects the `sidadm` user, SID, instance number, and checks whether the database is running.
- **Cluster status awareness**: If Pacemaker or Corosync isn't running, the tool warns you and falls back to static analysis from `corosync.conf`.
- **Hostname alias resolution**: When analyzing SOSreports, resolves mismatches between Corosync node names and system hostnames by cross-referencing IPs in `/etc/hosts`.
- **TCP port pre-check**: Checks if SSH port 22 is reachable (2-second timeout) before attempting login. Unreachable nodes are skipped immediately.

---

## Built-In Guidance

New to the tool or troubleshooting a failed check? The built-in guidance system helps you understand what to do next:

```bash
# Show a detailed usage guide with examples
./sap_cluster_checks.py --guide

# Show installation guide
./sap_cluster_checks.py --install

# Get suggestions for the first failing step from the last run
./sap_cluster_checks.py --suggest

# Get suggestions for a specific step
./sap_cluster_checks.py --suggest pacemaker

# List all health check steps with descriptions
./sap_cluster_checks.py --list-steps
```

The `--suggest` feature analyzes your last run results and provides targeted remediation advice for failing checks — saving you from searching through documentation manually.

---

## Automation & Cronjob Support

sap-cluster-checks is designed to run unattended:

- **Auto-timeout prompts**: All interactive prompts skip automatically after 20 seconds
- **Non-TTY detection**: When stdin is not a terminal (cron, pipes), interactive prompts are skipped
- **Spinner suppression**: Progress animations are disabled when output is redirected
- **Machine-readable output**: Results saved as YAML (`last_run_status.yaml`) for monitoring tools
- **Configurable workers**: Control parallelism with `--workers N` (default: 10)
- **Strict mode**: Use `--strict` to make all checks required (optional checks become failures instead of warnings)

Example cronjob for weekly health checks:

```bash
# Run every Monday at 6:00 AM
0 6 * * 1 /opt/sap-cluster-checks/sap_cluster_checks.py --local \
    --no-update-check --no-pdf >> /var/log/sap_healthcheck.log 2>&1
```

---

## Ansible Integration

Export your discovered cluster configuration as Ansible group_vars YAML for use in playbooks:

```bash
# Export cluster config as Ansible-compatible YAML
./sap_cluster_checks.py --export-ansible mycluster output.yml
```

You can also scope checks to a specific Ansible inventory group:

```bash
./sap_cluster_checks.py -g sap_hana_cluster
```

---

## Extending with Custom Checks

Health checks are defined as YAML rule files in `rules/health_checks/CHK_*.yaml`. The architecture separates:

1. **Data Collection** — how to get data (commands to run or files to read)
2. **Parsing** — how to extract values from command output
3. **Validation** — what conditions to check

This means the same validation logic works on both live systems and SOSreports. Adding a new check requires no Python coding — just create a YAML file following the rule format.

The dispatch manifest (`rules/check_dispatch.yaml`) controls:
- Which checks run in which step and phase
- Topology filtering (Scale-Up vs Scale-Out)
- Dependency gates (e.g., "only run HANA SR check if HANA is installed")

See [EXTENDING_HEALTH_CHECKS.md](EXTENDING_HEALTH_CHECKS.md) for the full technical guide.

---

## Complete Command Reference

```
./sap_cluster_checks.py [OPTIONS] [NODES...]
```

### Core Options

| Option | Description |
|--------|-------------|
| `hosts` | Hostname(s) to check (e.g., `hana01 hana02`) |
| `--local, -l` | Run on cluster node itself (local commands instead of SSH) |
| `-H, --hosts-file FILE` | File containing list of hosts (one per line) |
| `-s, --sosreport-dir DIR` | Directory containing SOSreport archives/directories |
| `-g, --group GROUP` | Only check hosts from this Ansible inventory group |
| `-C, --cluster NAME` | Use saved cluster by name (from previous discovery) |
| `-u, --usage` | Interactive mode — scan directory for resources |

### Output & Reporting

| Option | Description |
|--------|-------------|
| `-v, --verbose` | Verbose PDF — show all checks in detail (not just failures) |
| `--no-pdf` | Skip PDF report generation |
| `-d, --debug` | Enable debug mode (show config files and step progress) |
| `--strict` | Strict mode — all checks required, optional checks become errors |

### Discovery & Configuration

| Option | Description |
|--------|-------------|
| `-f, --force` | Force rediscovery (ignore existing cached config) |
| `-a, --access-only` | Only run access discovery step |
| `-S, --show-config [NAME]` | Display configuration (optionally filter by cluster or node) |
| `-D, --delete-reports` | Delete report files (keeps node access config) |
| `-c, --config-dir DIR` | Directory to store configuration (default: `./`) |
| `-E, --export-ansible CLUSTER [FILE]` | Export cluster config as Ansible group_vars YAML |

### SOSreport Collection

| Option | Description |
|--------|-------------|
| `-F, --fetch-sosreports [ARGS]` | Fetch SOSreports from nodes (prompts to create if missing) |
| `--create-sosreports` | Auto-create SOSreports on nodes where missing (use with `-F`) |
| `-R, --collect-sosreports NODE` | Full workflow: discover, configure extensions, create & fetch |
| `--configure-extensions` | Auto-configure SAP SOSreport extensions (use with `-R`) |

### Checks & Rules

| Option | Description |
|--------|-------------|
| `-L, --list-rules` | List available health check rules and exit |
| `-r, --rules-path DIR` | Path to custom rules directory |
| `--skip STEPS` | Skip specific steps (`access`, `config`, `pacemaker`, `sap`, `report`) |
| `-w, --workers N` | Number of parallel workers (default: 10) |

### Guidance & Help

| Option | Description |
|--------|-------------|
| `-G, --guide` | Show detailed usage guide with examples and next steps |
| `-i, --install` | Show installation guide |
| `--suggest [STEP]` | Show suggestions for a step (default: first failing step) |
| `--suggest-skip STEPS` | Skip these steps when auto-suggesting |
| `--list-steps` | List all health check steps with descriptions |
| `--no-update-check` | Skip checking for software updates |

---

## Supported Cluster Topologies

### Scale-Up (2 HANA nodes + optional additional cluster nodes)
- Package: `sap-hana-ha` (ANGI, RHEL 9+) or `resource-agents-sap-hana` (classic)
- Resource agent: `SAPHana`
- Additional cluster nodes (e.g., ASCS servers) are correctly identified and don't cause check failures

### Scale-Out (multiple HANA nodes + 1 majority maker)
- HANA nodes: `sap-hana-ha` (ANGI) or `resource-agents-sap-hana-scaleout` (classic)
- Majority maker: validated for correct constraint configuration
- Resource agent: `SAPHanaController`

---

## Tips & Best Practices

1. **First run takes longer** — it discovers and caches cluster topology. Subsequent runs reuse the cache.
2. **Use `-f` to force re-discovery** if cluster nodes changed.
3. **SOSreport analysis is safe** — completely offline, no SSH access needed.
4. **Use `-v` for audit-ready PDFs** — documents every check, not just failures.
5. **Cluster not running?** — the tool detects this and falls back to static configuration analysis.
6. **Multiple clusters?** — the tool prompts you to select which one to analyze.
7. **Use `--suggest`** after a failed run to get targeted remediation advice.
8. **Share PDF reports** with support teams or attach to audit documentation.

---

## Getting Started

```bash
git clone https://github.com/mmoster/tool.sap_cluster_checks.git
cd tool.sap_cluster_checks
./sap_cluster_checks.py --local
```

- **GitHub Repository:** [github.com/mmoster/tool.sap_cluster_checks](https://github.com/mmoster/tool.sap_cluster_checks)
- **How-To Guide:** [BLOG_HOWTO.md](BLOG_HOWTO.md)
- **Extending Health Checks:** [EXTENDING_HEALTH_CHECKS.md](EXTENDING_HEALTH_CHECKS.md)
- **License:** Apache License 2.0

---

*sap-cluster-checks is an open-source project. Contributions, feedback, and feature requests are welcome — open an issue or submit a pull request on GitHub.*
