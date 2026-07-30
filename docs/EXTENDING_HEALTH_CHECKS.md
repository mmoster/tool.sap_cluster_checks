# Extending Health Checks

## Health Check YAML Files

All health checks are defined in:
```
rules/health_checks/CHK_*.yaml
```

Currently **22 checks** are defined:

| Check ID | Severity | Description |
|----------|----------|-------------|
| `CHK_ALERT_FENCING` | WARNING | Validate SAPHanaSR alert and fencing configuration |
| `CHK_CIB_TIME_SYNC` | WARNING | CIB time synchronization |
| `CHK_CLONE_CONFIG` | CRITICAL | Validate clone/promotable resource configuration |
| `CHK_CLUSTER_QUORUM` | CRITICAL | Verify cluster has quorum |
| `CHK_CLUSTER_READY` | WARNING | Check if cluster is fully started (not in transition) |
| `CHK_CLUSTER_TYPE` | INFO | Detect Scale-Up vs Scale-Out configuration |
| `CHK_HADR_HOOKS` | CRITICAL | Validate HA/DR provider hooks |
| `CHK_HANA_AUTOSTART` | WARNING | Validate HANA autostart is disabled |
| `CHK_HANA_INSTALLED` | INFO | Detect HANA installation and running status |
| `CHK_HANA_SR_STATUS` | CRITICAL | Verify HANA System Replication status |
| `CHK_MAJORITY_MAKER` | CRITICAL | Validate majority maker configuration (Scale-Out) |
| `CHK_MASTER_SLAVE_ROLES` | CRITICAL | Verify master/slave role consistency |
| `CHK_NODE_STATUS` | CRITICAL | Verify all cluster nodes are online |
| `CHK_PACKAGE_CONSISTENCY` | WARNING | Verify package versions across nodes |
| `CHK_QUORUM_CONFIG` | CRITICAL | Validate quorum configuration (Scale-Up) |
| `CHK_REPLICATION_MODE` | WARNING | Verify replication mode is sync or syncmem |
| `CHK_RESOURCE_FAILURES` | WARNING | Detect failed resource operations |
| `CHK_RESOURCE_STATUS` | CRITICAL | Verify SAP HANA resources are running |
| `CHK_SETUP_VALIDATION` | CRITICAL | Validate against SAP HANA HA best practices |
| `CHK_SITE_ROLES` | CRITICAL | Verify site roles consistency |
| `CHK_STONITH_CONFIG` | CRITICAL | Verify STONITH/fencing is enabled |
| `CHK_SYSTEMD_SAP` | WARNING | Validate SAP Host Agent and systemd integration |

---

## Architecture Overview

This is a rule-based validation engine that separates:

1. **Data Collection** (`source_definitions`) - how to get data
2. **Parsing** (`parser`) - how to extract values from output
3. **Validation** (`validation_logic`) - what conditions to check

This separation allows checks to work both on **live systems** (via SSH/local commands) and **SOSreports** (offline analysis) with the same validation logic.

### Dispatch Manifest

The central dispatch manifest (`rules/check_dispatch.yaml`) controls:
- **Which checks run in which step** (config, pacemaker, sap)
- **Phase ordering** within each step (sequential phases, parallel checks within a phase)
- **Topology filtering** (Scale-Up vs Scale-Out)
- **Gates** (runtime conditions like "HANA resource running" or "HANA installed")

```
check_dispatch.yaml          CHK_*.yaml files
    (what runs when)     +    (what to check)
         ↓                        ↓
    _run_step()          →   engine.run_check()
```

---

## How to Add a New Health Check

### Step 1: Create the YAML Rule File

Create `rules/health_checks/CHK_YOUR_CHECK.yaml`:

```yaml
# CHK_YOUR_CHECK - Description of what this checks
check_id: CHK_YOUR_CHECK
version: "1.0"
severity: CRITICAL  # CRITICAL | WARNING | INFO
description: Your check description
enabled: true
optional: false  # If true, failures become warnings in non-strict mode

# Optional: restrict to specific topologies
# topology_filter: [Scale-Up]   # or [Scale-Out], or omit for all

source_definitions:
  # Command to run on live systems
  live_cmd: "your-command-here | grep 'pattern'"
  # Path within SOSreport for offline analysis
  sos_path: "sos_commands/pacemaker/your_file"
  # Optional: alternate paths to try
  sos_path_alternates:
    - "etc/some/config"

parser:
  type: regex
  multiline: true  # Search across multiple lines
  search_patterns:
    - name: my_value
      regex: "pattern_to_match"
      group: 0  # 0 = full match, 1+ = capture group

validation_logic:
  scope: cluster  # See scope options below
  expectations:
    - key: my_value
      operator: exists
      value: true
      message: "Error message when check fails"
```

### Step 2: Add to the Dispatch Manifest

Add an entry in `rules/check_dispatch.yaml` under the appropriate step and phase:

```yaml
steps:
  pacemaker:                      # Step to add to
    phases:
      - phase: 1                  # Phase within the step
        checks:
          - check_id: CHK_YOUR_CHECK
            # Optional: topology filter
            # topology: [Scale-Up]
            # Optional: gate (check only runs if gate passes)
            # gate: hana_resource_running
```

