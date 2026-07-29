# SAP HANA Pacemaker Cluster Health Check

## Why This Tool?

Setting up and maintaining SAP HANA HA clusters on Pacemaker/Corosync is complex — there are dozens of configuration parameters, HA/DR provider hooks, fencing settings, and replication options that must all be correct for a reliable failover. Misconfigurations often go unnoticed until an actual failure occurs, when it's too late. This tool automates the validation of your cluster setup against SAP and Red Hat best practices, catching issues before they become outages.

It supports **RHEL 8, 9, and 10** with both classic (`resource-agents-sap-hana`) and modern ANGI (`sap-hana-ha`) resource agent packages, covering **Scale-Up** and **Scale-Out** topologies. Support for ASCS/ERS environments is planned for a future release.

## Who Can Use It?

This tool is designed for **SAP Basis administrators, Linux system administrators, and consultants** responsible for SAP HANA HA clusters. It can be run directly on a cluster node, remotely via SSH, or offline against SOSreport archives — no agent installation required. Whether you're doing an initial setup validation, a periodic health check, or troubleshooting a replication issue, this tool gives you a clear pass/fail report with actionable findings.

## Quick Start

### On a Cluster Node (Recommended)

```bash
# Clone and run locally
git clone https://github.com/mmoster/tool.sap_cluster_checks.git
cd tool.sap_cluster_checks
./sap_cluster_checks.py --local
```

### Remote Check

```bash
# Check specific nodes
./sap_cluster_checks.py hana01 hana02

# Use a hosts file
./sap_cluster_checks.py -H hosts.txt

# Analyze SOSreports offline
./sap_cluster_checks.py -s /path/to/sosreports/

# Verbose PDF (show all checks in detail, not just failures)
./sap_cluster_checks.py hana01 -v

# Collect SOSreports from cluster (auto-discovers all nodes)
./sap_cluster_checks.py -R hana01
```

### Interactive Mode

```bash
# Scan directory for sosreports/inventory/results
./sap_cluster_checks.py -u
```

This scans the current directory for:
- SOSreport archives (`.tar.xz`, `.tar.gz`, `.tar`)
- Hosts files (`hosts.txt`, `inventory`)
- Previous health check results (`.yaml`)

Then presents an interactive menu:

```
Found resources:
  [1] SOSreports: ./sosreports/ (3 archives)
  [2] Hosts file: ./hosts.txt (2 hosts)
  [3] Previous result: cluster2 (2026-04-16)
  [4] Run on local cluster node
  [5] Enter hostnames manually

Select option [1-5/q]:
```

This is useful when you don't remember where your SOSreports are stored or want a guided setup.

## Features

- **Multiple Access Methods**: Local execution, SSH, Ansible inventory, or SOSreport analysis
- **Multithreaded Execution**: Parallel node connectivity checks and rule execution
- **22 Built-in Health Checks**: Cluster configuration, Pacemaker/Corosync, and SAP-specific validations
- **Smart Auto-Discovery**: Provide a single seed node — the tool discovers all cluster members automatically from Pacemaker configuration
- **Cluster Status Detection**: Warns if cluster services are not running, falls back to static config
- **Multi-Cluster Support**: Prompts for selection when multiple clusters are discovered
- **Version Detection**: Automatically detects RHEL and Pacemaker versions
- **HANA Status Detection**: Identifies SID, instance, sidadm user, and running processes
- **Progress Indicators**: Animated spinner shows work is in progress
- **PDF Reports**: Auto-generated PDF reports with optional verbose mode (requires fpdf2)

## Installation

### Option 1: Using git (recommended)

```bash
# Install git if not available
sudo dnf install git    # RHEL/Fedora
sudo yum install git    # RHEL 7

# Clone and run
git clone https://github.com/mmoster/tool.sap_cluster_checks.git
cd tool.sap_cluster_checks
./sap_cluster_checks.py --local
```

### Option 2: Download without git

```bash
# Download and extract
curl -L https://github.com/mmoster/tool.sap_cluster_checks/archive/refs/heads/main.tar.gz | tar xz
cd tool.sap_cluster_checks-main
./sap_cluster_checks.py --local
```

## Usage

