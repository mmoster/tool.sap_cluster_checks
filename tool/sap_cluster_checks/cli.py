"""
SAP Pacemaker Cluster Health Check - Main Wrapper

This is the main entry point for the cluster health check tool.
It orchestrates all checks starting with access discovery.

Workflow:
1. Discover access methods to cluster nodes
2. Run cluster configuration checks (CHK_* rules)
3. Run Pacemaker/Corosync checks
4. Run SAP-specific checks
5. Generate report
"""

import os
import sys
import re
import argparse
import itertools
import threading
from pathlib import Path
from datetime import datetime

from .lib.compat import asdict


import yaml

from .access.discover_access import AccessDiscovery
from .access.config_display import show_config, delete_config, export_ansible_vars
from .access.sosreport_ops import fetch_sosreports, create_and_fetch_sosreports
from .rules.engine import RulesEngine, CheckResult, CheckStatus, Severity, CheckDispatch
from .lib import (
    get_redhat_doc_urls,
    print_guide,
    print_steps,
    print_suggestions,
    interactive_startup,
    run_usage_scan,
    ClusterReportData,
    REPORT_VERSION,
)
from .lib.config_extractor import ConfigExtractor
from .lib.install_status import InstallStatusMixin
from .lib.install_guide import InstallGuideMixin
from .lib.hana_status import HanaStatusMixin

SCRIPT_DIR = Path(__file__).parent.resolve()
DEFAULT_OUTPUT_DIR = Path.cwd() / "check_results"


class Spinner:
    """
    A simple spinner context manager that shows progress during long operations.
    Usage:
        with Spinner("Checking nodes"):
            do_long_operation()
    """

    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    FALLBACK_FRAMES = ["|", "/", "-", "\\"]  # For terminals without Unicode

    def __init__(self, message: str = "Working", delay: float = 0.1):
        self.message = message
        self.delay = delay
        self._stop_event = threading.Event()
        self._thread = None
        # Test if Unicode works
        try:
            sys.stdout.write("\r⠋")
            sys.stdout.write("\r \r")
            sys.stdout.flush()
            self.frames = self.FRAMES
        except (UnicodeEncodeError, UnicodeError):
            self.frames = self.FALLBACK_FRAMES

    def _spin(self):
        """Spinner animation loop."""
        spinner = itertools.cycle(self.frames)
        while not self._stop_event.is_set():
            frame = next(spinner)
            sys.stdout.write(f"\r  {frame} {self.message}...")
            sys.stdout.flush()
            self._stop_event.wait(self.delay)
        # Clear the spinner line
        sys.stdout.write("\r" + " " * (len(self.message) + 10) + "\r")
        sys.stdout.flush()

    def __enter__(self):
        # Only show spinner if stdout is a terminal (not redirected)
        if sys.stdout.isatty():
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *args):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=0.5)

    def update(self, message: str):
        """Update the spinner message."""
        self.message = message


class GateRegistry:
    """Registry of named gate functions for dispatch-driven check execution.

    Gates are boolean predicates that control whether a check or phase runs.
    They represent runtime cluster state (e.g. HANA resource running, HANA installed).
    """

    def __init__(self):
        self._gates = {}

    def register(self, name, fn):
        """Register a gate function by name."""
        self._gates[name] = fn

    def evaluate(self, name):
        """Evaluate a gate. Returns True if gate passes (check should run).

        Returns True for unknown gates (fail-open) to avoid silently skipping checks.
        """
        fn = self._gates.get(name)
        if fn is None:
            return True
        try:
            return bool(fn())
        except Exception:
            return True


