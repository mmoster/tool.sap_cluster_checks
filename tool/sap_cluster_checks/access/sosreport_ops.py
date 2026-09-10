"""
SOSreport remote operations for SAP HANA HA cluster nodes.

Provides functions to discover cluster topology from a seed node, check for
existing SOSreports, create new SOSreports with SAP-specific plugins, configure
SAP HA SOSreport extensions, and fetch SOSreport archives via SCP.

Main entry points:
    - fetch_sosreports: Fetch (and optionally create) SOSreports for a cluster
    - create_and_fetch_sosreports: Full workflow from seed-node discovery to download

Helper functions:
    - check_sosreports_on_nodes: Check which nodes already have SOSreports
    - create_sosreports: Create SOSreports on remote cluster nodes
    - check_sos_sap_extensions: Check if SAP HA extensions are configured
    - configure_sos_sap_extensions: Deploy SAP HA SOSreport extensions
    - discover_cluster_from_node: Discover cluster info from a single seed node
"""

import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml


def check_sosreports_on_nodes(nodes: list, ssh_user: str = "root") -> dict:
    """
    Check which nodes have SOSreports available in /var/tmp.

    Args:
        nodes: List of node hostnames to check
        ssh_user: SSH user for connecting to nodes (default: root)

    Returns:
        Dict mapping hostname -> sosreport path (or None if not found)
    """

    def check_node(hostname: str) -> tuple:
        """Check if a node has a sosreport."""
        try:
            find_cmd = [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                "-o",
                "StrictHostKeyChecking=no",
                f"{ssh_user}@{hostname}",
                "ls -t /var/tmp/sosreport-*.tar.xz /var/tmp/sosreport-*.tar.gz 2>/dev/null | head -1",
            ]

            result = subprocess.run(
                find_cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                text=True,
                check=False,
            )

            if result.returncode == 0 and result.stdout.strip():
                return (hostname, result.stdout.strip())
            return (hostname, None)
        except Exception:
            return (hostname, None)

    results = {}
    with ThreadPoolExecutor(max_workers=min(len(nodes), 5)) as executor:
        futures = {executor.submit(check_node, node): node for node in nodes}
        for future in as_completed(futures):
            hostname, path = future.result()
            results[hostname] = path

    return results


def create_sosreports(
    nodes: list, ssh_user: str = "root", sos_options: str = None, cluster_name: str = None
) -> dict:
    """
    Create SOSreports on remote cluster nodes.

    Args:
        nodes: List of node hostnames to create sosreports on
        ssh_user: SSH user for connecting to nodes (default: root)
        sos_options: Additional options for sos report command (default: cluster plugins)
        cluster_name: Optional cluster name for SOSreport label (auto-discovered if None)

    Returns:
        Dict mapping hostname -> (success: bool, message: str)
    """

    # Try to discover cluster name if not provided
    if cluster_name is None and nodes:
        discovery = discover_cluster_from_node(nodes[0], ssh_user)
        if discovery["success"] and discovery["cluster_name"]:
            cluster_name = discovery["cluster_name"]
            print(f"\n  Discovered cluster: {cluster_name}")

    # Default: include cluster-relevant plugins
    # Note: ha_cluster plugin doesn't exist on RHEL 9, using only widely available plugins
    if sos_options is None:
        base_options = "--batch --all-logs -o pacemaker,corosync,sapnw,saphana,systemd,sos_extras"
        if cluster_name:
            # Sanitize cluster name for use as label (remove spaces, special chars)
            safe_label = re.sub(r"[^a-zA-Z0-9_-]", "", cluster_name)
            sos_options = f"{base_options} --label={safe_label}"
        else:
            sos_options = base_options

    print(f"\n{'=' * 60}")
    print(" Creating SOSreports on cluster nodes")
    print(f"{'=' * 60}")
    print(f"  Nodes: {', '.join(nodes)}")
    if cluster_name:
        print(f"  Cluster: {cluster_name}")
    print(f"  Command: sos report {sos_options}")
    print()
    print("  This may take several minutes per node...")
    print()

    def create_on_node(hostname: str) -> tuple:
        """Run sos report on a single node."""
        try:
            # Run sos report remotely
            sos_cmd = [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "ServerAliveInterval=30",
                f"{ssh_user}@{hostname}",
                f"sos report {sos_options}",
            ]

            result = subprocess.run(
                sos_cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=600,  # 10 minutes timeout
                text=True,
                check=False,
            )

            if result.returncode == 0:
                # Extract the generated filename from output
                output = result.stdout
                for line in output.split("\n"):
                    if "/var/tmp/sosreport-" in line and (".tar.xz" in line or ".tar.gz" in line):
                        match = re.search(r"(/var/tmp/sosreport-[^\s]+\.tar\.[xg]z)", line)
                        if match:
                            return (hostname, True, f"Created: {match.group(1)}")
                return (hostname, True, "SOSreport created successfully")
            error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            return (hostname, False, f"Failed: {error_msg[:100]}")

        except subprocess.TimeoutExpired:
            return (hostname, False, "Timeout (exceeded 10 minutes)")
        except Exception as e:
            return (hostname, False, f"Error: {str(e)}")

    results = {}
    # Run sequentially to avoid overloading nodes (sosreport is resource-intensive)
    # But use ThreadPoolExecutor for consistent interface
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(create_on_node, node): node for node in nodes}

        for future in as_completed(futures):
            hostname, success, message = future.result()
            results[hostname] = (success, message)
            status = "\u2713" if success else "\u2717"
            print(f"  [{hostname}] {status} {message}")

    print()
    success_count = sum(1 for s, _msg in results.values() if s)
    print(f"SOSreport creation: {success_count}/{len(nodes)} successful")

    return results


