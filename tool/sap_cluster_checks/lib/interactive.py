"""
Interactive startup and usage scanning functions for SAP Pacemaker Cluster Health Check.

This module contains functions for:
- Interactive startup menus
- Resource scanning (SOSreports, inventory files, etc.)
- Usage help display
"""

import os
import re
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

from .utils import scan_for_resources, extract_sosreports_parallel
from .installation import print_guide


def interactive_startup(config_path: Path) -> tuple:
    """
    Interactive startup when no arguments provided.
    Shows quick guide and asks user to confirm nodes or specify different ones.
    Returns: (nodes_list, should_continue)
    """
    print("""
╔═══════════════════════════════════════════════════════════════╗
║       SAP Pacemaker Cluster Health Check Tool                 ║
║       Red Hat Enterprise Linux (RHEL 8/9/10)                  ║
╠───────────────────────────────────────────────────────────────╣
║  -h help | -i install guide | -G usage guide | --suggest tips ║
╚═══════════════════════════════════════════════════════════════╝

QUICK START
-----------
This tool checks SAP HANA Pacemaker cluster health by:
  1. Discovering cluster nodes (via SSH, Ansible, or SOSreports)
  2. Running health checks on cluster configuration
  3. Validating Pacemaker/Corosync settings
  4. Checking SAP HANA System Replication status
  5. Generating a detailed report

USAGE EXAMPLES
--------------
  ./sap_cluster_checks.py hana01 hana02    Check specific nodes
  ./sap_cluster_checks.py -s /path/sos     Analyze SOSreports
  ./sap_cluster_checks.py -i               Show installation guide
  ./sap_cluster_checks.py -G               Show full usage guide
""")

    # Check for existing configuration
    existing_nodes = []
    clusters_config = {}
    nodes_config = {}
    config = {}
    if config_path.exists() and yaml:
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
            existing_nodes = list(config.get("nodes", {}).keys())
            clusters_config = config.get("clusters", {})
            nodes_config = config.get("nodes", {})
        except Exception:
            pass

    # Helper function to detect cluster name from SOSreport
    def get_cluster_name_from_sosreport(sosreport_path: str) -> str:
        """Extract cluster name from a sosreport's corosync.conf or pcs status output."""
        sos_path = Path(sosreport_path)

        # Try corosync.conf first
        corosync_conf = sos_path / "etc/corosync/corosync.conf"
        if corosync_conf.exists():
            try:
                content = corosync_conf.read_text()
                match = re.search(r"cluster_name:\s*(\S+)", content)
                if match:
                    return match.group(1)
            except Exception:
                pass

        # Try pcs status output
        pcs_status = sos_path / "sos_commands/pacemaker/pcs_status"
        if pcs_status.exists():
            try:
                content = pcs_status.read_text()
                match = re.search(r"Cluster name:\s*(\S+)", content)
                if match:
                    return match.group(1)
            except Exception:
                pass

        return None

    # Try to detect cluster names from SOSreports for nodes in "(unknown)" cluster
    # or nodes that have sosreport_path but aren't in any known cluster
    detected_clusters = {}  # node -> cluster_name
    for node_name in existing_nodes:
        node_info = nodes_config.get(node_name, {})
        sosreport_path = node_info.get("sosreport_path")
        if sosreport_path and Path(sosreport_path).exists():
            cluster_name = get_cluster_name_from_sosreport(sosreport_path)
            if cluster_name:
                detected_clusters[node_name] = cluster_name

    # Group existing nodes by cluster membership
    cluster_node_map = {}  # cluster_name -> list of nodes from existing_nodes
    unassigned_nodes = set(existing_nodes)

    # First, use detected clusters from SOSreports
    for node_name, cluster_name in detected_clusters.items():
        if cluster_name not in cluster_node_map:
            cluster_node_map[cluster_name] = []
        if node_name not in cluster_node_map[cluster_name]:
            cluster_node_map[cluster_name].append(node_name)
        unassigned_nodes.discard(node_name)

    # Then, use clusters from config for remaining nodes
    for cname, cinfo in clusters_config.items():
        if cname == "(unknown)":
            continue  # Skip the unknown cluster from config, we'll handle unassigned nodes later
        cluster_nodes = set(cinfo.get("nodes", []))
        matching_nodes = cluster_nodes & unassigned_nodes
        if matching_nodes:
            if cname not in cluster_node_map:
                cluster_node_map[cname] = []
            cluster_node_map[cname].extend(sorted(matching_nodes))
            unassigned_nodes -= matching_nodes

    # Sort node lists
    for cname in cluster_node_map:
        cluster_node_map[cname] = sorted(cluster_node_map[cname])

    # Add unassigned nodes as "(unknown)" cluster if any
    if unassigned_nodes:
        cluster_node_map["(unknown)"] = sorted(unassigned_nodes)

    print("-" * 63)
    if existing_nodes:
        print("EXISTING CONFIGURATION FOUND")

        # Check if we have multiple clusters or need cluster selection
        if len(cluster_node_map) > 1 or (
            len(cluster_node_map) == 1 and "(unknown)" in cluster_node_map
        ):
            # Multiple clusters or unknown cluster - show selection menu
            print()
            print("  Detected clusters and their nodes:")
            cluster_list = sorted([c for c in cluster_node_map if c != "(unknown)"])
            if "(unknown)" in cluster_node_map:
                cluster_list.append("(unknown)")

            for idx, cname in enumerate(cluster_list, 1):
                nodes_in_cluster = cluster_node_map[cname]
                print(f"    [{idx}] {cname}")
                print(f"        Nodes: {', '.join(nodes_in_cluster)}")
            print()
            print("Options:")
            print("  [1-N]       Select a cluster by number")
            print("  [a]         Continue with all nodes")
            print("  [l]         Run in local mode (on this cluster node)")
            print("  [nodes]     Enter different node names (space-separated)")
            print("  [d]         Delete reports and start fresh")
            print("  [q]         Quit")
            print("-" * 63)

            try:
                response = input("\nYour choice: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n")
                return None, False

            if response == "q":
                print("Exiting.")
                return None, False

            if response == "d":
                if config_path.exists():
                    config_path.unlink()
                    print(f"Deleted: {config_path}")
                print("Configuration deleted. Run again to start fresh.")
                return None, False

            if response == "l":
                return ["local"], True

            if response in ("a", ""):
                return existing_nodes, True

            # Check if user entered a cluster number
            try:
                choice = int(response)
                if 1 <= choice <= len(cluster_list):
                    selected_cluster = cluster_list[choice - 1]
                    selected_nodes = cluster_node_map[selected_cluster]
                    print(f"Selected cluster: {selected_cluster}")
                    print(f"Nodes: {', '.join(selected_nodes)}")
                    return selected_nodes, True
                print(f"Invalid choice. Enter 1-{len(cluster_list)}.")
                return None, False
            except ValueError:
                pass

            # User entered node names
            nodes = response.split()
            if nodes:
                return nodes, True

            return None, False

        # Single known cluster
        cluster_name = list(cluster_node_map.keys())[0] if cluster_node_map else None
        if cluster_name:
            print(f"  Cluster: {cluster_name}")
        print(f"  Nodes:   {', '.join(sorted(existing_nodes))}")
        print()
        print("Options:")
        print("  [Enter]     Continue with these nodes")
        print("  [l]         Run in local mode (on this cluster node)")
        print("  [nodes]     Enter different node names (space-separated)")
        print("  [d]         Delete reports and start fresh")
        print("  [q]         Quit")
    else:
        # No existing configuration — first run, offer usage guide
        while True:
            print("NO EXISTING CONFIGURATION")
            print()
            print("Options:")
            print("  [Enter]     Run in local mode (on this cluster node)  [default]")
            print("  [nodes]     Enter node names to check (space-separated)")
            print("  [g]         Show usage guide (-G)")
            print("  [q]         Quit")
            print("-" * 63)

            try:
                response = input("\nYour choice: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n")
                return None, False

            if response == "g":
                print_guide()
                print()
                print("-" * 63)
                continue

            if response == "q":
                print("Exiting.")
                return None, False

            if response == "":
                return ["local"], True

            # User entered node names
            nodes = response.split()
            if nodes:
                return nodes, True

            return None, False

    # Shared handling for single-cluster case (existing config, single cluster)
    print("-" * 63)

    try:
        response = input("\nYour choice: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n")
        return None, False

    if response == "q":
        print("Exiting.")
        return None, False

    if response == "d":
        if config_path.exists():
            config_path.unlink()
            print(f"Deleted: {config_path}")
        print("Configuration deleted. Run again to start fresh.")
        return None, False

    if response == "l":
        return ["local"], True

    if response == "":
        # Continue with existing nodes
        if existing_nodes:
            return existing_nodes, True
        return ["local"], True

    # User entered node names
    nodes = response.split()
    if nodes:
        return nodes, True

    return None, False