**That's it.** No Python code needed.

### Step 3: Verify

Run the tool and check that your new check appears. The dispatch manifest is validated at startup against loaded YAML rule files — you'll see a warning if:
- A check is in the manifest but has no YAML rule file
- A YAML rule file exists but is not in the manifest

---

## How to Update an Existing Health Check

All modifications to existing checks are done by editing the corresponding `CHK_*.yaml` file. No Python changes are needed unless the check uses `custom_check`.

### Change Severity

Edit the top-level `severity` field:

```yaml
# Before
severity: WARNING

# After
severity: CRITICAL
```

You can also override severity for individual expectations without changing the rule-level default:

```yaml
expectations:
  - key: stonith_disabled
    operator: not_exists
    severity: WARNING  # This expectation warns instead of using rule's CRITICAL
    message: "STONITH is disabled"
```

### Add or Modify Expectations

Add new entries to the `expectations` list. Each expectation checks one parsed value:

```yaml
expectations:
  # Existing expectation
  - key: offline_nodes
    operator: not_exists
    message: "One or more cluster nodes are OFFLINE"
  # New expectation added
  - key: standby_nodes
    operator: not_exists
    severity: WARNING
    message: "One or more nodes are in standby mode"
```

To modify an existing expectation, change any of its fields (`operator`, `value`, `message`, `severity`, `pass_message`).

### Update Regex Patterns

Modify patterns in `parser.search_patterns`. Each pattern extracts a named value from command output:

```yaml
search_patterns:
  # Adjust regex to catch additional patterns
  - name: offline_nodes
    regex: "(OFFLINE|Offline:.*\\S|Standby)"  # Added Standby detection
    group: 0
```

**Important:** If you add a new `name`, you can reference it in `validation_logic.expectations` via the `key` field. If you remove a `name`, remove any expectations that reference it.

### Change Scope

Update `validation_logic.scope` to change how the check is evaluated across nodes:

```yaml
validation_logic:
  # Change from cluster-wide to per-node evaluation
  scope: per_node
```