def check_sos_sap_extensions(hostname: str, ssh_user: str = "root") -> dict:
    """
    Check if SAP HANA HA SOSreport extensions are configured on a node.

    Returns:
        Dict with:
        - sos_conf_ok: True if /etc/sos/sos.conf has SAP plugins enabled
        - extras_ok: True if /etc/sos/extras.d/sap_hana_ha exists and has content
        - sos_extras_installed: True if sos-extras package is installed
    """
    result = {
        "reachable": False,
        "sos_conf_ok": False,
        "extras_ok": False,
        "sos_extras_installed": False,
    }

    try:
        # Check sos.conf for SAP plugins
        check_cmd = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "StrictHostKeyChecking=no",
            f"{ssh_user}@{hostname}",
            """
            # Check if sos-extras is installed
            rpm -q sos 2>/dev/null && echo "SOS_INSTALLED=yes" || echo "SOS_INSTALLED=no"

            # Check sos.conf for SAP plugins
            if grep -q 'saphana' /etc/sos/sos.conf 2>/dev/null; then
                echo "SOS_CONF_OK=yes"
            else
                echo "SOS_CONF_OK=no"
            fi

            # Check if extras.d/sap_hana_ha exists and has SAPHanaSR-showAttr
            if [ -f /etc/sos/extras.d/sap_hana_ha ] && grep -q 'SAPHanaSR-showAttr' /etc/sos/extras.d/sap_hana_ha 2>/dev/null; then
                echo "EXTRAS_OK=yes"
            else
                echo "EXTRAS_OK=no"
            fi
            """,
        ]

        proc = subprocess.run(
            check_cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            text=True,
            check=False,
        )

        if proc.returncode == 0:
            result["reachable"] = True
            output = proc.stdout
            result["sos_extras_installed"] = "SOS_INSTALLED=yes" in output
            result["sos_conf_ok"] = "SOS_CONF_OK=yes" in output
            result["extras_ok"] = "EXTRAS_OK=yes" in output
        else:
            result["reachable"] = False

    except subprocess.TimeoutExpired:
        result["reachable"] = False
    except Exception:
        result["reachable"] = False

    return result