```
./sap_cluster_checks.py [OPTIONS] [NODES...]

Options:
  -u, --usage           Interactive mode - scan directory for resources
  -H, --hosts-file      File containing list of hosts (one per line)
  -s, --sosreport-dir   Directory containing SOSreport archives/directories
  --local               Run locally on this cluster node
  -f, --force           Force rediscovery (ignore existing config)
  --reuse-config        Reuse existing config instead of fresh discovery
                        (same as SAP_HA_CHECK_REUSE_CONFIG=1)
  -D, --delete-reports  Delete reports and restart
  -S, --show-config [CLUSTER|NODE]  Display configuration (optionally filter by cluster or node)
  -L, --list-rules      List available health check rules
  -d, --debug           Enable debug mode
  -v, --verbose         Verbose PDF - show all checks in detail (not just failures)
  --no-pdf              Skip PDF report generation
  --no-update-check     Skip software update check

SOSreport Collection:
  -F, --fetch-sosreports [CLUSTER|node1 node2...]  Fetch existing SOSreports from nodes
  -R, --collect-sosreports NODE  Full SOSreport workflow: discover cluster from NODE,
                                 configure SAP extensions, create and fetch SOSreports
  --create-sosreports    Auto-create SOSreports on nodes where missing (use with -F)
  --configure-extensions Auto-configure SAP extensions without prompting (use with -R)
```

## Health Check Steps

| Step | Description |
|------|-------------|
| 1. Access Discovery | Discovers local, SSH, Ansible, or SOSreport access to cluster nodes |
| 2. Cluster Configuration | Node status, quorum, clone config, package consistency |
| 3. Pacemaker/Corosync | STONITH, resources, fencing, master/slave roles |
| 4. SAP-Specific | HANA SR status, replication mode, HA/DR hooks, systemd |
| 5. Report Generation | Summary YAML and PDF reports |

## Included Health Checks

### Cluster Configuration
| Check ID | Severity | Description |
|----------|----------|-------------|
| CHK_CLUSTER_READY | WARNING | Check if cluster is fully started (not in transition) |
| CHK_CLUSTER_TYPE | INFO | Detect Scale-Up vs Scale-Out configuration |
| CHK_NODE_STATUS | CRITICAL | Verify all cluster nodes are online |
| CHK_CLUSTER_QUORUM | CRITICAL | Verify cluster has quorum |
| CHK_QUORUM_CONFIG | CRITICAL | Validate quorum configuration |
| CHK_CLONE_CONFIG | CRITICAL | Validate clone resource configuration |
| CHK_SETUP_VALIDATION | CRITICAL | Validate against SAP HANA HA best practices |
| CHK_CIB_TIME_SYNC | WARNING | Verify CIB updates are synchronized |
| CHK_PACKAGE_CONSISTENCY | WARNING | Verify package versions across nodes |

### Pacemaker/Corosync
| Check ID | Severity | Description |
|----------|----------|-------------|
| CHK_STONITH_CONFIG | CRITICAL | Verify STONITH/fencing is enabled |
| CHK_RESOURCE_STATUS | CRITICAL | Verify SAP HANA resources are running |
| CHK_RESOURCE_FAILURES | WARNING | Detect failed resource operations |
| CHK_ALERT_FENCING | WARNING | Validate SAPHanaSR-alert-fencing |
| CHK_MASTER_SLAVE_ROLES | CRITICAL | Verify master/slave role consistency |
| CHK_MAJORITY_MAKER | CRITICAL | Validate majority maker constraints |

### SAP-Specific
| Check ID | Severity | Description |
|----------|----------|-------------|
| CHK_HANA_INSTALLED | INFO | Detect HANA installation, SID, instance, sidadm user, and running status |
| CHK_HANA_SR_STATUS | CRITICAL | Verify HANA System Replication status |
| CHK_REPLICATION_MODE | WARNING | Verify replication mode is sync or syncmem |
| CHK_HADR_HOOKS | CRITICAL | Validate HA/DR provider hooks |
| CHK_HANA_AUTOSTART | WARNING | Validate HANA autostart is disabled |
| CHK_SYSTEMD_SAP | WARNING | Validate SAP Host Agent and systemd |
| CHK_SITE_ROLES | CRITICAL | Verify site roles consistency |

## Cluster Types and Packages

### Scale-Up (2 HANA nodes + optional additional cluster nodes)
- Package: `sap-hana-ha` (ANGI, RHEL 9+) or `resource-agents-sap-hana` (classic)
- Resource agent: `SAPHana`
- Additional cluster nodes (ASCS servers, etc.) don't require HANA

### Scale-Out (4+ HANA nodes + 1 majority maker)
- HANA nodes: `sap-hana-ha` (ANGI) or `resource-agents-sap-hana-scaleout` (classic)
- Majority maker: Same packages as HANA nodes, or no SAP HANA packages
- Resource agent: `SAPHanaController`

## Example Output