class ClusterHealthCheck(InstallStatusMixin, InstallGuideMixin, HanaStatusMixin):
    """Main orchestrator for SAP Pacemaker cluster health checks."""

    # Default rules path relative to script directory
    DEFAULT_RULES_PATH = str(SCRIPT_DIR / "rules" / "health_checks")

    def __init__(
        self,
        config_dir: str = None,
        sosreport_dir: str = None,
        hosts_file: str = None,
        workers: int = 10,
        rules_path: str = None,
        debug: bool = False,
        ansible_group: str = None,
        skip_ansible: bool = False,
        cluster_name: str = None,
        local_mode: bool = False,
        strict_mode: bool = False,
        generate_pdf: bool = False,
        verbose_pdf: bool = False,
    ):
        self.config_dir = Path(config_dir) if config_dir else DEFAULT_OUTPUT_DIR
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.sosreport_dir = sosreport_dir
        self.hosts_file = hosts_file
        self.workers = workers
        self.rules_path = rules_path or self.DEFAULT_RULES_PATH
        self.access_config = None
        self.rules_engine = None
        self.check_results = []
        self.debug = debug
        self.ansible_group = ansible_group
        self.skip_ansible = skip_ansible
        self.cluster_name = cluster_name
        self.local_mode = local_mode
        self.strict_mode = strict_mode
        self.generate_pdf = generate_pdf
        self.verbose_pdf = verbose_pdf  # Show all checks in detail in PDF
        self.majority_makers = []  # Nodes that are majority makers (Scale-Out)
        self.last_pdf_file = None  # Track last generated PDF for auto-open
        self._hana_resource_state = "unknown"  # running/stopped/disabled/unmanaged/absent
        self._hana_installed = False  # Whether HANA is installed on any node
        self._hana_db_status = {}  # HANA DB status and replication info
        self._detected_topology = None  # 'Scale-Up' or 'Scale-Out'
        self._detected_arch_type = None  # 'legacy' or 'angi' (from detect_arch_type)
        self._install_results = []  # CHK_HANA_INSTALLED results (for _gather_hana_db_status)
        self._hana_nodes = {}  # Nodes where HANA is installed (filtered node dict)

        # Load dispatch manifest
        self.dispatch = CheckDispatch()
        if not self.dispatch.load():
            print(
                "[ERROR] rules/check_dispatch.yaml not found. Re-clone from repository to restore."
            )
            sys.exit(1)

        # Gate registry for dispatch-driven execution.
        # Lambdas capture `self` and read fields at evaluation time (late binding),
        # so gate results reflect current state when _run_step evaluates them.
        self._gate_registry = GateRegistry()
        self._gate_registry.register(
            "hana_resource_running", lambda: self._hana_resource_state == "running"
        )
        self._gate_registry.register("hana_installed", lambda: self._hana_installed)
        self._gate_registry.register(
            "not_legacy_scaleup",
            lambda: not (
                self._detected_arch_type == "legacy" and self._detected_topology == "Scale-Up"
            ),
        )

    def _debug_print(self, message: str):
        """Print debug message if debug mode is enabled."""
        if self.debug:
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            print(f"  [DEBUG {timestamp}] {message}")

    def _get_rhel_major(self) -> int:
        """Get RHEL major version from discovered cluster config, default 9."""
        if self.access_config and hasattr(self.access_config, "clusters"):
            for cinfo in self.access_config.clusters.values():
                rv = cinfo.get("rhel_version", "")
                m = re.search(r"(\d+)", str(rv))
                if m:
                    return int(m.group(1))
        return 9

    def _extract_cluster_config(self, cluster_name: str = None) -> dict:
        """
        Extract detailed cluster configuration using ConfigExtractor.

        Uses the appropriate extraction method based on access method:
        - SOSreport: parse pcs_config file directly
        - SSH offline: run pcs -f cib.xml config remotely
        - Running cluster: run pcs config

        Args:
            cluster_name: Cluster name for finding config

        Returns:
            Dict with extracted configuration merged with existing cluster_config
        """
        extracted = {}

        # Find source for extraction
        if self.access_config:
            # Get first node's info to determine access method
            nodes = self.access_config.nodes or {}
            for node_name, node_info in nodes.items():
                # Try SOSreport first
                sos_path = node_info.get("sosreport_path")
                if sos_path:
                    self._debug_print(f"Extracting config from SOSreport: {sos_path}")
                    extractor = ConfigExtractor.from_sosreport(sos_path)
                    if extractor:
                        extracted = extractor.get_config()
                        # Write config YAML for reference
                        config_yaml = self.config_dir / f"{cluster_name or 'cluster'}_config.yaml"
                        try:
                            extractor.write_yaml(str(config_yaml))
                            self._debug_print(f"Config written to: {config_yaml}")
                        except Exception as e:
                            self._debug_print(f"Failed to write config YAML: {e}")
                        break

                # Try SSH method if no SOSreport
                method = node_info.get("preferred_method")
                if method == "ssh":
                    user = node_info.get("ssh_user", "root")
                    # Check if cluster is running (from access config or default to trying running first)
                    cluster_running = True
                    if self.access_config and hasattr(self.access_config, "clusters"):
                        for cinfo in self.access_config.clusters.values():
                            if node_name in cinfo.get("nodes", []):
                                cluster_running = cinfo.get("cluster_running", True)
                                break

                    if cluster_running:
                        self._debug_print(
                            f"Extracting config from running cluster via SSH: {node_name}"
                        )
                        extractor = ConfigExtractor.from_running_cluster(node_name, user)
                        # If running cluster extraction fails, try offline
                        if not extractor:
                            self._debug_print(
                                f"Running cluster extraction failed, trying offline: {node_name}"
                            )
                            extractor = ConfigExtractor.from_ssh_offline(node_name, user)
                    else:
                        self._debug_print(
                            f"Extracting config from offline cluster via SSH: {node_name}"
                        )
                        extractor = ConfigExtractor.from_ssh_offline(node_name, user)

                    if extractor:
                        extracted = extractor.get_config()
                        config_yaml = self.config_dir / f"{cluster_name or 'cluster'}_config.yaml"
                        try:
                            extractor.write_yaml(str(config_yaml))
                            self._debug_print(f"Config written to: {config_yaml}")
                        except Exception as e:
                            self._debug_print(f"Failed to write config YAML: {e}")
                        break

        # Return sap_hana section merged with other relevant fields
        result = {}
        if extracted:
            hana = extracted.get("sap_hana", {})
            stonith = extracted.get("stonith", {})
            constraints = extracted.get("constraints", {})
            cluster = extracted.get("cluster", {})

            # Cluster/system info
            result["rhel_version"] = cluster.get("rhel_version")
            result["pacemaker_version"] = cluster.get("pacemaker_version")

            # SAP HANA config
            result["sid"] = hana.get("sid")
            result["instance_number"] = hana.get("instance_number")
            result["virtual_ip"] = hana.get("virtual_ip")
            result["secondary_vip"] = hana.get("secondary_vip")
            result["vip_resource"] = hana.get("vip_resource")
            result["secondary_vip_resource"] = hana.get("secondary_vip_resource")

            # HA parameters
            result["prefer_site_takeover"] = hana.get("prefer_site_takeover")
            result["automated_register"] = hana.get("automated_register")
            result["duplicate_primary_timeout"] = hana.get("duplicate_primary_timeout")
            result["clone_max"] = hana.get("clone_max")

            # Resource info
            result["resource_type"] = hana.get("resource_type")
            result["resource_name"] = hana.get("resource_name")
            if hana.get("topology"):
                result["topology_resource"] = hana["topology"].get("resource_name")

            # STONITH
            result["stonith_device"] = stonith.get("device")
            result["stonith_params"] = {
                "pcmk_host_map": stonith.get("pcmk_host_map", ""),
                "ssl": stonith.get("ssl", ""),
                "ssl_insecure": stonith.get("ssl_insecure", ""),
            }

            # Majority maker
            if constraints.get("majority_maker"):
                result["majority_maker"] = constraints["majority_maker"]

        return result

    def _build_cluster_report_data(
        self, cluster_name: str = None, summary: dict = None
    ) -> ClusterReportData:
        """
        Build unified ClusterReportData from current state.

        This is the single source of truth for all report data, consolidating
        information from:
        - AccessConfig (cluster configuration)
        - RulesEngine (data source info, resource config from cib.xml)
        - check_results (health check results)
        - check_install_status (installation status)

        Args:
            cluster_name: Override cluster name (auto-detected if None)
            summary: Pre-computed summary dict (computed if None)

        Returns:
            ClusterReportData instance with all fields populated
        """
        # Auto-detect cluster name if not provided
        if cluster_name is None:
            cluster_name = "unknown"
            if self.access_config and hasattr(self.access_config, "clusters"):
                # Find cluster name from nodes
                for cname, cinfo in self.access_config.clusters.items():
                    nodes = cinfo.get("nodes", [])
                    if any(n in (self.access_config.nodes or {}) for n in nodes):
                        cluster_name = cname
                        break
                # Fallback: use most recently discovered cluster
                if cluster_name == "unknown":
                    latest = None
                    for cname, cinfo in self.access_config.clusters.items():
                        discovered_at = cinfo.get("discovered_at", "")
                        if latest is None or discovered_at > latest:
                            latest = discovered_at
                            cluster_name = cname

        # Get cluster configuration from access_config
        cluster_config = {}
        if self.access_config and hasattr(self.access_config, "clusters"):
            cluster_config = self.access_config.clusters.get(cluster_name, {})

        # Extract detailed config from pcs config output
        # This fills in SAP HANA parameters, VIPs, STONITH, etc.
        extracted_config = self._extract_cluster_config(cluster_name)
        if extracted_config:
            # Merge extracted config - extracted values take precedence for None/empty values
            for key, value in extracted_config.items():
                if value is not None and (
                    cluster_config.get(key) is None or cluster_config.get(key) == ""
                ):
                    cluster_config[key] = value

        # Get node list
        node_list = list(self.access_config.nodes.keys()) if self.access_config else []

        # Determine cluster type from CHK_CLUSTER_TYPE result (uses clone-max)
        cluster_type = "Scale-Up"  # Default
        cluster_type_result = next(
            (r for r in self.check_results if r.check_id == "CHK_CLUSTER_TYPE"), None
        )
        if cluster_type_result and cluster_type_result.details:
            cluster_type = cluster_type_result.details.get("cluster_type", "Scale-Up")
        else:
            # Fallback if no check result - use clone-max from resource config
            clone_max = cluster_config.get("clone_max", 2)
            try:
                clone_max = int(clone_max) if clone_max else 2
            except (ValueError, TypeError):
                clone_max = 2
            if clone_max > 2:
                cluster_type = "Scale-Out"

        # Get data source info from rules engine
        data_source_info = {}
        if self.rules_engine:
            data_source_info = self.rules_engine.get_data_source_info()

        # Get resource configuration from cib.xml
        resource_config = {}
        majority_makers = list(self.majority_makers) if self.majority_makers else []
        if self.rules_engine:
            resource_config = self.rules_engine.get_cluster_resources_config()
            # Majority maker only exists in Scale-Out (clone-max >= 4)
            # Nodes with HANA exclusion constraints in Scale-Up are app servers, not majority makers
            if resource_config.get("available") and resource_config.get("majority_maker"):
                mm_node = resource_config["majority_maker"]
                if cluster_type == "Scale-Out":
                    if mm_node not in majority_makers:
                        majority_makers.append(mm_node)
                    self._debug_print(f"Scale-Out majority maker: {mm_node}")
                else:
                    self._debug_print(
                        f"Node {mm_node} has HANA exclusion constraints but cluster is {cluster_type} (app server, not majority maker)"
                    )

        # Build results list from check_results
        results_dict = [
            {
                "check_id": r.check_id,
                "node": r.node,
                "status": r.status.value,
                "severity": r.severity.value,
                "message": r.message,
                "description": r.description,
                "details": r.details if r.details else {},
            }
            for r in self.check_results
        ]

        # Compute summary if not provided
        if summary is None:
            total = len(self.check_results)
            passed = sum(1 for r in self.check_results if r.status == CheckStatus.PASSED)
            failed = sum(1 for r in self.check_results if r.status == CheckStatus.FAILED)
            skipped = sum(1 for r in self.check_results if r.status == CheckStatus.SKIPPED)
            errors = sum(1 for r in self.check_results if r.status == CheckStatus.ERROR)
            critical_failures = [
                r
                for r in self.check_results
                if r.status == CheckStatus.FAILED and r.severity == Severity.CRITICAL
            ]
            warnings = [
                r
                for r in self.check_results
                if r.status == CheckStatus.FAILED and r.severity == Severity.WARNING
            ]
            summary = {
                "total": total,
                "passed": passed,
                "failed": failed,
                "skipped": skipped,
                "errors": errors,
                "critical_count": len(critical_failures),
                "warning_count": len(warnings),
            }

        # Get installation status
        install_status = None
        try:
            install_status = self.check_install_status()
        except Exception:
            pass

        # Determine if cluster is running
        # First check discovery-time status (from access discovery)
        cluster_running = cluster_config.get("cluster_running", True)

        # Also check install status (runtime check)
        if install_status:
            has_config = install_status.get("corosync_conf_exists") or install_status.get(
                "cib_exists"
            )
            pacemaker_running = install_status.get("pacemaker_running")
            if has_config and not pacemaker_running:
                cluster_running = False

        # Build the unified report data
        report_data = ClusterReportData(
            # Metadata
            version=REPORT_VERSION,
            timestamp=datetime.now().isoformat(),
            # Data source
            data_source=data_source_info.get("description", "Unknown"),
            access_method=data_source_info.get("primary_method", "unknown"),
            used_cib_xml=data_source_info.get("used_cib_xml", False),
            cluster_running=cluster_running,
            hana_resource_state=self._hana_resource_state,
            hana_db_status=self._hana_db_status if self._hana_db_status else None,
            # Cluster info
            cluster_name=cluster_name,
            cluster_type=cluster_type,
            nodes=node_list,
            majority_makers=majority_makers,
            # OS/Software versions (from install_status or extracted config)
            rhel_version=(install_status.get("rhel_version") if install_status else None)
            or cluster_config.get("rhel_version"),
            pacemaker_version=(install_status.get("pacemaker_version") if install_status else None)
            or cluster_config.get("pacemaker_version"),
            resource_agent=self._get_resource_agent_label(),
            # SAP HANA config
            sid=cluster_config.get("sid"),
            instance_number=cluster_config.get("instance_number"),
            virtual_ip=cluster_config.get("virtual_ip"),
            secondary_vip=cluster_config.get("secondary_vip"),
            replication_mode=cluster_config.get("replication_mode"),
            operation_mode=cluster_config.get("operation_mode"),
            secondary_read=cluster_config.get("secondary_read"),
            # Node config
            node1_hostname=cluster_config.get("node1_hostname"),
            node1_ip=cluster_config.get("node1_ip"),
            node2_hostname=cluster_config.get("node2_hostname"),
            node2_ip=cluster_config.get("node2_ip"),
            sites=cluster_config.get("sites"),
            # HA parameters
            prefer_site_takeover=cluster_config.get("prefer_site_takeover"),
            automated_register=cluster_config.get("automated_register"),
            duplicate_primary_timeout=cluster_config.get("duplicate_primary_timeout"),
            migration_threshold=cluster_config.get("migration_threshold"),
            # Resource config
            resource_type=cluster_config.get("resource_type"),
            resource_name=cluster_config.get("resource_name"),
            topology_resource=cluster_config.get("topology_resource"),
            vip_resource=cluster_config.get("vip_resource"),
            secondary_vip_resource=cluster_config.get("secondary_vip_resource"),
            # STONITH
            stonith_device=cluster_config.get("stonith_device"),
            stonith_params=cluster_config.get("stonith_params"),
            # CIB resource config
            resource_config=resource_config if resource_config.get("available") else {},
            # Installation status
            install_status=install_status or {},
            # Results
            results=results_dict,
            summary=summary,
        )

        return report_data

    def print_banner(self):
        """Print the tool banner."""
        print("""
╔═══════════════════════════════════════════════════════════════╗
║       SAP Pacemaker Cluster Health Check Tool                 ║
║       Red Hat Enterprise Linux (RHEL 8/9/10)                  ║
╠───────────────────────────────────────────────────────────────╣
║  -h help | -i install guide | -G usage guide | --suggest tips ║
╚═══════════════════════════════════════════════════════════════╝
""")
        if self.debug:
            print("=" * 63)
            print(" DEBUG MODE ENABLED - Configuration Files")
            print("=" * 63)
            print(f"  Config directory:    {self.config_dir}")
            print(f"  Access config file:  {self.config_dir / AccessDiscovery.CONFIG_FILE}")
            print(f"  Rules path:          {self.rules_path}")
            print(f"  Strict mode:         {self.strict_mode}")
            print(f"  Local mode:          {self.local_mode}")
            print(f"  Hosts file:          {self.hosts_file or '(auto-discover from Ansible)'}")
            print(f"  SOSreport dir:       {self.sosreport_dir or '(not set)'}")
            print(f"  Workers:             {self.workers}")
            print()

    def step_access_discovery(self, force: bool = False) -> bool:
        """
        Step 1: Discover and validate access to cluster nodes.
        Returns True if at least one node is accessible.
        """
        print("\n" + "=" * 63)
        print(" STEP 1: Access Discovery")
        print("=" * 63)

        self._debug_print("Starting access discovery...")
        self._debug_print(f"Config file: {self.config_dir / AccessDiscovery.CONFIG_FILE}")
        self._debug_print(f"Force rediscover: {force}")

        discovery = AccessDiscovery(
            config_dir=str(self.config_dir),
            sosreport_dir=self.sosreport_dir,
            hosts_file=self.hosts_file,
            force_rediscover=force,
            debug=self.debug,
            ansible_group=self.ansible_group,
            skip_ansible=self.skip_ansible,
            cluster_name=self.cluster_name,
            local_mode=self.local_mode,
        )
        discovery.MAX_WORKERS = self.workers

        self._debug_print(f"Hosts file: {self.hosts_file or 'auto-discover'}")
        self._debug_print(f"SOSreport dir: {self.sosreport_dir or 'not set'}")

        self.access_config = discovery.discover_all()

        self._debug_print(f"Discovery complete, found {len(self.access_config.nodes)} node(s)")

        # Check if we have any accessible nodes
        accessible_nodes = [
            node for node in self.access_config.nodes.values() if node.get("preferred_method")
        ]

        if not accessible_nodes:
            print("\n[ERROR] No accessible nodes found!")
            print("Please ensure at least one of the following:")
            print("  - SSH access to cluster nodes")
            print("  - Valid Ansible inventory with reachable hosts")
            print("  - SOSreport directory with extracted reports")
            return False

        # Show cluster and nodes summary
        node_names = list(self.access_config.nodes.keys())
        cluster_name = None
        for cname, cinfo in self.access_config.clusters.items():
            if any(n in node_names for n in cinfo.get("nodes", [])):
                cluster_name = cname
                break

        print("\n" + "-" * 63)
        if cluster_name:
            print(f"  Cluster:  {cluster_name}")
        print(f"  Nodes:    {', '.join(sorted(node_names))}")
        print("-" * 63)
        print(f"\n[OK] {len(accessible_nodes)} node(s) accessible for health checks")
        return True

    def _load_rules_engine(self):
        """Initialize and load the rules engine."""
        if self.rules_engine is None:
            self._debug_print(f"Loading rules engine from: {self.rules_path}")
            access_dict = asdict(self.access_config) if self.access_config else {}
            self.rules_engine = RulesEngine(
                rules_path=self.rules_path, access_config=access_dict, strict_mode=self.strict_mode
            )
            self.rules_engine.load_rules()
            self._debug_print(f"Loaded {len(self.rules_engine.rules)} rules")
            if not self.strict_mode:
                optional_count = sum(1 for r in self.rules_engine.rules if r.optional)
                if optional_count > 0:
                    self._debug_print(
                        f"Non-strict mode: {optional_count} optional checks will be warnings"
                    )

            # Validate dispatch manifest against loaded rules
            warnings = self.dispatch.validate_against_rules(self.rules_engine.rules)
            for w in warnings:
                print(f"  [WARN] {w}")

    def _run_rules_parallel(self, rules: list, nodes: dict) -> list:
        """Run multiple rules in parallel using thread pool."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        all_results = []
        max_parallel_rules = min(len(rules), 4)  # Max 4 rules in parallel

        with ThreadPoolExecutor(max_workers=max_parallel_rules) as executor:
            futures = {}
            for rule in rules:
                future = executor.submit(self.rules_engine.run_check, rule, nodes)
                futures[future] = rule.check_id

            for future in as_completed(futures):
                check_id = futures[future]
                try:
                    results = future.result()
                    all_results.extend(results)
                    self._debug_print(f"Completed: {check_id} ({len(results)} results)")
                except Exception as e:
                    self._debug_print(f"Error in {check_id}: {e}")
                    all_results.append(
                        CheckResult(
                            check_id=check_id,
                            description="Check failed with exception",
                            status=CheckStatus.ERROR,
                            severity=Severity.WARNING,
                            message=str(e),
                            node=None,
                        )
                    )

        # Sync results to the engine so requires/context lookups work
        # (e.g., _get_hadr_sid, _get_rhel_major, requires gate)
        # NOTE: Within a single phase, checks run in parallel and cannot see
        # each other's results via self.results. This is intentional -- checks
        # in the same phase should not depend on each other.
        self.rules_engine.results.extend(all_results)

        return all_results

    def _filter_rules_by_prefix(self, prefixes: list) -> list:
        """Filter loaded rules by check_id prefix."""
        return [
            r for r in self.rules_engine.rules if any(r.check_id.startswith(p) for p in prefixes)
        ]

    # ------------------------------------------------------------------
    # Dispatch-driven step execution
    # ------------------------------------------------------------------

    def _run_step(self, step_name: str) -> bool:
        """Generic dispatch-driven step execution.

        Reads phases and checks from the dispatch manifest, evaluates gates,
        filters by topology, runs checks in parallel within each phase,
        and calls post-phase hooks for state extraction.
        """
        step = self.dispatch.get_step(step_name)
        if not step:
            print(f"[SKIP] No dispatch entry for step '{step_name}'")
            return True

        step_number = step.step_number
        step_display = step.name

        print("\n" + "=" * 63)
        print(f" STEP {step_number}: {step_display}")
        print("=" * 63)

        self._debug_print(f"Starting {step_display}...")
        self._load_rules_engine()

        nodes = self.access_config.nodes if self.access_config else {}
        self._debug_print(f"Target nodes: {list(nodes.keys())}")

        # For the SAP step, use filtered hana_nodes if available
        effective_nodes = nodes
        if step_name == "sap" and self._hana_nodes:
            effective_nodes = self._hana_nodes

        # Get phases with topology filtering (if topology is known)
        phases = self.dispatch.get_phases(step_name, self._detected_topology)

        all_check_ids = self.dispatch.get_all_check_ids(step_name)
        all_step_results = []

        for phase in phases:
            # Re-evaluate effective nodes each phase (SAP phase 1 sets _hana_nodes
            # which must be picked up by phase 2+ to exclude non-HANA nodes)
            if step_name == "sap" and self._hana_nodes:
                effective_nodes = self._hana_nodes

            # Evaluate phase-level gate
            if phase.gate and not self._gate_registry.evaluate(phase.gate):
                self._debug_print(f"Phase {phase.phase} skipped: gate '{phase.gate}' is closed")
                # Add SKIPPED results for all checks in this phase
                for chk_entry in phase.checks:
                    rule = next(
                        (r for r in self.rules_engine.rules if r.check_id == chk_entry.check_id),
                        None,
                    )
                    if rule:
                        skip_msg = self._gate_skip_message(phase.gate)
                        # hana_installed skip is INFO (not applicable), others are WARNING
                        skip_sev = (
                            Severity.INFO if phase.gate == "hana_installed" else Severity.WARNING
                        )
                        self.check_results.append(
                            CheckResult(
                                check_id=chk_entry.check_id,
                                description=rule.description,
                                status=CheckStatus.SKIPPED,
                                severity=skip_sev,
                                message=skip_msg,
                                node="all",
                            )
                        )
                if phase.gate == "hana_installed":
                    print("[SKIP] SAP HANA not installed - skipping HANA-specific checks")
                elif phase.gate == "hana_resource_running":
                    print(
                        f"  [WARN] HANA resource is {self._hana_resource_state}"
                        " - skipping resource-dependent checks"
                    )
                continue

            # Collect rules to run in this phase, evaluating per-check gates
            rules_to_run = []
            for chk_entry in phase.checks:
                # Check per-check gate
                if chk_entry.gate and not self._gate_registry.evaluate(chk_entry.gate):
                    rule = next(
                        (r for r in self.rules_engine.rules if r.check_id == chk_entry.check_id),
                        None,
                    )
                    if rule:
                        skip_msg = self._gate_skip_message(chk_entry.gate)
                        self.check_results.append(
                            CheckResult(
                                check_id=chk_entry.check_id,
                                description=rule.description,
                                status=CheckStatus.SKIPPED,
                                severity=Severity.WARNING,
                                message=skip_msg,
                                node="all",
                            )
                        )
                    continue

                rule = next(
                    (r for r in self.rules_engine.rules if r.check_id == chk_entry.check_id), None
                )
                if rule:
                    rules_to_run.append(rule)

            if not rules_to_run:
                self._debug_print(f"Phase {phase.phase}: no checks to run")
                continue

            self._debug_print(f"Phase {phase.phase} checks: {[r.check_id for r in rules_to_run]}")

            # Run checks (parallel or sequential)
            spinner_msg = f"Running {len(rules_to_run)} {step_display.lower()} checks"
            if step_name == "sap" and phase.phase == 1:
                spinner_msg = "Checking if SAP HANA is installed"

            with Spinner(spinner_msg):
                results = self._run_rules_parallel(rules_to_run, effective_nodes)
            self.check_results.extend(results)
            all_step_results.extend(results)

            # Post-phase hook: extract state needed by subsequent phases/steps
            self._post_phase_hook(step_name, phase.phase, results, nodes)

        # Print completion
        total_run = len(all_step_results)
        if total_run > 0:
            print(f"  Completed {step_display.lower()} checks")

        # Determine success (no CRITICAL failures in this step)
        failed = [
            r
            for r in self.check_results
            if r.status == CheckStatus.FAILED and r.check_id in all_check_ids
        ]
        return len([f for f in failed if f.severity == Severity.CRITICAL]) == 0

    def _get_resource_agent_label(self) -> str:
        """Return a human-readable label for the installed resource agent package."""
        ra_package = None
        for r in self.check_results:
            if r.check_id == "CHK_PACKAGE_CONSISTENCY" and r.details:
                parsed = r.details.get("parsed", {})
                if parsed.get("sap_hana_ha_version"):
                    ra_package = parsed["sap_hana_ha_version"]
                    break
                if parsed.get("resource_agents_sap_hana_scaleout"):
                    ra_package = parsed["resource_agents_sap_hana_scaleout"]
                    break
                if parsed.get("resource_agents_sap_hana"):
                    ra_package = parsed["resource_agents_sap_hana"]
                    break
        arch_suffix = {"angi": "sap-hana-ha (ANGI)", "legacy": "legacy"}.get(
            self._detected_arch_type, ""
        )
        if ra_package:
            return f"{ra_package} ({arch_suffix})" if arch_suffix else ra_package
        return {
            "angi": "sap-hana-ha (ANGI)",
            "legacy": "resource-agents-sap-hana (legacy)",
        }.get(self._detected_arch_type, self._detected_arch_type or "unknown")

    def _gate_skip_message(self, gate_name: str) -> str:
        """Return a human-readable skip message for a gate."""
        if gate_name == "hana_resource_running":
            return (
                f"Skipped: HANA resource is {self._hana_resource_state} (not managed by Pacemaker)"
            )
        if gate_name == "hana_installed":
            return "SAP HANA not installed"
        if gate_name == "not_legacy_scaleup":
            return "Skipped: not applicable for legacy scale-up (resource-agents-sap-hana)"
        return f"Skipped: gate '{gate_name}' is closed"

    def _post_phase_hook(self, step_name: str, phase: int, results: list, nodes: dict):
        """Execute post-phase hooks to extract state from results.

        These hooks extract runtime state needed by subsequent phases or steps.
        """
        if step_name == "config" and phase == 1:
            self._post_config_phase1(results)
        elif step_name == "pacemaker" and phase == 1:
            self._post_pacemaker_phase1(results)
        elif step_name == "sap" and phase == 1:
            self._post_sap_phase1(results, nodes)
        elif step_name == "sap" and phase == 2:
            self._post_sap_phase2(results)

    def _post_config_phase1(self, results: list):
        """After config phase 1: extract topology, apply retroactive filtering."""
        # Extract topology from CHK_CLUSTER_TYPE result
        cluster_type_result = next((r for r in results if r.check_id == "CHK_CLUSTER_TYPE"), None)
        if cluster_type_result and cluster_type_result.details:
            topology = cluster_type_result.details.get("cluster_type")
            if topology in ("Scale-Up", "Scale-Out"):
                self._detected_topology = topology
                if self.rules_engine:
                    self.rules_engine.set_detected_topology(topology)
                self._debug_print(f"Detected topology: {topology}")

        # Extract architecture type from CHK_PACKAGE_CONSISTENCY result
        pkg_result = next((r for r in results if r.check_id == "CHK_PACKAGE_CONSISTENCY"), None)
        if pkg_result and pkg_result.details:
            parsed = pkg_result.details.get("parsed", {})
            packages = []
            for key in (
                "sap_hana_ha_version",
                "resource_agents_sap_hana",
                "resource_agents_sap_hana_scaleout",
            ):
                val = parsed.get(key)
                if val:
                    packages.append(val)
            from .lib.hadr_provider.config_matrix import detect_arch_type

            arch = detect_arch_type(packages)
            if arch is not None:
                self._detected_arch_type = arch.value  # 'legacy' or 'angi'
                self._debug_print(f"Detected architecture: {self._detected_arch_type}")

        # Retroactive topology filtering: downgrade results for checks that
        # don't match the detected topology (they ran in the same phase because
        # topology wasn't known yet)
        if self._detected_topology:
            phases = self.dispatch.get_phases("config")  # Unfiltered
            if phases:
                for chk_entry in phases[0].checks:
                    if (
                        chk_entry.topology != "all"
                        and self._detected_topology not in chk_entry.topology
                    ):
                        # Find and downgrade results for this check
                        for result in self.check_results:
                            if result.check_id == chk_entry.check_id:
                                result.status = CheckStatus.SKIPPED
                                result.severity = Severity.INFO
                                result.message = (
                                    f"Not applicable for {self._detected_topology} topology"
                                )
                                self._debug_print(
                                    f"Retroactive skip: {chk_entry.check_id} "
                                    f"(topology {chk_entry.topology} vs {self._detected_topology})"
                                )

    def _post_pacemaker_phase1(self, results: list):
        """After pacemaker phase 1: extract HANA resource state and majority maker."""
        self._hana_resource_state = self._extract_hana_resource_state(results)
        self._debug_print(f"HANA resource state: {self._hana_resource_state}")
        if self.rules_engine:
            self.rules_engine.set_hana_resource_state(self._hana_resource_state)

        # Extract majority maker from CHK_MAJORITY_MAKER results
        # This works in all modes (SSH, local, SOSreport) and provides a unified
        # source for majority maker detection — the CIB parser in
        # _build_cluster_report_data() serves as an additional source.
        for r in results:
            if r.check_id == "CHK_MAJORITY_MAKER" and r.details:
                parsed = r.details.get("parsed", {})
                mm_node = parsed.get("majority_maker_node")
                if mm_node and mm_node not in self.majority_makers:
                    self.majority_makers.append(mm_node)
                    self._debug_print(f"Majority maker detected: {mm_node}")
                break

    def _post_sap_phase1(self, results: list, nodes: dict):
        """After SAP phase 1: determine HANA install status, handle majority maker nodes."""
        install_results = [r for r in results if r.check_id == "CHK_HANA_INSTALLED"]
        self._install_results = install_results

        # Distinguish actual HANA nodes from non-HANA nodes (app servers, majority makers)
        # Check parsed 'hana_installed' value: only "HANA_INSTALLED" means HANA is present
        # "NOT_HANA_NODE" passes the check but is NOT a HANA node
        # For SOSreport alternates (saphana/ dir): hdb_process match also confirms HANA
        nodes_with_hana = []
        nodes_without_hana = []
        for r in install_results:
            parsed = r.details.get("parsed", {}) if r.details else {}
            if parsed.get("hana_installed") == "HANA_INSTALLED":
                nodes_with_hana.append(r.node)
            elif parsed.get("hdb_process"):
                # SOSreport alternate: HANA detected from saphana process data
                nodes_with_hana.append(r.node)
                # Promote SID/instance from alternate fields if primary not set
                if not parsed.get("sid") and parsed.get("profile_sid"):
                    parsed["sid"] = parsed["profile_sid"]
                if not parsed.get("instance") and parsed.get("profile_instance"):
                    parsed["instance"] = parsed["profile_instance"]
                if not parsed.get("sidadm") and parsed.get("profile_sidadm"):
                    parsed["sidadm"] = parsed["profile_sidadm"]
                # Mark as HANA_INSTALLED for downstream consistency
                parsed["hana_installed"] = "HANA_INSTALLED"
                if r.details:
                    r.details["parsed"] = parsed
                # Fix check status (was ERROR because primary sos_path failed)
                if r.status == CheckStatus.ERROR:
                    r.status = CheckStatus.PASSED
                    r.message = f"HANA detected from SOSreport (SID: {parsed.get('sid', '?')}, Instance: {parsed.get('instance', '?')})"
            else:
                nodes_without_hana.append(r.node)

            # SOSreport: infer hana_running from detected HANA processes
            # (live_cmd sets HANA_RUNNING explicitly, but SOSreport data doesn't)
            if parsed.get("hana_running") is None and r.details:
                if parsed.get("hana_process") or parsed.get("hdb_process"):
                    parsed["hana_running"] = "yes"
                    r.details["parsed"] = parsed

        self._debug_print(
            f"HANA install check (raw): nodes_with={nodes_with_hana}, nodes_without={nodes_without_hana}"
        )

        # Nodes excluded from HANA: majority makers (from _post_pacemaker_phase1)
        # and any additional CIB constraint-based exclusions
        hana_excluded_nodes = set(self.majority_makers)
        if self.rules_engine:
            resource_config = self.rules_engine.get_cluster_resources_config()
            if resource_config.get("available"):
                excluded = resource_config.get("hana_excluded_node")
                if excluded:
                    hana_excluded_nodes.add(excluded)

        # Move false-positive HANA detections for excluded nodes
        # (Scale-Out majority makers mount /usr/sap via NFS, triggering false HANA_INSTALLED)
        if hana_excluded_nodes:
            false_positives = [n for n in nodes_with_hana if n in hana_excluded_nodes]
            for node_name in false_positives:
                nodes_with_hana.remove(node_name)
                nodes_without_hana.append(node_name)
                self._debug_print(
                    f"Overriding HANA detection for {node_name} (excluded by constraints)"
                )

        self._hana_installed = len(nodes_with_hana) > 0

        self._debug_print(
            f"HANA install check: nodes_with={nodes_with_hana}, nodes_without={nodes_without_hana}"
        )
        self._debug_print(f"HANA installed: {self._hana_installed}")

        # Update CHK_HANA_INSTALLED results for excluded nodes
        excluded_nodes_updated = []
        for node_name in nodes_without_hana:
            if node_name in hana_excluded_nodes:
                for result in self.check_results:
                    if result.check_id == "CHK_HANA_INSTALLED" and result.node == node_name:
                        result.status = CheckStatus.SKIPPED
                        result.message = (
                            "Node excluded from HANA resources by constraints (majority maker)"
                        )
                        break
                excluded_nodes_updated.append(node_name)

        other_without_hana = [n for n in nodes_without_hana if n not in hana_excluded_nodes]

        if excluded_nodes_updated:
            print(
                f"  [OK] Nodes excluded from HANA by constraints: {', '.join(excluded_nodes_updated)}"
            )
        if other_without_hana:
            print(f"  [INFO] Nodes without HANA: {', '.join(other_without_hana)}")

        # Filter nodes to only those with HANA installed (for subsequent phases)
        if nodes_with_hana:
            self._hana_nodes = {k: v for k, v in nodes.items() if k in nodes_with_hana}
        else:
            self._hana_nodes = nodes

        # Tell the engine which nodes don't have HANA (fallback for hana_nodes_only)
        if nodes_without_hana and self.rules_engine:
            self.rules_engine.set_non_hana_nodes(set(nodes_without_hana))

        # Gather HANA database status and replication info
        if self._hana_installed:
            self._gather_hana_db_status(install_results, self._hana_nodes)

    def _post_sap_phase2(self, results: list):
        """After SAP phase 2: extract HANA version, warn about missing saphana data."""
        if not self._hana_db_status:
            return

        for r in results:
            if r.check_id == "CHK_HANA_VERSION" and r.status == CheckStatus.PASSED and r.details:
                parsed = r.details.get("parsed", {})
                version = parsed.get("hana_version")
                if version:
                    sp = parsed.get("hana_sp", "")
                    self._hana_db_status["hana_version"] = version
                    if sp:
                        self._hana_db_status["hana_sp"] = sp
                    version_display = version
                    if sp:
                        version_display += f" (SPS{sp})"
                    print(f"  [INFO] HANA version: {version_display}")
                    break

        # Warn about nodes missing saphana sos plugin data
        hana_info_checks = {
            "CHK_HANA_VERSION", "CHK_HANA_PROCESS_STATUS",
            "CHK_HANA_SR_DETAIL", "CHK_HANA_LANDSCAPE",
        }
        nodes_missing = set()
        nodes_ok = set()
        for r in results:
            if r.check_id in hana_info_checks and r.node:
                if r.status == CheckStatus.ERROR:
                    nodes_missing.add(r.node)
                elif r.status == CheckStatus.PASSED:
                    nodes_ok.add(r.node)
        # Only warn for HANA nodes that failed all info checks (not just one)
        nodes_missing -= nodes_ok
        if nodes_missing:
            print(
                f"  [WARN] SAP HANA sos plugin data (sos_commands/saphana/) missing on: "
                f"{', '.join(sorted(nodes_missing))}"
            )
            print(
                "         To include it: sos report -o saphana"
            )

    def _extract_hana_resource_state(self, results: list) -> str:
        """Extract HANA resource state from CHK_RESOURCE_STATUS results.

        Checks parsed 'hana_resource_state' first (live_cmd emits this),
        then falls back to inferring from individual regex matches (SOSreport).
        """
        resource_status_result = next(
            (r for r in results if r.check_id == "CHK_RESOURCE_STATUS"), None
        )
        if not resource_status_result or not resource_status_result.details:
            return "unknown"

        parsed = resource_status_result.details.get("parsed", {})

        # Primary: use the explicit state summary (from live_cmd)
        state = parsed.get("hana_resource_state")
        if state and state != "unknown":
            return state

        # Fallback: infer from individual regex matches (SOSreport data)
        # NOTE: These regexes match the ENTIRE crm_mon output (not just HANA
        # lines), so order matters.  If resource_started is present (Master/
        # Slave/Promoted), the HANA resource is running - even if non-HANA
        # resources happen to be disabled (e.g. S4H_ERS29_group (disabled)).
        has_resource = parsed.get("sap_hana_resource") is not None
        if not has_resource:
            return "absent"
        if parsed.get("resource_unmanaged") is not None:
            return "unmanaged"
        if parsed.get("resource_started") is not None:
            return "running"
        if parsed.get("resource_disabled") is not None:
            return "disabled"
        if parsed.get("resource_stopped") is not None:
            return "stopped"
        return "unknown"

    # ------------------------------------------------------------------
    # Public step methods (thin wrappers around _run_step)
    # ------------------------------------------------------------------

    def step_cluster_config_check(self) -> bool:
        """Step 2: Check cluster configuration."""
        return self._run_step("config")

    def step_pacemaker_check(self) -> bool:
        """Step 3: Check Pacemaker/Corosync status."""
        return self._run_step("pacemaker")

    def step_sap_check(self) -> bool:
        """Step 4: SAP-specific checks."""
        return self._run_step("sap")

    def step_generate_report(self) -> bool:
        """
        Step 5: Generate final report.
        Summarizes all check results and optionally saves to file.
        """
        print("\n" + "=" * 63)
        print(" STEP 5: Health Check Report")
        print("=" * 63)

        self._debug_print("Generating report...")
        self._debug_print(f"Total results collected: {len(self.check_results)}")

        if not self.check_results:
            print("[INFO] No check results to report")
            return True

        # Summary statistics
        total = len(self.check_results)
        passed = len([r for r in self.check_results if r.status == CheckStatus.PASSED])
        failed = len([r for r in self.check_results if r.status == CheckStatus.FAILED])
        skipped = len([r for r in self.check_results if r.status == CheckStatus.SKIPPED])
        errors = len([r for r in self.check_results if r.status == CheckStatus.ERROR])

        critical_failures = [
            r
            for r in self.check_results
            if r.status == CheckStatus.FAILED and r.severity == Severity.CRITICAL
        ]
        warnings = [
            r
            for r in self.check_results
            if r.status == CheckStatus.FAILED and r.severity == Severity.WARNING
        ]

        # Cluster info summary
        if self._detected_topology or self._detected_arch_type:
            topo = self._detected_topology or "unknown"
            print(f"\n  Cluster Type:        {topo}")
            print(f"  Resource Agent:      {self._get_resource_agent_label()}")

        print(f"\n  Total Checks Run:    {total}")
        print(f"  Passed:              {passed}")
        print(f"  Failed:              {failed}")
        print(f"    - Critical:        {len(critical_failures)}")
        print(f"    - Warning:         {len(warnings)}")
        print(f"  Skipped:             {skipped}")
        print(f"  Errors:              {errors}")

        if skipped:
            skipped_results = [
                r for r in self.check_results if r.status == CheckStatus.SKIPPED
            ]
            print("\n  SKIPPED CHECKS:")
            for r in skipped_results:
                reason = r.message or "no reason given"
                node_str = f" ({r.node})" if r.node and r.node != "all" else ""
                print(f"    [SKIP] {r.check_id}{node_str}: {reason}")

        if critical_failures:
            print("\n  CRITICAL FAILURES:")
            for r in critical_failures:
                node_str = f" ({r.node})" if r.node else ""
                print(f"    [CRIT] {r.check_id}{node_str}")
                print(f"           {r.message}")

        if warnings:
            print("\n  WARNINGS:")
            for r in warnings[:10]:
                node_str = f" ({r.node})" if r.node else ""
                print(f"    [WARN] {r.check_id}{node_str}: {r.message}")
            if len(warnings) > 10:
                print(f"    ... and {len(warnings) - 10} more warnings")

        # Build unified report data using single source of truth
        # Use pre-computed summary to avoid recalculating
        summary = {
            "total": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "errors": errors,
            "critical_count": len(critical_failures),
            "warning_count": len(warnings),
        }

        # Use cluster_name override if explicitly set
        cluster_name_override = self.cluster_name if self.cluster_name else None
        report_data = self._build_cluster_report_data(
            cluster_name=cluster_name_override, summary=summary
        )

        # Sanitize cluster name for filename
        cluster_name = report_data.cluster_name
        cluster_name_safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in cluster_name)

        # Save unified report to YAML with format: YYYYMMDD_HHMMSS_clustername.yaml
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_file = self.config_dir / f"{timestamp}_{cluster_name_safe}.yaml"

        # Serialize unified data to YAML
        yaml_data = report_data.to_dict()
        with open(report_file, "w", encoding="utf-8") as f:
            yaml.dump(yaml_data, f, default_flow_style=False, sort_keys=False)

        print(f"\n  Report saved: {report_file}")

        # Generate PDF report if requested (fpdf2 availability checked at startup)
        if self.generate_pdf:
            try:
                from .report_generator import generate_health_check_report

                # Use unified data for PDF generation
                cluster_info = report_data.to_cluster_info()
                results_dict = report_data.get_results_list()
                summary_dict = report_data.get_summary_dict()
                install_status = report_data.get_install_status()

                # PDF filename format: YYYYMMDD_health_check_report_clustername_HHMM.pdf
                pdf_timestamp = datetime.now().strftime("%Y%m%d")
                pdf_time = datetime.now().strftime("%H%M")
                pdf_file = (
                    self.config_dir
                    / f"{pdf_timestamp}_health_check_report_{cluster_name_safe}_{pdf_time}.pdf"
                )

                # Use spinner for PDF generation (can take a while in verbose mode)
                with Spinner("Generating PDF report"):
                    generate_health_check_report(
                        results_dict,
                        summary_dict,
                        cluster_info,
                        str(pdf_file),
                        install_status if install_status else None,
                        verbose=self.verbose_pdf,
                    )
                print(f"  PDF report: {pdf_file}")
            except Exception as e:
                print(f"  [WARN] PDF generation failed: {e}")

        return len(critical_failures) == 0

    def run_all_checks(self, force_rediscover: bool = False, skip_steps: list = None) -> int:
        """
        Run all health checks in sequence.
        Returns exit code (0 = success, non-zero = failure).
        """
        # Clear results from any previous run
        self.check_results = []

        self.print_banner()
        print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Config directory: {self.config_dir}")

        # Show what source is being used
        config_file = self.config_dir / AccessDiscovery.CONFIG_FILE
        if self.local_mode:
            print("Mode: LOCAL (running on cluster node)")
        elif self.sosreport_dir:
            print(f"Source: SOSreports from {self.sosreport_dir}")
        elif self.hosts_file:
            print(f"Source: Hosts file {self.hosts_file}")
        elif self.cluster_name:
            print(f"Source: Saved cluster '{self.cluster_name}'")
        elif config_file.exists() and os.environ.get(
            "SAP_HA_CHECK_REUSE_CONFIG", ""
        ).strip() in ("1", "true", "yes"):
            print("Source: Existing config (SAP_HA_CHECK_REUSE_CONFIG is set)")
        elif config_file.exists():
            print("Source: Fresh discovery (set SAP_HA_CHECK_REUSE_CONFIG=1 to reuse config)")
        else:
            print("Source: Ansible inventory (auto-discovery)")

        print("-" * 63)
        print("To use different nodes:  ./sap_cluster_checks.py <node1> <node2>")
        print("To reset configuration:  ./sap_cluster_checks.py -D")
        print("-" * 63)

        skip_steps = skip_steps or []
        results = {}

        # Step 1: Access Discovery (required)
        if "access" not in skip_steps:
            results["access"] = self.step_access_discovery(force=force_rediscover)
            if not results["access"]:
                print("\n[ABORT] Cannot proceed without accessible nodes.")
                return 1

        # Step 2: Cluster Config Check
        if "config" not in skip_steps:
            results["config"] = self.step_cluster_config_check()

        # Step 3: Pacemaker Check
        if "pacemaker" not in skip_steps:
            results["pacemaker"] = self.step_pacemaker_check()

        # Step 4: SAP Check
        if "sap" not in skip_steps:
            results["sap"] = self.step_sap_check()

        # Step 5: Generate Report
        if "report" not in skip_steps:
            results["report"] = self.step_generate_report()

        # Final summary
        print("\n" + "=" * 63)
        print(" Health Check Complete")
        print("=" * 63)
        print(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # Show cluster and nodes info
        if self.access_config:
            nodes = list(self.access_config.nodes.keys())
            # Find cluster name from config
            cluster_name = None
            for cname, cinfo in self.access_config.clusters.items():
                if set(cinfo.get("nodes", [])) == set(nodes) or any(
                    n in nodes for n in cinfo.get("nodes", [])
                ):
                    cluster_name = cname
                    break

            if cluster_name:
                print(f"Cluster: {cluster_name}")
            print(f"Nodes checked: {', '.join(sorted(nodes))}")

            # Show detected cluster type from CHK_CLUSTER_TYPE
            if self.check_results:
                for r in self.check_results:
                    if hasattr(r, "check_id") and r.check_id == "CHK_CLUSTER_TYPE":
                        cluster_type = (
                            r.details.get("cluster_type", "Unknown") if r.details else "Unknown"
                        )
                        print(f"Cluster Type: {cluster_type}")
                        if r.message and "configuration" in r.message:
                            print(f"  ({r.message})")
                        break

        # Show health check results summary
        if self.check_results:
            all_results = self.check_results
            passed = [
                r
                for r in all_results
                if hasattr(r, "status") and r.status == CheckStatus.PASSED
            ]
            failed_checks = [
                r
                for r in all_results
                if hasattr(r, "status") and r.status == CheckStatus.FAILED
            ]
            skipped = [
                r
                for r in all_results
                if hasattr(r, "status") and r.status == CheckStatus.SKIPPED
            ]
            errors = [
                r
                for r in all_results
                if hasattr(r, "status") and r.status == CheckStatus.ERROR
            ]

            print("\nHealth Check Results:")
            print(
                f"  PASSED:  {len(passed):3d}  FAILED: {len(failed_checks):3d}  SKIPPED: {len(skipped):3d}  ERROR: {len(errors):3d}"
            )

            # Show data source information
            if self.rules_engine:
                data_source_info = self.rules_engine.get_data_source_info()
                data_source = data_source_info.get("description", "")
                if data_source:
                    print(f"\n  Data Source: {data_source}")

            # Show cluster configuration in verbose mode
            if self.verbose_pdf:
                config_file = self.config_dir / "cluster_access_config.yaml"
                if config_file.exists():
                    # Get cluster name from access config
                    cluster_to_show = None
                    if self.access_config and hasattr(self.access_config, "clusters"):
                        clusters = self.access_config.clusters
                        if clusters:
                            cluster_to_show = list(clusters.keys())[0]
                    print("\n" + "-" * 63)
                    print(" Cluster Configuration (verbose mode)")
                    print("-" * 63)
                    show_config(config_file, cluster_to_show, config_only=True)

            # Check for installation issues
            # Essential commands for RHEL clusters
            essential_commands = ["pacemaker", "corosync", "pcs", "crm_mon"]  # noqa: F841
            packages_missing = False
            commands_missing = []
            for r in all_results:
                msg = getattr(r, "message", "") or ""
                if (
                    "pacemaker package not found" in msg.lower()
                    or "corosync package not found" in msg.lower()
                ):
                    packages_missing = True
                if "command '" in msg.lower() and "not found" in msg.lower():
                    # Extract command name
                    match = re.search(r"command '(\w+)'", msg.lower())
                    if match:
                        cmd = match.group(1)
                        # Only track essential commands as missing
                        if cmd in essential_commands and cmd not in commands_missing:
                            commands_missing.append(cmd)

            if packages_missing or commands_missing:
                print()
                print("=" * 63)
                print(" INSTALLATION REQUIRED")
                print("=" * 63)
                if packages_missing:
                    print("  Cluster packages (pacemaker, corosync) are NOT installed!")
                if commands_missing:
                    print(f"  Missing commands: {', '.join(commands_missing)}")
                print()
                print("  To see installation steps, run:")
                print("    ./sap_cluster_checks.py -i")
                print("    ./sap_cluster_checks.py --suggest install")
                print("=" * 63)

            elif failed_checks:
                print()
                print("-" * 63)
                print(" Failed Checks (CRITICAL issues):")
                for r in failed_checks:
                    if hasattr(r, "severity") and r.severity == Severity.CRITICAL:
                        print(f"  - {r.check_id}: {r.message}")
                print("-" * 63)

            else:
                # All checks passed - show healthy banner
                resources_not_managed = self._hana_resource_state in (
                    "stopped",
                    "disabled",
                    "unmanaged",
                )
                print()
                print("=" * 63)
                if resources_not_managed:
                    print("  ╔═══════════════════════════════════════════════════════╗")
                    print("  ║                                                       ║")
                    print("  ║         ⚠  CLUSTER CHECKS PASSED  ⚠                  ║")
                    print("  ║                                                       ║")
                    print("  ║     All runnable checks passed, but HANA resource     ║")
                    state_msg = f"is {self._hana_resource_state}"
                    print(f"  ║     {state_msg:<42}       ║")
                    print("  ║     and NOT managed by Pacemaker.                     ║")
                    print("  ║     Some checks were skipped.                         ║")
                    print("  ║                                                       ║")
                    print("  ╚═══════════════════════════════════════════════════════╝")
                else:
                    print("  ╔═══════════════════════════════════════════════════════╗")
                    print("  ║                                                       ║")
                    print("  ║            ✓  CLUSTER IS HEALTHY  ✓                   ║")
                    print("  ║                                                       ║")
                    print("  ║     All health checks passed successfully.            ║")
                    print("  ║     Your SAP HANA cluster is properly configured.     ║")
                    print("  ║                                                       ║")
                    print("  ╚═══════════════════════════════════════════════════════╝")
                print("=" * 63)

                # Auto-generate PDF report on success (fpdf2 availability checked at startup)
                if self.generate_pdf:
                    try:
                        from .report_generator import generate_health_check_report

                        # Use unified data model for PDF generation
                        report_data = self._build_cluster_report_data()
                        cluster_name = report_data.cluster_name
                        cluster_name_safe = re.sub(r"[^\w\-]", "_", cluster_name)

                        # Generate PDF with default name
                        pdf_timestamp = datetime.now().strftime("%Y%m%d")
                        pdf_time = datetime.now().strftime("%H%M")
                        pdf_file = (
                            self.config_dir
                            / f"{pdf_timestamp}_health_check_report_{cluster_name_safe}_{pdf_time}.pdf"
                        )

                        # Use spinner for PDF generation
                        with Spinner("Generating PDF report"):
                            generate_health_check_report(
                                report_data.get_results_list(),
                                report_data.get_summary_dict(),
                                report_data.to_cluster_info(),
                                str(pdf_file),
                                report_data.get_install_status() or None,
                                verbose=self.verbose_pdf,
                            )
                        self.last_pdf_file = pdf_file  # Track for auto-open
                        print(f"\n  PDF report saved: {pdf_file}")
                    except Exception as e:
                        print(f"\n  [WARN] PDF generation failed: {e}")

                # Cluster is healthy - exit early without showing extra output
                return 0

        # Show all steps with status and results (only when there are issues)
        print("\nSteps completed:")
        step_names = {
            "access": "Access Discovery",
            "config": "Cluster Configuration",
            "pacemaker": "Pacemaker/Corosync",
            "sap": "SAP HANA",
            "report": "Report Generation",
        }

        # Map check IDs to steps for counting (from dispatch manifest)
        step_checks = {}
        for sn in ["config", "pacemaker", "sap"]:
            step_checks[sn] = self.dispatch.get_all_check_ids(sn)

        for step, success in results.items():
            name = step_names.get(step, step)

            # Get detailed results for this step
            if step == "access":
                nodes = self.access_config.nodes if self.access_config else {}
                accessible = sum(1 for n in nodes.values() if n.get("preferred_method"))
                total = len(nodes)
                if accessible == total and total > 0:
                    print(f"  [{accessible}/{total}] {name}: PASSED")
                else:
                    print(f"  [{accessible}/{total}] {name}: {accessible} node(s) accessible")
            elif step in step_checks and self.check_results:
                check_ids = step_checks[step]
                step_results = [r for r in self.check_results if r.check_id in check_ids]
                passed = sum(1 for r in step_results if r.status == CheckStatus.PASSED)
                failed = sum(1 for r in step_results if r.status == CheckStatus.FAILED)
                skipped = sum(1 for r in step_results if r.status == CheckStatus.SKIPPED)
                errors = sum(1 for r in step_results if r.status == CheckStatus.ERROR)
                total = len(step_results)

                if self.debug:
                    print(
                        f"  [DEBUG] {step}: {[(r.check_id, str(r.status), r.node) for r in step_results]}"
                    )

                # Show ratio and details
                if passed == total and total > 0:
                    print(f"  [{passed}/{total}] {name}: PASSED")
                else:
                    details = []
                    if failed:
                        details.append(f"{failed} failed")
                    if skipped:
                        details.append(f"{skipped} skipped")
                    if errors:
                        details.append(f"{errors} errors")
                    detail_str = f" ({', '.join(details)})" if details else ""
                    print(f"  [{passed}/{total}] {name}{detail_str}")
            elif step == "report":
                status_icon = "[OK]" if success else "[FAIL]"
                print(f"  {status_icon} {name}")
            else:
                status_icon = "[OK]" if success else "[FAIL]"
                print(f"  {status_icon} {name}")

        failed = [step for step, success in results.items() if not success]
        if failed:
            failed_labels = [step_names.get(s, s) for s in failed]
            print(f"\n[WARNING] Steps with failures: {', '.join(failed_labels)}")
            # Show which checks actually failed in each step
            for step in failed:
                if step in step_checks and self.check_results:
                    check_ids = step_checks[step]
                    failed_checks = [
                        r for r in self.check_results
                        if r.check_id in check_ids
                        and r.status == CheckStatus.FAILED
                    ]
                    for r in failed_checks:
                        sev = "CRIT" if r.severity == Severity.CRITICAL else "WARN"
                        node_str = f" ({r.node})" if r.node else ""
                        print(f"  [{sev}] {r.check_id}{node_str}: {r.message}")

        # Save step results for --suggest to use
        status_file = self.config_dir / "last_run_status.yaml"
        status_data = {
            "timestamp": datetime.now().isoformat(),
            "steps": {step: "passed" if success else "failed" for step, success in results.items()},
            "failed_steps": failed,
        }
        with open(status_file, "w", encoding="utf-8") as f:
            yaml.dump(status_data, f, default_flow_style=False)

        # Check actual health check results
        has_failures = False
        has_skipped = False
        needs_install = False
        # Essential commands - if these are missing, installation is needed
        essential_commands = ["pacemaker", "corosync", "pcs", "crm_mon"]
        if self.check_results:
            for r in self.check_results:
                status = str(getattr(r, "status", ""))
                msg = getattr(r, "message", "") or ""
                if status == "CheckStatus.FAILED":
                    has_failures = True
                if status == "CheckStatus.SKIPPED":
                    has_skipped = True
                # Only trigger needs_install for essential package/command issues
                if (
                    "pacemaker package not found" in msg.lower()
                    or "corosync package not found" in msg.lower()
                ):
                    needs_install = True
                elif "command '" in msg.lower() and "not found" in msg.lower():
                    match = re.search(r"command '(\w+)'", msg.lower())
                    if match and match.group(1) in essential_commands:
                        needs_install = True

        if failed:
            # Show hint about --suggest
            first_failed = failed[0]
            print(f"\n  Get help: ./sap_cluster_checks.py --suggest {first_failed}")
            print("  Or auto:  ./sap_cluster_checks.py --suggest")

        # Show next steps
        self._print_next_steps(results)

        # Final status and prompt
        if needs_install:
            print("\n" + "=" * 63)
            print(" [ACTION REQUIRED] Cluster packages not installed.")
            print("=" * 63)

            # Show first suggested commands
            print("\n  Quick start (run on cluster nodes):")
            print(
                "    dnf install -y pacemaker pcs sap-hana-ha  # or resource-agents-sap-hana-scaleout"
            )
            print("    systemctl enable --now pcsd")
            print("    ... (more steps required)")
            print("\n  For full guide: ./sap_cluster_checks.py -i")

            print("\nOptions:")
            print("  [Enter]  Rerun health check (monitor installation progress)")
            print("  [i]      Show installation guide")
            print("  [d]      Delete report files")
            print("  [q]      Quit")

            try:
                response = input("\nYour choice: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                response = "q"
                print()

            while True:
                if response == "":
                    # Rerun health check
                    print("\n" + "=" * 63)
                    print(" Rerunning health check...")
                    print("=" * 63)
                    return self.run_all_checks(force_rediscover=False, skip_steps=[])
                if response == "i":
                    print()
                    self.print_dynamic_install_guide()
                elif response == "d":
                    delete_config(self.config_dir / AccessDiscovery.CONFIG_FILE)
                    print("  Restarting health check...\n")
                    # Restart without -D flag
                    new_argv = [arg for arg in sys.argv if arg not in ["-D", "--delete-reports"]]
                    os.execv(sys.executable, [sys.executable] + new_argv)
                elif response == "q":
                    break
                else:
                    print("Invalid option.")

                # Show options again
                print("\n" + "-" * 63)
                print("Options:")
                print("  [Enter]  Rerun health check")
                print("  [i]      Show installation guide")
                print("  [d]      Delete report files")
                print("  [q]      Quit")

                try:
                    response = input("\nYour choice: ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    break

            return 2

        # Check installation progress
        install_complete = True
        steps_done = 0
        steps_total = 7
        missing_steps = []
        install_status = {}
        if not needs_install:
            try:
                install_status = self.check_install_status()
                steps_done = sum(
                    1
                    for v in [
                        install_status.get("subscription_registered"),
                        install_status.get("repos_enabled"),
                        install_status.get("packages_installed"),
                        install_status.get("pcsd_running"),
                        install_status.get("cluster_configured"),
                        install_status.get("stonith_configured"),
                        install_status.get("hana_resources"),
                    ]
                    if v
                )
                install_complete = steps_done >= steps_total

                # Build list of missing steps
                if not install_status.get("subscription_registered"):
                    missing_steps.append("subscription")
                if not install_status.get("repos_enabled"):
                    missing_steps.append("repos")
                if not install_status.get("packages_installed"):
                    missing_steps.append("packages")
                if not install_status.get("pcsd_running"):
                    missing_steps.append("pcsd")
                if not install_status.get("cluster_configured"):
                    missing_steps.append("cluster")
                if not install_status.get("stonith_configured"):
                    if install_status.get("stonith_disabled"):
                        missing_steps.append("stonith (device disabled!)")
                    else:
                        missing_steps.append("stonith")
                if not install_status.get("hana_resources"):
                    missing_steps.append("hana_resources")
            except Exception:
                pass

        # Determine overall status
        if failed or has_failures:
            if not install_complete:
                print(
                    f"\n[WARNING] Installation incomplete ({steps_done}/{steps_total} steps) and health checks FAILED."
                )
                if missing_steps:
                    print(f"          Missing: {', '.join(missing_steps)}")
                print("          Run ./sap_cluster_checks.py -i to see remaining steps.")
            else:
                print("\n[WARNING] Some health checks FAILED. Review report for details.")
            return 1
        if not install_complete:
            print(
                f"\n[INCOMPLETE] Installation in progress: {steps_done}/{steps_total} steps complete."
            )
            if missing_steps:
                print(f"             Missing: {', '.join(missing_steps)}")
            print("             Run ./sap_cluster_checks.py -i to see remaining steps.")
            return 2
        if has_skipped:
            print("\n[INFO] Some checks were skipped (commands not available).")
            return 0
        print("\n[OK] All health checks passed! Cluster is fully configured.")
        return 0

    def _print_next_steps(self, results: dict):
        """Print suggested next steps based on results."""
        print("\n")
        print("=" * 63)
        print("=" * 63)
        print(" NEXT STEPS")
        print("=" * 63)
        print("=" * 63)

        # Check what was done and suggest next actions
        if not results.get("access"):
            print("""
  Access discovery failed. Try:
    ./sap_cluster_checks.py --debug hana01    # Debug with specific node
    ./sap_cluster_checks.py -s /path/to/sos   # Use SOSreports instead
