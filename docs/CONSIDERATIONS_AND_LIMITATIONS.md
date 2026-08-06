# Considerations and Limitations

This document describes the design boundaries, known constraints, and operational considerations for the SAP HANA Pacemaker Cluster Health Check tool.

---

## Platform Support

### Supported Operating Systems

- **Red Hat Enterprise Linux for SAP Solutions** 8.x, 9.x, and 10.x

The tool relies on RHEL-specific components:
- `pcs` CLI for cluster management queries
- `subscription-manager` for entitlement validation
- `/etc/redhat-release` for OS version detection
- Red Hat package naming conventions (`resource-agents-sap-hana`, `sap-hana-ha`)

### Unsupported Operating Systems

The following operating systems are **not supported**:

- **SUSE Linux Enterprise Server for SAP Applications** (SLES for SAP) — not supported. SLES uses different cluster management tooling (`crmsh` instead of `pcs`), different package names (`SAPHanaSR`, `SAPHanaSR-ScaleOut`), and different OS detection mechanisms (`/etc/os-release`, `/etc/SuSE-release`). These differences make SLES incompatible with this tool in its current version.
- **Other Linux distributions** (Debian, Ubuntu, Oracle Linux, etc.) — not supported.

### Supported Cluster Stack

- **Pacemaker/Corosync** with the `pcs` CLI (Red Hat)
- Other cluster frameworks (Veritas, Windows Server Failover Clustering, etc.) are not applicable
- **Note:** Pacemaker/Corosync with `crmsh` (SUSE) is **not supported** — this tool requires the `pcs` CLI

---

## Cluster Type Scope

### Supported Topologies

| Topology | Status |
|----------|--------|
| SAP HANA Scale-Up (2+ nodes) | Supported |
| SAP HANA Scale-Out (4+ HANA nodes + majority maker) | Supported |
| ASCS/ERS (SAP Central Services) | Not yet implemented (planned) |
| Standalone SAP HANA (no cluster) | Not applicable |

### Scope Boundaries

- Only **SAP HANA System Replication (HSR)** clusters managed by Pacemaker are validated
- Non-HANA workloads running in the same cluster are not inspected
- Multi-SID configurations on a single cluster have limited support — the tool focuses on the primary HANA SID detected
- Active/Active (read-enabled) secondary configurations are detected but not all aspects are specifically validated

---

## Read-Only Operation

The tool performs **exclusively read-only operations**. It does not:
- Modify any Pacemaker, Corosync, or SAP HANA configuration
- Move, restart, or clean up cluster resources
- Register or deregister HANA System Replication
- Start or stop systemd services
- Write to any system file on the cluster nodes

This design ensures production safety but also means the tool **cannot remediate** any issues it finds. All findings require manual action by the administrator.

See [command_reference.md](command_reference.md) for the complete list of commands and their impact assessment.

---

## Access and Connectivity

### SSH Access

- SSH key-based authentication must be configured for remote checks
- Password-based SSH authentication is not supported in non-interactive mode
- The SSH user must have sufficient privileges (typically `root` or a user with `sudo`) to run cluster status commands and `su - <sid>adm`

### Ansible Access

- Ansible must be installed and configured on the machine running the tool
- Ansible inventory must include the target cluster nodes
- The tool uses `ansible <host> -m shell` for command execution

### Local Execution

- Requires the tool to be run directly on a cluster node
- The user must have privileges to run `pcs`, `crm_mon`, and `su - <sid>adm`

### SOSreport Analysis

- No network connectivity to the cluster is required
- Analysis is limited to the data captured in the SOSreport at the time of collection
- If the SOSreport was collected without SAP extensions (`sap`, `saphana`), some SAP-specific checks may produce incomplete results or be skipped
- SOSreport formats supported: `.tar.xz`, `.tar.gz`, `.tar`

---

## SOSreport Completeness

The quality of offline (SOSreport-based) analysis depends entirely on the data collected:

- **Missing SAP plugins**: If the SOSreport was collected without the `sap` and `saphana` plugins enabled, HANA-specific checks (global.ini, SR status, profiles) will lack data
- **Timing**: SOSreport captures a point-in-time snapshot. Transient states (e.g., cluster in transition, resource migration in progress) are reflected as they were at collection time
- **Partial SOSreports**: Truncated or corrupted archives may cause extraction failures or missing data for specific checks
- **Node coverage**: For a complete cluster assessment, SOSreports should be collected from **all** cluster nodes. Missing nodes will result in skipped per-node checks for those nodes

To ensure maximum coverage, use the built-in SOSreport collection workflow:

```bash
# Auto-configure SAP extensions and collect from all nodes
./sap_cluster_checks.py -R <seed-node>
```

---

## Testing Coverage

### What Is Regularly Tested

- SAP HANA Scale-Up HA on RHEL 9 with ANGI resource agents (`sap-hana-ha`) on x86_64

### What Cannot Be Exhaustively Tested

It is not feasible to test every combination of:
- RHEL version (8.x, 9.x, 10.x) and minor release
- CPU architecture (x86_64, ppc64le, aarch64)
- SAP HANA version (HANA 2.0 SPS 05, 06, 07, etc.)
- Resource agent package and version (`sap-hana-ha` vs `resource-agents-sap-hana` vs `resource-agents-sap-hana-scaleout`)
- Cluster topology (Scale-Up, Scale-Out with varying node counts)
- Fencing agent type (IPMI, SBD, cloud-specific, etc.)