def configure_sos_sap_extensions(hostname: str, ssh_user: str = "root") -> tuple:
    """
    Configure SAP HANA HA SOSreport extensions on a node.

    Creates /etc/sos/extras.d/sap_hana_ha with commands for collecting
    HANA cluster state, and updates /etc/sos/sos.conf to enable SAP plugins.

    Returns:
        Tuple (success: bool, message: str)
    """
    sudo_prefix = "sudo " if ssh_user != "root" else ""

    extras_content = """# SAP HANA HA/DR data collection for SOSreports
# Collects System Replication status and cluster state
SAPHanaSR-showAttr
SAPHanaSR-showAttr --format=script
crm_mon -1 -r -n
pcs status --full
pcs resource config
pcs constraint config
cibadmin --query --scope resources
cibadmin --query --scope constraints
cat /hana/shared/*/global/hdb/custom/config/global.ini
cat /usr/sap/*/SYS/global/hdb/custom/config/global.ini
cat /etc/sudoers.d/20-saphana /etc/sudoers.d/*sap* /etc/sudoers.d/*hana*
ls /usr/share/sap-hana-ha/HanaSR.py /usr/share/SAPHanaSR/SAPHanaSR.py
rpm -q sap-hana-ha resource-agents-sap-hana resource-agents-sap-hana-scaleout
cat /etc/redhat-release
"""

    # Commands to deploy configuration
    # Important: We need to properly update existing sections in sos.conf, not add duplicates
    deploy_script = f"""
# Create directories if needed
{sudo_prefix}mkdir -p /etc/sos/extras.d

# Check if sos.conf already has SAP plugins configured
if grep -q 'saphana' /etc/sos/sos.conf 2>/dev/null && grep -q 'sos_extras' /etc/sos/sos.conf 2>/dev/null; then
    echo "SOS_CONF_ALREADY_OK"
else
    # Backup existing sos.conf
    [ -f /etc/sos/sos.conf ] && {sudo_prefix}cp /etc/sos/sos.conf /etc/sos/sos.conf.bak.$(date +%s)

    # We need to update the existing [report] and [plugin_options] sections
    # Create a Python script to properly update the INI file
    {sudo_prefix}python3 << 'PYEOF'
import configparser
import os

conf_path = '/etc/sos/sos.conf'
config = configparser.ConfigParser(allow_no_value=True)

# Preserve case of option names
config.optionxform = str

# Read existing config
if os.path.exists(conf_path):
    config.read(conf_path)

# Ensure sections exist
if 'report' not in config.sections():
    config.add_section('report')
if 'plugin_options' not in config.sections():
    config.add_section('plugin_options')

# Update enable-plugins in [report] section
existing_plugins = config.get('report', 'enable-plugins', fallback='')
needed_plugins = ['saphana', 'sapnw', 'pacemaker', 'corosync', 'sos_extras']
if existing_plugins:
    current = [p.strip() for p in existing_plugins.split(',')]
else:
    current = []
for p in needed_plugins:
    if p not in current:
        current.append(p)
config.set('report', 'enable-plugins', ', '.join(current))

# Remove pacemaker.crm-scrub if present (not supported in all sos versions)
if config.has_option('plugin_options', 'pacemaker.crm-scrub'):
    config.remove_option('plugin_options', 'pacemaker.crm-scrub')

# Write back
with open(conf_path, 'w') as f:
    config.write(f)

print("SOS_CONF_UPDATED")
PYEOF
fi

# Create extras.d/sap_hana_ha
cat << 'EXTRASEOF' | {sudo_prefix}tee /etc/sos/extras.d/sap_hana_ha >/dev/null
{extras_content}
EXTRASEOF

# Verify
if [ -f /etc/sos/extras.d/sap_hana_ha ] && grep -q 'SAPHanaSR-showAttr' /etc/sos/extras.d/sap_hana_ha 2>/dev/null; then
    echo "EXTRAS_DEPLOYED_OK"
else
    echo "EXTRAS_DEPLOY_FAILED"
fi
"""

    try:
        deploy_cmd = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "StrictHostKeyChecking=no",
            f"{ssh_user}@{hostname}",
            deploy_script,
        ]

        proc = subprocess.run(
            deploy_cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            text=True,
            check=False,
        )

        if proc.returncode == 0:
            output = proc.stdout
            if "EXTRAS_DEPLOYED_OK" in output:
                if "SOS_CONF_ALREADY_OK" in output:
                    return (True, "Extensions configured (sos.conf was already OK)")
                return (True, "Extensions configured successfully")
            return (False, "Deployment verification failed")
        return (False, f"SSH command failed: {proc.stderr.strip()[:80]}")

    except subprocess.TimeoutExpired:
        return (False, "Timeout")
    except Exception as e:
        return (False, f"Error: {str(e)}")


