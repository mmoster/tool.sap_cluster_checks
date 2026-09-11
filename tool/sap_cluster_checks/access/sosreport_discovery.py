"""
SAP Pacemaker Cluster Health Check - SOSreport Discovery

Mixin class providing SOSreport discovery, extraction, and cluster analysis
methods for AccessDiscovery.
"""

import os
import re
import select
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Any

from ..lib.cib_parser import CIBParser


class SOSReportDiscoveryMixin:
    """Mixin providing SOSreport discovery methods for AccessDiscovery."""

    def _extract_sosreport(self, archive_path: str) -> tuple:
        """
        Extract a single SOSreport archive if not already extracted.
        Returns (success: bool, extracted_dir: str or error_msg: str)
        """
        archive_name = os.path.basename(archive_path)
        base_dir = os.path.dirname(archive_path)

        # Determine the expected directory name (remove .tar.xz, .tar.gz, etc.)
        dir_name = archive_name
        for ext in [".tar.xz", ".tar.gz", ".tar.bz2", ".tgz", ".txz", ".tar"]:
            if dir_name.endswith(ext):
                dir_name = dir_name[: -len(ext)]
                break

        expected_dir = os.path.join(base_dir, dir_name)

        # Check if already extracted
        if os.path.isdir(expected_dir):
            return (True, expected_dir)

        # Determine extraction command based on extension
        if archive_path.endswith(".tar.xz") or archive_path.endswith(".txz"):
            cmd = ["tar", "xJf", archive_path, "-C", base_dir]
        elif archive_path.endswith(".tar.gz") or archive_path.endswith(".tgz"):
            cmd = ["tar", "xzf", archive_path, "-C", base_dir]
        elif archive_path.endswith(".tar.bz2"):
            cmd = ["tar", "xjf", archive_path, "-C", base_dir]
        elif archive_path.endswith(".tar"):
            cmd = ["tar", "xf", archive_path, "-C", base_dir]
        else:
            return (False, f"Unknown archive format: {archive_name}")

        try:
            result = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=300,  # 5 minute timeout for large archives
                check=False,
            )
            if result.returncode == 0:
                # Find the extracted directory
                if os.path.isdir(expected_dir):
                    return (True, expected_dir)
                # Sometimes the directory name differs slightly, look for it
                for item in os.listdir(base_dir):
                    item_path = os.path.join(base_dir, item)
                    if (
                        os.path.isdir(item_path)
                        and item.startswith("sosreport-")
                        and dir_name.startswith(item[:20])
                    ):
                        return (True, item_path)
                return (True, expected_dir)  # Assume it worked
            return (False, f"Extract failed: {result.stderr[:100]}")
        except subprocess.TimeoutExpired:
            return (False, "Extract timed out (>5 min)")
        except Exception as e:
            return (False, str(e))

    def discover_sosreports(self) -> Dict[str, str]:
        """Discover SOSreport directories and map to hostnames."""
        sosreports = {}
        if not self.sosreport_dir or not os.path.exists(self.sosreport_dir):
            return sosreports

        print(f"\n=== Discovering SOSreports in {self.sosreport_dir} ===")
        self.config.sosreport_directory = self.sosreport_dir

        # First, find and extract any compressed SOSreports (multithreaded)
        archives = []
        archive_extensions = (".tar.xz", ".tar.gz", ".tar.bz2", ".tgz", ".txz", ".tar")
        for item in os.listdir(self.sosreport_dir):
            if item.startswith("sosreport-") and item.endswith(archive_extensions):
                archive_path = os.path.join(self.sosreport_dir, item)
                archives.append(archive_path)

        if archives:
            print(f"  Found {len(archives)} compressed sosreports, checking/extracting...")
            with ThreadPoolExecutor(max_workers=min(len(archives), 4)) as executor:
                futures = {
                    executor.submit(self._extract_sosreport, arch): arch for arch in archives
                }
                for future in as_completed(futures):
                    archive = futures[future]
                    archive_name = os.path.basename(archive)
                    try:
                        success, result = future.result()
                        if success:
                            print(f"    [OK] {archive_name}")
                        else:
                            print(f"    [FAIL] {archive_name}: {result}")
                    except Exception as e:
                        print(f"    [ERROR] {archive_name}: {e}")

        # Look for sosreport directories (pattern: sosreport-<hostname>-<id>)
        for item in os.listdir(self.sosreport_dir):
            item_path = os.path.join(self.sosreport_dir, item)
            if os.path.isdir(item_path) and item.startswith("sosreport-"):
                # Extract hostname from sosreport directory name
                parts = item.split("-")
                if len(parts) >= 2:
                    hostname = parts[1]
                    sosreports[hostname] = item_path
                    print(f"  Found: {hostname} -> {item}")

        # Also check for extracted sosreports by reading etc/hostname
        for item in os.listdir(self.sosreport_dir):
            item_path = os.path.join(self.sosreport_dir, item)
            hostname_file = os.path.join(item_path, "etc/hostname")
            if os.path.isdir(item_path) and os.path.exists(hostname_file):
                with open(hostname_file, "r", encoding="utf-8") as f:
                    hostname = f.read().strip().split(".")[0]  # Get short hostname
                if hostname and hostname not in sosreports:
                    sosreports[hostname] = item_path
                    print(f"  Found: {hostname} -> {item}")

        print(f"Found {len(sosreports)} SOSreports")

        # Check for extended SAP HANA HA data and suggest configuration if missing
        if sosreports:
            self._check_sosreport_extended_data(sosreports)

        return sosreports

    def _check_sosreport_extended_data(self, sosreports: Dict[str, str]) -> None:
        """
        Check if SOSreports have extended SAP HANA HA data.
        Print suggestions if the data is missing.
        """
        missing_extended = []
        has_extended = []

        for hostname, sos_path in sosreports.items():
            extras_path = Path(sos_path) / "sos_commands/sos_extras/sap_hana_ha"
            _saphana_path = Path(sos_path) / "sos_commands/saphana"  # noqa: F841

            # Check for extended data: SAPHanaSR-showAttr or HADR data
            # (old-style: single script output, new-style: individual command files)
            has_sr_attr = (
                (extras_path / "SAPHanaSR-showAttr").exists() if extras_path.exists() else False
            )
            has_hadr = False
            if extras_path.exists():
                # Old-style: single script output
                if (extras_path / "usr.local.sbin.sap-ha-collect-hadr").exists():
                    has_hadr = True
                else:
                    # New-style: individual command files (check for redhat-release
                    # or rpm output as indicators)
                    has_hadr = bool(
                        list(extras_path.glob("cat_*redhat-release*"))
                        or list(extras_path.glob("rpm_*sap-hana*"))
                    )

            if has_sr_attr or has_hadr:
                has_extended.append(hostname)
            else:
                missing_extended.append(hostname)

        if missing_extended and not has_extended:
            # All SOSreports are missing extended data
            print("\n" + "=" * 63)
            print(" [SUGGESTION] SOSreports missing extended SAP HANA HA data")
            print("=" * 63)
            print("""
  The SOSreports do not contain SAPHanaSR-showAttr output, which is
  critical for analyzing SAP HANA System Replication cluster state.

  To collect extended SAP HANA HA data in future SOSreports, deploy
  the following configuration to all cluster nodes:

  Run: ./sap_cluster_checks.py -R --configure-extensions
  Or:  ./sap_cluster_checks.py -R <node>

  This will configure SAP HANA HA data collection (global.ini,
  sudoers, SAPHanaSR-showAttr, cluster state) and create SOSreports.
""")
            print("=" * 63)

    def scan_sosreports_recursive(self, base_dir: str = ".") -> Dict[str, str]:
        """
        Recursively scan base_dir and subdirectories for SOSreport directories.
        Returns dict mapping hostname -> sosreport_path.
        """
        sosreports = {}
        base_path = Path(base_dir).resolve()

        # Walk through all subdirectories
        for root, dirs, _files in os.walk(base_path):
            # Skip hidden directories and common non-sosreport dirs
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".") and d not in ["__pycache__", "venv", ".git"]
            ]

            for d in dirs:
                if d.startswith("sosreport-"):
                    dir_path = os.path.join(root, d)
                    # Extract hostname from directory name
                    parts = d.split("-")
                    if len(parts) >= 2:
                        hostname = parts[1]
                        if hostname not in sosreports:
                            sosreports[hostname] = dir_path

                    # Also try reading etc/hostname for accurate hostname
                    hostname_file = os.path.join(dir_path, "etc/hostname")
                    if os.path.exists(hostname_file):
                        try:
                            with open(hostname_file, "r", encoding="utf-8") as f:
                                hostname = f.read().strip().split(".")[0]
                            if hostname and hostname not in sosreports:
                                sosreports[hostname] = dir_path
                        except Exception:
                            pass

        return sosreports

    def was_cluster_running_in_sosreport(self, sosreport_path: str) -> tuple:
        """
        Check if the cluster was running when the SOSreport was captured.
        Returns tuple: (was_running, reason_message)
        """
        sos_path = Path(sosreport_path)

        # Check pcs status output for connection errors
        for pcs_file in [
            "sos_commands/pacemaker/pcs_status",
            "sos_commands/pacemaker/pcs_status_--full",
        ]:
            pcs_status = sos_path / pcs_file
            if pcs_status.exists():
                try:
                    content = pcs_status.read_text()
                    # Check for connection failure messages
                    if (
                        "Connection to cluster failed" in content
                        or "Error: cluster is not currently running" in content
                    ):
                        return (False, "pcs status shows cluster connection failed")
                    if "error:" in content.lower() and "cluster" in content.lower():
                        return (False, "pcs status shows cluster error")
                    # If we have normal cluster output, it was running
                    if "Cluster name:" in content or "nodes configured" in content:
                        return (True, "pcs status shows cluster was running")
                except Exception:
                    pass

        # Check crm_mon output
        crm_mon = sos_path / "sos_commands/pacemaker/crm_mon_-1"
        if crm_mon.exists():
            try:
                content = crm_mon.read_text()
                if "Connection to cluster failed" in content or "Could not connect" in content:
                    return (False, "crm_mon shows cluster connection failed")
                if "error:" in content.lower() and "cluster" in content.lower():
                    return (False, "crm_mon shows cluster error")
                # Normal output indicates cluster was running
                if "nodes configured" in content.lower() or "online" in content.lower():
                    return (True, "crm_mon shows cluster was running")
            except Exception:
                pass

        # Check systemd service status if available
        systemd_dir = sos_path / "sos_commands/systemd"
        if systemd_dir.exists():
            for service_file in systemd_dir.glob("systemctl_status_*pacemaker*"):
                try:
                    content = service_file.read_text()
                    if "Active: active (running)" in content:
                        return (True, "systemctl shows pacemaker was running")
                    if "Active: inactive" in content or "Active: failed" in content:
                        return (False, "systemctl shows pacemaker was not running")
                except Exception:
                    pass

        # Default: unknown, assume running (to avoid false positives)
        return (True, "cluster status unknown from sosreport")

    def get_cluster_name_from_sosreport(self, sosreport_path: str) -> Optional[str]:
        """Extract cluster name from a sosreport's corosync.conf or pcs status output."""
        sos_path = Path(sosreport_path)

        # Try corosync.conf first
        corosync_conf = sos_path / "etc/corosync/corosync.conf"
        if corosync_conf.exists():
            try:
                content = corosync_conf.read_text()
                # Look for cluster_name: <name> in totem section
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

        # Try crm_mon output
        crm_mon = sos_path / "sos_commands/pacemaker/crm_mon_-1"
        if crm_mon.exists():
            try:
                content = crm_mon.read_text()
                # Some versions show cluster name in the header
                match = re.search(r"Cluster\s+(\S+)\s+status", content, re.IGNORECASE)
                if match:
                    return match.group(1)
            except Exception:
                pass

        return None

    def get_cluster_nodes_from_sosreport(self, sosreport_path: str) -> List[str]:
        """Extract cluster node list from a sosreport's corosync.conf."""
        sos_path = Path(sosreport_path)
        nodes = []

        # Try corosync.conf
        corosync_conf = sos_path / "etc/corosync/corosync.conf"
        if corosync_conf.exists():
            try:
                content = corosync_conf.read_text()
                # Look for ring0_addr or name in nodelist section
                # Pattern: ring0_addr: <hostname/ip> or name: <hostname>
                ring_matches = re.findall(r"ring0_addr:\s*(\S+)", content)
                name_matches = re.findall(r"^\s*name:\s*(\S+)", content, re.MULTILINE)

                # Prefer name matches as they're usually hostnames
                if name_matches:
                    nodes = name_matches
                elif ring_matches:
                    nodes = ring_matches
            except Exception:
                pass

        return nodes

    def _get_sosreport_hostname_aliases(self, sosreport_path: str) -> set:
        """
        Get all hostname aliases for a sosreport node by parsing /etc/hosts
        and matching against the node's own IP addresses.

        Returns a set of all hostnames (short names) that resolve to this node,
        including the etc/hostname value itself.
        """
        sos_path = Path(sosreport_path)
        aliases = set()

        # Get the primary hostname
        hostname_file = sos_path / "etc/hostname"
        if hostname_file.exists():
            try:
                primary = hostname_file.read_text().strip().split(".")[0]
                if primary:
                    aliases.add(primary)
            except Exception:
                pass

        # Collect this node's IP addresses from network interface data
        node_ips = set()
        ip_addr_files = [
            sos_path / "sos_commands/networking/ip_-o_addr",
            sos_path / "sos_commands/networking/ip_addr",
        ]
        for ip_file in ip_addr_files:
            if ip_file.exists():
                try:
                    content = ip_file.read_text()
                    # Match inet <ip>/prefix patterns
                    for match in re.findall(r"inet\s+(\d+\.\d+\.\d+\.\d+)/", content):
                        if not match.startswith("127."):
                            node_ips.add(match)
                    break  # Use first available file
                except Exception:
                    pass

        if not node_ips:
            return aliases

        # Parse /etc/hosts and find all hostnames mapping to this node's IPs
        hosts_file = sos_path / "etc/hosts"
        if hosts_file.exists():
            try:
                for line in hosts_file.read_text().splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split()
                    if len(parts) >= 2 and parts[0] in node_ips:
                        # Add all hostnames for this IP (short names only)
                        for name in parts[1:]:
                            short = name.split(".")[0]
                            if short:
                                aliases.add(short)
            except Exception:
                pass

        return aliases

    def _resolve_sosreport_aliases(
        self, sosreports: Dict[str, str], missing_nodes: set
    ) -> Dict[str, str]:
        """
        Resolve missing corosync node names to existing sosreports by hostname alias.

        When /etc/hostname differs from corosync nodelist names (e.g., AWS instance
        name vs application hostname), this method matches them via /etc/hosts.

        Returns dict: {corosync_name: sosreport_path} for resolved nodes.
        """
        if not missing_nodes:
            return {}

        resolved = {}

        # Build alias map: for each sosreport, get all hostname aliases
        sos_alias_map = {}  # alias -> (primary_hostname, sosreport_path)
        for hostname, sos_path in sosreports.items():
            aliases = self._get_sosreport_hostname_aliases(sos_path)
            for alias in aliases:
                sos_alias_map[alias] = (hostname, sos_path)

        # Try to resolve each missing node
        for node_name in list(missing_nodes):
            short_name = node_name.split(".")[0]
            if short_name in sos_alias_map:
                primary_hostname, sos_path = sos_alias_map[short_name]
                resolved[node_name] = sos_path
                if self.debug:
                    print(f"    [DEBUG] Resolved {node_name} -> {primary_hostname} (same host)")

        return resolved

    def extract_cluster_config_from_cib(self, sosreport_path: str = None) -> dict:
        """
        Extract detailed cluster configuration from cib.xml.

        Works with both:
        - SOSreport directory (pass sosreport_path)
        - Live/stopped cluster (pass sosreport_path=None to use local cib.xml)

        Parses cib.xml and other files to extract:
        - HANA SID and instance number
        - VIP configuration
        - STONITH configuration
        - Replication mode and operation mode
        - Node hostnames, IPs, FQDNs
        - Cluster properties (automated_register, prefer_site_takeover, etc.)

        Returns:
            Dict with cluster configuration suitable for storing in self.config.clusters
        """
        sos_path = Path(sosreport_path) if sosreport_path else None
        config = {}

        # Try to use CIBParser for detailed config
        try:
            from ..lib.cib_parser import CIBParser

            # Use SOSreport or local cib.xml
            if sosreport_path:
                parser = CIBParser.from_sosreport(sosreport_path)
            else:
                parser = CIBParser.from_live_system()

            if parser and parser.is_available():
                cib_config = parser.get_resource_config()
                if cib_config.get("success"):
                    hana_config = cib_config.get("sap_hana", {})
                    if hana_config:
                        config["sid"] = hana_config.get("sid")
                        config["instance_number"] = hana_config.get("instance_number")
                        config["resource_name"] = hana_config.get("resource_name")
                        config["resource_type"] = hana_config.get("resource_type")
                        config["clone_max"] = hana_config.get("clone_max")

                        # Extract HA parameters from resource attributes
                        attrs = hana_config.get("attributes", {})
                        if attrs.get("AUTOMATED_REGISTER"):
                            config["automated_register"] = (
                                attrs["AUTOMATED_REGISTER"].lower() == "true"
                            )
                        if attrs.get("PREFER_SITE_TAKEOVER"):
                            config["prefer_site_takeover"] = (
                                attrs["PREFER_SITE_TAKEOVER"].lower() == "true"
                            )
                        if attrs.get("DUPLICATE_PRIMARY_TIMEOUT"):
                            try:
                                config["duplicate_primary_timeout"] = int(
                                    attrs["DUPLICATE_PRIMARY_TIMEOUT"]
                                )
                            except ValueError:
                                pass

                # Get STONITH config
                stonith_config = parser.get_stonith()
                if stonith_config.get("enabled") is not None:
                    config["stonith_enabled"] = stonith_config["enabled"]
                if stonith_config.get("devices"):
                    config["stonith_device"] = (
                        stonith_config["devices"][0] if stonith_config["devices"] else None
                    )
        except Exception:
            pass

        # Parse additional files (only for SOSreport mode)
        if not sos_path:
            return config  # For local mode, CIBParser has all we need

        # Parse pcs_config for additional info (VIPs, etc.)
        pcs_config_paths = [
            sos_path / "sos_commands/pacemaker/pcs_config",
            sos_path / "sos_commands/pacemaker/pcs_resource_config",
        ]

        for pcs_config_path in pcs_config_paths:
            if pcs_config_path.exists():
                try:
                    content = pcs_config_path.read_text()

                    # Extract VIP resources
                    vip_pattern = (
                        r"Resource:\s*(vip\S*)\s+.*?type=IPaddr2\).*?ip=(\d+\.\d+\.\d+\.\d+)"
                    )
                    vip_matches = re.findall(vip_pattern, content, re.DOTALL | re.IGNORECASE)
                    if vip_matches:
                        # Primary VIP is usually the first one or one with SID in name
                        for name, ip in vip_matches:
                            if "vip_" in name.lower() and not config.get("virtual_ip"):
                                config["virtual_ip"] = ip
                                config["vip_resource"] = name
                            elif "vip2_" in name.lower() or ("secondary" in name.lower()):
                                config["secondary_vip"] = ip
                                config["secondary_vip_resource"] = name

                    # If we found VIPs but didn't set primary, use first one
                    if vip_matches and not config.get("virtual_ip"):
                        config["virtual_ip"] = vip_matches[0][1]
                        config["vip_resource"] = vip_matches[0][0]
                        if len(vip_matches) > 1:
                            config["secondary_vip"] = vip_matches[1][1]
                            config["secondary_vip_resource"] = vip_matches[1][0]

                    # Extract SID/instance if not already found
                    if not config.get("sid"):
                        sid_match = re.search(r"SID=([A-Z0-9]{3})", content)
                        if sid_match:
                            config["sid"] = sid_match.group(1)

                    if not config.get("instance_number"):
                        inst_match = re.search(r"InstanceNumber=(\d{2})", content)
                        if inst_match:
                            config["instance_number"] = inst_match.group(1)

                    # Extract STONITH device if not found
                    if not config.get("stonith_device"):
                        stonith_match = re.search(r"Resource:\s*(\S+)\s+\(class=stonith", content)
                        if stonith_match:
                            config["stonith_device"] = stonith_match.group(1)

                    # Extract STONITH params (pcmk_host_map, etc.)
                    if config.get("stonith_device"):
                        host_map_match = re.search(r"pcmk_host_map=([^\n]+)", content)
                        if host_map_match:
                            if "stonith_params" not in config:
                                config["stonith_params"] = {}
                            config["stonith_params"]["pcmk_host_map"] = host_map_match.group(
                                1
                            ).strip()

                    break  # Found config, stop looking
                except Exception:
                    pass

        # Parse SAPHanaSR-showAttr for replication info
        sr_attr_paths = [
            sos_path / "sos_commands/sos_extras/sap_hana_ha/SAPHanaSR-showAttr",
            sos_path / "sos_commands/saphana/SAPHanaSR-showAttr",
        ]

        for sr_attr_path in sr_attr_paths:
            if sr_attr_path.exists():
                try:
                    content = sr_attr_path.read_text()

                    # Extract replication mode (sync, syncmem, async)
                    mode_match = re.search(r"sync_state\s*:\s*(\w+)", content, re.IGNORECASE)
                    if not mode_match:
                        mode_match = re.search(r"srmode\s*[=:]\s*(\w+)", content, re.IGNORECASE)
                    if mode_match:
                        mode = mode_match.group(1).lower()
                        if mode in ("sync", "syncmem", "async"):
                            config["replication_mode"] = mode

                    # Extract operation mode (logreplay, delta_datashipping)
                    op_match = re.search(r"op_mode\s*[=:]\s*(\w+)", content, re.IGNORECASE)
                    if op_match:
                        config["operation_mode"] = op_match.group(1)

                    # Extract sites (filter out numeric site IDs like "100")
                    site_matches = re.findall(r"site\s*[=:]\s*(\w+)", content, re.IGNORECASE)
                    site_names = [s for s in set(site_matches) if not s.isdigit()]
                    if site_names:
                        config["sites"] = site_names

                    break  # Found SR attr, stop looking
                except Exception:
                    pass

        # Extract node info from corosync.conf
        corosync_conf = sos_path / "etc/corosync/corosync.conf"
        if corosync_conf.exists():
            try:
                content = corosync_conf.read_text()

                # Extract node hostnames and IPs
                node_blocks = re.findall(r"node\s*\{([^}]+)\}", content, re.DOTALL)
                nodes = []
                for i, block in enumerate(node_blocks):
                    name_match = re.search(r"name:\s*(\S+)", block)
                    ring_match = re.search(r"ring0_addr:\s*(\S+)", block)
                    if name_match:
                        node_name = name_match.group(1)
                        nodes.append(node_name)
                        # Store node info
                        node_key = f"node{i + 1}_hostname"
                        config[node_key] = node_name
                        if ring_match:
                            config[f"node{i + 1}_ip"] = ring_match.group(1)
            except Exception:
                pass

        # Get RHEL version
        redhat_release = sos_path / "etc/redhat-release"
        if redhat_release.exists():
            try:
                content = redhat_release.read_text().strip()
                match = re.search(r"release\s+(\d+\.?\d*)", content)
                if match:
                    config["rhel_version"] = f"RHEL {match.group(1)}"
            except Exception:
                pass

        # Get Pacemaker version
        installed_rpms = sos_path / "installed-rpms"
        if installed_rpms.exists():
            try:
                content = installed_rpms.read_text()
                match = re.search(r"pacemaker-(\d+\.\d+\.\d+)", content)
                if match:
                    config["pacemaker_version"] = match.group(1)
            except Exception:
                pass

        return config

    def _discover_cluster_from_sosreports(
        self, available_sosreports: Dict[str, str]
    ) -> Dict[str, str]:
        """
        From the SOSreports of nodes we already have, discover other cluster nodes
        and find their matching SOSreports. Also extracts cluster name.

        Args:
            available_sosreports: Dict of hostname -> sosreport_path for all available sosreports

        Returns:
            Dict of newly discovered hostname -> sosreport_path
        """
        discovered = {}
        cluster_name = None

        # Get cluster nodes from existing node's sosreport
        for hostname, node_info in self.config.nodes.items():
            sos_path = node_info.get("sosreport_path")
            if not sos_path:
                continue

            # Get cluster name from this sosreport (if not already found)
            if not cluster_name:
                cluster_name = self.get_cluster_name_from_sosreport(sos_path)
                if cluster_name:
                    # Add cluster to config
                    if cluster_name not in self.config.clusters:
                        self.config.clusters[cluster_name] = {
                            "nodes": [hostname],
                            "discovered_from": f"sosreport:{hostname}",
                        }
                        print(f"  [CLUSTER] Detected cluster name: {cluster_name}")
                    # Add existing node to cluster
                    if hostname not in self.config.clusters[cluster_name].get("nodes", []):
                        self.config.clusters[cluster_name].setdefault("nodes", []).append(hostname)

            # Get cluster nodes from this sosreport
            cluster_nodes = self.get_cluster_nodes_from_sosreport(sos_path)
            if not cluster_nodes:
                continue

            # Find matching sosreports for cluster nodes
            for cluster_node in cluster_nodes:
                if cluster_node in self.config.nodes:
                    continue  # Already have this node
                if cluster_node in discovered:
                    continue  # Already discovered

                # Look for matching sosreport
                if cluster_node in available_sosreports:
                    discovered[cluster_node] = available_sosreports[cluster_node]
                    # Add to cluster nodes list
                    if cluster_name and cluster_name in self.config.clusters:
                        if cluster_node not in self.config.clusters[cluster_name].get("nodes", []):
                            self.config.clusters[cluster_name].setdefault("nodes", []).append(
                                cluster_node
                            )
                else:
                    # Try partial match (hostname might be short vs FQDN)
                    for sos_hostname, sos_path_match in available_sosreports.items():
                        if cluster_node in sos_hostname or sos_hostname in cluster_node:
                            discovered[cluster_node] = sos_path_match
                            # Add to cluster nodes list
                            if cluster_name and cluster_name in self.config.clusters:
                                if cluster_node not in self.config.clusters[cluster_name].get(
                                    "nodes", []
                                ):
                                    self.config.clusters[cluster_name].setdefault(
                                        "nodes", []
                                    ).append(cluster_node)
                            break

        return discovered

    def discover_sosreports_with_clusters(self) -> Dict[str, Dict[str, Any]]:
        """
        Discover SOSreports and group them by cluster.
        Returns dict: {cluster_name: {'nodes': {hostname: sosreport_path}}}
        """
        clusters = {}  # cluster_name -> {'nodes': {hostname: path}}
        unassigned = {}  # hostname -> path (for sosreports without cluster info)

        # First discover all sosreports
        sosreports = self.discover_sosreports()

        if not sosreports:
            return clusters

        print("\n=== Detecting cluster membership from SOSreports ===")

        for hostname, sos_path in sosreports.items():
            cluster_name = self.get_cluster_name_from_sosreport(sos_path)

            if cluster_name:
                if cluster_name not in clusters:
                    clusters[cluster_name] = {"nodes": {}}
                clusters[cluster_name]["nodes"][hostname] = sos_path
                print(f"  {hostname}: cluster '{cluster_name}'")
            else:
                unassigned[hostname] = sos_path
                print(f"  {hostname}: (no cluster info)")

        # If there are unassigned nodes, try to match them to existing clusters
        # by checking if their hostnames appear in any cluster's nodelist
        if unassigned and clusters:
            for cluster_name, cluster_info in clusters.items():
                # Get expected nodes from first sosreport in this cluster
                # Use CIBParser for accurate node list from cib.xml
                first_sos_path = list(cluster_info["nodes"].values())[0]
                expected_nodes = self.get_cluster_nodes_from_sosreport(first_sos_path)

                # Also try to get nodes from cib.xml for more accurate matching
                try:
                    from ..lib.cib_parser import CIBParser

                    parser = CIBParser.from_sosreport(first_sos_path)
                    if parser and parser.is_available():
                        cib_nodes = parser.get_nodes()
                        if cib_nodes.get("success") and cib_nodes.get("nodes"):
                            expected_nodes = cib_nodes["nodes"]
                except Exception:
                    pass  # Fall back to corosync.conf nodes

                for hostname, sos_path in list(unassigned.items()):
                    # Check if hostname exactly matches any expected node
                    if hostname in expected_nodes:
                        clusters[cluster_name]["nodes"][hostname] = sos_path
                        del unassigned[hostname]
                        print(f"  {hostname}: matched to cluster '{cluster_name}' (from nodelist)")
                        continue

        # Put remaining unassigned in 'unknown' cluster
        if unassigned:
            clusters["(unknown)"] = {"nodes": unassigned}

        return clusters

    def prompt_cluster_selection(
        self, clusters: Dict[str, Dict[str, Any]], default_cluster: str = None
    ) -> Optional[str]:
        """
        Prompt user to select which cluster to analyze when multiple clusters are found.
        If default_cluster is set, pressing Enter selects that cluster.
        Returns selected cluster name or None if user cancels.
        """
        if len(clusters) <= 1:
            return list(clusters.keys())[0] if clusters else None

        cluster_list = list(clusters.keys())
        default_idx = None
        for i, cluster_name in enumerate(cluster_list, 1):
            nodes = list(clusters[cluster_name]["nodes"].keys())
            marker = " (default)" if cluster_name == default_cluster else ""
            print(f"\n  [{i}] Cluster: {cluster_name}{marker}")
            print(f"      Nodes ({len(nodes)}): {', '.join(sorted(nodes))}")
            if cluster_name == default_cluster:
                default_idx = i

        print("\n  [a] Analyze all clusters together")
        print("  [q] Quit")

        timeout_seconds = 10

        if default_idx:
            prompt = f"\nSelect cluster to analyze [1-{len(cluster_list)}/a/q] (Enter={default_idx}, auto-select in {timeout_seconds}s): "
        else:
            prompt = f"\nSelect cluster to analyze [1-{len(cluster_list)}/a/q]: "

        while True:
            try:
                sys.stdout.write(prompt)
                sys.stdout.flush()

                if default_idx:
                    # Wait for input with timeout; auto-select default if no response
                    ready, _wlist, _xlist = select.select([sys.stdin], [], [], timeout_seconds)
                    if ready:
                        choice = sys.stdin.readline().strip().lower()
                    else:
                        # Timeout reached, auto-select default
                        selected = cluster_list[default_idx - 1]
                        print(f"\n\n  Auto-selected (timeout): {selected}")
                        return selected
                else:
                    choice = input().strip().lower()
            except (EOFError, KeyboardInterrupt):
                return None

            if choice == "" and default_idx:
                selected = cluster_list[default_idx - 1]
                print(f"\n  Selected: {selected}")
                return selected
            if choice == "q":
                return None
            if choice == "a":
                return "__all__"  # Special value to indicate all clusters
            if choice.isdigit():
                idx = int(choice)
                if 1 <= idx <= len(cluster_list):
                    selected = cluster_list[idx - 1]
                    print(f"\n  Selected: {selected}")
                    return selected

            print(f"  Invalid choice. Enter 1-{len(cluster_list)}, 'a' for all, or 'q' to quit.")