See [Scope Options](#scope-options) for available values.

### Modify Source Commands

Update what data is collected:

```yaml
source_definitions:
  # Update the live command
  live_cmd: "crm_mon -1 -r 2>/dev/null || pcs status 2>/dev/null"
  # Update the SOSreport path
  sos_path: "sos_commands/pacemaker/crm_mon_-1*"
```

When updating `live_cmd`, test the command on a representative system first. The command runs on each node (for `per_node` scope) or on one node (for `cluster` scope).

### Enable or Disable a Check

```yaml
# Temporarily disable a check (remains in manifest but won't execute)
enabled: false
```

### Change Dispatch Position

To move a check to a different step, phase, or change its gate/topology filter, edit `rules/check_dispatch.yaml`:

```yaml
# Move check to a different phase or add a gate
- check_id: CHK_YOUR_CHECK
  gate: hana_installed        # Now only runs if HANA is installed
  topology: [Scale-Up]        # Now only runs on Scale-Up clusters
```

### Checklist for Updates

1. Edit the `CHK_*.yaml` file
2. If regex patterns changed: verify the regex matches expected output
3. If scope changed: consider whether expectations still make sense for the new scope
4. If `live_cmd` changed: test on a live system or verify against SOSreport data
5. Run the test suite: `cd tool.sap_cluster_checks && python -m pytest tests/ -v`

---

## Dispatch Manifest Reference

### Steps and Phases

Checks are organized into steps, each with one or more phases:

```yaml
steps:
  config:       # Step 2: Cluster configuration
  pacemaker:    # Step 3: Pacemaker/Corosync
  sap:          # Step 4: SAP-specific
```

Phases within a step run **sequentially** (phase 1 completes before phase 2 starts). Checks within a phase run **in parallel**.

### Topology Filtering

Restrict a check to specific cluster types:

```yaml
- check_id: CHK_MAJORITY_MAKER
  topology: [Scale-Out]       # Only runs on Scale-Out clusters
```

Topology is detected in the config step by `CHK_CLUSTER_TYPE`. If omitted, the check runs for all topologies.

You can also set `topology_filter` in the YAML rule file itself as a safety net:

```yaml
# In CHK_MAJORITY_MAKER.yaml
topology_filter: [Scale-Out]
```

### Gates

Gates are runtime conditions that control whether a phase or individual check runs:

```yaml
# Phase-level gate: skip entire phase if HANA not installed
- phase: 2
  gate: hana_installed
  checks:
    - check_id: CHK_HANA_SR_STATUS
      gate: hana_resource_running   # Check-level gate
    - check_id: CHK_REPLICATION_MODE  # No gate, always runs in this phase
```

Available gates:

| Gate | Condition |
|------|-----------|
| `hana_installed` | At least one node has HANA installed |
| `hana_resource_running` | HANA resource is active in Pacemaker |
| `not_legacy_scaleup` | Cluster is NOT a legacy Scale-Up (skips checks not applicable to older setups) |

When a gate is closed, affected checks are recorded as SKIPPED.

---

## YAML Rule Reference

### Scope Options

| Scope | Behavior |
|-------|----------|
| `cluster` | Run on **one node only** (cluster-wide info) |
| `per_node` | Check **each node independently** (default) |
| `any_node` | Pass if **at least one node** passes |
| `all_nodes_equal` | All nodes must have **identical values** |

### Operators

| Operator | Description | Example |
|----------|-------------|---------|
| `exists` | Value is not None | Check if pattern was found |
| `not_exists` | Value is None | Ensure error pattern is absent |
| `eq` / `ne` | Equal / Not equal | `value: "expected"` |
| `in` / `not_in` | In list | `value: ["a", "b", "c"]` |
| `contains` | Substring match | `value: "substring"` |
| `regex` | Regex match | `value: "pattern.*"` |
| `gt` / `lt` | Greater/less than | `value: 10` |
| `info_if_exists` | Always passes, shows info | Informational only |

### Advanced Features

#### Override Severity Per-Expectation

```yaml
expectations:
  - key: stonith_disabled
    operator: not_exists
    severity: WARNING  # Override rule's CRITICAL severity
    message: "STONITH disabled (testing only)"
```

#### Match Mode (any vs all)

```yaml
validation_logic:
  match_mode: any  # Pass if ANY expectation passes (default: all)
```

#### Pass Messages (Informational)

```yaml
expectations:
  - key: syncmem_mode
    operator: info_if_exists
    pass_message: "Using syncmem mode (consider sync for max security)"
```

#### Detection-Type Checks (No Pass/Fail)

```yaml
validation_logic:
  type: detection  # Informational gathering only
  expectations: []
```

#### Compare Values Across Nodes

```yaml
validation_logic:
  scope: all_nodes_equal
  compare_keys:
    - pacemaker_version
    - corosync_version
```

#### Topology Filter (Rule-Level)

```yaml
# Only run this check on Scale-Out clusters
topology_filter: [Scale-Out]
```

#### HANA Nodes Only

```yaml
# Skip nodes excluded from HANA resources (majority makers, app servers)
hana_nodes_only: true
```

#### Dependency Gating

```yaml
# Only run if CHK_CLUSTER_TYPE passed
requires: CHK_CLUSTER_TYPE
```

---

## Example: Adding a New Check

**Check if cluster has no failed actions:**

### 1. Create the rule file

```yaml
# rules/health_checks/CHK_FAILED_ACTIONS.yaml
check_id: CHK_FAILED_ACTIONS
version: "1.0"
severity: WARNING
description: Check for failed actions in cluster history
enabled: true

source_definitions:
  live_cmd: "pcs status 2>/dev/null | grep -E 'Failed|failure' || echo 'NO_FAILURES'"
  sos_path: "sos_commands/pacemaker/crm_mon_-1"

parser:
  type: regex
  multiline: true
  search_patterns:
    - name: has_failures
      regex: "(Failed|failure)"
      group: 0
    - name: no_failures
      regex: "NO_FAILURES"
      group: 0

validation_logic:
  scope: cluster
  expectations:
    - key: has_failures
      operator: not_exists
      message: "Cluster has failed actions - review with 'pcs status'"
```

### 2. Add to dispatch manifest

In `rules/check_dispatch.yaml`, add to the pacemaker step:

```yaml
  pacemaker:
    phases:
      - phase: 1
        checks:
          # ... existing checks ...
          - check_id: CHK_FAILED_ACTIONS
```

---

## Processing Flow

```
check_dispatch.yaml  →  _run_step()  →  For each phase:
                                           1. Evaluate phase gate
                                           2. Filter by topology
                                           3. Evaluate per-check gates
                                           4. Run checks in parallel
                                           5. Post-phase hooks
                                                  ↓
CHK_*.yaml  →  engine.run_check()  →  1. Check topology_filter
                                       2. Check requires dependency
                                       3. Execute live_cmd OR read sos_path
                                       4. Parse output with regex
                                       5. Evaluate expectations
                                       6. Return CheckResult
```

---

## Key Files

| File | Purpose |
|------|---------|
| `rules/check_dispatch.yaml` | Dispatch manifest: step/phase/gate/topology assignment |
| `rules/health_checks/CHK_*.yaml` | Health check definitions (data + validation) |
| `rules/engine.py` | Rules engine, CheckDispatch loader |
| `sap_cluster_checks.py` | Main CLI, GateRegistry, step orchestration |
| `report_generator.py` | PDF/YAML report generation |

---

## Design Philosophy

The engine uses a **declarative approach** where you describe *what* to check, not *how* to check it. Adding new checks requires **only YAML edits**:

1. **Rule file** (`CHK_*.yaml`) — defines data collection, parsing, and validation
2. **Dispatch manifest** (`check_dispatch.yaml`) — defines when and where the check runs

The `rules/engine.py` handles command execution, SOSreport reading, regex parsing, expectation evaluation, and result aggregation. The `sap_cluster_checks.py` orchestrator handles step sequencing, gate evaluation, topology filtering, and post-phase state extraction.