def discover_cluster_from_node(seed_node: str, ssh_user: str = "root") -> dict:
    """
    Discover cluster information from a single seed node via SSH.

    Returns:
        Dict with:
        - success: bool
        - cluster_name: str or None
        - cluster_running: bool
        - nodes: list of cluster node hostnames
        - error: str if not successful
    """
    result = {
        "success": False,
        "cluster_name": None,
        "cluster_running": False,
        "nodes": [],
        "error": None,
    }

    sudo_prefix = "sudo " if ssh_user != "root" else ""

    # First check if we can reach the node
    try:
        test_cmd = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "StrictHostKeyChecking=no",
            f"{ssh_user}@{seed_node}",
            "echo ok",
        ]
        proc = subprocess.run(
            test_cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            text=True,
            check=False,
        )
        if proc.returncode != 0 or "ok" not in proc.stdout:
            result["error"] = f"Cannot connect to {seed_node} via SSH"
            return result
    except subprocess.TimeoutExpired:
        result["error"] = f"SSH connection to {seed_node} timed out"
        return result
    except Exception as e:
        result["error"] = f"SSH error: {str(e)}"
        return result

    # Check if cluster is running
    cluster_check_script = f"""
# Check if pacemaker is running
if systemctl is-active pacemaker >/dev/null 2>&1; then
    echo "CLUSTER_RUNNING=yes"
else
    echo "CLUSTER_RUNNING=no"
fi

# Get cluster name (try multiple methods)
CLUSTER_NAME=""
CLUSTER_NAME=$({sudo_prefix}crm_attribute -G -n cluster-name -q 2>/dev/null)
[ -z "$CLUSTER_NAME" ] && CLUSTER_NAME=$({sudo_prefix}pcs property show cluster-name 2>/dev/null | grep cluster-name | awk '{{print $2}}')
[ -z "$CLUSTER_NAME" ] && CLUSTER_NAME=$({sudo_prefix}corosync-cmapctl totem.cluster_name 2>/dev/null | cut -d= -f2 | tr -d ' ')
[ -z "$CLUSTER_NAME" ] && CLUSTER_NAME=$(grep -oP 'cluster_name:\\s*\\K\\S+' /etc/corosync/corosync.conf 2>/dev/null)
echo "CLUSTER_NAME=$CLUSTER_NAME"

# Get cluster nodes (try multiple methods)
NODES=""
NODES=$({sudo_prefix}pcs status nodes 2>/dev/null | grep -oP 'Online:\\s*\\K.*' | tr -d '[]' | tr ' ' '\\n' | grep -v '^$' | sort -u | tr '\\n' ' ')
[ -z "$NODES" ] && NODES=$({sudo_prefix}crm_node -l 2>/dev/null | awk '{{print $2}}' | sort -u | tr '\\n' ' ')
[ -z "$NODES" ] && NODES=$(grep -oP 'ring0_addr:\\s*\\K\\S+' /etc/corosync/corosync.conf 2>/dev/null | tr '\\n' ' ')
[ -z "$NODES" ] && NODES=$(grep -oP 'name:\\s*\\K\\S+' /etc/corosync/corosync.conf 2>/dev/null | grep -v '^$' | tr '\\n' ' ')
echo "CLUSTER_NODES=$NODES"
"""

    try:
        discover_cmd = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "StrictHostKeyChecking=no",
            f"{ssh_user}@{seed_node}",
            cluster_check_script,
        ]

        proc = subprocess.run(
            discover_cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            text=True,
            check=False,
        )

        output = proc.stdout

        # Parse results
        result["cluster_running"] = "CLUSTER_RUNNING=yes" in output

        for line in output.split("\n"):
            if line.startswith("CLUSTER_NAME="):
                name = line.split("=", 1)[1].strip()
                if name:
                    result["cluster_name"] = name
            elif line.startswith("CLUSTER_NODES="):
                nodes_str = line.split("=", 1)[1].strip()
                if nodes_str:
                    result["nodes"] = [n.strip() for n in nodes_str.split() if n.strip()]

        # If no nodes found, at least include the seed node
        if not result["nodes"]:
            result["nodes"] = [seed_node]

        result["success"] = True

    except subprocess.TimeoutExpired:
        result["error"] = "Discovery command timed out"
    except Exception as e:
        result["error"] = f"Discovery error: {str(e)}"

    return result