def run_usage_scan(base_dir: str = None, seed_hosts: list = None):
    """
    Run the usage scan mode: find resources, present options, and help user get started.

    Args:
        base_dir: Base directory to scan for resources (default: current directory)
        seed_hosts: Hostnames provided on CLI (used as seed node for SOSreport fetch)
    """
    # Use specified base directory or current directory
    scan_dir = base_dir if base_dir else "."

    # Check if specified directory exists
    if base_dir and not os.path.exists(base_dir):
        print(f"\n  Directory not found: {base_dir}")
        print("  Creating directory...")
        try:
            os.makedirs(base_dir, exist_ok=True)
        except Exception as e:
            print(f"  Error creating directory: {e}")
            return None

    print(f"\n{'=' * 63}")
    print(" Scanning for resources...")
    print(f"{'=' * 63}")
    print(f"  Base directory: {os.path.abspath(scan_dir)}")

    # Scan for resources
    resources = scan_for_resources(scan_dir)

    # Count what we found
    n_compressed = len(resources["sosreports_compressed"])
    n_extracted = len(resources["sosreports_extracted"])
    n_inventory = len(resources["inventory_files"])
    n_hosts = len(resources["hosts_files"])
    n_results = len(resources["former_results"])
    n_config = len(resources["config_files"])
    n_pdf = len(resources["pdf_reports"])

    has_sosreports = n_compressed > 0 or n_extracted > 0
    has_inventory = n_inventory > 0 or n_hosts > 0
    has_former = n_results > 0 or n_config > 0

    # Print summary
    print("\n" + "-" * 63)
    print(" Found Resources")
    print("-" * 63)

    if n_compressed > 0:
        print(f"  SOSreports (compressed):  {n_compressed}")
        for f in resources["sosreports_compressed"][:5]:
            print(f"    - {os.path.basename(f)}")
        if n_compressed > 5:
            print(f"    ... and {n_compressed - 5} more")

    if n_extracted > 0:
        print(f"  SOSreports (extracted):   {n_extracted}")
        for f in resources["sosreports_extracted"][:5]:
            print(f"    - {os.path.basename(f)}")
        if n_extracted > 5:
            print(f"    ... and {n_extracted - 5} more")

    if n_inventory > 0:
        print(f"  Inventory files:          {n_inventory}")
        for f in resources["inventory_files"][:3]:
            print(f"    - {f}")

    if n_hosts > 0:
        print(f"  Hosts files:              {n_hosts}")
        for f in resources["hosts_files"][:3]:
            print(f"    - {f}")

    if n_results > 0:
        print(f"  Former results:           {n_results}")
        for f in sorted(resources["former_results"], reverse=True)[:3]:
            print(f"    - {os.path.basename(f)}")

    if n_pdf > 0:
        print(f"  PDF reports:              {n_pdf}")

    if n_config > 0:
        print(f"  Config files:             {n_config}")

    if not (has_sosreports or has_inventory or has_former):
        print("  (No resources found in this directory)")

    # Present options based on what was found
    print("\n" + "-" * 63)
    print(" Options")
    print("-" * 63)

    options = []

    if has_former:
        options.append(("d", "Delete former results and config, then run health check"))
        options.append(("c", "Continue with existing configuration"))

    if n_compressed > 0:
        options.append(("e", f"Extract {n_compressed} compressed sosreports and analyze"))

    if n_extracted > 0 or n_compressed > 0:
        sos_dir = (
            os.path.dirname(resources["sosreports_extracted"][0])
            if n_extracted > 0
            else os.path.dirname(resources["sosreports_compressed"][0])
        )
        options.append(("s", f"Analyze sosreports in {sos_dir}"))

    if has_inventory:
        inv_file = (
            resources["inventory_files"][0] if n_inventory > 0 else resources["hosts_files"][0]
        )
        options.append(("i", f"Use inventory/hosts file: {inv_file}"))

    # Always offer the option to fetch SOSreports from a cluster
    if seed_hosts:
        options.append(
            ("f", f"Fetch SOSreports from cluster (using {seed_hosts[0]} to discover all nodes)")
        )
    else:
        options.append(("f", "Fetch SOSreports from cluster (enter one node to discover all)"))
    options.append(("n", "Enter hostnames manually"))
    options.append(("l", "Run locally (on this cluster node)"))
    options.append(("h", "Show help and examples"))
    options.append(("q", "Quit"))

    for key, desc in options:
        print(f"  [{key}] {desc}")

    print("-" * 63)

    try:
        choice = input("\n  Your choice: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n  Cancelled.")
        return None

    if choice == "q":
        print("  Exiting.")
        return None

    if choice == "h":
        print_usage_help()
        return None

    if choice == "d":
        # Delete former results and continue
        print("\n  Deleting former results and config...")
        deleted = 0
        for f in resources["former_results"] + resources["config_files"] + resources["pdf_reports"]:
            try:
                os.remove(f)
                print(f"    Deleted: {os.path.basename(f)}")
                deleted += 1
            except Exception as e:
                print(f"    Failed to delete {f}: {e}")
        print(f"  Deleted {deleted} file(s).\n")

        # Continue with health check - determine best action based on available resources
        if n_extracted > 0:
            sos_dir = os.path.dirname(resources["sosreports_extracted"][0])
            print(f"  Continuing with sosreports in {sos_dir}...")
            return {"action": "sosreport", "sosreport_dir": sos_dir}
        if n_compressed > 0:
            # Extract and analyze
            extracted = extract_sosreports_parallel(resources["sosreports_compressed"])
            if extracted:
                sos_dir = os.path.dirname(extracted[0])
                print(f"  Continuing with extracted sosreports in {sos_dir}...")
                return {"action": "sosreport", "sosreport_dir": sos_dir}
        if has_inventory:
            inv_file = (
                resources["inventory_files"][0] if n_inventory > 0 else resources["hosts_files"][0]
            )
            print(f"  Continuing with inventory file {inv_file}...")
            return {"action": "hosts_file", "hosts_file": inv_file}
        # No resources found, ask for hostnames
        try:
            hosts = input("  Enter hostnames (space-separated): ").strip()
            if hosts:
                return {"action": "hosts", "hosts": hosts.split()}
        except (EOFError, KeyboardInterrupt):
            pass
        return None

    if choice == "c":
        # Continue with existing config
        if n_config > 0:
            config_dir = os.path.dirname(resources["config_files"][0])
            return {"action": "continue", "config_dir": config_dir}
        print("  No existing configuration found.")
        return None

    if choice == "e":
        # Extract and analyze sosreports
        extracted = extract_sosreports_parallel(resources["sosreports_compressed"])
        if extracted:
            sos_dir = os.path.dirname(extracted[0])
            print(f"\n  Extracted {len(extracted)} sosreports.")
            print("  Run health check with:")
            print(f"    ./sap_cluster_checks.py -s {sos_dir}")
            return {"action": "sosreport", "sosreport_dir": sos_dir}
        print("  No sosreports extracted.")
        return None

    if choice == "s":
        # Analyze sosreports directly
        if n_extracted > 0:
            sos_dir = os.path.dirname(resources["sosreports_extracted"][0])
        else:
            # Extract first
            extracted = extract_sosreports_parallel(resources["sosreports_compressed"])
            sos_dir = os.path.dirname(extracted[0]) if extracted else None

        if sos_dir:
            return {"action": "sosreport", "sosreport_dir": sos_dir}
        print("  No sosreports found to analyze.")
        return None

    if choice == "i":
        # Use inventory file
        inv_file = (
            resources["inventory_files"][0] if n_inventory > 0 else resources["hosts_files"][0]
        )
        return {"action": "hosts_file", "hosts_file": inv_file}

    if choice == "f":
        # Fetch SOSreports from cluster - use seed_hosts from CLI or prompt
        try:
            if seed_hosts:
                host = seed_hosts[0]
                print(f"  Using {host} to discover all cluster nodes...")
            else:
                host = input(
                    "  Enter one cluster node hostname (all nodes will be discovered): "
                ).strip()
            if host:
                output_dir = scan_dir if scan_dir != "." else None
                return {"action": "fetch_sosreports", "seed_node": host, "output_dir": output_dir}
        except (EOFError, KeyboardInterrupt):
            pass
        return None

    if choice == "n":
        # Manual hostname entry
        try:
            hosts = input("  Enter hostnames (space-separated): ").strip()
            if hosts:
                return {"action": "hosts", "hosts": hosts.split()}
        except (EOFError, KeyboardInterrupt):
            pass
        return None

    if choice == "l":
        return {"action": "local"}

    print(f"  Unknown option: {choice}")
    return None