```
Health Check Results:
  PASSED:   26  FAILED:   0  SKIPPED:   0  ERROR:   0

  ╔═══════════════════════════════════════════════════════╗
  ║            ✓  CLUSTER IS HEALTHY  ✓                   ║
  ╚═══════════════════════════════════════════════════════╝

  PDF report saved: health_check_report_cluster2_1507.pdf
```

## Configuration

Results are stored in `cluster_access_config.yaml`:

```yaml
clusters:
  my_cluster:
    nodes:
    - hana01
    - hana02
nodes:
  hana01:
    hostname: hana01
    ssh_reachable: true
    preferred_method: ssh
```

### Viewing Configuration

```bash
# Show all discovered clusters and nodes
./sap_cluster_checks.py --show-config

# Show config for a specific cluster
./sap_cluster_checks.py --show-config my_cluster

# Show config for cluster containing a specific node
./sap_cluster_checks.py -S hana01
```

**Example Output:**

```
--- Cluster: my_cluster ---
    Nodes: hana01, hana02
    Discovered from: hana01
    Discovered at: 2026-04-16T16:30:40
    Cluster status: Running

    Cluster Configuration:
    --------------------------------------------
      # Node 1
      node1_hostname: hana01
      node1_fqdn: hana01.example.com
      node1_ip: 192.168.5.232

      # Node 2
      node2_hostname: hana02
      node2_fqdn: hana02.example.com
      node2_ip: 192.168.5.233

      # Virtual IP Configuration
      vip: 192.168.5.231
      vip_resource: vip_RH2_HDB00
      secondary_vip: 192.168.5.234

      # System Replication
      replication_mode: sync
      operation_mode: logreplay
      site1_name: DC1

      # STONITH/Fencing
      stonith_device: my_fence
      pcmk_host_map: hana01:hana01-ipmi;hana02:hana02-ipmi

      # Cluster Properties
      automated_register: true
      prefer_site_takeover: true
      duplicate_primary_timeout: 7200
      secondary_read: true
```

Delete with `-D` to restart the investigation from scratch.

### Reusing Access Discovery

By default, access discovery runs from scratch on every invocation — even if `cluster_access_config.yaml` already exists. This ensures the tool always reflects the current state of the cluster.

To reuse the cached config from a previous run (e.g., in CI/CD pipelines or repeated testing), set the `SAP_HA_CHECK_REUSE_CONFIG` environment variable or use the `--reuse-config` flag:

```bash
# Environment variable (accepts 1, true, or yes)
export SAP_HA_CHECK_REUSE_CONFIG=1
./sap_cluster_checks.py

# CLI flag (equivalent)
./sap_cluster_checks.py --reuse-config

# Force fresh discovery even when reuse is enabled
SAP_HA_CHECK_REUSE_CONFIG=1 ./sap_cluster_checks.py -f
```

| Flag / Variable | Behavior |
|-----------------|----------|
| *(default)* | Fresh discovery every run |
| `--reuse-config` or `SAP_HA_CHECK_REUSE_CONFIG=1` | Reuse existing `cluster_access_config.yaml` |
| `-f` / `--force` | Always force fresh discovery (overrides reuse) |

### Audit & Compliance Mode

Use `-v` (verbose) to generate a complete PDF report documenting **all** health checks — not just failures. This is ideal for audits, compliance reviews, or handover documentation:

```bash
./sap_cluster_checks.py --local -v
```

The verbose report includes every check with its full result, the discovered cluster configuration, and system details — providing a complete snapshot of your cluster's health status.

## Test Sequence

See [docs/test_sequence.md](docs/test_sequence.md) for the full execution order of all 22 health checks, including steps, phases, gates, and the read-only commands used.

## PDF Color Scheme

The PDF report defaults to **Red Hat brand colors**. To use a **corporate-neutral blue/gray palette** instead, edit `tool/sap_cluster_checks/report_generator.py` and change the `PdfColors` alias:

```python
# Default (Red Hat branding):
PdfColors = RedHatColors

# Alternative (neutral blue/gray):
PdfColors = NeutralColors
```

This is a one-line change. All report elements (headers, status badges, tables, recommendation boxes) use the alias, so switching takes effect everywhere.

## Extending Health Checks

See [docs/EXTENDING_HEALTH_CHECKS.md](docs/EXTENDING_HEALTH_CHECKS.md) for details on creating custom health check rules.

## Requirements

| Component | Requirement |
| --- | --- |
| Operating System | Red Hat Enterprise Linux for SAP Solutions 8.x, 9.x, and 10.x |
| Python | 3.6 or higher (included in RHEL 8/9/10) |
| PyYAML | `pip install pyyaml` or `dnf install python3-pyyaml` |
| fpdf2 (optional) | For PDF report generation: `pip install fpdf2` |

## Platform Compatibility Matrix