def create_and_fetch_sosreports(
    seed_node: str,
    output_dir: str = None,
    ssh_user: str = "root",
    configure_extensions: bool = None,
    interactive: bool = True,
) -> list:
    """
    Complete workflow to create SOSreports on a cluster and fetch them locally.

    This function:
    1. Discovers cluster name and all nodes from a seed node
    2. Checks if cluster is running
    3. Checks/configures SAP SOSreport extensions (prompts user if interactive)
    4. Creates SOSreports in parallel with cluster name as label
    5. Fetches SOSreports via SCP

    Args:
        seed_node: A cluster node to start discovery from
        output_dir: Directory to save sosreports (default: ./sosreports)
        ssh_user: SSH user for connecting to nodes (default: root)
        configure_extensions: If True, configure SAP extensions; if None, prompt
        interactive: If True, prompt user for confirmations

    Returns:
        List of downloaded file paths, or empty list on failure
    """
    print(f"\n{'=' * 63}")
    print(" SAP HANA Cluster SOSreport Collection")
    print(f"{'=' * 63}")
    print(f"  Seed node: {seed_node}")
    print(f"  SSH user: {ssh_user}")
    print()

    # Step 1: Discover cluster info
    print("Step 1: Discovering cluster configuration...")
    discovery = discover_cluster_from_node(seed_node, ssh_user)

    if not discovery["success"]:
        print(f"  [ERROR] {discovery['error']}")
        return []

    cluster_name = discovery["cluster_name"] or "unknown"
    cluster_nodes = discovery["nodes"]
    cluster_running = discovery["cluster_running"]

    print(f"  Cluster name: {cluster_name}")
    print(f"  Cluster status: {'Running' if cluster_running else 'Stopped'}")
    print(f"  Nodes: {', '.join(cluster_nodes)}")
    print()

    # Step 2: Check SSH access to all nodes and filter reachable ones
    print("Step 2: Checking SSH access to cluster nodes...")
    reachable_nodes = []
    unreachable_nodes = []

    def check_node_ssh(hostname: str) -> tuple:
        """Check SSH access to a node."""
        try:
            cmd = [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                "-o",
                "StrictHostKeyChecking=no",
                f"{ssh_user}@{hostname}",
                "echo ok",
            ]
            proc = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
                text=True,
                check=False,
            )
            return (hostname, proc.returncode == 0 and "ok" in proc.stdout)
        except Exception:
            return (hostname, False)

    with ThreadPoolExecutor(max_workers=min(len(cluster_nodes), 5)) as executor:
        futures = {executor.submit(check_node_ssh, node): node for node in cluster_nodes}
        for future in as_completed(futures):
            hostname, reachable = future.result()
            if reachable:
                reachable_nodes.append(hostname)
                print(f"  [{hostname}] \u2713 SSH OK")
            else:
                unreachable_nodes.append(hostname)
                print(f"  [{hostname}] \u2717 Unreachable (skipping)")

    if not reachable_nodes:
        print("\n[ERROR] No reachable nodes found. Cannot proceed.")
        return []

    print()

    # Step 3: Check and configure SAP SOSreport extensions
    print("Step 3: Checking SAP SOSreport extensions...")
    nodes_need_config = []
    nodes_have_config = []

    for node in reachable_nodes:
        ext_status = check_sos_sap_extensions(node, ssh_user)
        if ext_status["extras_ok"]:
            nodes_have_config.append(node)
            print(f"  [{node}] \u2713 SAP extensions configured")
        else:
            nodes_need_config.append(node)
            print(f"  [{node}] \u2717 SAP extensions missing")

    if nodes_need_config:
        print()
        do_configure = configure_extensions

        if do_configure is None and interactive and sys.stdin.isatty():
            print("  SAP SOSreport extensions enhance data collection for cluster analysis.")
            print("  They add SAPHanaSR-showAttr and other cluster state commands.")
            print()
            response = (
                input(f"  Configure SAP extensions on {len(nodes_need_config)} node(s)? [Y/n]: ")
                .strip()
                .lower()
            )
            do_configure = response not in ("n", "no")

        if do_configure:
            print()
            print("  Configuring SAP extensions...")
            for node in nodes_need_config:
                success, message = configure_sos_sap_extensions(node, ssh_user)
                status = "\u2713" if success else "\u2717"
                print(f"    [{node}] {status} {message}")

    print()

    # Step 4: Create SOSreports with cluster name label
    print("Step 4: Creating SOSreports on cluster nodes...")
    print(f"  Using cluster name as label: {cluster_name}")
    print(f"  Running on {len(reachable_nodes)} node(s) in parallel: {', '.join(reachable_nodes)}")
    print("  This may take several minutes...")
    print()

    # SOSreport options with cluster name label
    # Note: Using only widely available plugins (ha_cluster doesn't exist on RHEL 9)
    sos_options = f"--batch --all-logs --label={cluster_name} -o pacemaker,corosync,sapnw,saphana,systemd,sos_extras"

    def create_on_node(hostname: str) -> tuple:
        """Run sos report on a single node."""
        try:
            sos_cmd = [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "ServerAliveInterval=30",
                f"{ssh_user}@{hostname}",
                f"sos report {sos_options}",
            ]

            result = subprocess.run(
                sos_cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=600,  # 10 minutes timeout
                text=True,
                check=False,
            )

            if result.returncode == 0:
                # Extract the generated filename from output
                output = result.stdout
                for line in output.split("\n"):
                    if "/var/tmp/sosreport-" in line and (".tar.xz" in line or ".tar.gz" in line):
                        match = re.search(r"(/var/tmp/sosreport-[^\s]+\.tar\.[xg]z)", line)
                        if match:
                            return (hostname, True, match.group(1))
                return (hostname, True, "Created successfully (path unknown)")
            error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            return (hostname, False, f"Failed: {error_msg[:100]}")

        except subprocess.TimeoutExpired:
            return (hostname, False, "Timeout (exceeded 10 minutes)")
        except Exception as e:
            return (hostname, False, f"Error: {str(e)}")

    created_reports = {}
    # Run in parallel with limited workers (sosreport is resource-intensive)
    with ThreadPoolExecutor(max_workers=min(len(reachable_nodes), 3)) as executor:
        futures = {executor.submit(create_on_node, node): node for node in reachable_nodes}

        for future in as_completed(futures):
            hostname, success, result = future.result()
            status = "\u2713" if success else "\u2717"
            print(f"  [{hostname}] {status} {result}")
            if success and result.startswith("/var/tmp/"):
                created_reports[hostname] = result

    print()

    # Step 5: Fetch created SOSreports
    if not created_reports:
        # Fall back to checking for existing reports
        print("Step 5: Checking for existing SOSreports...")
        existing = check_sosreports_on_nodes(reachable_nodes, ssh_user)
        created_reports = {k: v for k, v in existing.items() if v}

        if not created_reports:
            print("  No SOSreports available to download.")
            return []

    # Determine output directory
    if output_dir:
        sos_dir = Path(output_dir)
    else:
        sos_dir = Path.cwd() / "results" / "sosreports"

    sos_dir.mkdir(parents=True, exist_ok=True)

    print(f"Step 5: Fetching SOSreports to {sos_dir}...")
    print()

    downloaded_files = []

    def fetch_from_node(hostname: str, remote_path: str) -> tuple:
        """Download sosreport from a single node."""
        try:
            filename = os.path.basename(remote_path)
            local_path = sos_dir / filename

            # Check if already downloaded
            if local_path.exists():
                return (hostname, str(local_path), "Already exists (skipped)")

            # Download via SCP
            scp_cmd = [
                "scp",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                "-o",
                "StrictHostKeyChecking=no",
                f"{ssh_user}@{hostname}:{remote_path}",
                str(local_path),
            ]

            proc = subprocess.run(
                scp_cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=300,  # 5 minutes for large files
                text=True,
                check=False,
            )

            if proc.returncode == 0:
                size_mb = local_path.stat().st_size / (1024 * 1024)
                return (hostname, str(local_path), f"Downloaded ({size_mb:.1f} MB)")
            return (hostname, None, f"SCP failed: {proc.stderr.strip()[:60]}")

        except subprocess.TimeoutExpired:
            return (hostname, None, "Timeout")
        except Exception as e:
            return (hostname, None, f"Error: {str(e)}")

    with ThreadPoolExecutor(max_workers=min(len(created_reports), 5)) as executor:
        futures = {
            executor.submit(fetch_from_node, node, path): node
            for node, path in created_reports.items()
        }

        for future in as_completed(futures):
            hostname, filepath, message = future.result()
            status = "\u2713" if filepath else "\u2717"
            print(f"  [{hostname}] {status} {message}")
            if filepath:
                downloaded_files.append(filepath)

    print()
    print(f"{'=' * 63}")
    if downloaded_files:
        print(f" Downloaded {len(downloaded_files)} sosreports to: {sos_dir}")
        print()
        print(" To analyze with health check:")
        print(f"   ./sap_cluster_checks.py -s {sos_dir}")
    else:
        print(" No SOSreports were downloaded.")
    print(f"{'=' * 63}")

    return downloaded_files