""")
            return

        # Get results from rules engine if available
        all_results = self.check_results

        if all_results:
            # Analyze results
            critical = [
                r
                for r in all_results
                if hasattr(r, "status")
                and r.status == CheckStatus.FAILED
                and hasattr(r, "severity")
                and r.severity == Severity.CRITICAL
            ]
            warnings = [
                r
                for r in all_results
                if hasattr(r, "status")
                and r.status == CheckStatus.FAILED
                and hasattr(r, "severity")
                and r.severity == Severity.WARNING
            ]
            skipped = [
                r
                for r in all_results
                if hasattr(r, "status") and r.status == CheckStatus.SKIPPED
            ]

            # Check for essential package/command not found issues
            essential_commands = ["pacemaker", "corosync", "pcs", "crm_mon"]
            packages_missing = False
            essential_cmd_missing = False
            for r in all_results:
                msg = getattr(r, "message", "") or ""
                if (
                    "pacemaker package not found" in msg.lower()
                    or "corosync package not found" in msg.lower()
                ):
                    packages_missing = True
                if "command '" in msg.lower() and "not found" in msg.lower():
                    match = re.search(r"command '(\w+)'", msg.lower())
                    if match and match.group(1) in essential_commands:
                        essential_cmd_missing = True

            # Check for "cluster not running" scenario - many errors, packages installed
            errors = [
                r
                for r in all_results
                if hasattr(r, "status") and r.status == CheckStatus.ERROR
            ]
            cluster_not_running = False
            cluster_not_created = False
            install_status = None
            if len(errors) >= 3 and not packages_missing and not essential_cmd_missing:
                # Many errors with packages installed - check if cluster exists
                try:
                    install_status = self.check_install_status()
                    if not install_status.get("corosync_conf_exists") and not install_status.get(
                        "cib_exists"
                    ):
                        # Neither corosync.conf nor cib.xml exist - cluster not created
                        cluster_not_created = True
                    elif not install_status.get("pacemaker_running"):
                        # Cluster config exists (corosync.conf or cib.xml) but not running
                        cluster_not_running = True
                except Exception:
                    # Fallback - assume cluster might not be running
                    cluster_not_running = True

            if packages_missing or essential_cmd_missing:
                print("""
  INSTALLATION REQUIRED: Cluster packages not installed!
    Run: ./sap_cluster_checks.py --suggest install

    This will show step-by-step installation instructions for:
    - Pacemaker, Corosync, pcs
    - SAP HANA resource agents
    - Cluster setup and configuration