This tool uses platform-specific commands and paths. The following matrix shows what is supported:

| Component | Supported | Not Supported |
|-----------|-----------|---------------|
| **Operating System** | RHEL for SAP Solutions 8.x, 9.x, 10.x | SLES for SAP, other Linux distributions |
| **Cluster Stack** | Pacemaker/Corosync with `pcs` CLI | Pacemaker/Corosync with `crmsh` (SUSE) |
| **Cluster Topology** | Scale-Up (2+ nodes), Scale-Out (4+ nodes + majority maker) | ASCS/ERS (planned), standalone HANA |
| **Resource Agents** | `sap-hana-ha` (ANGI, RHEL 9+), `resource-agents-sap-hana` (classic), `resource-agents-sap-hana-scaleout` (classic) | SUSE resource agents (`SAPHanaSR`, `SAPHanaSR-ScaleOut`) |
| **Fencing** | All STONITH agents supported by RHEL | — |
| **Subscription** | Red Hat Subscription Manager (`subscription-manager`) | SUSEConnect, zypper |
| **Package Manager** | `rpm`, `dnf`/`yum` | `zypper` |
| **OS Detection** | `/etc/redhat-release` | `/etc/os-release` (generic), `/etc/SuSE-release` |
| **Python** | 3.6+ (included in RHEL 8/9/10) | Python 2.x |
| **Access Methods** | Local, SSH, Ansible, SOSreport | — |

> **Note:** Red Hat-specific dependencies include `pcs` (cluster CLI), `subscription-manager` (entitlement), `/etc/redhat-release` (version detection), and Red Hat-branded PDF report formatting. Contributions to support additional platforms are welcome.

## Testing

This tool has been tested across different operating systems, cluster topologies, and resource agent packages.

Operating systems:

- Red Hat Enterprise Linux for SAP Solutions 8.x, 9.x, and 10.x

Cluster topologies:

- SAP HANA Scale-Up (2 HANA nodes + optional additional cluster nodes)
- SAP HANA Scale-Out (4+ HANA nodes + 1 majority maker)

Resource agent packages:

- `sap-hana-ha` (ANGI, RHEL 9+)
- `resource-agents-sap-hana` (classic)
- `resource-agents-sap-hana-scaleout` (classic, Scale-Out)

Access methods:

- Local execution on cluster node
- Remote execution via SSH
- Offline analysis via SOSreport archives

> **Testing Disclaimer**<br>
> It is not possible to test every combination of operating system, CPU architecture, SAP HANA version, and cluster configuration with every release.<br>
> Testing is regularly done for common scenarios: SAP HANA Scale-Up HA on RHEL 9 with ANGI resource agents on x86_64.

## Disclaimer

> **Supported Platforms**<br>
> This tool is designed for and tested on Red Hat Enterprise Linux for SAP Solutions 8.x, 9.x, and 10.x. SUSE Linux Enterprise Server (SLES) for SAP Applications is not supported in this version. It might be supportable with minor changes, but it is not supported.

> **Testing Coverage**<br>
> Health checks are validated against SAP and Red Hat best practice documentation. Not every combination of SAP HANA version, RHEL version, CPU architecture (x86_64, ppc64le, aarch64), cluster topology, and resource agent package can be covered. Results should be verified against the applicable Red Hat and SAP documentation for your specific environment.

> **Constraints and Limitations**
> - Only SAP HANA System Replication (HSR) clusters managed by Pacemaker are supported
> - ASCS/ERS cluster validation is not yet implemented
> - SUSE Linux Enterprise Server is not supported
> - Health checks do not include CPU-, architecture-, or platform-specific tuning recommendations (e.g. kernel parameters, NUMA settings, or power profiles)
> - The tool performs read-only checks and does not modify any cluster or SAP configuration
> - SOSreport analysis depends on the completeness of the collected SOSreport data
> - PDF report generation requires the optional `fpdf2` package

> **Use at Your Own Responsibility**<br>
> This tool is provided as-is, without warranties or guarantees of any kind. While it performs only read-only operations, it is your responsibility to verify findings against the official SAP and Red Hat documentation for your environment. Use it at your own risk and responsibility.

## Contributing

Contributions are welcome! Please open an issue or submit a pull request.

## Contributors
- [Markus Moster](https://github.com/mmoster) (Author)
- [Janine Fuchs](https://github.com/ja9fuchs) (Maintainer)
- [Amir Memon](https://github.com/amemon) (Maintainer)

## Support

You can report any issues using the [Issues](https://github.com/mmoster/tool.sap_cluster_checks/issues) section.

## License

[Apache 2.0](LICENSE)