def fetch_sosreports(  # pylint: disable=unknown-option-value,too-many-positional-arguments
    config_path: Path,
    cluster_name: str = None,
    nodes: list = None,
    output_dir: str = None,
    ssh_user: str = "root",
    auto_create: bool = False,
    interactive: bool = True,
):
    """
    Fetch the latest sosreports from cluster nodes via SCP.

    First checks if SOSreports exist on nodes. If missing, prompts user
    to create them (unless auto_create or non-interactive mode).

    When a single node is specified, discovers and includes all cluster nodes.

    Args:
        config_path: Path to the configuration file
        cluster_name: Name of the cluster to fetch sosreports from
        nodes: List of specific node hostnames (alternative to cluster_name)
        output_dir: Directory to save sosreports (default: ./sosreports)
        ssh_user: SSH user for connecting to nodes (default: root)
        auto_create: If True, automatically create missing sosreports without prompting
        interactive: If True, prompt user for missing sosreports; if False, skip missing

    Returns:
        List of downloaded file paths, or empty list on failure
    """

    # Determine output directory
    if output_dir:
        sos_dir = Path(output_dir)
    else:
        sos_dir = config_path.parent / "sosreports"

    sos_dir.mkdir(parents=True, exist_ok=True)

    # Get list of nodes to fetch from
    target_nodes = []
    discovered_cluster_name = None

    if nodes:
        # If single node specified, try to discover all cluster nodes
        if len(nodes) == 1:
            seed_node = nodes[0]
            print(f"\n{'=' * 60}")
            print(" Discovering cluster from seed node")
            print(f"{'=' * 60}")
            print(f"  Seed node: {seed_node}")

            discovery = discover_cluster_from_node(seed_node, ssh_user)

            if discovery["success"]:
                discovered_cluster_name = discovery["cluster_name"]
                cluster_status = "Running" if discovery["cluster_running"] else "Stopped"
                print(f"  Cluster: {discovered_cluster_name or 'unknown'}")
                print(f"  Status: {cluster_status}")
                print(f"  Nodes discovered: {', '.join(discovery['nodes'])}")

                # Check SSH access to all discovered nodes
                print()
                print("  Checking SSH access to cluster nodes...")
                reachable = []
                unreachable = []

                def check_ssh(hostname):
                    try:
                        cmd = [
                            "ssh",
                            "-o",
                            "BatchMode=yes",
                            "-o",
                            "ConnectTimeout=10",
                            "-o",
                            "StrictHostKeyChecking=no",
                            f"{ssh_user}@{hostname}",
                            "echo ok",
                        ]
                        proc = subprocess.run(
                            cmd,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            timeout=15,
                            text=True,
                            check=False,
                        )
                        return (hostname, proc.returncode == 0 and "ok" in proc.stdout)
                    except Exception:
                        return (hostname, False)

                with ThreadPoolExecutor(max_workers=min(len(discovery["nodes"]), 5)) as executor:
                    futures = {executor.submit(check_ssh, n): n for n in discovery["nodes"]}
                    for future in as_completed(futures):
                        hostname, ok = future.result()
                        if ok:
                            reachable.append(hostname)
                            print(f"    [{hostname}] \u2713 SSH OK")
                        else:
                            unreachable.append(hostname)
                            print(f"    [{hostname}] \u2717 Unreachable (skipping)")

                target_nodes = reachable
                if unreachable:
                    print(f"\n  Note: {len(unreachable)} node(s) unreachable, will be skipped")
            else:
                print(f"  Could not discover cluster: {discovery.get('error', 'unknown error')}")
                print(f"  Using only specified node: {seed_node}")
                target_nodes = nodes
        else:
            # Multiple nodes specified - use them directly
            target_nodes = nodes
    elif cluster_name:
        # Load nodes from cluster config
        if not config_path.exists():
            print(f"[ERROR] Configuration file not found: {config_path}")
            return []

        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        clusters = config.get("clusters", {})
        if cluster_name not in clusters:
            print(f"[ERROR] Cluster '{cluster_name}' not found in configuration.")
            print(f"Available clusters: {', '.join(clusters.keys())}")
            return []

        target_nodes = clusters[cluster_name].get("nodes", [])
    else:
        print("[ERROR] Either cluster_name or nodes must be specified.")
        return []

    if not target_nodes:
        print("[ERROR] No nodes found to fetch sosreports from.")
        return []

    # Step 1: Check which nodes have existing SOSreports
    print(f"\n{'=' * 60}")
    print(" Checking for existing SOSreports")
    print(f"{'=' * 60}")
    print(f"  Nodes: {', '.join(target_nodes)}")
    print()

    existing = check_sosreports_on_nodes(target_nodes, ssh_user)

    nodes_with_sos = [n for n, p in existing.items() if p]
    nodes_without_sos = [n for n, p in existing.items() if not p]

    for node in target_nodes:
        if existing.get(node):
            print(f"  [{node}] \u2713 Found: {os.path.basename(existing[node])}")
        else:
            print(f"  [{node}] \u2717 No SOSreport found")

    print()

    # Step 2: Handle missing SOSreports
    if nodes_without_sos:
        print(
            f"Missing SOSreports on {len(nodes_without_sos)} node(s): {', '.join(nodes_without_sos)}"
        )

        create_missing = False

        if auto_create:
            create_missing = True
        elif interactive and sys.stdin.isatty():
            print()
            response = input("Create SOSreports on these nodes? [y/N]: ").strip().lower()
            create_missing = response in ("y", "yes")

        if create_missing:
            create_sosreports(nodes_without_sos, ssh_user, cluster_name=discovered_cluster_name)

            # Re-check for newly created sosreports
            new_existing = check_sosreports_on_nodes(nodes_without_sos, ssh_user)
            existing.update(new_existing)

            # Update lists
            nodes_with_sos = [n for n, p in existing.items() if p]
            nodes_without_sos = [n for n, p in existing.items() if not p]

    # Step 3: Fetch existing SOSreports
    if not nodes_with_sos:
        print("\nNo SOSreports available to download.")
        return []

    print(f"\n{'=' * 60}")
    print(" Fetching SOSreports from cluster nodes")
    print(f"{'=' * 60}")
    print(f"  Nodes: {', '.join(nodes_with_sos)}")
    print(f"  Output: {sos_dir}")
    print(f"  SSH user: {ssh_user}")
    print()

    downloaded_files = []

    def fetch_from_node(hostname: str, remote_path: str) -> tuple:
        """Download sosreport from a single node using known path."""
        try:
            filename = os.path.basename(remote_path)
            local_path = sos_dir / filename

            # Check if already downloaded
            if local_path.exists():
                return (hostname, str(local_path), "Already exists (skipped)")

            # Download via SCP
            scp_cmd = [
                "scp",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                "-o",
                "StrictHostKeyChecking=no",
                f"{ssh_user}@{hostname}:{remote_path}",
                str(local_path),
            ]

            result = subprocess.run(
                scp_cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=300,  # 5 minutes for large files
                text=True,
                check=False,
            )

            if result.returncode == 0:
                # Get file size
                size_mb = local_path.stat().st_size / (1024 * 1024)
                return (hostname, str(local_path), f"Downloaded ({size_mb:.1f} MB)")
            return (hostname, None, f"SCP failed: {result.stderr.strip()}")

        except subprocess.TimeoutExpired:
            return (hostname, None, "Timeout")
        except Exception as e:
            return (hostname, None, f"Error: {str(e)}")

    # Fetch from nodes with sosreports in parallel (using known paths)
    with ThreadPoolExecutor(max_workers=min(len(nodes_with_sos), 5)) as executor:
        futures = {
            executor.submit(fetch_from_node, node, existing[node]): node for node in nodes_with_sos
        }

        for future in as_completed(futures):
            hostname, filepath, message = future.result()
            if filepath:
                downloaded_files.append(filepath)
                print(f"  [{hostname}] \u2713 {message}")
            else:
                print(f"  [{hostname}] \u2717 {message}")

    print()
    if downloaded_files:
        print(f"Downloaded {len(downloaded_files)} sosreports to: {sos_dir}")
        print("\nTo analyze with health check:")
        print(f"  ./sap_cluster_checks.py -s {sos_dir}")
    else:
        print("No sosreports were downloaded.")

    return downloaded_files