""")
            elif cluster_not_created:
                # Build list of missing steps only
                missing_steps = []
                if install_status:
                    if not install_status.get("hacluster_password"):
                        missing_steps.append("passwd hacluster")
                    if not install_status.get("pcsd_running"):
                        missing_steps.append("systemctl enable --now pcsd")
                    if not install_status.get("nodes_authenticated"):
                        missing_steps.append("pcs host auth <node1> <node2>")
                # These are always needed if cluster not created
                missing_steps.append("pcs cluster setup <name> <node1> <node2>")
                missing_steps.append("pcs cluster start --all")

                print("""
  ╔═══════════════════════════════════════════════════════════════╗
  ║  [!] CLUSTER NOT YET CREATED                                  ║
  ╠═══════════════════════════════════════════════════════════════╣
  ║                                                               ║
  ║  /etc/corosync/corosync.conf does not exist                  ║
  ║                                                               ║
  ║  ACTION REQUIRED:                                             ║
  ║  ─────────────────                                            ║""")
                for i, step in enumerate(missing_steps, 1):
                    print(f"  ║  {i}. {step:<55} ║")
                print("""  ║                                                               ║
  ║  Run ./sap_cluster_checks.py -i for detailed guide          ║
  ╚═══════════════════════════════════════════════════════════════╝
