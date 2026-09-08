"""
SAP Pacemaker Cluster Health Check - Access Discovery Module

Discovers available access methods to cluster nodes:
1. SSH direct access (preferred)
2. Ansible inventory
3. SOSreport files

Results are stored in a YAML config file for incremental investigation.
"""

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from .models import NodeAccess, AccessConfig, asdict
from .config_display import show_config, delete_config
from .sosreport_discovery import SOSReportDiscoveryMixin
from .ssh_discovery import SSHDiscoveryMixin


class AccessDiscovery(SOSReportDiscoveryMixin, SSHDiscoveryMixin):
    """Discovers and validates access methods to cluster nodes."""

    CONFIG_FILE = "cluster_access_config.yaml"
    ANSIBLE_CFG_LOCATIONS = [
        "./ansible.cfg",
        os.path.expanduser("~/.ansible.cfg"),
        "/etc/ansible/ansible.cfg",
    ]
    DEFAULT_ANSIBLE_INVENTORY = "/etc/ansible/hosts"
    SSH_TIMEOUT = 5
    MAX_WORKERS = 10

    def __init__(  # pylint: disable=unknown-option-value,too-many-positional-arguments
        self,
        config_dir: str = ".",
        sosreport_dir: Optional[str] = None,
        hosts_file: Optional[str] = None,
        force_rediscover: bool = False,
        debug: bool = False,
        ansible_group: Optional[str] = None,
        skip_ansible: bool = False,
        cluster_name: Optional[str] = None,
        local_mode: bool = False,
    ):
        self.config_dir = Path(config_dir)
        self.config_path = self.config_dir / self.CONFIG_FILE
        self.sosreport_dir = sosreport_dir
        self.hosts_file = hosts_file
        self.force_rediscover = force_rediscover
        self.debug = debug
        self.ansible_group = ansible_group
        self.skip_ansible = skip_ansible
        self.cluster_name = cluster_name
        self.local_mode = local_mode
        self.local_hostname = None
        self.config = self._load_or_create_config()

    def _load_or_create_config(self) -> AccessConfig:
        """Load existing config or create new one.

        By default, discovery always runs from scratch. To reuse a previously
        saved config set the environment variable SAP_HA_CHECK_REUSE_CONFIG=1
        (or use the --reuse-config CLI flag).  The -f/--force flag always
        overrides and forces a fresh discovery.
        """
        reuse_config = os.environ.get("SAP_HA_CHECK_REUSE_CONFIG", "").strip()
        allow_reuse = reuse_config in ("1", "true", "yes")

        if self.config_path.exists() and allow_reuse and not self.force_rediscover:
            print(f"Loading existing config from {self.config_path}")
            print("  (SAP_HA_CHECK_REUSE_CONFIG is set)")
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                config = AccessConfig(**data)
                # Clear old nodes if hosts file specified (fresh discovery for those hosts)
                if self.hosts_file:
                    if self.debug:
                        print("  [DEBUG] Clearing old nodes for fresh cluster discovery")
                    config.nodes = {}
                return config
        if self.force_rediscover:
            print("Starting fresh discovery (--force)")
        return AccessConfig()

    def save_config(self):
        """Save current configuration to YAML file."""
        self.config.discovery_timestamp = datetime.now().isoformat()
        self.config_dir.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.dump(asdict(self.config), f, default_flow_style=False)
        print(f"Configuration saved to {self.config_path}")

    def discover_all(self) -> AccessConfig:
        """
        Main discovery routine - discovers all access methods using multithreading.
        """
        print("=" * 60)
        print("SAP Pacemaker Cluster - Access Discovery")
        print("=" * 60)

        # Handle local mode - running on the cluster node itself
        if self.local_mode:
            return self._discover_local_mode()

        # Collect all hosts from different sources
        all_hosts = {}  # hostname -> {ansible_info, sosreport_path}
        file_hosts = []

        # 0. If SOSreport directory specified, discover SOSreports FIRST and use ONLY those nodes
        if self.sosreport_dir:
            # Use cluster-aware discovery
            clusters = self.discover_sosreports_with_clusters()

            if clusters:
                # Determine which sosreports to use
                sosreports = {}

                if len(clusters) > 1:
                    # Multiple clusters detected - prompt for selection
                    print("\n" + "=" * 60)
                    print(" Multiple clusters detected in SOSreports")
                    print("=" * 60)
                    # Default to the cluster containing CLI-specified nodes
                    default_cluster = None
                    if self.hosts_file and os.path.exists(self.hosts_file):
                        with open(self.hosts_file, "r", encoding="utf-8") as f:
                            cli_hosts = [
                                line.strip().split()[0]
                                for line in f
                                if line.strip() and not line.strip().startswith("#")
                            ]
                        if cli_hosts:
                            for cname, cinfo in clusters.items():
                                if set(cli_hosts) & set(cinfo["nodes"].keys()):
                                    default_cluster = cname
                                    break
                    selected_cluster = self.prompt_cluster_selection(
                        clusters, default_cluster=default_cluster
                    )

                    if selected_cluster is None:
                        print("\n[INFO] Cluster selection cancelled.")
                        sys.exit(0)
                    elif selected_cluster == "__all__":
                        # Use all sosreports
                        cluster_name = list(clusters.keys())[0]
                        print(f"\n[INFO] Analyzing all {len(clusters)} clusters together")
                        for cluster_info in clusters.values():
                            sosreports.update(cluster_info["nodes"])
                    else:
                        # Use only selected cluster's sosreports
                        cluster_name = selected_cluster
                        sosreports = clusters[selected_cluster]["nodes"]
                        print(f"\n[INFO] Analyzing cluster '{selected_cluster}' only")
                        # Store cluster info
                        self.config.clusters[selected_cluster] = {
                            "nodes": list(sosreports.keys()),
                            "discovered_from": "sosreport",
                            "discovered_at": datetime.now().isoformat(),
                        }
                else:
                    # Single cluster - use all sosreports
                    cluster_name = list(clusters.keys())[0]
                    sosreports = clusters[cluster_name]["nodes"]
                    if cluster_name != "(unknown)":
                        print(f"\n[INFO] Single cluster detected: {cluster_name}")
                        self.config.clusters[cluster_name] = {
                            "nodes": list(sosreports.keys()),
                            "discovered_from": "sosreport",
                            "discovered_at": datetime.now().isoformat(),
                        }

                if sosreports:
                    print(f"\n[INFO] SOSreport mode: {len(sosreports)} SOSreport(s) found")

                    # Extract detailed cluster configuration from the first available SOSreport
                    first_sosreport = list(sosreports.values())[0]
                    cluster_config = self.extract_cluster_config_from_cib(first_sosreport)
                    if cluster_config:
                        # Merge into cluster config
                        if cluster_name and cluster_name in self.config.clusters:
                            self.config.clusters[cluster_name].update(cluster_config)
                        # Print what we found
                        if cluster_config.get("sid"):
                            print(
                                f"  SAP HANA SID: {cluster_config.get('sid')}, Instance: {cluster_config.get('instance_number', 'N/A')}"
                            )
                        if cluster_config.get("virtual_ip"):
                            print(f"  Virtual IP: {cluster_config.get('virtual_ip')}")
                        if cluster_config.get("replication_mode"):
                            print(f"  Replication Mode: {cluster_config.get('replication_mode')}")
                        if cluster_config.get("stonith_device"):
                            print(f"  STONITH Device: {cluster_config.get('stonith_device')}")

                    # Check if cluster was running when SOSreports were captured
                    for hostname, sos_path in sosreports.items():
                        was_running, reason = self.was_cluster_running_in_sosreport(sos_path)
                        if not was_running:
                            print(
                                f"\n  \u26a0\ufe0f  WARNING: Cluster was NOT running when {hostname}'s SOSreport was captured"
                            )
                            print(f"     Reason: {reason}")
                            print("     Some health check results may be incomplete or show errors")
                            print("     Consider creating new SOSreports with cluster running\n")
                            break  # Only warn once

                    # Extract all expected cluster nodes from corosync.conf
                    expected_nodes = set()
                    for hostname, sos_path in sosreports.items():
                        nodes_in_cluster = self.get_cluster_nodes_from_sosreport(sos_path)
                        if nodes_in_cluster:
                            expected_nodes.update(nodes_in_cluster)

                    # Add the SOSreport hostnames too (in case extraction failed)
                    expected_nodes.update(sosreports.keys())

                    # Find nodes we don't have SOSreports for
                    missing_sosreports = expected_nodes - set(sosreports.keys())

                    # Resolve hostname aliases: corosync names may differ from /etc/hostname
                    # (e.g., corosync uses "syz2bns2dbn01" but /etc/hostname is "ANL0117800957")
                    # When resolved, register under the corosync name (what pacemaker uses)
                    # and drop the /etc/hostname duplicate to avoid running checks twice
                    resolved_aliases = {}
                    if missing_sosreports:
                        resolved_aliases = self._resolve_sosreport_aliases(
                            sosreports, missing_sosreports
                        )
                        if resolved_aliases:
                            for corosync_name, sos_path in resolved_aliases.items():
                                primary = [h for h, p in sosreports.items() if p == sos_path]
                                primary_name = primary[0] if primary else "?"
                                print(
                                    f"\n[INFO] Hostname alias resolved: {corosync_name} = {primary_name} (same host)"
                                )
                                # Re-register sosreport under corosync name, remove /etc/hostname entry
                                sosreports[corosync_name] = sos_path
                                if primary_name in sosreports and primary_name != corosync_name:
                                    del sosreports[primary_name]
                            # Remove resolved nodes from missing set
                            missing_sosreports -= set(resolved_aliases.keys())
                            # Update cluster nodes list to use corosync names
                            if cluster_name and cluster_name in self.config.clusters:
                                self.config.clusters[cluster_name]["nodes"] = list(
                                    sosreports.keys()
                                )

                    if missing_sosreports:
                        print(
                            f"\n[INFO] Cluster has {len(expected_nodes)} nodes, but only {len(sosreports)} SOSreport(s)"
                        )
                        print(
                            f"       Missing SOSreports for: {', '.join(sorted(missing_sosreports))}"
                        )
                        print("       Attempting SSH access to get live data...")

                    # Clear old nodes
                    self.config.nodes = {}

                    # Get local hostname to detect if we're running on a cluster node
                    local_hostname = self.local_hostname or self.get_local_hostname()

                    # SOSreport-only mode: nodes with SOSreports use them directly (no SSH probing)
                    # Only nodes WITHOUT SOSreports (missing from cluster) attempt SSH
                    total_nodes = len(sosreports) + len(missing_sosreports)
                    print(f"\n=== Checking access to {total_nodes} cluster node(s) ===")
                    print("  [INFO] SOSreport-only mode: skipping SSH for nodes with SOSreports")

                    # Process nodes WITH SOSreports - use sosreport directly
                    for hostname, path in sosreports.items():
                        node = NodeAccess(hostname=hostname)
                        node.last_checked = datetime.now().isoformat()
                        node.sosreport_path = path
                        node.machine_id = self.get_machine_id_sosreport(path)

                        # Check if this node is the local machine
                        if hostname == local_hostname:
                            node.preferred_method = "local"
                            print(f"  {hostname}: SOSreport + Local (this node) -> local")
                        else:
                            node.preferred_method = "sosreport"
                            print(f"  {hostname}: SOSreport -> sosreport")

                        self.config.nodes[hostname] = asdict(node)

                    # Process nodes WITHOUT SOSreports - try SSH/local
                    if missing_sosreports:
                        with ThreadPoolExecutor(
                            max_workers=min(len(missing_sosreports), self.MAX_WORKERS)
                        ) as executor:
                            futures = {
                                executor.submit(
                                    self.check_node_access, hostname, None, None
                                ): hostname
                                for hostname in missing_sosreports
                            }
                            for future in as_completed(futures):
                                hostname = futures[future]
                                try:
                                    node = future.result()
                                    # Check if this missing node is the local machine
                                    if hostname == local_hostname and not node.ssh_reachable:
                                        node.preferred_method = "local"
                                        print(
                                            f"  {hostname}: Local (this node, no SOSreport) -> local"
                                        )
                                    elif node.ssh_reachable:
                                        print(
                                            f"  {hostname}: SSH({node.ssh_user}) -> ssh (live, no SOSreport)"
                                        )
                                    else:
                                        print(f"  {hostname}: NO ACCESS (missing SOSreport)")
                                    self.config.nodes[hostname] = asdict(node)
                                except Exception as e:
                                    print(f"  {hostname}: Error - {e}")

                    self.config.sosreport_directory = self.sosreport_dir
                    self.config.discovery_complete = True
                    self.save_config()
                    self._print_summary()
                    return self.config

        # 1. Check if cluster name specified - use saved cluster nodes
        if self.cluster_name:
            if self.cluster_name in self.config.clusters:
                cluster_info = self.config.clusters[self.cluster_name]
                file_hosts = cluster_info.get("nodes", [])
                print(f"\n=== Using saved cluster: {self.cluster_name} ===")
                print(f"  Nodes: {', '.join(file_hosts)}")
                print(f"  Discovered from: {cluster_info.get('discovered_from', 'unknown')}")
                # Clear old nodes - only check this cluster's nodes
                self.config.nodes = {}
            else:
                print(f"\n[WARNING] Cluster '{self.cluster_name}' not found in config")
                print(f"  Known clusters: {', '.join(self.config.clusters.keys()) or '(none)'}")
                print("  Run with a node name first to discover the cluster")

        # 2. Get hosts from file/command line
        if not file_hosts:
            file_hosts = self.get_hosts_from_file()
            if file_hosts:
                # Hosts specified on command line - clear old nodes, only check these
                print("\n[INFO] Host mode: analyzing only specified hosts")
                self.config.nodes = {}

        # 3. If hosts specified, try to discover cluster members from first reachable host
        if file_hosts and not self.cluster_name:
            if self.debug:
                print("  [DEBUG] Hosts specified, attempting cluster auto-discovery")

            # Try to discover cluster nodes from the first specified host
            for seed_host in file_hosts:
                # Quick SSH check
                reachable, ssh_user = self.check_ssh_access(seed_host)
                if reachable:
                    # Discover cluster members (returns cluster_name, nodes)
                    _discovered_name, cluster_nodes = self.discover_cluster_nodes(
                        seed_host, ssh_user
                    )
                    # Only use discovered nodes if we found more than what was specified
                    # or if we successfully discovered the cluster
                    if cluster_nodes and len(cluster_nodes) >= len(file_hosts):
                        file_hosts = cluster_nodes
                    elif not cluster_nodes or len(cluster_nodes) < len(file_hosts):
                        # Cluster discovery failed or incomplete, keep original hosts
                        if self.debug:
                            print("  [DEBUG] Cluster discovery incomplete, keeping specified hosts")
                    break
                if self.debug:
                    print(f"  [DEBUG] {seed_host} not reachable, trying next...")

        # 3. Discover Ansible inventory (skip if hosts provided)
        if not self.skip_ansible and not file_hosts:
            self.discover_ansible_inventory()
            ansible_hosts = self.get_ansible_hosts()

            # Filter by group if specified
            if self.ansible_group:
                filtered_hosts = {}
                for hostname, info in ansible_hosts.items():
                    groups = info.get("groups", [])
                    if self.ansible_group in groups or self.ansible_group == "all":
                        filtered_hosts[hostname] = info
                if self.debug:
                    print(
                        f"  [DEBUG] Filtered to group '{self.ansible_group}': {len(filtered_hosts)} hosts"
                    )
                ansible_hosts = filtered_hosts

            for hostname, info in ansible_hosts.items():
                all_hosts[hostname] = {"ansible_info": info, "sosreport_path": None}

        # 4. Add hosts from file/cluster discovery
        for hostname in file_hosts:
            if hostname not in all_hosts:
                all_hosts[hostname] = {"ansible_info": None, "sosreport_path": None}

        # 3. Discover SOSreports
        sosreports = self.discover_sosreports()
        for hostname, path in sosreports.items():
            if hostname in all_hosts:
                all_hosts[hostname]["sosreport_path"] = path
            else:
                all_hosts[hostname] = {"ansible_info": None, "sosreport_path": path}

        if not all_hosts:
            print("\nNo hosts discovered. Please provide:")
            print("  - Ansible inventory (ansible.cfg or $ANSIBLE_INVENTORY)")
            print("  - Hosts file (--hosts-file)")
            print("  - SOSreport directory (--sosreport-dir)")
            return self.config

        # 4. Check access to all hosts using thread pool
        print(f"\n=== Checking access to {len(all_hosts)} hosts (multithreaded) ===")

        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            futures = {}
            for hostname, info in all_hosts.items():
                future = executor.submit(
                    self.check_node_access,
                    hostname,
                    info.get("ansible_info"),
                    info.get("sosreport_path"),
                )
                futures[future] = hostname

            for future in as_completed(futures):
                hostname = futures[future]
                try:
                    node = future.result()
                    self.config.nodes[hostname] = asdict(node)
                    status = []
                    if node.ssh_reachable:
                        status.append(f"SSH({node.ssh_user})")
                    if node.ansible_reachable:
                        status.append("Ansible")
                    if node.sosreport_path:
                        status.append("SOSreport")
                    machine_id_short = f" [{node.machine_id[:8]}]" if node.machine_id else ""
                    print(
                        f"  {hostname}: {', '.join(status) if status else 'NO ACCESS'}{machine_id_short} "
                        f"-> {node.preferred_method or 'none'}"
                    )
                except Exception as e:
                    print(f"  {hostname}: Error - {e}")

        # Scan for SOSreports in subdirectories and update nodes without sosreport_path
        local_sosreports = self.scan_sosreports_recursive(str(self.config_dir))
        if local_sosreports:
            updated_count = 0
            for hostname, node_info in self.config.nodes.items():
                if not node_info.get("sosreport_path") and hostname in local_sosreports:
                    node_info["sosreport_path"] = local_sosreports[hostname]
                    # Set preferred_method to sosreport if no other access method
                    if not node_info.get("preferred_method"):
                        node_info["preferred_method"] = "sosreport"
                    updated_count += 1
            if updated_count > 0:
                print(f"\n  [INFO] Found {updated_count} matching SOSreport(s) in subdirectories")

            # Discover cluster nodes from SOSreports and find their sosreports
            discovered_nodes = self._discover_cluster_from_sosreports(local_sosreports)
            if discovered_nodes:
                for hostname, sos_path in discovered_nodes.items():
                    if hostname not in self.config.nodes:
                        node_info = {
                            "hostname": hostname,
                            "sosreport_path": sos_path,
                            "preferred_method": "sosreport",
                            "last_checked": datetime.now().isoformat(),
                        }
                        self.config.nodes[hostname] = node_info
                        print(f"  [CLUSTER] Discovered {hostname} from SOSreport cluster info")

        # Check if multiple clusters were discovered - prompt for selection
        if len(self.config.clusters) > 1:
            # Build clusters dict in format expected by prompt_cluster_selection
            clusters_for_selection = {}
            for cname, cinfo in self.config.clusters.items():
                if cname == "(unknown)":
                    continue
                cluster_nodes_list = cinfo.get("nodes", [])
                # Map nodes to their info
                nodes_dict = {}
                for node in cluster_nodes_list:
                    if node in self.config.nodes:
                        nodes_dict[node] = self.config.nodes[node].get("sosreport_path", "")
                    else:
                        nodes_dict[node] = ""
                if nodes_dict:
                    clusters_for_selection[cname] = {"nodes": nodes_dict}

            if len(clusters_for_selection) > 1:
                print("\n" + "=" * 60)
                print(" Multiple clusters discovered from hosts")
                print("=" * 60)
                # Default to the cluster containing CLI-specified nodes
                default_cluster = None
                if file_hosts:
                    for cname, cinfo in clusters_for_selection.items():
                        if set(file_hosts) & set(cinfo["nodes"].keys()):
                            default_cluster = cname
                            break
                selected_cluster = self.prompt_cluster_selection(
                    clusters_for_selection, default_cluster=default_cluster
                )

                if selected_cluster is None:
                    print("\n[INFO] Cluster selection cancelled.")
                    sys.exit(0)
                elif selected_cluster != "__all__":
                    # Filter to only selected cluster's nodes
                    selected_nodes = self.config.clusters[selected_cluster].get("nodes", [])
                    filtered_nodes = {
                        n: self.config.nodes[n] for n in selected_nodes if n in self.config.nodes
                    }

                    print(f"\n[INFO] Analyzing cluster '{selected_cluster}' only")
                    print(f"       Nodes: {', '.join(sorted(filtered_nodes.keys()))}")

                    # Remove nodes not in selected cluster
                    self.config.nodes = filtered_nodes
                    # Keep only selected cluster
                    self.config.clusters = {
                        selected_cluster: self.config.clusters[selected_cluster]
                    }

        self.config.discovery_complete = True
        self.save_config()

        # Print summary
        self._print_summary()

        return self.config

    def _discover_local_mode(self) -> AccessConfig:
        """
        Discovery routine for local mode - running on the cluster node itself.
        The local node uses direct command execution, other nodes use SSH.
        """
        print("\n[INFO] Local mode: running on cluster node")

        # Clear old nodes for fresh discovery
        self.config.nodes = {}

        # Discover cluster nodes locally
        _cluster_name, cluster_nodes = self.discover_cluster_nodes_local()

        if not cluster_nodes:
            print("[ERROR] Could not discover any cluster nodes")
            return self.config

        # Get local hostname
        local_hostname = self.local_hostname or self.get_local_hostname()

        print(f"\n=== Checking access to {len(cluster_nodes)} cluster node(s) ===")

        # Process each node
        for node_name in cluster_nodes:
            if node_name == local_hostname:
                # Local node - use local execution
                node = NodeAccess(hostname=node_name)
                node.last_checked = datetime.now().isoformat()
                node.preferred_method = "local"
                self.config.nodes[node_name] = asdict(node)
                print(f"  {node_name}: LOCAL (this node)")
            else:
                # Remote node - check SSH access
                node = self.check_node_access(node_name, None, None)
                self.config.nodes[node_name] = asdict(node)
                status = []
                if node.ssh_reachable:
                    status.append(f"SSH({node.ssh_user})")
                machine_id_short = f" [{node.machine_id[:8]}]" if node.machine_id else ""
                print(
                    f"  {node_name}: {', '.join(status) if status else 'NO ACCESS'}{machine_id_short} "
                    f"-> {node.preferred_method or 'none'}"
                )

        self.config.discovery_complete = True
        self.save_config()

        # Print summary
        self._print_summary()

        return self.config

    def _print_summary(self):
        """Print discovery summary."""
        print("\n" + "=" * 60)
        print("Discovery Summary")
        print("=" * 60)

        total = len(self.config.nodes)
        local_count = sum(
            1 for n in self.config.nodes.values() if n.get("preferred_method") == "local"
        )
        ssh_count = sum(1 for n in self.config.nodes.values() if n.get("ssh_reachable"))
        ansible_count = sum(1 for n in self.config.nodes.values() if n.get("ansible_reachable"))
        sos_count = sum(1 for n in self.config.nodes.values() if n.get("sosreport_path"))
        no_access = sum(1 for n in self.config.nodes.values() if not n.get("preferred_method"))

        print(f"Total nodes:      {total}")
        if local_count > 0:
            print(f"Local (this node):{local_count}")
        print(f"SSH accessible:   {ssh_count}")
        print(f"Ansible access:   {ansible_count}")
        print(f"SOSreport avail:  {sos_count}")
        print(f"No access:        {no_access}")
        print(f"\nConfig saved to: {self.config_path}")

        if self.config.ansible_inventory_path:
            print(f"Ansible inventory: {self.config.ansible_inventory_path}")
            print(f"  (source: {self.config.ansible_inventory_source})")