def print_usage_help():
    """Print usage help with examples."""
    print("""
===============================================================
 SAP HANA Pacemaker Cluster Health Check - Quick Start Guide
===============================================================

BASIC USAGE:
  ./sap_cluster_checks.py                    # Interactive mode
  ./sap_cluster_checks.py <hostname>         # Check cluster via hostname
  ./sap_cluster_checks.py -s <sosreport_dir> # Analyze sosreports offline

SCANNING FOR RESOURCES (-u):
  ./sap_cluster_checks.py -u

  This scans the current directory and subdirectories for:
  - SOSreport archives (.tar.xz, .tar.gz) - extracts in parallel if needed
  - SOSreport directories (already extracted)
  - Ansible inventory files
  - Hosts files (hosts.txt)
  - Former health check results

  Then presents interactive options to:
  - Delete former results and run health check
  - Continue with existing configuration
  - Extract and analyze sosreports
  - Use found inventory/hosts files
  - Enter hostnames manually

COMMON WORKFLOWS:

  1. Analyze SOSreports from a support case:
     # Copy sosreports to a directory
     mkdir sosreports && cd sosreports
     cp /path/to/sosreport-*.tar.xz .

     # Scan and analyze
     ../sap_cluster_checks.py -u
     # Or directly:
     ../sap_cluster_checks.py -s .

  2. Check a live cluster:
     ./sap_cluster_checks.py hana01 hana02
     # Or from a hosts file:
     ./sap_cluster_checks.py -H hosts.txt

  3. Check from a cluster node:
     ./sap_cluster_checks.py --local
     # Or:
     ./sap_cluster_checks.py -l

  4. PDF reports are generated automatically after each health check.

MORE OPTIONS:
  ./sap_cluster_checks.py --help     # Full help
  ./sap_cluster_checks.py --guide    # Detailed guide
  ./sap_cluster_checks.py -L         # List health checks
""")