""")
            elif cluster_not_running:
                print("""
  ╔═══════════════════════════════════════════════════════════════╗
  ║  [!] CLUSTER NOT RUNNING                                      ║
  ╠═══════════════════════════════════════════════════════════════╣
  ║                                                               ║
  ║  Cluster exists but is not started                            ║
  ║                                                               ║
  ║  ACTION REQUIRED:                                             ║
  ║  ─────────────────                                            ║
  ║  Start the cluster:   pcs cluster start --all                 ║
  ║                                                               ║
  ╚═══════════════════════════════════════════════════════════════╝
""")
            elif critical:
                # Build context-aware hints based on actual failures
                hints = ["Check the report file for details"]
                critical_ids = {r.check_id for r in critical if hasattr(r, "check_id")}
                if any("STONITH" in cid or "FENCING" in cid for cid in critical_ids):
                    hints.append("Address STONITH/fencing issues")
                if any("QUORUM" in cid for cid in critical_ids):
                    hints.append("Verify quorum configuration")
                if any("HADR" in cid or "HOOK" in cid for cid in critical_ids):
                    hints.append("Fix HA/DR provider hook configuration in global.ini")
                if any("RESOURCE" in cid for cid in critical_ids):
                    hints.append("Check Pacemaker resource configuration")
                if any("COROSYNC" in cid for cid in critical_ids):
                    hints.append("Review Corosync configuration")
                hint_lines = "\n".join(f"    - {h}" for h in hints)
                print(f"""
  CRITICAL issues found ({len(critical)}). Review:
{hint_lines}
""")

            if warnings and not packages_missing:
                print(f"  Warnings found ({len(warnings)}). Review report for details.")

            if skipped and not essential_cmd_missing:
                print(f"  Skipped checks ({len(skipped)}). Some commands may not be available.")

        print("""
  Common next steps:
    ./sap_cluster_checks.py --suggest install   # Installation guide
    ./sap_cluster_checks.py --show-config       # View all clusters config
    ./sap_cluster_checks.py -S mycluster        # View specific cluster config
    ./sap_cluster_checks.py -f hana01           # Force re-discovery
    ./sap_cluster_checks.py --list-rules        # List all health checks
    ./sap_cluster_checks.py --guide             # Show detailed usage guide