def main():
    parser = argparse.ArgumentParser(
        description="Discover access methods to SAP Pacemaker cluster nodes"
    )
    parser.add_argument(
        "--config-dir",
        "-c",
        default="results",
        help="Directory to store configuration (default: ./results)",
    )
    parser.add_argument("--hosts-file", "-H", help="File containing list of hosts (one per line)")
    parser.add_argument(
        "--sosreport-dir", "-s", help="Directory containing SOSreport archives/directories"
    )
    parser.add_argument(
        "--force", "-f", action="store_true", help="Force rediscovery (ignore existing config)"
    )
    parser.add_argument(
        "--workers", "-w", type=int, default=10, help="Number of parallel workers (default: 10)"
    )
    parser.add_argument(
        "--show-config", "-S", action="store_true", help="Display current configuration and exit"
    )
    parser.add_argument(
        "--delete-config",
        "-D",
        action="store_true",
        help="Delete configuration file to restart investigation",
    )

    args = parser.parse_args()

    config_path = Path(args.config_dir) / AccessDiscovery.CONFIG_FILE

    # Handle show-config action
    if args.show_config:
        show_config(config_path)
        sys.exit(0)

    # Handle delete-config action
    if args.delete_config:
        delete_config(config_path)
        sys.exit(0)

    discovery = AccessDiscovery(
        config_dir=args.config_dir,
        sosreport_dir=args.sosreport_dir,
        hosts_file=args.hosts_file,
        force_rediscover=args.force,
    )
    discovery.MAX_WORKERS = args.workers

    try:
        discovery.discover_all()
        # Show the saved config at the end
        print("\n" + "-" * 60)
        print("Saved Configuration:")
        print("-" * 60)
        show_config(config_path)
    except KeyboardInterrupt:
        print("\nDiscovery interrupted. Saving partial results...")
        discovery.save_config()
        sys.exit(1)


if __name__ == "__main__":
    main()