Results should always be verified against the applicable Red Hat and SAP documentation for your specific environment.

---

## Health Check Limitations

### No Tuning Recommendations

The tool does **not** validate or recommend:
- Kernel parameter tuning (e.g., `vm.swappiness`, `net.core.somaxconn`)
- NUMA settings or CPU pinning
- Power profiles or CPU governor settings
- Storage I/O tuning (e.g., `noop` scheduler, multipath settings)
- Network tuning (e.g., jumbo frames, bonding modes)
- SAP HANA memory allocation or parameter tuning

These are environment-specific and best addressed with tools like `tuned` profiles, SAP HANA hardware configuration checks, or platform-specific assessment tools.

### No Network Validation

The tool does not check:
- Network redundancy (bonding, teaming)
- Network latency between cluster nodes
- Corosync ring/link health beyond basic quorum status
- Virtual IP reachability or network segmentation
- DNS resolution consistency

### No Storage Validation

The tool does not check:
- Storage subsystem health (SAN, NFS, local disk)
- Filesystem capacity or mount points
- HANA data/log volume configuration
- Shared storage for Scale-Out configurations beyond basic cluster config

### Severity Classification

Health checks are classified as CRITICAL, WARNING, or INFO based on general best practices. The actual impact of a finding depends on your specific environment, SLA requirements, and operational procedures. A WARNING in one environment may be CRITICAL in another.

---

## PDF Report Generation

- PDF reports require the optional `fpdf2` Python package
- If `fpdf2` is not installed, the tool still runs all checks and produces YAML output but skips PDF generation
- The PDF supports different color scheme templates (e.g., Red Hat branding or a corporate-neutral palette) which can be switched in the report generator configuration
- The PDF uses embedded fonts; rendering is consistent across systems
- Very large clusters or verbose mode reports may produce multi-page PDFs

---

## Concurrency and Performance

- Health checks within a phase run in parallel using `ThreadPoolExecutor`
- Phases run sequentially due to data dependencies (e.g., SAP checks depend on HANA detection)
- SSH command execution is subject to network latency and SSH connection limits
- No configurable timeout for individual health check commands — commands that hang (e.g., due to unresponsive nodes) may delay the overall check
- The tool is designed for one-time checks, not continuous monitoring

---

## Security Considerations

- The tool executes commands on cluster nodes via SSH or locally with the user's privileges
- Node names and SSH usernames are sanitized with `shlex.quote()` before being interpolated into SSH commands to prevent shell injection
- No credentials are stored by the tool itself — it relies on SSH keys or the current user session
- SOSreport archives may contain sensitive data (hostnames, IP addresses, SAP SIDs, configuration details) and should be handled according to your organization's data handling policies
- The `cluster_access_config.yaml` file stores discovered node information (hostnames, access methods) and should be treated as operational data
- The tool checks for updates via `git fetch` against the public GitHub repository and displays an informational message if a newer version is available — it does **not** auto-update or restart itself. This check can be disabled with `--no-update-check`

---

## Version Compatibility

### Resource Agent Packages

The tool supports both the classic and modern (ANGI) resource agent packages:

| Package | RHEL Version | Notes |
|---------|-------------|-------|
| `sap-hana-ha` (ANGI) | RHEL 9+ | Modern, recommended |
| `resource-agents-sap-hana` (classic) | RHEL 8, 9 | Legacy, still supported |
| `resource-agents-sap-hana-scaleout` (classic) | RHEL 8, 9 | Scale-Out only |

The tool auto-detects which package is installed and adjusts validation accordingly. Mixed package versions across cluster nodes may produce unexpected results.

### Python Compatibility

- Requires Python 3.6 or higher
- Python 2.x is not supported
- The tool uses only standard library modules plus `PyYAML` (required) and `fpdf2` (optional)

---

## Operational Considerations

### When to Run

- **Initial setup validation**: After configuring a new HA cluster, before going live
- **Periodic health checks**: As part of regular operational procedures
- **Pre-maintenance**: Before planned maintenance windows to establish a baseline
- **Post-incident**: After cluster events (failovers, node failures) to verify recovery state
- **Compliance audits**: Use verbose mode (`-v`) for complete documentation of cluster state

### When NOT to Run

- **During active failover**: The tool captures a point-in-time snapshot; running during a failover produces results reflecting a transient state
- **On non-Pacemaker clusters**: The tool is specific to Pacemaker/Corosync and will not produce meaningful results on other cluster frameworks
- **As a monitoring replacement**: This is a diagnostic tool, not a monitoring solution. For continuous monitoring, use Pacemaker's built-in alerting, SAP HANA cockpit, or external monitoring tools

### Interpreting Results

- A **PASSED** result means the check matched the expected best-practice configuration
- A **FAILED** result means a deviation was detected — review the finding and determine if it applies to your environment
- A **SKIPPED** result means a prerequisite was not met (e.g., HANA not installed, resource not running)
- An **ERROR** result means the check could not be executed (e.g., command failed, data unavailable)
- Not all FAILED findings necessarily require action — some may be intentional deviations for your specific setup

---

## Future Scope

The following capabilities are not currently available but are planned or under consideration:

- **ASCS/ERS cluster validation**: Support for SAP Central Services HA configurations
- **SUSE Linux Enterprise Server**: Full SLES for SAP support with `crmsh` integration
- **Custom check definitions**: User-defined health checks beyond the built-in 22
- **Ansible collection packaging**: Distribution as an Ansible collection for integration into automation workflows
