"""
Rules Engine for SAP Pacemaker Cluster Health Check

Loads and executes health check rules from YAML files.
Supports both live command execution and SOSreport parsing.
"""

import os
import re
import shlex
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum
from pathlib import Path
from typing import List, Dict, Optional, Any, Tuple

import yaml

from ..lib import CIBParser

from ..lib.compat import dataclass


class Severity(Enum):
    """Check severity levels."""

    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class CheckStatus(Enum):
    """Check result status."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"


@dataclass
class CheckResult:
    """Result of a single health check."""

    check_id: str = None
    description: str = None
    status: CheckStatus = None
    severity: Severity = None
    message: str = None
    details: Dict[str, Any] = None
    node: Optional[str] = None

    def __post_init__(self):
        if self.details is None:
            self.details = {}


@dataclass
class RuleDefinition:
    """Parsed rule definition from YAML."""

    check_id: str = None
    version: str = None
    severity: str = None
    description: str = None
    enabled: bool = True
    optional: bool = False  # If True, failures are warnings in non-strict mode
    hana_nodes_only: bool = False  # If True, skip majority maker nodes (no HANA)
    source_definitions: Dict[str, Any] = None
    parser: Dict[str, Any] = None
    validation_logic: Dict[str, Any] = None
    topology_filter: Any = None  # str, list of str, or None (all topologies)
    requires: Optional[str] = None  # Check ID that must pass before this one runs
    raw_yaml: Dict[str, Any] = None

    def __post_init__(self):
        if self.raw_yaml is None:
            self.raw_yaml = {}


# ------------------------------------------------------------------
# Dispatch manifest dataclasses and loader
# ------------------------------------------------------------------


@dataclass
class DispatchCheckEntry:
    """A single check entry in a dispatch phase."""

    check_id: str = None
    topology: Any = "all"  # 'all', str, or list of str
    gate: Optional[str] = None

    def __post_init__(self):
        # Normalize topology to a list or 'all'
        if self.topology is None or self.topology == "all":
            self.topology = "all"
        elif isinstance(self.topology, str):
            self.topology = [self.topology]


@dataclass
class DispatchPhase:
    """A sequential phase within a dispatch step."""

    phase: int = 1
    parallel: bool = True
    gate: Optional[str] = None
    checks: List[DispatchCheckEntry] = None

    def __post_init__(self):
        if self.checks is None:
            self.checks = []


@dataclass
class DispatchStep:
    """A top-level dispatch step (config, pacemaker, sap)."""

    name: str = None
    step_number: int = 0
    phases: List[DispatchPhase] = None

    def __post_init__(self):
        if self.phases is None:
            self.phases = []


class CheckDispatch:
    """Loader and query interface for the check dispatch manifest.

    The dispatch manifest (rules/check_dispatch.yaml) declares which checks
    run in which step/phase, with optional topology filters and gates.
    """

    DEFAULT_MANIFEST = str(Path(__file__).parent / "check_dispatch.yaml")

    def __init__(self, manifest_path: str = None):
        self._manifest_path = manifest_path or self.DEFAULT_MANIFEST
        self._steps: Dict[str, DispatchStep] = {}
        self._topologies: List[str] = []
        self._loaded = False

    @property
    def loaded(self) -> bool:
        return self._loaded

    def load(self) -> bool:
        """Load the YAML manifest. Returns False if file not found."""
        path = Path(self._manifest_path)
        if not path.exists():
            return False

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception:
            return False

        if not data or not isinstance(data, dict):
            return False

        self._topologies = data.get("topologies", [])

        for step_name, step_data in data.get("steps", {}).items():
            phases = []
            for phase_data in step_data.get("phases", []):
                checks = []
                for chk in phase_data.get("checks", []):
                    checks.append(
                        DispatchCheckEntry(
                            check_id=chk.get("check_id"),
                            topology=chk.get("topology", "all"),
                            gate=chk.get("gate"),
                        )
                    )
                phases.append(
                    DispatchPhase(
                        phase=phase_data.get("phase", 1),
                        parallel=phase_data.get("parallel", True),
                        gate=phase_data.get("gate"),
                        checks=checks,
                    )
                )
            self._steps[step_name] = DispatchStep(
                name=step_data.get("name", step_name),
                step_number=step_data.get("step_number", 0),
                phases=phases,
            )

        self._loaded = True
        return True

    def get_step(self, step_name: str) -> Optional[DispatchStep]:
        """Return the DispatchStep for a given step name, or None."""
        return self._steps.get(step_name)

    def get_phases(self, step_name: str, detected_topology: str = None) -> List[DispatchPhase]:
        """Return phases for a step, with checks filtered by topology.

        If detected_topology is provided, checks whose topology list does
        not include the detected topology are removed.  If detected_topology
        is None (not yet known), no filtering is applied.
        """
        step = self._steps.get(step_name)
        if not step:
            return []

        if detected_topology is None:
            return list(step.phases)

        filtered_phases = []
        for phase in step.phases:
            filtered_checks = []
            for chk in phase.checks:
                if chk.topology == "all" or detected_topology in chk.topology:
                    filtered_checks.append(chk)
            # Keep the phase even if empty (gate may still matter)
            filtered_phases.append(
                DispatchPhase(
                    phase=phase.phase,
                    parallel=phase.parallel,
                    gate=phase.gate,
                    checks=filtered_checks,
                )
            )
        return filtered_phases

    def get_all_check_ids(self, step_name: str) -> List[str]:
        """All check IDs in a step (unfiltered), for summary display."""
        step = self._steps.get(step_name)
        if not step:
            return []
        ids = []
        for phase in step.phases:
            for chk in phase.checks:
                if chk.check_id not in ids:
                    ids.append(chk.check_id)
        return ids

    def get_step_name(self, step_name: str) -> str:
        """Return the display name for a step."""
        step = self._steps.get(step_name)
        return step.name if step else step_name

    def get_step_number(self, step_name: str) -> int:
        """Return the step number for a step."""
        step = self._steps.get(step_name)
        return step.step_number if step else 0

    def validate_against_rules(self, loaded_rules: List[RuleDefinition]) -> List[str]:
        """Cross-reference manifest vs loaded YAML rules. Return warnings."""
        warnings = []
        loaded_ids = {r.check_id for r in loaded_rules}

        # Checks in manifest but not in loaded rules
        manifest_ids = set()
        for step in self._steps.values():
            for phase in step.phases:
                for chk in phase.checks:
                    manifest_ids.add(chk.check_id)

        for cid in sorted(manifest_ids - loaded_ids):
            warnings.append(
                f"Dispatch manifest references '{cid}' but no matching YAML rule file was loaded"
            )

        for cid in sorted(loaded_ids - manifest_ids):
            warnings.append(
                f"Rule file '{cid}' exists but is not referenced in the dispatch manifest"
            )

        return warnings


class RulesEngine:
    """Engine for loading and executing health check rules."""

    # TODO: Add CHK_*.yaml health check rules to this directory
    DEFAULT_RULES_PATH = str(Path(__file__).parent / "health_checks")
    CMD_TIMEOUT = 15  # Reduced from 30 to avoid long waits
    MAX_WORKERS = 5
    CIB_PATH = "/var/lib/pacemaker/cib/cib.xml"

    def __init__(
        self, rules_path: str = None, access_config: dict = None, strict_mode: bool = False
    ):
        self.rules_path = Path(rules_path) if rules_path else Path(self.DEFAULT_RULES_PATH)
        self.access_config = access_config or {}
        self.rules: List[RuleDefinition] = []
        self.results: List[CheckResult] = []
        self.strict_mode = strict_mode
        # Track cluster running state and cib.xml availability per node
        self._cluster_running: Dict[str, bool] = {}
        self._cib_available: Dict[str, bool] = {}
        # Track data source information
        self._access_methods_used: Dict[str, str] = (
            {}
        )  # node -> method (ssh, sosreport, local, ansible)
        self._used_cib_xml: bool = False  # True if sos_cmd with cib.xml was used
        # Track HANA resource state (running/stopped/disabled/unmanaged/absent/unknown)
        self._hana_resource_state: str = "unknown"
        # Detected cluster topology (Scale-Up / Scale-Out)
        self._detected_topology: Optional[str] = None
        # Nodes confirmed to not have HANA (from CHK_HANA_INSTALLED)
        self._non_hana_nodes: set = set()

    def set_hana_resource_state(self, state: str):
        """Set the HANA resource state detected by CHK_RESOURCE_STATUS."""
        self._hana_resource_state = state

    def get_hana_resource_state(self) -> str:
        """Get the detected HANA resource state."""
        return self._hana_resource_state

    def set_detected_topology(self, topology: str):
        """Set the detected cluster topology (e.g. 'Scale-Up', 'Scale-Out')."""
        self._detected_topology = topology

    def get_detected_topology(self) -> Optional[str]:
        """Get the detected cluster topology, or None if not yet determined."""
        return self._detected_topology

    def set_non_hana_nodes(self, nodes: set):
        """Set nodes confirmed to not have HANA (from CHK_HANA_INSTALLED)."""
        self._non_hana_nodes = nodes

    def get_data_source_info(self) -> Dict[str, Any]:
        """Get summary of data sources used for checks.

        Returns dict with:
        - access_methods: dict of node -> method used
        - primary_method: most common access method
        - used_cib_xml: whether cib.xml was parsed (cluster not running)
        - description: human-readable description of data source
        """
        methods = self._access_methods_used
        if not methods:
            return {
                "access_methods": {},
                "primary_method": "unknown",
                "used_cib_xml": False,
                "description": "No data collected",
            }

        # Find most common method
        method_counts: Dict[str, int] = {}
        for method in methods.values():
            method_counts[method] = method_counts.get(method, 0) + 1
        primary_method = max(method_counts, key=method_counts.get)

        # Build description
        if primary_method == "sosreport":
            if self._used_cib_xml:
                description = "SOSreport analysis (cluster was stopped - using cib.xml)"
            else:
                description = "SOSreport analysis (offline data)"
        elif primary_method == "ssh":
            description = "Live cluster via SSH"
        elif primary_method == "local":
            description = "Local execution on cluster node"
        elif primary_method == "ansible":
            description = "Live cluster via Ansible"
        else:
            description = f"Data source: {primary_method}"

        return {
            "access_methods": methods,
            "primary_method": primary_method,
            "used_cib_xml": self._used_cib_xml,
            "description": description,
        }

    def get_cluster_resources_config(self) -> Dict[str, Any]:
        """Extract cluster resource configuration for the report.

        Uses the unified CIBParser library to parse cib.xml from either:
        - SOSreport directory
        - Live system (if cluster stopped but cib.xml exists)

        Returns dict with report summary from CIBParser.
        """
        # Find cib.xml from sosreport or live system
        nodes = self.access_config.get("nodes", {})
        parser = None

        # Try SOSreport paths first
        for _node_name, node_info in nodes.items():
            sos_path = node_info.get("sosreport_path")
            if sos_path:
                parser = CIBParser.from_sosreport(sos_path)
                if parser and parser.is_available():
                    break
                parser = None

        # Fall back to live system cib.xml
        if not parser:
            parser = CIBParser.from_live_system()

        if not parser or not parser.is_available():
            return {"available": False}

        # Use unified parser to get report summary
        return parser.get_report_summary()

    def load_rules(self) -> List[RuleDefinition]:
        """Load all CHK_*.yaml rule files."""
        self.rules = []

        if not self.rules_path.exists():
            print(f"[WARNING] Rules path does not exist: {self.rules_path}")
            return self.rules

        rule_files = sorted(self.rules_path.glob("CHK_*.yaml"))
        print(f"Found {len(rule_files)} rule files in {self.rules_path}")

        for rule_file in rule_files:
            try:
                with open(rule_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)

                if not data or not data.get("enabled", True):
                    print(f"  [SKIP] {rule_file.name} (disabled)")
                    continue

                rule = RuleDefinition(
                    check_id=data.get("check_id", rule_file.stem),
                    version=data.get("version", "1.0"),
                    severity=data.get("severity", "WARNING"),
                    description=data.get("description", ""),
                    enabled=data.get("enabled", True),
                    optional=data.get("optional", False),
                    hana_nodes_only=data.get("hana_nodes_only", False),
                    source_definitions=data.get("source_definitions", {}),
                    parser=data.get("parser", {}),
                    validation_logic=data.get("validation_logic", {}),
                    topology_filter=data.get("topology_filter"),
                    requires=data.get("requires"),
                    raw_yaml=data,
                )
                self.rules.append(rule)
                print(f"  [LOAD] {rule.check_id}: {rule.description[:50]}...")

            except Exception as e:
                print(f"  [ERROR] Failed to load {rule_file.name}: {e}")

        return self.rules

    def list_rules(self) -> List[Dict[str, str]]:
        """Return a summary list of loaded rules."""
        return [
            {
                "check_id": r.check_id,
                "severity": r.severity,
                "description": r.description,
                "enabled": r.enabled,
            }
            for r in self.rules
        ]

    def _check_cluster_status(
        self, node: str, method: str = "ssh", user: str = None
    ) -> Tuple[bool, bool]:
        """
        Check if cluster is running and if cib.xml exists on a node.
        Returns (cluster_running, cib_available) tuple.
        Caches results per node.
        """
        if node in self._cluster_running:
            return self._cluster_running[node], self._cib_available.get(node, False)

        # Check if pacemaker is running
        cluster_running = False
        cib_available = False

        check_cmd = "systemctl is-active pacemaker 2>/dev/null"
        success, output = self._execute_command_raw(check_cmd, node, method, user)
        # Check for exact 'active' status (not 'inactive')
        cluster_running = success and output.strip() == "active"

        # Check if cib.xml exists
        cib_check_cmd = f"test -f {self.CIB_PATH} && echo 'exists'"
        success, output = self._execute_command_raw(cib_check_cmd, node, method, user)
        cib_available = success and "exists" in output

        self._cluster_running[node] = cluster_running
        self._cib_available[node] = cib_available

        return cluster_running, cib_available

    def _transform_pcs_for_cib(self, cmd: str) -> str:
        """
        Transform pcs commands to use -f cib.xml for offline cluster queries.
        Only transforms commands that can work with -f flag.
        """
        # Commands that support -f flag for offline queries
        pcs_offline_cmds = ["pcs property", "pcs resource", "pcs stonith", "pcs constraint"]

        for pcs_cmd in pcs_offline_cmds:
            if pcs_cmd in cmd:
                # Insert -f cib.xml after 'pcs'
                # Handle: pcs property -> pcs -f /path/cib.xml property
                # Also handle: pcs resource config -> pcs -f /path/cib.xml resource config
                transformed = cmd.replace(pcs_cmd, f"pcs -f {self.CIB_PATH} {pcs_cmd.split()[1]}", 1)
                return transformed

        return None  # Command cannot be transformed

    def _execute_command_raw(
        self, cmd: str, node: str = None, method: str = "ssh", user: str = None
    ) -> Tuple[bool, str]:
        """Execute a command without fallback logic (internal use)."""
        try:
            if method == "local":
                full_cmd = cmd
            elif node and method == "ssh":
                ssh_user = user or os.environ.get("USER", "root")
                if ssh_user != "root":
                    cmd = f"sudo {cmd}"
                escaped_cmd = cmd.replace("'", "'\"'\"'")
                full_cmd = (
                    f"ssh -o BatchMode=yes -o ConnectTimeout=10 {shlex.quote(f'{ssh_user}@{node}')} '{escaped_cmd}'"
                )
            elif node and method == "ansible":
                escaped_cmd = cmd.replace("'", "'\"'\"'")
                full_cmd = f"ansible {node} -m shell -a '{escaped_cmd}' -o"
            else:
                full_cmd = cmd

            result = subprocess.run(
                full_cmd,
                shell=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=self.CMD_TIMEOUT,
                check=False,
            )

            output = result.stdout
            if method == "ansible" and node:
                if "|" in output and ">>" in output:
                    output = output.split(">>", 1)[-1].strip()

            return result.returncode == 0, output

        except subprocess.TimeoutExpired:
            return False, f"Command timed out after {self.CMD_TIMEOUT}s"
        except Exception as e:
            return False, str(e)

    def _execute_command(
        self, cmd: str, node: str = None, method: str = "ssh", user: str = None
    ) -> Tuple[bool, str]:
        """Execute a command locally, via SSH, or via Ansible.
        Uses pcs -f cib.xml if cluster is not running but cib.xml exists."""
        # For pcs commands, pre-check if cluster is running
        # If not running but cib.xml exists, use -f cib.xml from the start
        if "pcs " in cmd and node:
            cluster_running, cib_available = self._check_cluster_status(node, method, user)

            if not cluster_running and cib_available:
                # Transform pcs command to use -f cib.xml
                transformed_cmd = self._transform_pcs_for_cib(cmd)
                if transformed_cmd:
                    return self._execute_command_raw(transformed_cmd, node, method, user)

        # Execute the original command
        return self._execute_command_raw(cmd, node, method, user)

    def _run_sos_cmd(
        self, sos_cmd: str, sos_cmd_file: str, node: str, sos_base: str
    ) -> Tuple[bool, str]:
        """Run a local command using a file from the sosreport.

        This allows running commands like 'pcs -f {file} resource config'
        where {file} is replaced with the full path to a file in the sosreport.
        """
        import glob as glob_module
        import shutil

        # Check if the command tool exists locally
        cmd_tool = sos_cmd.split()[0]
        if not shutil.which(cmd_tool):
            return False, f"Command '{cmd_tool}' not found locally"

        # Find the file in the sosreport
        sos_base_path = Path(sos_base)

        # Determine the node's sosreport directory
        if (sos_base_path / "etc").exists():
            node_sos = sos_base_path
        else:
            node_sos = sos_base_path / node
            if not node_sos.exists():
                for item in sos_base_path.iterdir():
                    if item.is_dir() and node in item.name:
                        node_sos = item
                        break

        # Find the file (supports glob patterns)
        if "*" in sos_cmd_file or "?" in sos_cmd_file:
            pattern = str(node_sos / sos_cmd_file)
            matches = glob_module.glob(pattern)
            if not matches:
                return False, f"No files matching pattern: {pattern}"
            file_path = matches[0]
        else:
            file_path = str(node_sos / sos_cmd_file)
            if not Path(file_path).exists():
                return False, f"File not found: {file_path}"

        # Replace {file} placeholder with actual path
        full_cmd = sos_cmd.replace("{file}", file_path)

        # Execute locally
        try:
            result = subprocess.run(
                full_cmd,
                shell=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=self.CMD_TIMEOUT,
                check=False,
            )
            return result.returncode == 0, result.stdout
        except subprocess.TimeoutExpired:
            return False, f"Command timed out after {self.CMD_TIMEOUT}s"
        except Exception as e:
            return False, str(e)

    def _read_sosreport(self, sos_path: str, node: str, sos_base: str) -> Tuple[bool, str]:
        """Read data from SOSreport directory."""
        import glob as glob_module

        sos_base_path = Path(sos_base)

        # If sos_base is a direct sosreport path (contains etc/ dir), use it directly
        if (sos_base_path / "etc").exists():
            node_sos = sos_base_path
        else:
            # Build full path - sos_base is a directory containing sosreports
            node_sos = sos_base_path / node
            if not node_sos.exists():
                # Try to find matching sosreport directory
                for item in sos_base_path.iterdir():
                    if item.is_dir() and node in item.name:
                        node_sos = item
                        break

        # Check if sos_path contains glob patterns
        if "*" in sos_path or "?" in sos_path:
            # Use glob to find matching files
            pattern = str(node_sos / sos_path)
            matches = glob_module.glob(pattern)
            if matches:
                # Use first matching file
                try:
                    with open(matches[0], "r", encoding="utf-8") as f:
                        return True, f.read()
                except Exception as e:
                    return False, str(e)
            return False, f"No files matching pattern: {pattern}"

        file_path = node_sos / sos_path
        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    return True, f.read()
            except Exception as e:
                return False, str(e)

        return False, f"File not found: {file_path}"

    def _parse_output(self, output: str, parser_config: Dict) -> Dict[str, Any]:
        """Parse command output using configured parser."""
        parsed = {}

        if parser_config.get("type") != "regex":
            return {"raw": output}

        patterns = parser_config.get("search_patterns", [])
        flags = re.MULTILINE if parser_config.get("multiline", False) else 0

        for pattern in patterns:
            name = pattern.get("name")
            regex = pattern.get("regex")
            group = pattern.get("group", 0)

            if not name or not regex:
                continue

            try:
                match = re.search(regex, output, flags)
                if match:
                    if group == 0:
                        parsed[name] = match.group(0)
                    else:
                        parsed[name] = match.group(group) if group <= len(match.groups()) else None
                else:
                    parsed[name] = None
            except Exception as e:
                parsed[name] = None
                parsed[f"{name}_error"] = str(e)

        return parsed

    def _handle_detection_check(self, rule: RuleDefinition, parsed: Dict, node: str) -> CheckResult:
        """Handle detection-type checks that gather information rather than validate."""
        if rule.check_id == "CHK_CLUSTER_TYPE":
            return self._detect_cluster_type(rule, parsed, node)

        # Default: return parsed data as info
        return CheckResult(
            check_id=rule.check_id,
            description=rule.description,
            status=CheckStatus.PASSED,
            severity=Severity.INFO,
            message="Detection completed",
            details={"parsed": parsed},
            node=node,
        )

    def _detect_cluster_type(self, rule: RuleDefinition, parsed: Dict, node: str) -> CheckResult:
        """Detect SAP HANA HA cluster configuration type.

        Configuration types:
        - Scale-Up: 2 HANA instances (1 per site), clone-max=2
        - Scale-Out: 4+ HANA instances (2+ per site), clone-max>2, has majority maker

        IMPORTANT: SAPHanaController can be used for BOTH Scale-Up and Scale-Out.
        The definitive indicator is clone-max value, NOT the resource agent type.
        hdbnsutil -sr_state validates actual HANA topology (2+ hosts per site = Scale-Out).

        Majority maker detection: by location constraints (primary) or name pattern.
        """
        # Extract parsed values
        node_count_str = parsed.get("node_count")
        saphana_resource = parsed.get("saphana_resource")  # SAPHana_* = older Scale-Up
        saphana_controller = parsed.get("saphana_controller")  # SAPHanaController_* = can be either
        majority_maker = parsed.get("majority_maker")
        majority_maker_node = parsed.get("majority_maker_node")  # Actual node name
        clone_max_str = parsed.get("clone_max")

        # hdbnsutil -sr_state output for Scale-Out validation
        site_hosts_count_str = parsed.get("site_hosts_count")  # Number of hosts per site
        sidadm_user = parsed.get("sidadm_user")
        hdbnsutil_failed = parsed.get("hdbnsutil_failed")

        # Count nodes
        try:
            node_count = int(node_count_str) if node_count_str else 0
        except (ValueError, TypeError):
            node_count = 0

        # Get clone-max value (number of HANA nodes)
        try:
            clone_max = int(clone_max_str) if clone_max_str else 2
        except (ValueError, TypeError):
            clone_max = 2  # Default to Scale-Up assumption

        # Detect based on resource presence
        has_saphana = saphana_resource is not None
        has_controller = saphana_controller is not None
        has_topology = parsed.get("saphana_topology") is not None
        has_hana_resource = has_saphana or has_controller or has_topology
        # Detect if location constraints exclude a node from HANA resources
        # This could indicate a majority maker (Scale-Out) OR an app server (Scale-Up)
        has_constraint_excluded_node = (
            majority_maker is not None and majority_maker != "none"
        ) or (majority_maker_node is not None and majority_maker_node != "none")

        # Validate Scale-Out using hdbnsutil -sr_state
        # True Scale-Out has multiple hosts per site (site_hosts_count > 1)
        hdbnsutil_confirms_scaleout = False
        hdbnsutil_host_count = 0
        if site_hosts_count_str:
            try:
                hdbnsutil_host_count = int(site_hosts_count_str)
                hdbnsutil_confirms_scaleout = hdbnsutil_host_count >= 2
            except (ValueError, TypeError):
                hdbnsutil_host_count = 0

        # SAP HANA Scale-Out architecture:
        #   - At least 2 HANA instances per site (minimum 4 total = clone-max >= 4)
        #   - Minimum 5 nodes: 4 HANA nodes + 1 majority maker
        #   - Majority maker has constraints excluding SAPHanaTopology + SAPHanaController
        #
        # SAP HANA Scale-Up architecture:
        #   - Exactly 1 HANA instance per site (clone-max = 2)
        #   - May have additional app server nodes in the cluster
        #   - App servers may have HANA exclusion constraints (NOT majority makers)
        #
        # Key rule: Majority maker ONLY exists in Scale-Out (clone-max >= 4)
        is_scale_out = clone_max >= 4
        has_majority_maker = is_scale_out and has_constraint_excluded_node

        cluster_type = "Unknown"
        details = {
            "node_count": node_count,
            "clone_max": clone_max,
            "has_saphana_resource": has_saphana,
            "has_saphana_controller": has_controller,
            "has_majority_maker": has_majority_maker,
            "majority_maker_node": majority_maker_node if has_majority_maker else None,
            "hdbnsutil_host_count": hdbnsutil_host_count,
            "hdbnsutil_confirms_scaleout": hdbnsutil_confirms_scaleout,
            "sidadm_user": sidadm_user,
            "parsed": parsed,
        }

        # Decision tree:
        # 1. HANA resources + clone-max → definitive type (clone-max >= 4 = Scale-Out, < 4 = Scale-Up)
        # 2. No HANA resources + hdbnsutil data → inferred type from HANA topology
        # 3. No HANA resources + no hdbnsutil → Unknown
        # 4. node_count is informational, not required for type detection
        if not has_hana_resource:
            if node_count == 0:
                cluster_type = "Not detected"
                message = "Could not detect cluster configuration (no HANA resources found)"
            elif node_count == 1:
                cluster_type = "Single Node"
                message = "Single node configuration (no HA)"
            elif hdbnsutil_host_count >= 2:
                cluster_type = "Scale-Out"
                details["inferred_from_hana_topology"] = True
                message = (
                    f"Scale-Out configuration inferred from HANA topology "
                    f"({hdbnsutil_host_count} hosts per site, {node_count} cluster nodes) "
                    f"- no SAPHana/SAPHanaController resources in CIB"
                )
            elif hdbnsutil_host_count == 1:
                cluster_type = "Scale-Up"
                details["inferred_from_hana_topology"] = True
                message = (
                    f"Scale-Up configuration inferred from HANA topology "
                    f"(1 host per site, {node_count} cluster nodes) "
                    f"- no SAPHana/SAPHanaController resources in CIB"
                )
            else:
                cluster_type = "Unknown"
                message = f"Cluster detected ({node_count} nodes) but no SAP HANA resources found"
        elif is_scale_out:
            # Scale-Out: clone-max >= 4 (at least 2 HANA instances per site)
            cluster_type = "Scale-Out"
            hana_nodes = clone_max

            if has_majority_maker:
                mm_info = f" [{majority_maker_node}]" if majority_maker_node else ""
                base_message = (
                    f"Scale-Out configuration ({hana_nodes} HANA nodes + majority maker{mm_info})"
                )
            else:
                base_message = f"Scale-Out configuration ({hana_nodes} HANA nodes) - WARNING: no majority maker detected"

            # Validate with hdbnsutil -sr_state
            if hdbnsutil_failed:
                message = (
                    f"{base_message} - NOTE: could not verify with hdbnsutil ({hdbnsutil_failed})"
                )
            elif hdbnsutil_confirms_scaleout:
                message = (
                    f"{base_message} - verified: {hdbnsutil_host_count} HANA instances per site"
                )
            elif hdbnsutil_host_count == 1:
                message = f"{base_message} - WARNING: hdbnsutil shows only 1 HANA instance per site"
            else:
                message = base_message
            details["hana_nodes"] = hana_nodes
        else:
            # Scale-Up: clone-max < 4 (1 HANA instance per site, typically clone-max=2)
            cluster_type = "Scale-Up"
            extra_nodes = max(0, node_count - clone_max) if node_count > 0 else 0
            if node_count in (2, 0):
                message = f"Scale-Up configuration ({clone_max} HANA nodes)"
            elif extra_nodes > 0 and has_constraint_excluded_node:
                # Extra node with HANA exclusion constraints = app server, NOT majority maker
                message = f"Scale-Up configuration ({clone_max} HANA nodes, {extra_nodes} app server node(s))"
            elif extra_nodes > 0:
                message = f"Scale-Up configuration ({clone_max} HANA nodes, {extra_nodes} additional node(s))"
            else:
                message = f"Scale-Up configuration ({clone_max} HANA nodes)"
            details["hana_nodes"] = clone_max

        details["cluster_type"] = cluster_type

        return CheckResult(
            check_id=rule.check_id,
            description=rule.description,
            status=CheckStatus.PASSED,
            severity=Severity.INFO,
            message=message,
            details=details,
            node=node,
        )

    def _validate_clone_max(self, rule: RuleDefinition, parsed: Dict, node: str) -> CheckResult:
        """Validate clone-max setting for SAPHanaController and SAPHanaTopology.

        For Scale-Out clusters:
        - clone-max should equal the number of HANA nodes (total nodes - majority makers)
        - clone-node-max should be 1
        - interleave should be true
        """
        issues = []
        info = []

        # Get clone-max values
        controller_clone_max = parsed.get("controller_clone_max")
        topology_clone_max = parsed.get("topology_clone_max")
        controller_clone_node_max = parsed.get("controller_clone_node_max")
        topology_clone_node_max = parsed.get("topology_clone_node_max")
        controller_interleave = parsed.get("controller_interleave")
        topology_interleave = parsed.get("topology_interleave")
        controller_promotable = parsed.get("controller_promotable")

        # Check if we have any data
        if not controller_clone_max and not topology_clone_max:
            # No clone config found - might be using pcs -f cib.xml or cluster not running
            return CheckResult(
                check_id=rule.check_id,
                description=rule.description,
                status=CheckStatus.PASSED,
                severity=Severity.INFO,
                message="Clone configuration not available (cluster may be stopped)",
                details={"parsed": parsed},
                node=node,
            )

        # Validate clone-node-max = 1
        if controller_clone_node_max and controller_clone_node_max != "1":
            issues.append(
                f"SAPHanaController clone-node-max={controller_clone_node_max} (should be 1)"
            )
        if topology_clone_node_max and topology_clone_node_max != "1":
            issues.append(f"SAPHanaTopology clone-node-max={topology_clone_node_max} (should be 1)")

        # Validate interleave = true
        if controller_interleave and controller_interleave != "true":
            issues.append(f"SAPHanaController interleave={controller_interleave} (should be true)")
        if topology_interleave and topology_interleave != "true":
            issues.append(f"SAPHanaTopology interleave={topology_interleave} (should be true)")

        # Validate promotable = true for controller
        if controller_promotable and controller_promotable != "true":
            issues.append(f"SAPHanaController promotable={controller_promotable} (should be true)")

        # Report clone-max values (informational - we can't validate without knowing HANA node count)
        if controller_clone_max:
            info.append(f"SAPHanaController clone-max={controller_clone_max}")
        if topology_clone_max:
            info.append(f"SAPHanaTopology clone-max={topology_clone_max}")

        # Check if controller and topology have matching clone-max
        if (
            controller_clone_max
            and topology_clone_max
            and controller_clone_max != topology_clone_max
        ):
            issues.append(
                f"clone-max mismatch: Controller={controller_clone_max}, Topology={topology_clone_max}"
            )

        if issues:
            return CheckResult(
                check_id=rule.check_id,
                description=rule.description,
                status=CheckStatus.FAILED,
                severity=Severity.WARNING,
                message="; ".join(issues),
                details={"parsed": parsed, "issues": issues},
                node=node,
            )

        # All checks passed
        message = "Clone configuration valid"
        if info:
            message += f" ({'; '.join(info)})"

        return CheckResult(
            check_id=rule.check_id,
            description=rule.description,
            status=CheckStatus.PASSED,
            severity=Severity.INFO,
            message=message,
            details={"parsed": parsed, "info": info},
            node=node,
        )

    # ------------------------------------------------------------------
    # HA/DR provider hook validation (CHK_HADR_HOOKS v2.0)
    # ------------------------------------------------------------------

    def _validate_hadr_hooks(
        self, rule: RuleDefinition, _parsed: Dict, node: str, raw_output: str
    ) -> CheckResult:
        """Architecture-aware HA/DR provider hook validation.

        Uses context from prior checks (CHK_CLUSTER_TYPE, CHK_PACKAGE_CONSISTENCY,
        CHK_HANA_INSTALLED) plus the collected raw_output (global.ini, sudoers,
        provider files, packages, RHEL version).

        Skips when running from SOSreport (insufficient data).
        """
        try:
            from ..lib.hadr_provider import (
                has_required_data,
                parse_collected_output,
                get_expected_config,
                validate_rhel_arch_compatibility,
                HadrValidator,
            )
            from ..lib.hadr_provider.suggestions import format_finding_message
        except ImportError:
            return CheckResult(
                check_id=rule.check_id,
                description=rule.description,
                status=CheckStatus.SKIPPED,
                severity=Severity[rule.severity],
                message="hadr_provider module not available (check HA_DR_PROVIDER installation)",
                node=node,
            )

        # Skip if data is insufficient (SOSreport mode or empty output)
        if not has_required_data(raw_output):
            return CheckResult(
                check_id=rule.check_id,
                description=rule.description,
                status=CheckStatus.SKIPPED,
                severity=Severity[rule.severity],
                message="Skipped: requires SSH/local access (SOSreport data insufficient)",
                node=node,
            )

        # Gather context from prior check results
        rhel_major = self._get_rhel_major()
        topology = self._get_hadr_topology()
        sid = self._get_hadr_sid()
        arch_type = self._detect_hadr_arch_type()

        if not sid:
            return CheckResult(
                check_id=rule.check_id,
                description=rule.description,
                status=CheckStatus.SKIPPED,
                severity=Severity[rule.severity],
                message="Skipped: SID not detected (CHK_HANA_INSTALLED may have failed)",
                node=node,
            )

        if arch_type is None:
            return CheckResult(
                check_id=rule.check_id,
                description=rule.description,
                status=CheckStatus.SKIPPED,
                severity=Severity[rule.severity],
                message="Skipped: resource agent package not detected (sap-hana-ha / resource-agents-sap-hana)",
                node=node,
            )

        # Validate RHEL/arch compatibility
        compatible, compat_msg = validate_rhel_arch_compatibility(rhel_major, arch_type)
        if not compatible:
            return CheckResult(
                check_id=rule.check_id,
                description=rule.description,
                status=CheckStatus.FAILED,
                severity=Severity.CRITICAL,
                message=compat_msg,
                details={"rhel_major": rhel_major, "arch_type": arch_type.value},
                node=node,
            )

        # Get expected config and parse actual config
        expected = get_expected_config(rhel_major, topology, arch_type, sid)
        actual = parse_collected_output(raw_output, node, sid)

        # Validate
        findings = HadrValidator().validate(actual, expected)

        if not findings:
            return CheckResult(
                check_id=rule.check_id,
                description=rule.description,
                status=CheckStatus.PASSED,
                severity=Severity.INFO,
                message=(
                    f"HA/DR hooks correctly configured "
                    f"({arch_type.value}, {topology.value}, RHEL {rhel_major})"
                ),
                details={
                    "arch_type": arch_type.value,
                    "topology": topology.value,
                    "rhel_major": rhel_major,
                    "hooks": [h.section_name for h in expected.hooks if not h.is_optional],
                },
                node=node,
            )

        # Build result with findings
        critical_findings = [f for f in findings if f.severity == "CRITICAL"]
        warning_findings = [f for f in findings if f.severity == "WARNING"]
        info_findings = [f for f in findings if f.severity == "INFO"]

        max_severity = (
            "CRITICAL" if critical_findings else ("WARNING" if warning_findings else "INFO")
        )

        messages = [format_finding_message(f) for f in findings if f.severity != "INFO"]
        summary = "; ".join(messages[:3])
        if len(messages) > 3:
            summary += f" (+{len(messages) - 3} more)"

        return CheckResult(
            check_id=rule.check_id,
            description=rule.description,
            status=CheckStatus.FAILED if max_severity != "INFO" else CheckStatus.PASSED,
            severity=Severity[max_severity],
            message=summary or f"HA/DR hooks: {len(info_findings)} info note(s)",
            details={
                "arch_type": arch_type.value,
                "topology": topology.value,
                "rhel_major": rhel_major,
                "findings": [
                    {
                        "category": f.category,
                        "severity": f.severity,
                        "what_is_wrong": f.what_is_wrong,
                        "expected": f.expected_value,
                        "actual": f.actual_value,
                        "fix": f.fix_description,
                        "fix_command": f.fix_command,
                    }
                    for f in findings
                ],
                "total_findings": len(findings),
                "critical_count": len(critical_findings),
                "warning_count": len(warning_findings),
                "info_count": len(info_findings),
            },
            node=node,
        )

    def _get_rhel_major(self) -> int:
        """Get RHEL major version from access config or prior results."""
        # Try access config first (set during discovery)
        if hasattr(self.access_config, "clusters"):
            for cluster_info in self.access_config.clusters.values():
                rv = cluster_info.get("rhel_version", "")
                match = re.search(r"(\d+)", str(rv))
                if match:
                    return int(match.group(1))
        elif isinstance(self.access_config, dict):
            rv = self.access_config.get("rhel_version", "")
            match = re.search(r"(\d+)", str(rv))
            if match:
                return int(match.group(1))
        # Fallback: check CHK_PACKAGE_CONSISTENCY results for el<N> in package names
        for result in self.results:
            if result.check_id == "CHK_PACKAGE_CONSISTENCY" and result.details:
                parsed = result.details.get("parsed", {})
                for _key, val in parsed.items():
                    if val:
                        el_match = re.search(r"\.el(\d+)", str(val))
                        if el_match:
                            return int(el_match.group(1))
        return 9  # Safe default (RHEL 9 supports both ANGI and Legacy)

    def _get_hadr_topology(self):
        """Get cluster topology from CHK_CLUSTER_TYPE result."""
        from ..lib.hadr_provider.models import Topology

        for result in self.results:
            if result.check_id == "CHK_CLUSTER_TYPE" and result.details:
                ct = result.details.get("cluster_type", "Scale-Up")
                if ct == "Scale-Out":
                    return Topology.SCALE_OUT
        return Topology.SCALE_UP

    def _get_hadr_sid(self) -> str:
        """Get SID from CHK_HANA_INSTALLED result."""
        for result in self.results:
            if result.check_id == "CHK_HANA_INSTALLED" and result.details:
                parsed = result.details.get("parsed", {})
                sid = parsed.get("sid")
                if sid:
                    return sid
        # Fallback: try the cluster config
        resource_config = self.get_cluster_resources_config()
        return resource_config.get("sid", "")

    def _detect_hadr_arch_type(self):
        """Detect resource agent arch type from CHK_PACKAGE_CONSISTENCY results."""
        from ..lib.hadr_provider.config_matrix import detect_arch_type

        for result in self.results:
            if result.check_id == "CHK_PACKAGE_CONSISTENCY" and result.details:
                parsed = result.details.get("parsed", {})
                packages = []
                for key in (
                    "sap_hana_ha_version",
                    "resource_agents_sap_hana",
                    "resource_agents_sap_hana_scaleout",
                ):
                    val = parsed.get(key)
                    if val:
                        packages.append(val)
                arch = detect_arch_type(packages)
                if arch is not None:
                    return arch
        return None

    def _evaluate_expectation(self, parsed: Dict, expectation: Dict) -> Tuple[bool, str, str]:
        """Evaluate a single expectation against parsed data.

        Returns: (passed, fail_message, pass_message)

        Special operators:
        - info_if_exists: Always passes, but shows pass_message if key exists (informational)
        """
        key = expectation.get("key")
        operator = expectation.get("operator")
        expected = expectation.get("value")
        message = expectation.get("message", f"Check failed for {key}")
        pass_message = expectation.get(
            "pass_message"
        )  # Optional message shown when expectation passes

        actual = parsed.get(key)

        # Support template variables in pass_message: ${key} is replaced with parsed[key]
        if pass_message and "${" in pass_message:

            def replace_var(match):
                var_name = match.group(1)
                return str(parsed.get(var_name, f"${{{var_name}}}"))

            pass_message = re.sub(r"\$\{(\w+)\}", replace_var, pass_message)

        # Handle info_if_exists: always passes, shows message if key exists
        if operator == "info_if_exists":
            if actual is not None and pass_message:
                return True, message, pass_message
            return True, message, None

        if operator == "exists":
            # 'exists' checks if the key has a non-None value
            # If value is specified as False, check that key does NOT exist
            if expected is False:
                passed = actual is None
            else:
                # Default: pass if actual exists (is not None)
                passed = actual is not None
        elif operator == "not_exists":
            passed = actual is None
        elif operator == "eq":
            passed = actual == expected
        elif operator == "ne":
            passed = actual != expected
        elif operator == "in":
            passed = actual in expected if isinstance(expected, list) else actual == expected
        elif operator == "not_in":
            passed = actual not in expected if isinstance(expected, list) else actual != expected
        elif operator == "contains":
            passed = expected in str(actual) if actual else False
        elif operator == "regex":
            passed = bool(re.search(expected, str(actual))) if actual else False
        elif operator == "gt":
            try:
                passed = float(actual) > float(expected)
            except (TypeError, ValueError):
                passed = False
        elif operator == "lt":
            try:
                passed = float(actual) < float(expected)
            except (TypeError, ValueError):
                passed = False
        else:
            passed = False
            message = f"Unknown operator: {operator}"

        return passed, message, pass_message if passed else None

    def _check_command_available(self, cmd: str, node: str, method: str, user: str = None) -> tuple:
        """
        Quick check if any command in a pipeline/fallback chain is available.
        Returns (available: bool, reason: str)

        Handles:
        - Simple commands: 'SAPHanaSR-showAttr'
        - Pipelines: 'cmd1 | grep foo'
        - Fallbacks: 'cmd1 || cmd2' (if cmd1 not available, check cmd2)
        - Multi-line scripts with comments
        - Shell constructs (if/for/while)
        """
        builtins = [
            "grep",
            "cat",
            "echo",
            "awk",
            "sed",
            "head",
            "tail",
            "cut",
            "tr",
            "sort",
            "timeout",
            "if",
            "for",
            "while",
            "then",
            "else",
            "fi",
            "do",
            "done",
            "case",
            "esac",
            "ls",
            "test",
            "[",
        ]

        def extract_cmd_name(cmd_part: str) -> str:
            """Extract the primary command name from a command string."""
            # Remove leading whitespace
            cmd_part = cmd_part.strip()

            # Skip empty parts
            if not cmd_part:
                return ""

            # For multi-line commands, find first non-comment, non-empty line
            lines = cmd_part.split("\n")
            for line in lines:
                line = line.strip()
                if line and not line.startswith("#"):
                    # Split on pipe to get first command in pipeline
                    first_part = line.split("|")[0].split(";")[0].split("&&")[0].strip()
                    # Get the command name (first word)
                    cmd_name = first_part.split()[0] if first_part else ""
                    if cmd_name and not cmd_name.startswith("#"):
                        # Handle variable assignments: VAR=$(cmd ...) or VAR=val cmd
                        if "=" in cmd_name:
                            # VAR=$(cmd ...) - extract command from subshell
                            after_eq = cmd_name.split("=", 1)[1]
                            if after_eq.startswith("$("):
                                cmd_name = after_eq[2:].rstrip(")")
                            elif after_eq.startswith("`"):
                                cmd_name = after_eq[1:].rstrip("`")
                            else:
                                # VAR=value (no command), skip to next line
                                continue
                        return cmd_name
            return ""

        # Split on '||' to handle fallback commands
        fallback_parts = cmd.split("||")

        for part in fallback_parts:
            cmd_name = extract_cmd_name(part)

            # Skip empty command names
            if not cmd_name:
                continue

            # Skip check for built-in commands and common utilities
            if cmd_name in builtins or cmd_name.startswith("/"):
                return True, "builtin/path"

            # Check if command exists (locally or on remote node)
            check_cmd = f"command -v {cmd_name} >/dev/null 2>&1 && echo 'OK' || echo 'MISSING'"
            success, output = self._execute_command(check_cmd, node, method, user)

            if success and "OK" in output:
                return True, f"{cmd_name} available"

        # If we get here, either all commands were builtins (which is fine) or none were found
        all_cmds = [extract_cmd_name(p) for p in fallback_parts if extract_cmd_name(p)]
        if not all_cmds:
            # All commands were builtins/shell constructs
            return True, "shell script"
        return False, f"Commands not found on {node}: {', '.join(all_cmds)}"

    def _run_check_on_node(  # pylint: disable=unknown-option-value,too-many-positional-arguments
        self, rule: RuleDefinition, node: str, method: str, user: str = None, sos_base: str = None
    ) -> CheckResult:
        """Run a single check on a specific node."""
        source_defs = rule.source_definitions

        # Get data based on access method
        if method == "sosreport" and sos_base:
            sos_path = source_defs.get("sos_path")
            alternates = source_defs.get("sos_path_alternates", [])
            sos_cmd = source_defs.get("sos_cmd")
            sos_cmd_file = source_defs.get("sos_cmd_file")

            success = False
            output = ""

            # Try sos_path first (has actual SOSreport data, e.g. crm_mon output)
            if sos_path:
                success, output = self._read_sosreport(sos_path, node, sos_base)
                self._access_methods_used[node] = "sosreport"

            # If sos_path failed/missing, try sos_cmd (pcs -f cib.xml fallback)
            if not success or (success and output.strip().startswith("Error:")):
                if sos_cmd and sos_cmd_file:
                    cmd_success, cmd_output = self._run_sos_cmd(
                        sos_cmd, sos_cmd_file, node, sos_base
                    )
                    if cmd_success and cmd_output.strip():
                        success, output = cmd_success, cmd_output
                        # Only mark as used_cib_xml if a sos_path was defined but
                        # failed (true fallback = cluster was likely stopped).
                        # If no sos_path exists, sos_cmd is the primary source
                        # by design (e.g. CHK_CLUSTER_TYPE) - not a fallback.
                        if sos_path:
                            self._used_cib_xml = True
                        self._access_methods_used[node] = "sosreport"

            # Try file alternates as last resort
            if not success or (success and output.strip().startswith("Error:")):
                for alt_path in alternates:
                    alt_success, alt_output = self._read_sosreport(alt_path, node, sos_base)
                    if alt_success and not alt_output.strip().startswith("Error:"):
                        success, output = alt_success, alt_output
                        break
        else:
            cmd = source_defs.get("live_cmd")
            if not cmd:
                return CheckResult(
                    check_id=rule.check_id,
                    description=rule.description,
                    status=CheckStatus.SKIPPED,
                    severity=Severity[rule.severity],
                    message="No live command defined",
                    node=node,
                )

            # Pre-flight check: verify primary command is available
            preflight = source_defs.get("preflight_check", True)
            if preflight:
                cmd_available, reason = self._check_command_available(cmd, node, method, user)
                if not cmd_available:
                    return CheckResult(
                        check_id=rule.check_id,
                        description=rule.description,
                        status=CheckStatus.SKIPPED,
                        severity=Severity[rule.severity],
                        message=f"Skipped: {reason}",
                        node=node,
                    )

            success, output = self._execute_command(cmd, node, method, user)
            self._access_methods_used[node] = method

        if not success:
            return CheckResult(
                check_id=rule.check_id,
                description=rule.description,
                status=CheckStatus.ERROR,
                severity=Severity[rule.severity],
                message=f"Failed to get data: {output[:100]}",
                node=node,
            )

        # Parse output
        parsed = self._parse_output(output, rule.parser)

        # Handle detection-type checks (e.g., CHK_CLUSTER_TYPE)
        validation = rule.validation_logic
        if validation.get("type") == "detection":
            return self._handle_detection_check(rule, parsed, node)

        # Handle custom checks (e.g., clone_max_validation, hadr_hooks_validation)
        custom_check = validation.get("custom_check")
        if custom_check == "clone_max_validation":
            return self._validate_clone_max(rule, parsed, node)
        if custom_check == "hadr_hooks_validation":
            return self._validate_hadr_hooks(rule, parsed, node, output)

        # Evaluate expectations
        expectations = validation.get("expectations", [])
        match_mode = validation.get("match_mode", "all")  # 'all' (default) or 'any'

        failed_expectations = []
        passed_expectations = []
        info_messages = []  # Collect informational messages from passing expectations
        for exp in expectations:
            passed, message, pass_msg = self._evaluate_expectation(parsed, exp)
            if not passed:
                failed_expectations.append(
                    {
                        "key": exp.get("key"),
                        "severity": exp.get("severity", rule.severity),
                        "message": message,
                    }
                )
            else:
                passed_expectations.append(exp)
                if pass_msg:
                    info_messages.append(pass_msg)

        # match_mode: any - pass if at least one expectation passes
        # match_mode: all (default) - fail if any expectation fails
        check_failed = False
        if match_mode == "any":
            # Pass if ANY expectation passed
            check_failed = len(passed_expectations) == 0
        else:
            # Fail if ANY expectation failed
            check_failed = len(failed_expectations) > 0

        if check_failed:
            # Use highest severity from failed expectations
            max_severity = rule.severity
            for fe in failed_expectations:
                if fe["severity"] == "CRITICAL":
                    max_severity = "CRITICAL"
                    break
                if fe["severity"] == "WARNING" and max_severity != "CRITICAL":
                    max_severity = "WARNING"

            # In non-strict mode, downgrade optional checks from CRITICAL to WARNING
            if rule.optional and not self.strict_mode and max_severity == "CRITICAL":
                max_severity = "WARNING"

            return CheckResult(
                check_id=rule.check_id,
                description=rule.description,
                status=CheckStatus.FAILED,
                severity=Severity[max_severity],
                message="; ".join(fe["message"] for fe in failed_expectations),
                details={
                    "parsed": parsed,
                    "failed": failed_expectations,
                    "optional": rule.optional,
                },
                node=node,
            )

        # Build result message - include info messages if any
        result_message = "All checks passed"
        if info_messages:
            result_message = "; ".join(info_messages)

        return CheckResult(
            check_id=rule.check_id,
            description=rule.description,
            status=CheckStatus.PASSED,
            severity=Severity[rule.severity],
            message=result_message,
            details={"parsed": parsed, "info_messages": info_messages},
            node=node,
        )

    def run_check(self, rule: RuleDefinition, nodes: Dict[str, dict]) -> List[CheckResult]:
        """
        Run a check across nodes based on scope.

        Scope modes:
        - per_node: Check each node independently (default)
        - any_node: Pass if at least one node passes
        - all_nodes_equal: All nodes must return the same parsed values
        - cluster: Run only on one node (cluster-wide info)
        """
        results = []

        # Check requires dependency - skip if required check did not pass
        # NOTE: This only works when results are accumulated in self.results
        # (i.e., via run_all_checks). The orchestrator handles gating separately
        # for the parallel execution path via _run_rules_parallel.
        if rule.requires:
            required_passed = any(
                r.check_id == rule.requires and r.status == CheckStatus.PASSED for r in self.results
            )
            if not required_passed:
                return [
                    CheckResult(
                        check_id=rule.check_id,
                        description=rule.description,
                        status=CheckStatus.SKIPPED,
                        severity=Severity.WARNING,
                        message=f"Skipped: required check {rule.requires} did not pass",
                        node=None,
                    )
                ]

        # Check topology_filter - skip if rule specifies a topology that
        # doesn't match the detected topology (engine-level safety net)
        if rule.topology_filter and self._detected_topology:
            allowed = rule.topology_filter
            if isinstance(allowed, str):
                allowed = [allowed]
            if self._detected_topology not in allowed:
                return [
                    CheckResult(
                        check_id=rule.check_id,
                        description=rule.description,
                        status=CheckStatus.SKIPPED,
                        severity=Severity.INFO,
                        message=f"Not applicable for {self._detected_topology} topology",
                        node=None,
                    )
                ]

        scope = rule.validation_logic.get("scope", "per_node")
        compare_keys = rule.validation_logic.get("compare_keys", [])

        # For 'cluster' scope, only run on first accessible node
        # If cluster_retry_if is set and the parsed field has a value, try the next node
        # (e.g., hdbnsutil_failed on majority maker → retry on a real HANA node)
        if scope == "cluster":
            retry_key = rule.validation_logic.get("cluster_retry_if")
            fallback_result = None
            for node_name, node_info in nodes.items():
                method = node_info.get("preferred_method")
                if method:
                    user = node_info.get("ssh_user") or node_info.get("ansible_user")
                    # Use node's specific sosreport_path if available, otherwise fall back to sosreport_directory
                    sos_base = node_info.get("sosreport_path") or self.access_config.get(
                        "sosreport_directory"
                    )
                    result = self._run_check_on_node(rule, node_name, method, user, sos_base)
                    result.node = f"{node_name} (cluster)"
                    # Check if result is incomplete and we should try next node
                    if retry_key and fallback_result is None:
                        parsed = result.details.get("parsed", {}) if result.details else {}
                        if parsed.get(retry_key):
                            fallback_result = result
                            continue  # Try next node
                    return [result]
            if fallback_result:
                return [fallback_result]
            # No accessible node
            return [
                CheckResult(
                    check_id=rule.check_id,
                    description=rule.description,
                    status=CheckStatus.SKIPPED,
                    severity=Severity[rule.severity],
                    message="No accessible node for cluster check",
                    node=None,
                )
            ]

        # Get nodes excluded from HANA by constraints (app servers or majority makers)
        hana_excluded_nodes = set()
        if rule.hana_nodes_only:
            resource_config = self.get_cluster_resources_config()
            if resource_config.get("available"):
                # hana_excluded_node: node with SAPHanaTopology + SAPHanaController exclusion constraints
                excluded = resource_config.get("hana_excluded_node")
                if excluded:
                    hana_excluded_nodes.add(excluded)
                # Also check legacy majority_maker field
                mm_node = resource_config.get("majority_maker")
                if mm_node:
                    hana_excluded_nodes.add(mm_node)
            # Fallback: nodes confirmed to not have HANA (from CHK_HANA_INSTALLED)
            if self._non_hana_nodes:
                hana_excluded_nodes |= self._non_hana_nodes

        # Run on all nodes (multithreaded)
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            futures = {}

            for node_name, node_info in nodes.items():
                # Skip nodes excluded from HANA resources by constraints
                if rule.hana_nodes_only and node_name in hana_excluded_nodes:
                    results.append(
                        CheckResult(
                            check_id=rule.check_id,
                            description=rule.description,
                            status=CheckStatus.SKIPPED,
                            severity=Severity[rule.severity],
                            message="Node excluded from HANA resources by constraints",
                            node=node_name,
                        )
                    )
                    continue

                method = node_info.get("preferred_method")
                if not method:
                    results.append(
                        CheckResult(
                            check_id=rule.check_id,
                            description=rule.description,
                            status=CheckStatus.SKIPPED,
                            severity=Severity[rule.severity],
                            message="No access method available",
                            node=node_name,
                        )
                    )
                    continue

                user = node_info.get("ssh_user") or node_info.get("ansible_user")
                # Use node's specific sosreport_path if available, otherwise fall back to sosreport_directory
                sos_path = node_info.get("sosreport_path") or self.access_config.get(
                    "sosreport_directory"
                )

                future = executor.submit(
                    self._run_check_on_node, rule, node_name, method, user, sos_path
                )
                futures[future] = node_name

            for future in as_completed(futures):
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    results.append(
                        CheckResult(
                            check_id=rule.check_id,
                            description=rule.description,
                            status=CheckStatus.ERROR,
                            severity=Severity[rule.severity],
                            message=str(e),
                            node=futures[future],
                        )
                    )

        # Handle scope-specific logic
        if scope == "any_node":
            # Pass if at least one node passed
            passed = [r for r in results if r.status == CheckStatus.PASSED]
            if passed:
                return [
                    CheckResult(
                        check_id=rule.check_id,
                        description=rule.description,
                        status=CheckStatus.PASSED,
                        severity=Severity[rule.severity],
                        message=f"Passed on {len(passed)}/{len(results)} node(s)",
                        details={"passed_nodes": [r.node for r in passed]},
                        node=None,
                    )
                ]
            return [
                CheckResult(
                    check_id=rule.check_id,
                    description=rule.description,
                    status=CheckStatus.FAILED,
                    severity=Severity[rule.severity],
                    message="Failed on all nodes",
                    details={"results": [{"node": r.node, "message": r.message} for r in results]},
                    node=None,
                )
            ]

        if scope == "all_nodes_equal":
            # All nodes must have the same values for compare_keys
            passed_results = [r for r in results if r.status == CheckStatus.PASSED]
            if len(passed_results) < 2:
                return results  # Not enough nodes to compare

            # Get values to compare
            if not compare_keys:
                # Use all parsed keys
                compare_keys = list(passed_results[0].details.get("parsed", {}).keys())

            # Compare values across nodes
            mismatches = []
            reference_node = passed_results[0].node
            reference_values = passed_results[0].details.get("parsed", {})

            for result in passed_results[1:]:
                node_values = result.details.get("parsed", {})
                for key in compare_keys:
                    ref_val = reference_values.get(key)
                    node_val = node_values.get(key)
                    if ref_val != node_val:
                        mismatches.append(
                            {
                                "key": key,
                                "node": result.node,
                                "expected": ref_val,
                                "actual": node_val,
                            }
                        )

            if mismatches:
                # SAP HANA package keys - differences in these on majority maker nodes are expected
                sap_hana_package_keys = {
                    "sap_hana_ha_version",
                    "resource_agents_sap_hana",
                    "resource_agents_sap_hana_scaleout",
                    "saphanasr_version",
                }

                # Build detailed message about differences
                missing_packages = []  # Package exists on some nodes but not others
                version_diffs = []  # Same package, different versions
                critical_mismatches = []  # Non-SAP-HANA package mismatches (always report)
                sap_hana_mismatches = (
                    []
                )  # SAP HANA package mismatches (may be expected on majority maker)

                for m in mismatches:
                    key = m["key"]
                    expected = m.get("expected")
                    actual = m.get("actual")
                    is_sap_hana_pkg = key in sap_hana_package_keys

                    if expected is None and actual is not None:
                        # Package only on this node (extra package)
                        msg = f"{key}: only on {m['node']} ({actual})"
                        missing_packages.append(msg)
                        if is_sap_hana_pkg:
                            sap_hana_mismatches.append(m)
                        else:
                            critical_mismatches.append(m)
                    elif expected is not None and actual is None:
                        # Package missing on this node
                        msg = f"{key}: missing on {m['node']} (reference: {expected})"
                        missing_packages.append(msg)
                        if is_sap_hana_pkg:
                            sap_hana_mismatches.append(m)
                        else:
                            critical_mismatches.append(m)
                    elif expected != actual:
                        # Different versions
                        msg = f"{key}: {m['node']} has {actual} (reference: {expected})"
                        version_diffs.append(msg)
                        if is_sap_hana_pkg:
                            sap_hana_mismatches.append(m)
                        else:
                            critical_mismatches.append(m)

                # Build version_table for structured rendering (e.g., PDF comparison table)
                # Maps each mismatched key to {node: version_string}
                version_table = {}
                for m in mismatches:
                    key = m["key"]
                    if key not in version_table:
                        ref_val = reference_values.get(key)
                        version_table[key] = {
                            reference_node: ref_val if ref_val else "not installed"
                        }
                    actual = m.get("actual")
                    version_table[key][m["node"]] = actual if actual else "not installed"

                # Determine status: if only SAP HANA package differences, it's INFO (expected for majority maker)
                only_sap_hana_diffs = len(critical_mismatches) == 0 and len(sap_hana_mismatches) > 0

                # Build concise message listing only which packages differ
                diff_keys = sorted(set(m["key"] for m in mismatches))
                pkg_short_names = {
                    "pacemaker_version": "pacemaker",
                    "corosync_version": "corosync",
                    "sap_hana_ha_version": "sap-hana-ha",
                    "resource_agents_sap_hana": "resource-agents-sap-hana",
                    "resource_agents_sap_hana_scaleout": "res-agents-sap-hana-scaleout",
                    "saphanasr_version": "SAPHanaSR",
                }
                diff_names = [pkg_short_names.get(k, k) for k in diff_keys]

                if only_sap_hana_diffs:
                    message = f"Differs: {', '.join(diff_names)} (expected for MajorityMaker)"
                    results.append(
                        CheckResult(
                            check_id=rule.check_id,
                            description=rule.description,
                            status=CheckStatus.PASSED,
                            severity=Severity.INFO,
                            message=message,
                            details={
                                "mismatches": mismatches,
                                "reference_node": reference_node,
                                "majority_maker_expected": True,
                                "version_table": version_table,
                            },
                            node="(comparison)",
                        )
                    )
                else:
                    message = f"Differs across nodes: {', '.join(diff_names)}"
                    # Add a comparison failure result
                    results.append(
                        CheckResult(
                            check_id=rule.check_id,
                            description=rule.description,
                            status=CheckStatus.FAILED,
                            severity=Severity[rule.severity],
                            message=message,
                            details={
                                "mismatches": mismatches,
                                "reference_node": reference_node,
                                "version_table": version_table,
                            },
                            node="(comparison)",
                        )
                    )

        return results

    def run_all_checks(self, nodes: Dict[str, dict]) -> List[CheckResult]:
        """Run all loaded checks on all nodes."""
        self.results = []

        if not self.rules:
            self.load_rules()

        print(f"\nRunning {len(self.rules)} checks on {len(nodes)} node(s)...")

        for rule in self.rules:
            print(f"\n  [{rule.severity}] {rule.check_id}: {rule.description[:40]}...")
            check_results = self.run_check(rule, nodes)

            for result in check_results:
                self.results.append(result)
                status_icon = {
                    CheckStatus.PASSED: "✓",
                    CheckStatus.FAILED: "✗",
                    CheckStatus.SKIPPED: "○",
                    CheckStatus.ERROR: "!",
                }.get(result.status, "?")
                node_str = f" ({result.node})" if result.node else ""
                print(f"    {status_icon} {result.status.value}{node_str}: {result.message[:60]}")

        return self.results

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all check results."""
        summary = {
            "total": len(self.results),
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "errors": 0,
            "critical_failures": [],
            "warnings": [],
        }

        for result in self.results:
            if result.status == CheckStatus.PASSED:
                summary["passed"] += 1
            elif result.status == CheckStatus.FAILED:
                summary["failed"] += 1
                if result.severity == Severity.CRITICAL:
                    summary["critical_failures"].append(result)
                else:
                    summary["warnings"].append(result)
            elif result.status == CheckStatus.SKIPPED:
                summary["skipped"] += 1
            elif result.status == CheckStatus.ERROR:
                summary["errors"] += 1

        return summary

    def print_summary(self):
        """Print formatted summary of results."""
        summary = self.get_summary()

        print("\n" + "=" * 63)
        print(" Health Check Results Summary")
        print("=" * 63)
        print(f"  Total checks:  {summary['total']}")
        print(f"  Passed:        {summary['passed']}")
        print(f"  Failed:        {summary['failed']}")
        print(f"  Skipped:       {summary['skipped']}")
        print(f"  Errors:        {summary['errors']}")

        if summary["critical_failures"]:
            print("\n  CRITICAL FAILURES:")
            for r in summary["critical_failures"]:
                print(f"    - [{r.check_id}] {r.message[:50]}")

        if summary["warnings"]:
            print("\n  WARNINGS:")
            for r in summary["warnings"][:5]:  # Show first 5
                print(f"    - [{r.check_id}] {r.message[:50]}")
            if len(summary["warnings"]) > 5:
                print(f"    ... and {len(summary['warnings']) - 5} more")
