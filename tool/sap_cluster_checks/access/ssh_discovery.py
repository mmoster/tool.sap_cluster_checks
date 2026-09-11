"""
SAP Pacemaker Cluster Health Check - SSH/Ansible Discovery

Mixin class providing SSH, Ansible, and live cluster discovery
methods for AccessDiscovery.
"""

import os
import re
import socket
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

import yaml

from .models import NodeAccess


class SSHDiscoveryMixin:
    """Mixin providing SSH/Ansible discovery methods for AccessDiscovery."""

    def discover_ansible_inventory(self) -> Optional[str]:
        """
        Discover Ansible inventory location.
        Priority:
        1. $ANSIBLE_INVENTORY environment variable
        2. ansible.cfg inventory = setting
        3. Default /etc/ansible/hosts
        """
        print("\n=== Discovering Ansible Inventory ===")

        # Check environment variable
        env_inventory = os.environ.get("ANSIBLE_INVENTORY")
        if env_inventory and os.path.exists(env_inventory):
            print(f"Found via $ANSIBLE_INVENTORY: {env_inventory}")
            self.config.ansible_inventory_source = "environment"
            self.config.ansible_inventory_path = env_inventory
            return env_inventory

        # Check ansible.cfg files
        for cfg_path in self.ANSIBLE_CFG_LOCATIONS:
            cfg_path = os.path.expanduser(cfg_path)
            if os.path.exists(cfg_path):
                print(f"Checking {cfg_path}...")
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    # Look for inventory = <path> in [defaults] section
                    match = re.search(r"^\s*inventory\s*=\s*(.+?)\s*$", content, re.MULTILINE)
                    if match:
                        inv_path = os.path.expanduser(match.group(1).strip())
                        if os.path.exists(inv_path):
                            print(f"Found via {cfg_path}: {inv_path}")
                            self.config.ansible_inventory_source = cfg_path
                            self.config.ansible_inventory_path = inv_path
                            return inv_path
                except Exception as e:
                    print(f"  Error reading {cfg_path}: {e}")

        # Check default location
        if os.path.exists(self.DEFAULT_ANSIBLE_INVENTORY):
            print(f"Using default: {self.DEFAULT_ANSIBLE_INVENTORY}")
            self.config.ansible_inventory_source = "default"
            self.config.ansible_inventory_path = self.DEFAULT_ANSIBLE_INVENTORY
            return self.DEFAULT_ANSIBLE_INVENTORY

        print("No Ansible inventory found")
        return None

    def get_ansible_hosts(self) -> Dict[str, Dict[str, Any]]:
        """Get hosts from Ansible inventory using ansible-inventory command."""
        print("\n=== Retrieving Ansible Hosts ===")
        hosts = {}

        try:
            cmd = ["ansible-inventory", "--list", "--yaml"]
            if self.config.ansible_inventory_path:
                cmd.extend(["-i", self.config.ansible_inventory_path])

            result = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=30,
                check=False,
            )

            if result.returncode == 0:
                inventory = yaml.safe_load(result.stdout)
                hosts = self._parse_ansible_inventory(inventory)
                print(f"Found {len(hosts)} hosts in Ansible inventory")
            else:
                print(f"ansible-inventory failed: {result.stderr}")
        except subprocess.TimeoutExpired:
            print("ansible-inventory timed out")
        except FileNotFoundError:
            print("ansible-inventory command not found")
        except Exception as e:
            print(f"Error getting Ansible hosts: {e}")

        return hosts

    def _parse_ansible_inventory(
        self, inventory: dict, hosts: dict = None
    ) -> Dict[str, Dict[str, Any]]:
        """Recursively parse Ansible inventory structure."""
        if hosts is None:
            hosts = {}

        if not isinstance(inventory, dict):
            return hosts

        # Parse 'all' group structure
        if "all" in inventory:
            return self._parse_ansible_inventory(inventory["all"], hosts)

        # Parse hosts at current level
        if "hosts" in inventory and isinstance(inventory["hosts"], dict):
            for hostname, hostvars in inventory["hosts"].items():
                hosts[hostname] = {
                    "ansible_host": (
                        hostvars.get("ansible_host", hostname) if hostvars else hostname
                    ),
                    "ansible_user": hostvars.get("ansible_user") if hostvars else None,
                }

        # Recursively parse children groups
        if "children" in inventory and isinstance(inventory["children"], dict):
            for _group_name, group_data in inventory["children"].items():
                self._parse_ansible_inventory(group_data, hosts)

        return hosts

    def get_hosts_from_file(self) -> List[str]:
        """Read hosts from a simple hosts file (one host per line)."""
        hosts = []
        if self.hosts_file and os.path.exists(self.hosts_file):
            print(f"\n=== Reading hosts from {self.hosts_file} ===")
            with open(self.hosts_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        hosts.append(line.split()[0])  # Take first column
            print(f"Found {len(hosts)} hosts")
            self.config.hosts_file = self.hosts_file
        return hosts

    def discover_cluster_name(self, host: str, user: str = None) -> Optional[str]:
        """Discover cluster name from a node."""
        ssh_user = user or "root"
        # Use sudo for non-root users (cluster commands need root)
        sudo_prefix = "sudo " if ssh_user != "root" else ""

        # Commands to try for getting cluster name
        name_commands = [
            "crm_attribute -G -n cluster-name -q 2>/dev/null",
            "pcs property show cluster-name 2>/dev/null | grep cluster-name | awk '{print $2}'",
            "corosync-cmapctl totem.cluster_name 2>/dev/null | cut -d= -f2 | tr -d ' '",
            "grep -oP 'cluster_name:\\s*\\K\\S+' /etc/corosync/corosync.conf 2>/dev/null",
        ]

        for cmd in name_commands:
            try:
                ssh_cmd = [
                    "ssh",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    f"ConnectTimeout={self.SSH_TIMEOUT}",
                    "-o",
                    "StrictHostKeyChecking=no",
                    f"{ssh_user}@{host}",
                    f"{sudo_prefix}{cmd}",
                ]
                result = subprocess.run(
                    ssh_cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    timeout=self.SSH_TIMEOUT + 2,
                    check=False,
                )

                if result.returncode == 0 and result.stdout.strip():
                    cluster_name = result.stdout.strip()
                    if cluster_name and cluster_name != "(null)":
                        return cluster_name
            except Exception:
                continue

        return None

    def discover_hana_info(self, host: str, user: str = None, cluster_nodes: list = None) -> dict:
        """
        Discover SAP HANA cluster configuration parameters.

        Returns Ansible-compatible parameters for sap_hana_ha_pacemaker role:
        - Core: sid, instance_number
        - Nodes: node1_fqdn, node1_ip, node2_fqdn, node2_ip
        - VIP: virtual_ip, secondary_vip
        - Cluster: cluster_name, secondary_read
        - Resources: resource_name, topology_resource, vip_resource
        - STONITH: stonith_device, stonith_type
        - Replication: replication_mode, operation_mode, sites
        """
        ssh_user = user or "root"
        sudo_prefix = "sudo " if ssh_user != "root" else ""
        hana_info = {}

        def run_ssh_cmd(cmd: str, target_host: str = None) -> str:
            """Helper to run SSH command and return output."""
            target = target_host or host
            try:
                ssh_cmd = [
                    "ssh",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    f"ConnectTimeout={self.SSH_TIMEOUT}",
                    "-o",
                    "StrictHostKeyChecking=no",
                    f"{ssh_user}@{target}",
                    f"{sudo_prefix}{cmd}",
                ]
                result = subprocess.run(
                    ssh_cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    timeout=self.SSH_TIMEOUT + 2,
                    check=False,
                )
                if result.returncode == 0:
                    return result.stdout.strip()
            except Exception:
                pass
            return ""

        # === Core SAP HANA Parameters + Virtual IP Configuration ===
        # Discover resources by agent type (not by name pattern) using pcs resource config.
        # This handles all naming conventions: default (SAPHana_SID_NN),
        # SAP Ansible role (rsc_SAPHana_*, rsc_SAPHanaCon_*), and custom names.
        # TODO: SLES environments use 'crm' instead of 'pcs'. The crm equivalent
        # would be 'crm configure show' which uses a different output format.
        # Updates required to support SLES/crmsh resource detection by agent type.
        pcs_config_output = run_ssh_cmd("pcs resource config 2>/dev/null")

        if pcs_config_output:
            resource_re = re.compile(
                r"\s*Resource:\s*(\S+)\s*\(class=(\S+)\s+(?:provider=(\S+)\s+)?type=(\S+)\)"
            )
            current_res_name = None
            current_res_type = None
            vip_resources = []  # List of {"name": ..., "ip": ...} dicts

            for line in pcs_config_output.split("\n"):
                stripped = line.strip()

                # Match resource definition lines
                res_match = resource_re.match(line)
                if res_match:
                    current_res_name = res_match.group(1)
                    current_res_type = res_match.group(4)

                    # SAPHana or SAPHanaController resource
                    if "SAPHanaController" in current_res_type or (
                        "SAPHana" in current_res_type
                        and "Controller" not in current_res_type
                        and "Topology" not in current_res_type
                        and "Filesystem" not in current_res_type
                    ):
                        hana_info["resource_name"] = current_res_name
                        if "SAPHanaController" in current_res_type:
                            hana_info["resource_type"] = "SAPHanaController"
                        else:
                            hana_info["resource_type"] = "SAPHana"

                    # SAPHanaTopology resource
                    elif "SAPHanaTopology" in current_res_type:
                        hana_info["topology_resource"] = current_res_name

                    # IPaddr2 or IPaddr resource (VIP candidate)
                    elif "IPaddr2" in current_res_type or "IPaddr" in current_res_type:
                        # IP will be extracted from attributes below
                        vip_resources.append({"name": current_res_name, "ip": None})

                    continue

                # Parse attributes for the current resource
                if current_res_type and "=" in stripped:
                    attr_match = re.match(r"(\S+)=(.+)", stripped)
                    if attr_match:
                        key = attr_match.group(1)
                        value = attr_match.group(2).strip()

                        # Extract SID and InstanceNumber from HANA resources
                        if "SAPHana" in (current_res_type or ""):
                            if key == "SID" and "sid" not in hana_info:
                                hana_info["sid"] = value
                            elif key == "InstanceNumber" and "instance_number" not in hana_info:
                                hana_info["instance_number"] = value

                        # Extract IP from VIP resources
                        if (
                            key == "ip"
                            and vip_resources
                            and vip_resources[-1]["ip"] is None
                        ):
                            vip_resources[-1]["ip"] = value

            # Assign VIP info from collected resources
            if vip_resources:
                first_vip = vip_resources[0]
                if first_vip["ip"]:
                    hana_info["virtual_ip"] = first_vip["ip"]
                hana_info["vip_resource"] = first_vip["name"]
                if len(vip_resources) > 1:
                    second_vip = vip_resources[1]
                    if second_vip["ip"]:
                        hana_info["secondary_vip"] = second_vip["ip"]
                    hana_info["secondary_vip_resource"] = second_vip["name"]

        # === Cluster Node Information ===
        nodes = cluster_nodes or []
        if len(nodes) >= 2:
            # Get FQDN for node1
            node1_fqdn = run_ssh_cmd("hostname -f 2>/dev/null || hostname", nodes[0])
            if node1_fqdn:
                hana_info["node1_fqdn"] = node1_fqdn
            hana_info["node1_hostname"] = nodes[0]

            # Get IP for node1
            node1_ip = run_ssh_cmd("hostname -i 2>/dev/null | awk '{print $1}'", nodes[0])
            if node1_ip:
                hana_info["node1_ip"] = node1_ip

            # Get FQDN for node2
            node2_fqdn = run_ssh_cmd("hostname -f 2>/dev/null || hostname", nodes[1])
            if node2_fqdn:
                hana_info["node2_fqdn"] = node2_fqdn
            hana_info["node2_hostname"] = nodes[1]

            # Get IP for node2
            node2_ip = run_ssh_cmd("hostname -i 2>/dev/null | awk '{print $1}'", nodes[1])
            if node2_ip:
                hana_info["node2_ip"] = node2_ip

        # Check if secondary read is enabled (look for second VIP or AUTOMATED_REGISTER)
        auto_reg = run_ssh_cmd(
            "pcs resource config 2>/dev/null | grep -i 'AUTOMATED_REGISTER' | grep -oE 'true|false' | head -1"
        )
        if auto_reg:
            hana_info["automated_register"] = auto_reg.lower() == "true"

        # Check for secondary read (multiple VIPs or specific config)
        hana_info["secondary_read"] = "secondary_vip" in hana_info

        # === STONITH/Fencing Configuration ===
        # Get STONITH device name and type
        stonith_name = run_ssh_cmd(
            "pcs stonith status 2>/dev/null | grep -oE '^[[:space:]]*\\*[[:space:]]+[A-Za-z0-9_-]+' | awk '{print $2}' | head -1"
        )
        if stonith_name:
            hana_info["stonith_device"] = stonith_name

        # Get STONITH device type (agent)
        stonith_type = run_ssh_cmd(
            f"pcs stonith config {stonith_name} 2>/dev/null | grep -oE 'stonith:[a-z_]+' | head -1"
            if stonith_name
            else "echo ''"
        )
        if stonith_type:
            hana_info["stonith_type"] = stonith_type.replace("stonith:", "")

        # Get fence device parameters (for VMware, Azure, etc.)
        if stonith_name:
            fence_params = run_ssh_cmd(
                f"pcs stonith config {stonith_name} 2>/dev/null | grep -E 'ipaddr|login|passwd|ssl|pcmk_host' | head -5"
            )
            if fence_params:
                params = {}
                for line in fence_params.split("\n"):
                    if "=" in line or ":" in line:
                        # Parse key=value or key: value
                        parts = re.split(r"[=:]", line.strip(), 1)
                        if len(parts) == 2:
                            key = parts[0].strip()
                            val = parts[1].strip()
                            # Don't store passwords
                            if "pass" not in key.lower():
                                params[key] = val
                if params:
                    hana_info["stonith_params"] = params

        # === Cluster Properties ===
        # Get resource-stickiness
        stickiness = run_ssh_cmd(
            "pcs property show 2>/dev/null | grep -i 'resource-stickiness' | grep -oE '[0-9]+' | head -1"
        )
        if stickiness:
            hana_info["resource_stickiness"] = int(stickiness)

        # Get migration-threshold
        migration = run_ssh_cmd(
            "pcs resource config 2>/dev/null | grep -i 'migration-threshold' | grep -oE '[0-9]+' | head -1"
        )
        if migration:
            hana_info["migration_threshold"] = int(migration)

        # === SAP HANA System Replication ===
        # Get replication mode from SAPHanaSR
        repl_mode = run_ssh_cmd(
            "SAPHanaSR-showAttr 2>/dev/null | grep -oE 'sync|syncmem|async' | head -1"
        )
        if repl_mode:
            hana_info["replication_mode"] = repl_mode

        # Get operation mode
        op_mode = run_ssh_cmd(
            "SAPHanaSR-showAttr 2>/dev/null | grep -oE 'logreplay|delta_datashipping' | head -1"
        )
        if op_mode:
            hana_info["operation_mode"] = op_mode

        # Get site names from SAPHanaSR or crm_attribute
        sites_output = run_ssh_cmd(
            "SAPHanaSR-showAttr 2>/dev/null | awk '/^Host/ {next} /^-/ {next} {print $4}' | sort -u | head -2"
        )
        if not sites_output:
            # Try alternative: get from pcs resource config
            sid = hana_info.get("sid", "")
            if sid:
                sites_output = run_ssh_cmd(
                    "pcs resource config 2>/dev/null | grep -oE 'PREFER_SITE_TAKEOVER|site=[A-Za-z0-9]+' | grep -oE '[A-Z][A-Z0-9]+' | sort -u | head -2"
                )
        if sites_output:
            # Filter out non-site values and extract clean site names
            sites = []
            for s in sites_output.split("\n"):
                s = s.strip()
                # Extract just the site name (e.g., DC1, DC2, SITE1, etc.)
                if s and s not in ["", "-", "true", "false", "PREFER", "SITE", "TAKEOVER"]:
                    # If it contains 'value=', extract just the value
                    if "value=" in s:
                        s = s.split("value=")[-1].strip()
                    # Filter out numeric site IDs (e.g. "100") — keep names like "DC1"
                    if s and len(s) <= 20 and not s.isdigit():
                        sites.append(s)
            sites = list(dict.fromkeys(sites))  # Remove duplicates while preserving order
            if sites:
                hana_info["sites"] = sites
                if len(sites) >= 1:
                    hana_info["site1_name"] = sites[0]
                if len(sites) >= 2:
                    hana_info["site2_name"] = sites[1]

        # Get PREFER_SITE_TAKEOVER
        prefer_takeover = run_ssh_cmd(
            "pcs resource config 2>/dev/null | grep -i 'PREFER_SITE_TAKEOVER' | grep -oE 'true|false' | head -1"
        )
        if prefer_takeover:
            hana_info["prefer_site_takeover"] = prefer_takeover.lower() == "true"

        # Get DUPLICATE_PRIMARY_TIMEOUT
        dup_timeout = run_ssh_cmd(
            "pcs resource config 2>/dev/null | grep -i 'DUPLICATE_PRIMARY_TIMEOUT' | grep -oE '[0-9]+' | head -1"
        )
        if dup_timeout:
            hana_info["duplicate_primary_timeout"] = int(dup_timeout)

        return hana_info

    def get_local_hostname(self) -> str:
        """Get the local hostname (short form)."""
        try:
            result = subprocess.run(
                ["hostname", "-s"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass

        # Fallback to socket
        return socket.gethostname().split(".")[0]

    def check_cluster_services_running(self, host: str = None, user: str = None) -> tuple:
        """
        Check if cluster services (pacemaker/corosync) are running.
        Returns tuple: (pacemaker_running, corosync_running, service_status_message)
        """
        if host:
            # Remote check via SSH
            ssh_user = user or "root"
            sudo_prefix = "sudo " if ssh_user != "root" else ""
            cmd = f"{sudo_prefix}systemctl is-active pacemaker corosync 2>/dev/null"
            try:
                ssh_cmd = [
                    "ssh",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    f"ConnectTimeout={self.SSH_TIMEOUT}",
                    "-o",
                    "StrictHostKeyChecking=no",
                    f"{ssh_user}@{host}",
                    cmd,
                ]
                result = subprocess.run(
                    ssh_cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    timeout=self.SSH_TIMEOUT + 2,
                    check=False,
                )
                lines = result.stdout.strip().split("\n")
                pacemaker_active = len(lines) > 0 and lines[0].strip() == "active"
                corosync_active = len(lines) > 1 and lines[1].strip() == "active"
            except Exception:
                return (False, False, "Could not check service status")
        else:
            # Local check
            try:
                result = subprocess.run(
                    "systemctl is-active pacemaker corosync 2>/dev/null",
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    timeout=5,
                    check=False,
                )
                lines = result.stdout.strip().split("\n")
                pacemaker_active = len(lines) > 0 and lines[0].strip() == "active"
                corosync_active = len(lines) > 1 and lines[1].strip() == "active"
            except Exception:
                return (False, False, "Could not check service status")

        if pacemaker_active and corosync_active:
            return (True, True, "Cluster services running")
        if not pacemaker_active and not corosync_active:
            return (False, False, "Cluster is NOT running (pacemaker and corosync are stopped)")
        if not pacemaker_active:
            return (False, corosync_active, "Pacemaker is NOT running")
        return (pacemaker_active, False, "Corosync is NOT running")

    def get_nodes_from_corosync_conf(self, host: str = None, user: str = None) -> List[str]:
        """
        Get cluster nodes from /etc/corosync/corosync.conf (static config).
        This works even when cluster services are not running.
        """
        nodes = []
        if host:
            # Remote read via SSH
            ssh_user = user or "root"
            sudo_prefix = "sudo " if ssh_user != "root" else ""
            cmd = f"{sudo_prefix}cat /etc/corosync/corosync.conf 2>/dev/null"
            try:
                ssh_cmd = [
                    "ssh",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    f"ConnectTimeout={self.SSH_TIMEOUT}",
                    "-o",
                    "StrictHostKeyChecking=no",
                    f"{ssh_user}@{host}",
                    cmd,
                ]
                result = subprocess.run(
                    ssh_cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    timeout=self.SSH_TIMEOUT + 2,
                    check=False,
                )
                if result.returncode == 0:
                    content = result.stdout
                else:
                    return nodes
            except Exception:
                return nodes
        else:
            # Local read
            try:
                corosync_conf = Path("/etc/corosync/corosync.conf")
                if corosync_conf.exists():
                    content = corosync_conf.read_text(encoding="utf-8")
                else:
                    return nodes
            except Exception:
                return nodes

        # Parse corosync.conf for node names
        # Look for ring0_addr or name in nodelist section
        name_matches = re.findall(r"^\s*name:\s*(\S+)", content, re.MULTILINE)
        if name_matches:
            nodes = name_matches
        else:
            ring_matches = re.findall(r"ring0_addr:\s*(\S+)", content)
            if ring_matches:
                nodes = ring_matches

        return nodes

    def discover_cluster_nodes_local(self) -> tuple:
        """
        Discover cluster members by running commands locally.
        Returns tuple: (cluster_name, list of cluster node hostnames)
        """
        cluster_nodes = []
        cluster_name = None
        cluster_running = True

        print("\n=== Discovering Cluster (local mode) ===")

        # Get local hostname
        self.local_hostname = self.get_local_hostname()
        print(f"  Local hostname: {self.local_hostname}")

        # Check if cluster services are running
        pacemaker_up, corosync_up, status_msg = self.check_cluster_services_running()
        if not pacemaker_up or not corosync_up:
            cluster_running = False
            print(f"\n  \u26a0\ufe0f  WARNING: {status_msg}")
            print("     Cluster commands will fail - falling back to static configuration")
            print("     To start the cluster: pcs cluster start --all\n")

        # Get cluster name locally
        name_commands = [
            "crm_attribute -G -n cluster-name -q 2>/dev/null",
            "pcs property show cluster-name 2>/dev/null | grep cluster-name | awk '{print $2}'",
            "corosync-cmapctl totem.cluster_name 2>/dev/null | cut -d= -f2 | tr -d ' '",
            "grep -oP 'cluster_name:\\s*\\K\\S+' /etc/corosync/corosync.conf 2>/dev/null",
        ]

        for cmd in name_commands:
            try:
                result = subprocess.run(
                    cmd,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    timeout=self.SSH_TIMEOUT,
                    check=False,
                )
                if result.returncode == 0 and result.stdout.strip():
                    name = result.stdout.strip()
                    if name and name != "(null)":
                        cluster_name = name
                        print(f"  Cluster name: {cluster_name}")
                        break
            except Exception:
                continue

        # Discover cluster nodes locally
        discovery_commands = [
            # Pacemaker commands (work on RHEL)
            "crm_node -l | awk '{print $2}'",
            "pcs status nodes | grep -E 'Online|Standby|Offline' | tr ' ' '\\n' | grep -v -E '^$|Online|Standby|Offline|:'",
            "corosync-cmapctl -b nodelist.node | grep 'ring0_addr' | cut -d= -f2 | tr -d ' '",
        ]

        for cmd in discovery_commands:
            try:
                result = subprocess.run(
                    cmd,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    timeout=self.SSH_TIMEOUT,
                    check=False,
                )
                if result.returncode == 0 and result.stdout.strip():
                    nodes = [n.strip() for n in result.stdout.strip().split("\n") if n.strip()]
                    if nodes:
                        cluster_nodes = nodes
                        if self.debug:
                            print(f"  [DEBUG] Found cluster nodes via: {cmd[:40]}...")
                        print(
                            f"  Found {len(cluster_nodes)} cluster node(s): {', '.join(cluster_nodes)}"
                        )
                        break
            except Exception as e:
                if self.debug:
                    print(f"  [DEBUG] Command failed: {e}")
                continue

        if not cluster_nodes:
            # Try static fallback from corosync.conf
            static_nodes = self.get_nodes_from_corosync_conf()
            if static_nodes:
                cluster_nodes = static_nodes
                if not cluster_running:
                    print(
                        f"  Found {len(cluster_nodes)} node(s) from corosync.conf: {', '.join(cluster_nodes)}"
                    )
                else:
                    print(
                        f"  Found {len(cluster_nodes)} cluster node(s) (static): {', '.join(cluster_nodes)}"
                    )
            else:
                if not cluster_running:
                    print(
                        "  Could not discover cluster nodes (cluster not running, no corosync.conf)"
                    )
                else:
                    print("  Could not discover cluster nodes locally")
                print(f"  Using {self.local_hostname} as only node")
                cluster_nodes = [self.local_hostname]

        # Extract cluster configuration
        cluster_config = {}
        if not cluster_running:
            # Stopped cluster: extract from local cib.xml (same logic as SOSreport)
            print("  Extracting configuration from cib.xml (cluster stopped)...")
            cluster_config = self.extract_cluster_config_from_cib(None)
            if cluster_config:
                sid = cluster_config.get("sid", "")
                vip = cluster_config.get("virtual_ip", "")
                if sid:
                    print(
                        f"  SAP HANA SID: {sid}, Instance: {cluster_config.get('instance_number', 'N/A')}"
                    )
                if vip:
                    print(f"  Virtual IP: {vip}")
                if cluster_config.get("stonith_device"):
                    print(f"  STONITH Device: {cluster_config.get('stonith_device')}")

        # Store cluster info
        if cluster_name:
            cluster_data = {
                "nodes": cluster_nodes,
                "cluster_running": cluster_running,
                "discovered_from": self.local_hostname,
                "discovered_at": datetime.now().isoformat(),
            }
            # Add config from cib.xml if available
            if cluster_config:
                cluster_data.update(cluster_config)
            self.config.clusters[cluster_name] = cluster_data

        return cluster_name, cluster_nodes

    def discover_cluster_nodes(self, seed_host: str, user: str = None) -> tuple:
        """
        Discover cluster members by connecting to a seed node and querying the cluster.
        Tries multiple methods: crm_node, pcs status, corosync-cmapctl.
        Returns tuple: (cluster_name, list of cluster node hostnames)
        """
        ssh_user = user or "root"
        # Use sudo for non-root users (cluster commands need root)
        sudo_prefix = "sudo " if ssh_user != "root" else ""
        cluster_nodes = []
        cluster_name = None
        cluster_running = True

        print(f"\n=== Discovering Cluster from {seed_host} ===")

        # Check if cluster services are running on the seed host
        pacemaker_up, corosync_up, status_msg = self.check_cluster_services_running(
            seed_host, ssh_user
        )
        if not pacemaker_up or not corosync_up:
            cluster_running = False
            print(f"\n  \u26a0\ufe0f  WARNING: {status_msg}")
            print("     Cluster commands will fail - falling back to static configuration")
            print("     To start the cluster: pcs cluster start --all\n")

        # First get cluster name
        cluster_name = self.discover_cluster_name(seed_host, ssh_user)
        if cluster_name:
            print(f"  Cluster name: {cluster_name}")
        else:
            if self.debug:
                print("  [DEBUG] Could not determine cluster name")

        # Commands to try for discovering cluster nodes (RHEL)
        discovery_commands = [
            # crm_node (Pacemaker command)
            "crm_node -l | awk '{print $2}'",
            # pcs status (RHEL primary method)
            "pcs status nodes | grep -E 'Online|Standby|Offline' | tr ' ' '\\n' | grep -v -E '^$|Online|Standby|Offline|:'",
            # corosync-cmapctl
            "corosync-cmapctl -b nodelist.node | grep 'ring0_addr' | cut -d= -f2 | tr -d ' '",
        ]

        for cmd in discovery_commands:
            try:
                ssh_cmd = [
                    "ssh",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    f"ConnectTimeout={self.SSH_TIMEOUT}",
                    "-o",
                    "StrictHostKeyChecking=no",
                    f"{ssh_user}@{seed_host}",
                    f"{sudo_prefix}{cmd}",
                ]
                result = subprocess.run(
                    ssh_cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    timeout=self.SSH_TIMEOUT + 2,
                    check=False,
                )

                if result.returncode == 0 and result.stdout.strip():
                    nodes = [n.strip() for n in result.stdout.strip().split("\n") if n.strip()]
                    if nodes:
                        cluster_nodes = nodes
                        if self.debug:
                            print(f"  [DEBUG] Found cluster nodes via: {cmd[:40]}...")
                        print(
                            f"  Found {len(cluster_nodes)} cluster node(s): {', '.join(cluster_nodes)}"
                        )
                        break
            except subprocess.TimeoutExpired:
                continue
            except Exception as e:
                if self.debug:
                    print(f"  [DEBUG] Command failed: {e}")
                continue

        if not cluster_nodes:
            # Try static fallback from corosync.conf on the remote host
            static_nodes = self.get_nodes_from_corosync_conf(seed_host, ssh_user)
            if static_nodes:
                cluster_nodes = static_nodes
                if not cluster_running:
                    print(
                        f"  Found {len(cluster_nodes)} node(s) from corosync.conf: {', '.join(cluster_nodes)}"
                    )
                else:
                    print(
                        f"  Found {len(cluster_nodes)} cluster node(s) (static): {', '.join(cluster_nodes)}"
                    )
            else:
                if not cluster_running:
                    print(
                        "  Could not discover cluster nodes (cluster not running, no corosync.conf)"
                    )
                else:
                    print(f"  Could not discover cluster nodes from {seed_host}")
                print(f"  Using {seed_host} as only node")
                cluster_nodes = [seed_host]

        # Discover SAP HANA info
        hana_info = {}
        if cluster_running:
            # Running cluster: use pcs commands via SSH
            hana_info = self.discover_hana_info(seed_host, ssh_user, cluster_nodes)
        else:
            # Stopped cluster: extract from cib.xml (same logic as SOSreport)
            # First try to copy cib.xml from remote and parse locally
            print("  Extracting configuration from cib.xml (cluster stopped)...")
            try:
                import tempfile

                with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
                    tmp_path = tmp.name

                # Copy cib.xml from remote node
                scp_cmd = [
                    "scp",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=10",
                    "-o",
                    "StrictHostKeyChecking=no",
                    f"{ssh_user}@{seed_host}:/var/lib/pacemaker/cib/cib.xml",
                    tmp_path,
                ]
                result = subprocess.run(
                    scp_cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=30,
                    check=False,
                )
                if result.returncode == 0:
                    # Parse the cib.xml
                    from ..lib.cib_parser import CIBParser

                    parser = CIBParser.from_file(tmp_path)
                    if parser and parser.is_available():
                        cib_config = parser.get_resource_config()
                        if cib_config.get("success"):
                            hana_cfg = cib_config.get("sap_hana", {})
                            if hana_cfg:
                                hana_info["sid"] = hana_cfg.get("sid")
                                hana_info["instance_number"] = hana_cfg.get("instance_number")
                                hana_info["resource_name"] = hana_cfg.get("resource_name")
                                hana_info["resource_type"] = hana_cfg.get("resource_type")
                                attrs = hana_cfg.get("attributes", {})
                                if attrs.get("AUTOMATED_REGISTER"):
                                    hana_info["automated_register"] = (
                                        attrs["AUTOMATED_REGISTER"].lower() == "true"
                                    )
                                if attrs.get("PREFER_SITE_TAKEOVER"):
                                    hana_info["prefer_site_takeover"] = (
                                        attrs["PREFER_SITE_TAKEOVER"].lower() == "true"
                                    )
                                if attrs.get("DUPLICATE_PRIMARY_TIMEOUT"):
                                    try:
                                        hana_info["duplicate_primary_timeout"] = int(
                                            attrs["DUPLICATE_PRIMARY_TIMEOUT"]
                                        )
                                    except ValueError:
                                        pass

                        # Get STONITH
                        stonith_config = parser.get_stonith()
                        if stonith_config.get("devices"):
                            hana_info["stonith_device"] = stonith_config["devices"][0]

                        # Get VIPs from resource config
                        if cib_config.get("vips"):
                            vips = cib_config["vips"]
                            if vips:
                                hana_info["virtual_ip"] = vips[0].get("ip")
                                hana_info["vip_resource"] = vips[0].get("name")
                                if len(vips) > 1:
                                    hana_info["secondary_vip"] = vips[1].get("ip")

                # Clean up temp file
                os.unlink(tmp_path)
            except Exception as e:
                if self.debug:
                    print(f"  Warning: Could not extract cib.xml config: {e}")
                # Fall back to discover_hana_info which may get partial info
                hana_info = self.discover_hana_info(seed_host, ssh_user, cluster_nodes)

        if hana_info:
            sid = hana_info.get("sid", "")
            inst = hana_info.get("instance_number", "")
            vip = hana_info.get("virtual_ip", "")
            if sid:
                print(f"  SAP HANA SID: {sid}, Instance: {inst}")
            if vip:
                print(f"  Virtual IP: {vip}")

        # Store cluster info
        if cluster_name:
            cluster_data = {
                "nodes": cluster_nodes,
                "cluster_running": cluster_running,
                "discovered_from": seed_host,
                "discovered_at": datetime.now().isoformat(),
            }
            # Add HANA info if discovered
            if hana_info:
                cluster_data.update(hana_info)
            self.config.clusters[cluster_name] = cluster_data

        return cluster_name, cluster_nodes

    def _is_port_open(self, hostname: str, port: int = 22, timeout: float = 2) -> bool:
        """Fast TCP port check. Returns True if port is open, False otherwise."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                return sock.connect_ex((hostname, port)) == 0
        except (socket.gaierror, OSError):
            return False

    def check_ssh_access(self, hostname: str, user: str = None) -> tuple:
        """Check SSH access to a host. Returns (reachable, user)."""
        # Fast pre-check: verify SSH port is open before attempting login.
        # If port check fails, still try SSH — the host may be reachable via
        # ~/.ssh/config (ProxyJump, tunnel, HostName alias) which raw sockets
        # don't see.
        port_open = self._is_port_open(hostname)
        if not port_open:
            if self.debug:
                print(
                    f"    [DEBUG] {hostname}: port 22 not directly open, trying SSH (may use ssh_config proxy)"
                )

        users_to_try = [user] if user else ["root", os.environ.get("USER", "root")]

        for try_user in users_to_try:
            if try_user is None:
                continue
            try:
                cmd = [
                    "ssh",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    f"ConnectTimeout={self.SSH_TIMEOUT}",
                    "-o",
                    "StrictHostKeyChecking=no",
                    f"{try_user}@{hostname}",
                    "echo ok",
                ]
                result = subprocess.run(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    timeout=self.SSH_TIMEOUT + 2,
                    check=False,
                )
                if result.returncode == 0 and "ok" in result.stdout:
                    return True, try_user
                if self.debug:
                    print(
                        f"    [DEBUG] SSH {try_user}@{hostname} failed: {result.stderr.strip()[:60]}"
                    )
            except subprocess.TimeoutExpired:
                if self.debug:
                    print(f"    [DEBUG] SSH {try_user}@{hostname} timed out")
            except Exception as e:
                if self.debug:
                    print(f"    [DEBUG] SSH {try_user}@{hostname} error: {e}")

        return False, None

    def get_machine_id(self, hostname: str, user: str = None) -> Optional[str]:
        """Get the machine ID from a remote host via SSH."""
        ssh_user = user or "root"
        try:
            cmd = [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                f"ConnectTimeout={self.SSH_TIMEOUT}",
                "-o",
                "StrictHostKeyChecking=no",
                f"{ssh_user}@{hostname}",
                "cat /etc/machine-id 2>/dev/null || hostid",
            ]
            result = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=self.SSH_TIMEOUT + 2,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()[:32]  # machine-id is 32 chars
        except Exception as e:
            if self.debug:
                print(f"    [DEBUG] Failed to get machine-id from {hostname}: {e}")
        return None

    def get_machine_id_ansible(self, hostname: str) -> Optional[str]:
        """Get the machine ID from a remote host via Ansible."""
        try:
            cmd = [
                "ansible",
                hostname,
                "-m",
                "shell",
                "-a",
                "cat /etc/machine-id 2>/dev/null || hostid",
                "--one-line",
            ]
            result = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=30,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                # Ansible output format: "hostname | SUCCESS | rc=0 >> <output>"
                output = result.stdout.strip()
                if ">>" in output:
                    machine_id = output.split(">>")[-1].strip()
                    return machine_id[:32]
        except Exception as e:
            if self.debug:
                print(f"    [DEBUG] Failed to get machine-id via Ansible from {hostname}: {e}")
        return None

    def get_machine_id_sosreport(self, sosreport_path: str) -> Optional[str]:
        """Get the machine ID from a SOSreport."""
        try:
            sos_path = Path(sosreport_path)
            machine_id_file = sos_path / "etc/machine-id"
            if machine_id_file.exists():
                return machine_id_file.read_text().strip()[:32]
        except Exception as e:
            if self.debug:
                print(f"    [DEBUG] Failed to get machine-id from SOSreport: {e}")
        return None

    def check_ansible_access(
        self, hostname: str, _ansible_host: str = None, _ansible_user: str = None
    ) -> bool:
        """Check Ansible access to a host using ansible ping."""
        try:
            cmd = ["ansible", hostname, "-m", "ping", "-o"]
            if self.config.ansible_inventory_path:
                cmd.extend(["-i", self.config.ansible_inventory_path])

            result = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=15,
                check=False,
            )
            return "SUCCESS" in result.stdout
        except Exception:
            return False

    def check_node_access(
        self, hostname: str, ansible_info: dict = None, sosreport_path: str = None
    ) -> NodeAccess:
        """Check all access methods for a single node (thread-safe)."""
        node = NodeAccess(hostname=hostname)
        node.last_checked = datetime.now().isoformat()

        # Check SSH access (preferred)
        ssh_user = ansible_info.get("ansible_user") if ansible_info else None
        ssh_host = ansible_info.get("ansible_host", hostname) if ansible_info else hostname
        node.ssh_reachable, node.ssh_user = self.check_ssh_access(ssh_host, ssh_user)

        # If SSH is reachable, get the machine ID for verification
        if node.ssh_reachable:
            node.machine_id = self.get_machine_id(ssh_host, node.ssh_user)

        # Check Ansible access
        if ansible_info:
            node.ansible_host = ansible_info.get("ansible_host")
            node.ansible_user = ansible_info.get("ansible_user")
            if not node.ssh_reachable:  # Only check Ansible if SSH failed
                node.ansible_reachable = self.check_ansible_access(
                    hostname, node.ansible_host, node.ansible_user
                )
                # Get machine ID via Ansible if SSH failed
                if node.ansible_reachable:
                    node.machine_id = self.get_machine_id_ansible(hostname)

        # Record SOSreport path
        if sosreport_path:
            node.sosreport_path = sosreport_path
            # Try to get machine ID from SOSreport
            if not node.machine_id:
                node.machine_id = self.get_machine_id_sosreport(sosreport_path)

        # Determine preferred access method
        if node.ssh_reachable:
            node.preferred_method = "ssh"
        elif node.ansible_reachable:
            node.preferred_method = "ansible"
        elif node.sosreport_path:
            node.preferred_method = "sosreport"

        return node