""")

        doc_urls = get_redhat_doc_urls(self._get_rhel_major())
        print("  Documentation:")
        print("    SAP HANA Admin:  https://help.sap.com/docs/SAP_HANA_PLATFORM")
        print(
            "    SAP HANA SR:     https://help.sap.com/docs/SAP_HANA_PLATFORM/6b94445c94ae495c83a19646e7c3fd56"
        )
        print(f"    Red Hat HA:      {doc_urls['ha_clusters']}")
        print(f"    Scale-Up HA:     {doc_urls['sap_scale_up']}")
        print(f"    Scale-Out HA:    {doc_urls['sap_scale_out']}")
        print(f"    Multitarget DR:  {doc_urls['sap_multitarget_dr']}")
        print("    Pacemaker:       https://clusterlabs.org/pacemaker/doc/")

        print("\n" + "-" * 63)
        print(" Quick: -h help | -i install | -G guide | --suggest | --list-steps")
        print("-" * 63)


# Note: print_guide(), print_steps(), print_suggestions(), interactive_startup(),
# run_usage_scan(), print_usage_help(), scan_for_resources(), extract_sosreports_parallel()
# are now imported from lib module


# Functions moved to lib/ modules:
# - print_guide, print_steps, print_suggestions (lib/installation.py)
# - interactive_startup, run_usage_scan, print_usage_help (lib/interactive.py)
# - scan_for_resources, extract_sosreports_parallel, check_for_updates (lib/utils.py)


# [Content removed - see lib/ modules]


def _rhel_major_from_config(config_dir: Path) -> int:
    """Extract RHEL major version from saved access config, default 9."""
    config_path = config_dir / AccessDiscovery.CONFIG_FILE
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            for cinfo in data.get("clusters", {}).values():
                rv = cinfo.get("rhel_version", "")
                m = re.search(r"(\d+)", str(rv))
                if m:
                    return int(m.group(1))
        except Exception:
            pass
    return 9


def main():
    parser = argparse.ArgumentParser(
        description="SAP Pacemaker Cluster Health Check Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                          Run on cluster node (auto-detects local mode)
  %(prog)s --local                  Explicit local mode (on cluster node)
  %(prog)s hana03                   Auto-discover cluster from hana03 and check all members
  %(prog)s -C mycluster             Use previously discovered cluster 'mycluster'
  %(prog)s -d hana03                Same with debug output
  %(prog)s --access-only hana03     Only test access (discover cluster members)
  %(prog)s -g sap_cluster           Only check hosts in Ansible group 'sap_cluster'
  %(prog)s --show-config            Show all discovered clusters and nodes
  %(prog)s --show-config mycluster  Show config for specific cluster
  %(prog)s -S hana03                Show config for cluster containing hana03
  %(prog)s -H hosts.txt             Use custom hosts file
  %(prog)s -s /path/to/sosreports   Use SOSreport directory
  %(prog)s -v hana03                Verbose PDF - show all checks in detail (for audits)
  %(prog)s -i                        Show installation guide (shortcut)
  %(prog)s --suggest                Show suggestions for first failing step
  %(prog)s --suggest install        Show full installation guide
  %(prog)s --list-steps             List all steps with suggestion commands
        """,
    )

    # Input sources
    parser.add_argument("hosts", nargs="*", help="Hostname(s) to check (e.g., hana01 hana02)")
    parser.add_argument("--hosts-file", "-H", help="File containing list of hosts (one per line)")
    parser.add_argument(
        "--sosreport-dir",
        "-s",
        help="Directory containing SOSreport archives/directories (default: ./sosreports)",
    )
    parser.add_argument("--group", "-g", help="Only check hosts from this Ansible inventory group")
    parser.add_argument(
        "--cluster", "-C", help="Use saved cluster by name (from previous discovery)"
    )
    parser.add_argument("--config-dir", "-c", help="Directory to store configuration and reports (default: ./check_results)")

    # Actions
    parser.add_argument(
        "--access-only", "-a", action="store_true", help="Only run access discovery step"
    )
    parser.add_argument(
        "--show-config",
        "-S",
        nargs="?",
        const=True,
        default=False,
        metavar="CLUSTER|NODE",
        help="Display configuration and exit. Optionally specify cluster name or hostname to show only that cluster.",
    )
    parser.add_argument(
        "--delete-reports",
        "-D",
        action="store_true",
        help="Delete report files (keeps node access config)",
    )
    parser.add_argument(
        "--export-ansible",
        "-E",
        nargs="+",
        metavar=("CLUSTER", "OUTPUT_FILE"),
        help="Export cluster config as Ansible group_vars YAML. Usage: --export-ansible CLUSTER [output.yml]",
    )
    parser.add_argument(
        "--fetch-sosreports",
        "-F",
        nargs="*",
        metavar="CLUSTER_OR_NODE",
        help="Fetch SOSreports from cluster nodes via SCP. Prompts to create if missing. Usage: -F [CLUSTER|node1 node2...]",
    )
    parser.add_argument(
        "--create-sosreports",
        action="store_true",
        help="Auto-create SOSreports on nodes where missing (use with -F). Skips confirmation prompt.",
    )
    parser.add_argument(
        "--collect-sosreports",
        "-R",
        metavar="NODE",
        help="Collect SOSreports from cluster: discover nodes from NODE, configure SAP extensions, create and fetch SOSreports",
    )
    parser.add_argument(
        "--configure-extensions",
        action="store_true",
        default=None,
        help="Auto-configure SAP SOSreport extensions without prompting (use with -R)",
    )
    parser.add_argument(
        "--force", "-f", action="store_true", help="Force rediscovery (ignore existing config)"
    )
    parser.add_argument(
        "--reuse-config",
        action="store_true",
        help="Reuse existing cluster_access_config.yaml instead of fresh discovery "
        "(same as SAP_HA_CHECK_REUSE_CONFIG=1)",
    )

    # Performance
    parser.add_argument(
        "--workers", "-w", type=int, default=10, help="Number of parallel workers (default: 10)"
    )

    # Rules
    parser.add_argument(
        "--rules-path",
        "-r",
        help="Path to CHK_*.yaml rules directory (default: ./rules/health_checks)",
    )
    parser.add_argument(
        "--list-rules", "-L", action="store_true", help="List available health check rules and exit"
    )

    # Skip options
    parser.add_argument(
        "--skip",
        nargs="+",
        choices=["access", "config", "pacemaker", "sap", "report"],
        help="Skip specific steps",
    )

    # Debug option
    parser.add_argument(
        "--debug",
        "-d",
        action="store_true",
        help="Enable debug mode (show config files used and step progress)",
    )

    # Strict mode option
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Strict mode: all checks required (fencing, alerts). Default: optional checks are warnings only",
    )

    # PDF report option (now default, kept for backwards compatibility)
    parser.add_argument(
        "--pdf",
        action="store_true",
        help="Generate PDF report (default: enabled, this flag is kept for compatibility)",
    )

    # No-PDF option to skip PDF generation
    parser.add_argument(
        "--no-pdf",
        action="store_true",
        help="Skip PDF report generation (useful if fpdf2 is not installed)",
    )

    # Verbose PDF option to show all checks in detail
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose PDF report - show all checks in detail (not just failed/warnings)",
    )

    # No-update-check option
    parser.add_argument(
        "--no-update-check", action="store_true", help="Skip checking for software updates"
    )

    # Local mode option
    parser.add_argument(
        "--local",
        "-l",
        action="store_true",
        help="Run on cluster node itself (execute commands locally instead of via SSH)",
    )

    # Guide option
    parser.add_argument(
        "--guide",
        "-G",
        action="store_true",
        help="Show detailed usage guide with examples and next steps",
    )

    # Install guide shortcut
    parser.add_argument(
        "--install",
        "-i",
        action="store_true",
        help="Show installation guide (shortcut for --suggest install)",
    )

    # Suggest option
    parser.add_argument(
        "--suggest",
        nargs="?",
        const="auto",
        choices=["access", "config", "pacemaker", "sap", "install", "all", "auto"],
        help="Show suggestions for a step (default: first failing step from last run)",
    )
    parser.add_argument(
        "--suggest-skip",
        nargs="+",
        choices=["access", "config", "pacemaker", "sap", "install"],
        help="Skip these steps when auto-suggesting (use with --suggest)",
    )

    # List steps option
    parser.add_argument(
        "--list-steps", action="store_true", help="List all health check steps with descriptions"
    )

    # Usage/scan option
    parser.add_argument(
        "--usage",
        "-u",
        action="store_true",
        help="Scan current directory for sosreports, inventory files, and former results; interactive setup",
    )

    args = parser.parse_args()

    # Check for software updates
    def check_for_updates():
        """Check if a newer version is available via git and offer to update."""
        try:
            import subprocess

            # Check if we're in a git repository
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=SCRIPT_DIR,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode != 0:
                return  # Not a git repo

            # Fetch latest from remote (quietly)
            subprocess.run(
                ["git", "fetch", "--quiet"],
                cwd=SCRIPT_DIR,
                capture_output=True,
                timeout=30,
                check=False,
            )

            # Get local and remote HEAD
            local_head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=SCRIPT_DIR,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout.strip()

            remote_head = subprocess.run(
                ["git", "rev-parse", "@{u}"],
                cwd=SCRIPT_DIR,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout.strip()

            if local_head != remote_head:
                # Check how many commits behind (remote has that we don't)
                behind_count = subprocess.run(
                    ["git", "rev-list", "--count", f"{local_head}..{remote_head}"],
                    cwd=SCRIPT_DIR,
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                ).stdout.strip()

                # Only show update prompt if actually behind (not if ahead with local commits)
                try:
                    behind_int = int(behind_count)
                except ValueError:
                    behind_int = 0

                if behind_int > 0:
                    print(
                        f"\n[INFO] A newer version is available ({behind_count} commit(s) behind)."
                    )
                    print("  To update, run: git pull")
        except Exception:
            pass  # Silently ignore any errors in update check

    # Check for updates (skip only if explicitly disabled with --no-update-check)
    if sys.stdin.isatty() and not args.no_update_check:
        check_for_updates()

    # Handle usage/scan action (-u)
    if args.usage:
        # Pass sosreport_dir and any CLI-provided hosts to avoid re-prompting
        result = run_usage_scan(base_dir=args.sosreport_dir, seed_hosts=args.hosts or None)
        if result is None:
            sys.exit(0)

        # Process the result and run health check
        if result["action"] == "local":
            args.local = True
        elif result["action"] == "hosts":
            # Create temp hosts file
            import tempfile

            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as temp_file:
                for host in result["hosts"]:
                    temp_file.write(f"{host}\n")
            args.hosts_file = temp_file.name
        elif result["action"] == "hosts_file":
            args.hosts_file = result["hosts_file"]
        elif result["action"] == "sosreport":
            args.sosreport_dir = result["sosreport_dir"]
        elif result["action"] == "continue":
            args.config_dir = result.get("config_dir")
        elif result["action"] == "fetch_sosreports":
            # Fetch SOSreports from cluster and then analyze them
            seed_node = result["seed_node"]
            output_dir = result.get("output_dir") or args.sosreport_dir
            downloaded = create_and_fetch_sosreports(
                seed_node=seed_node, output_dir=output_dir, interactive=sys.stdin.isatty()
            )
            if downloaded:
                # Set sosreport_dir to where we downloaded them
                args.sosreport_dir = output_dir or str(Path.cwd() / "sosreports")
            else:
                print("  No SOSreports were collected.")
                sys.exit(1)
        # Continue to run the health check with the set arguments

    # Handle guide action
    if args.guide:
        _cfg_dir = Path(args.config_dir) if args.config_dir else DEFAULT_OUTPUT_DIR
        print_guide(_rhel_major_from_config(_cfg_dir))
        sys.exit(0)

    # Handle install guide shortcut (-i / --install)
    if args.install:
        # Try to use dynamic guide if we have access config
        config_dir = Path(args.config_dir) if args.config_dir else DEFAULT_OUTPUT_DIR
        config_path = config_dir / AccessDiscovery.CONFIG_FILE
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    access_data = yaml.safe_load(f) or {}
                if access_data.get("nodes"):
                    # Create minimal health check instance for dynamic guide
                    hc = ClusterHealthCheck(config_dir=str(config_dir), local_mode=args.local)
                    hc.access_config = type("Config", (), {"nodes": access_data.get("nodes", {})})()
                    hc.print_dynamic_install_guide()
                    sys.exit(0)
            except Exception:
                pass
        # Fall back to static guide
        print_suggestions("install", _rhel_major_from_config(config_dir))
        sys.exit(0)

    # Handle suggest action
    if args.suggest:
        step = args.suggest
        skip_steps = args.suggest_skip or []
        config_dir = Path(args.config_dir) if args.config_dir else DEFAULT_OUTPUT_DIR

        if step == "auto":
            # Read last run status to find first failing step
            status_file = config_dir / "last_run_status.yaml"

            if not status_file.exists():
                print("No previous run found. Run a health check first:")
                print("  ./sap_cluster_checks.py hana01")
                print("\nOr specify a step directly:")
                print("  ./sap_cluster_checks.py --suggest config")
                sys.exit(1)

            with open(status_file, "r", encoding="utf-8") as f:
                status = yaml.safe_load(f)

            # Check for package/command issues in the last report
            packages_missing = False
            # Find most recent report
            import glob

            reports = sorted(
                glob.glob(str(config_dir / "health_check_report_*.yaml")), reverse=True
            )
            if reports:
                try:
                    with open(reports[0], "r", encoding="utf-8") as f:
                        report = yaml.safe_load(f)
                    for result in report.get("results", []):
                        msg = result.get("message", "") or ""
                        if "package not found" in msg.lower() or (
                            "command '" in msg.lower() and "not found" in msg.lower()
                        ):
                            packages_missing = True
                            break
                except Exception:
                    pass

            if packages_missing and "install" not in skip_steps:
                print("Cluster packages not installed!")
                print("Showing installation guide...\n")
                step = "install"
            else:
                failed_steps = status.get("failed_steps", [])

                # Filter out skipped steps
                if skip_steps:
                    failed_steps = [s for s in failed_steps if s not in skip_steps]
                    if skip_steps:
                        print(f"Skipping: {', '.join(skip_steps)}\n")

                if not failed_steps:
                    print("No failing steps found!")
                    if skip_steps:
                        print(f"(after skipping: {', '.join(skip_steps)})")
                    print("\nAll steps passed in the last run.")
                    sys.exit(0)

                step = failed_steps[0]
                print(f"First failing step: {step}")
                if len(failed_steps) > 1:
                    others = ", ".join(failed_steps[1:])
                    print(f"Other failing steps: {others}")
                    print(f"\nTo skip this and see next: --suggest --suggest-skip {step}")
                print()

        # Use dynamic guide for install step
        if step == "install":
            config_path = config_dir / AccessDiscovery.CONFIG_FILE
            if config_path.exists():
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        access_data = yaml.safe_load(f) or {}
                    if access_data.get("nodes"):
                        hc = ClusterHealthCheck(config_dir=str(config_dir), local_mode=args.local)
                        hc.access_config = type(
                            "Config", (), {"nodes": access_data.get("nodes", {})}
                        )()
                        hc.print_dynamic_install_guide()
                        sys.exit(0)
                except Exception:
                    pass

        print_suggestions(step, _rhel_major_from_config(config_dir))
        sys.exit(0)

    # Handle list-steps action
    if args.list_steps:
        print_steps()
        sys.exit(0)

    # Determine config directory
    config_dir = Path(args.config_dir) if args.config_dir else DEFAULT_OUTPUT_DIR
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / AccessDiscovery.CONFIG_FILE

    # Handle export-ansible action (before interactive mode)
    if args.export_ansible:
        cluster_name = args.export_ansible[0]
        output_file = args.export_ansible[1] if len(args.export_ansible) > 1 else None
        success = export_ansible_vars(config_path, cluster_name, output_file)
        sys.exit(0 if success else 1)

    # Handle fetch-sosreports action
    if args.fetch_sosreports is not None:
        # Check what was provided: cluster name or node names
        fetch_args = args.fetch_sosreports
        auto_create = getattr(args, "create_sosreports", False)

        if not fetch_args:
            # No arguments - use cluster from -C if provided
            if args.cluster:
                downloaded = fetch_sosreports(
                    config_path, cluster_name=args.cluster, auto_create=auto_create
                )
            else:
                print("[ERROR] Please specify a cluster name or node names.")
                print("Usage: --fetch-sosreports CLUSTER")
                print("       --fetch-sosreports node1 node2 ...")
                print("       -C CLUSTER --fetch-sosreports")
                sys.exit(1)
        elif len(fetch_args) == 1:
            # Single argument - check if it's a cluster name or a node
            arg = fetch_args[0]
            # Load config to check if it's a cluster name
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)
                clusters = config.get("clusters", {})
                if arg in clusters:
                    downloaded = fetch_sosreports(
                        config_path, cluster_name=arg, auto_create=auto_create
                    )
                else:
                    # Treat as node name
                    downloaded = fetch_sosreports(config_path, nodes=[arg], auto_create=auto_create)
            else:
                # No config, treat as node name
                downloaded = fetch_sosreports(config_path, nodes=[arg], auto_create=auto_create)
        else:
            # Multiple arguments - treat as node names
            downloaded = fetch_sosreports(config_path, nodes=fetch_args, auto_create=auto_create)

        sys.exit(0 if downloaded else 1)

    # Handle collect-sosreports action (new comprehensive workflow)
    if args.collect_sosreports:
        seed_node = args.collect_sosreports
        configure_ext = getattr(args, "configure_extensions", None)

        downloaded = create_and_fetch_sosreports(
            seed_node=seed_node,
            output_dir=args.sosreport_dir,
            configure_extensions=configure_ext,
            interactive=sys.stdin.isatty(),
        )
        sys.exit(0 if downloaded else 1)

    # Interactive mode: if no arguments provided, show intro and ask user
    local_mode = args.local
    interactive_hosts = None

    no_input_specified = (
        not args.hosts
        and not args.hosts_file
        and not args.sosreport_dir
        and not args.cluster
        and not args.local
        and not args.access_only
        and not args.show_config
        and not args.delete_reports
        and not args.list_rules
        and not args.force
        and not args.export_ansible
        and args.fetch_sosreports is None
    )

    if no_input_specified:
        # Run interactive startup
        nodes, should_continue = interactive_startup(config_path)
        if not should_continue:
            sys.exit(0)

        if nodes == ["local"]:
            local_mode = True
        elif nodes:
            interactive_hosts = nodes

    # Handle hosts provided on command line or from interactive mode
    hosts_file = args.hosts_file
    temp_hosts_file = None
    hosts_to_use = args.hosts or interactive_hosts

    if hosts_to_use and not hosts_file:
        # Create temporary hosts file from command line or interactive input
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as temp_hosts_file:
            for host in hosts_to_use:
                temp_hosts_file.write(f"{host}\n")
        hosts_file = temp_hosts_file.name
        if args.debug:
            print(f"[DEBUG] Created temp hosts file: {hosts_file}")
            print(f"[DEBUG] Hosts: {', '.join(hosts_to_use)}")

    # Handle show-config action
    if args.show_config:
        # args.show_config is True (no argument) or a string (cluster/node name)
        cluster_or_node = None if args.show_config is True else args.show_config
        show_config(config_path, cluster_or_node, config_only=True)
        sys.exit(0)

    # Handle delete-config action
    if args.delete_reports:
        delete_config(config_path)
        print("  Restarting health check...\n")
        # Restart without -D flag to prevent loop
        new_argv = [arg for arg in sys.argv if arg not in ["-D", "--delete-reports"]]
        os.execv(sys.executable, [sys.executable] + new_argv)

    # Handle list-rules action
    if args.list_rules:
        rules_path = args.rules_path or ClusterHealthCheck.DEFAULT_RULES_PATH
        engine = RulesEngine(rules_path=rules_path)
        engine.load_rules()
        print("\n" + "=" * 63)
        print(" Available Health Check Rules")
        print("=" * 63)
        print(f"\nRules path: {rules_path}\n")
        print(f"{'Check ID':<30} {'Severity':<10} Description")
        print("-" * 63)
        for rule in engine.rules:
            print(f"{rule.check_id:<30} {rule.severity:<10} {rule.description[:40]}")
        print(f"\nTotal: {len(engine.rules)} rules")
        sys.exit(0)

    # Create health check instance
    # PDF generation is enabled by default, can be disabled with --no-pdf
    generate_pdf = not args.no_pdf
    verbose_pdf = args.verbose  # Show all checks in detail in PDF

    # Check upfront if PDF dependencies are available - inform user of missing packages
    if generate_pdf:
        from .report_generator import is_pdf_available

        if not is_pdf_available():
            # Check which PDF-related packages are missing
            pdf_packages = {
                "fpdf2": "fpdf",  # PDF generation library (import name differs)
            }
            recommended_packages = {
                "PyYAML": "yaml",  # YAML report serialization
                "paramiko": "paramiko",  # SSH access to cluster nodes
            }
            missing_required = []
            missing_recommended = []
            for pkg_name, import_name in pdf_packages.items():
                try:
                    __import__(import_name)
                except ImportError:
                    missing_required.append(pkg_name)
            for pkg_name, import_name in recommended_packages.items():
                try:
                    __import__(import_name)
                except ImportError:
                    missing_recommended.append(pkg_name)

            print("\n  [INFO] PDF report cannot be created - missing packages")
            print("  " + "-" * 50)
            if missing_required:
                print(f"    Required (for PDF):   {', '.join(missing_required)}")
            if missing_recommended:
                print(f"    Recommended:          {', '.join(missing_recommended)}")
            install_all = missing_required + missing_recommended
            if install_all:
                print(f"\n    Install with: pip install {' '.join(install_all)}")
            print("  " + "-" * 50)
            generate_pdf = False

    # --reuse-config flag sets the env var so discover_access.py picks it up
    if getattr(args, "reuse_config", False):
        os.environ["SAP_HA_CHECK_REUSE_CONFIG"] = "1"

    health_check = ClusterHealthCheck(
        config_dir=str(config_dir),
        sosreport_dir=args.sosreport_dir,
        hosts_file=hosts_file,
        workers=args.workers,
        rules_path=args.rules_path,
        debug=args.debug,
        ansible_group=args.group,
        cluster_name=args.cluster,
        local_mode=local_mode,
        strict_mode=args.strict,
        generate_pdf=generate_pdf,
        verbose_pdf=verbose_pdf,
    )

    def cleanup_temp_file():
        """Clean up temporary hosts file if created."""
        if temp_hosts_file:
            try:
                os.unlink(temp_hosts_file.name)
            except Exception:
                pass

    def show_interactive_menu():
        """Show interactive menu and return user choice."""
        print("\n" + "=" * 63)
        print(" What would you like to do next?")
        print("=" * 63)
        print("  [1] Show installation status (-i)  [default]")
        print("  [2] Rerun health check")
        print("  [3] Run on different hosts")
        print("  [4] Show configuration")
        if generate_pdf:
            print("  [5] Save PDF report (custom filename)")
        else:
            print("  (5) Save PDF report (requires fpdf2)")
        print("  [6] Show suggestions")
        print("  [7] Reset configuration (delete cached discovery)")
        if generate_pdf:
            print("  [q] Save PDF and quit")
        else:
            print("  [q] Quit")
        print("-" * 63)
        try:
            import select as _select

            sys.stdout.write("  Enter choice [1-7/q] (auto-quit in 20s): ")
            sys.stdout.flush()
            ready, _wlist, _xlist = _select.select([sys.stdin], [], [], 20)
            if ready:
                choice = sys.stdin.readline().strip().lower()
                return choice if choice else "1"  # Default to installation status
            if generate_pdf:
                print("\n  No response, saving PDF and exiting.")
            else:
                print("\n  No response, exiting.")
            return "q"
        except (EOFError, KeyboardInterrupt):
            return "q"

    try:
        if args.access_only:
            # Only run access discovery
            health_check.print_banner()
            success = health_check.step_access_discovery(force=args.force)
            cleanup_temp_file()
            sys.exit(0 if success else 1)
        else:
            # Run all checks
            exit_code = health_check.run_all_checks(
                force_rediscover=args.force, skip_steps=args.skip
            )

            # If cluster is healthy (exit_code == 0), exit directly
            if exit_code == 0:
                # Auto-open PDF if generated
                if generate_pdf and health_check.last_pdf_file:
                    import subprocess
                    import platform

                    try:
                        system = platform.system()
                        if system == "Linux":
                            subprocess.Popen(  # pylint: disable=consider-using-with
                                ["xdg-open", str(health_check.last_pdf_file)],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                        elif system == "Darwin":  # macOS
                            subprocess.Popen(  # pylint: disable=consider-using-with
                                ["open", str(health_check.last_pdf_file)],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                        elif system == "Windows":
                            os.startfile(  # pylint: disable=no-member
                                str(health_check.last_pdf_file)
                            )
                        print("  Opening PDF...")
                    except Exception:
                        pass  # Silently ignore if can't open
                print("\n  Goodbye!")
                cleanup_temp_file()
                sys.exit(0)

            # Interactive menu loop (only shown when there are issues)
            while True:
                choice = show_interactive_menu()

                if choice in ("1", "i"):
                    # Show installation status
                    health_check.print_dynamic_install_guide()
                elif choice in ("2", "r"):
                    # Rerun health check
                    print("\n" + "=" * 63)
                    print(" Rerunning health check...")
                    print("=" * 63)
                    exit_code = health_check.run_all_checks(
                        force_rediscover=False, skip_steps=args.skip
                    )
                elif choice in ("3", "h"):
                    # Run on different hosts
                    try:
                        new_hosts = input("  Enter hostnames (space-separated): ").strip()
                        if new_hosts:
                            host_list = new_hosts.split()
                            print(f"\n  Running health check on: {', '.join(host_list)}")
                            print("=" * 63)

                            # Create temporary hosts file
                            import tempfile

                            with tempfile.NamedTemporaryFile(
                                mode="w", suffix=".txt", delete=False
                            ) as tmp:
                                tmp.write("\n".join(host_list))
                                tmp_hosts_path = tmp.name

                            try:
                                # Create new health check instance with new hosts
                                new_health_check = ClusterHealthCheck(
                                    config_dir=str(config_dir),
                                    sosreport_dir=args.sosreport_dir,
                                    hosts_file=tmp_hosts_path,
                                    workers=args.workers,
                                    rules_path=args.rules_path,
                                    debug=args.debug,
                                    ansible_group=args.group,
                                    cluster_name=None,  # Force rediscovery
                                    local_mode=False,
                                    strict_mode=args.strict,
                                    generate_pdf=not args.no_pdf,
                                    verbose_pdf=verbose_pdf,
                                )
                                # Run health check with force rediscovery
                                exit_code = new_health_check.run_all_checks(
                                    force_rediscover=True, skip_steps=args.skip
                                )
                                # Update reference for subsequent menu options
                                health_check = new_health_check
                            finally:
                                # Clean up temp file
                                try:
                                    os.unlink(tmp_hosts_path)
                                except Exception:
                                    pass
                    except (EOFError, KeyboardInterrupt):
                        print("\n  Cancelled.")
                elif choice in ("4", "c"):
                    # Show configuration (show_config imported at module level)
                    show_config(health_check.config_dir / "cluster_access_config.yaml")
                elif choice in ("5", "p"):
                    # Save PDF report with custom filename
                    if not generate_pdf:
                        print("\n  [INFO] PDF reports not available (fpdf2 not installed)")
                        continue
                    if not health_check.check_results:
                        print(
                            "\n  [WARN] No health check results available. Run a health check first."
                        )
                        continue
                    try:
                        # Get cluster name for default filename
                        cluster_name = "(unknown)"
                        if health_check.access_config and health_check.access_config.clusters:
                            for cname in health_check.access_config.clusters.keys():
                                if cname != "(unknown)":
                                    cluster_name = cname
                                    break
                        cluster_name_safe = re.sub(r"[^\w\-]", "_", cluster_name)

                        # Default filename
                        pdf_timestamp = datetime.now().strftime("%Y%m%d")
                        pdf_time = datetime.now().strftime("%H%M")
                        default_name = f"{pdf_timestamp}_health_check_report_{cluster_name_safe}_{pdf_time}.pdf"

                        print(f"\n  Default filename: {default_name}")
                        custom_name = input(
                            "  Enter filename (or press Enter for default): "
                        ).strip()

                        if custom_name:
                            # Ensure .pdf extension
                            if not custom_name.lower().endswith(".pdf"):
                                custom_name += ".pdf"
                            pdf_file = health_check.config_dir / custom_name
                        else:
                            pdf_file = health_check.config_dir / default_name

                        # Generate PDF using unified data model
                        from .report_generator import generate_health_check_report

                        # Use unified data model for PDF generation
                        # pylint: disable-next=protected-access
                        report_data = health_check._build_cluster_report_data()

                        generate_health_check_report(
                            report_data.get_results_list(),
                            report_data.get_summary_dict(),
                            report_data.to_cluster_info(),
                            str(pdf_file),
                            report_data.get_install_status() or None,
                            verbose=verbose_pdf,
                        )
                        print(f"\n  PDF report saved: {pdf_file}")
                        print("  Goodbye!")
                        break

                    except ImportError:
                        print("\n  [ERROR] PDF generation requires fpdf2")
                        print("         Install with: pip install fpdf2")
                        print("         Or run with --no-pdf to skip PDF generation")
                    except (EOFError, KeyboardInterrupt):
                        print("\n  Cancelled.")
                    except Exception as e:
                        print(f"\n  [ERROR] PDF generation failed: {e}")
                elif choice in ("6", "s"):
                    # Show suggestions
                    print("\n  Available suggestion topics:")
                    print("    [1] install   - Full installation guide")
                    print("    [2] access    - Access discovery help")
                    print("    [3] config    - Cluster configuration")
                    print("    [4] pacemaker - Pacemaker/Corosync")
                    print("    [5] sap       - SAP HANA configuration")
                    print("    [a] all       - Show all suggestions")
                    print("    [q] back      - Return to main menu")
                    try:
                        topic = input("\n  Select topic: ").strip().lower()
                        topic_map = {
                            "1": "install",
                            "2": "access",
                            "3": "config",
                            "4": "pacemaker",
                            "5": "sap",
                        }
                        _rhel = health_check._get_rhel_major()  # pylint: disable=protected-access
                        if topic in topic_map:
                            print_suggestions(topic_map[topic], _rhel)
                        elif topic in ["install", "access", "config", "pacemaker", "sap"]:
                            print_suggestions(topic, _rhel)
                        elif topic in ("a", "all"):
                            for t in ["install", "access", "config", "pacemaker", "sap"]:
                                print_suggestions(t, _rhel)
                        elif topic in ("q", "back", ""):
                            pass  # Return to main menu
                        else:
                            print(f"  Unknown topic: {topic}")
                    except (EOFError, KeyboardInterrupt):
                        pass
                elif choice in ("7", "d"):
                    # Reset/delete configuration
                    config_file = health_check.config_dir / "cluster_access_config.yaml"
                    if config_file.exists():
                        try:
                            confirm = (
                                input(
                                    "  Delete saved configuration? This will force fresh discovery. [y/N]: "
                                )
                                .strip()
                                .lower()
                            )
                            if confirm in ("y", "yes"):
                                config_file.unlink()
                                print("  Configuration deleted.")
                                print("\n  To rediscover, run:")
                                print("    ./sap_cluster_checks.py <hostname>")
                                print("    ./sap_cluster_checks.py -s sosreports/")
                            else:
                                print("  Cancelled.")
                        except (EOFError, KeyboardInterrupt):
                            print("\n  Cancelled.")
                    else:
                        print("  No configuration file found.")
                elif choice in ("q", "quit", "exit"):
                    # Save PDF before quitting (if available)
                    if generate_pdf and health_check.check_results:
                        try:
                            # Get cluster name for filename
                            cluster_name = "(unknown)"
                            if health_check.access_config and health_check.access_config.clusters:
                                for cname in health_check.access_config.clusters.keys():
                                    if cname != "(unknown)":
                                        cluster_name = cname
                                        break
                            cluster_name_safe = re.sub(r"[^\w\-]", "_", cluster_name)

                            # Generate filename
                            pdf_timestamp = datetime.now().strftime("%Y%m%d")
                            pdf_time = datetime.now().strftime("%H%M")
                            pdf_file = (
                                health_check.config_dir
                                / f"{pdf_timestamp}_health_check_report_{cluster_name_safe}_{pdf_time}.pdf"
                            )

                            # Generate PDF
                            from .report_generator import generate_health_check_report

                            # pylint: disable-next=protected-access
                            report_data = health_check._build_cluster_report_data()
                            generate_health_check_report(
                                report_data.get_results_list(),
                                report_data.get_summary_dict(),
                                report_data.to_cluster_info(),
                                str(pdf_file),
                                report_data.get_install_status() or None,
                                verbose=verbose_pdf,
                            )
                            print(f"\n  PDF report saved: {pdf_file}")

                            # Open PDF with default viewer
                            import subprocess
                            import platform

                            try:
                                system = platform.system()
                                if system == "Linux":
                                    subprocess.Popen(  # pylint: disable=consider-using-with
                                        ["xdg-open", str(pdf_file)],
                                        stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL,
                                    )
                                elif system == "Darwin":  # macOS
                                    subprocess.Popen(  # pylint: disable=consider-using-with
                                        ["open", str(pdf_file)],
                                        stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL,
                                    )
                                elif system == "Windows":
                                    os.startfile(str(pdf_file))  # pylint: disable=no-member
                                print("  Opening PDF...")
                            except Exception:
                                pass  # Silently ignore if can't open
                        except Exception as e:
                            print(f"\n  [WARN] Could not save PDF: {e}")
                    print("  Goodbye!")
                    break
                else:
                    print(f"  Invalid choice: {choice}")

            cleanup_temp_file()
            sys.exit(exit_code)

    except KeyboardInterrupt:
        cleanup_temp_file()
        print("\n\n[INTERRUPTED] Health check aborted by user.")
        sys.exit(130)


if __name__ == "__main__":
    main()
