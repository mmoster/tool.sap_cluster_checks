"""
SAP Pacemaker Cluster Health Check - Installation Status

Mixin class providing installation status checking methods
for ClusterHealthCheck. Checks both live nodes and SOSreports.
"""

import re
import subprocess
from pathlib import Path

from .install_checks import make_status_dict


class InstallStatusMixin:
    """Mixin providing installation status checking for ClusterHealthCheck."""

    def check_install_status_sosreport(self, node: str, sosreport_path: str) -> dict:
        """
        Check installation status from a SOSreport directory.
        Returns dict with status of each installation step based on captured data.
        """
        sos_path = Path(sosreport_path)

        status = make_status_dict(node, "sosreport")

        # Detect RHEL version from redhat-release
        redhat_release = sos_path / "etc/redhat-release"
        if redhat_release.exists():
            try:
                content = redhat_release.read_text().strip()
                match = re.search(r"release\s+(\d+\.?\d*)", content)
                if match:
                    status["rhel_version"] = f"RHEL {match.group(1)}"
                else:
                    status["rhel_version"] = content[:50]
            except Exception:
                pass

        # Detect Pacemaker version from installed-rpms
        installed_rpms = sos_path / "installed-rpms"
        if installed_rpms.exists():
            try:
                content = installed_rpms.read_text()
                match = re.search(r"pacemaker-(\d+\.\d+\.\d+)", content)
                if match:
                    status["pacemaker_version"] = match.group(1)
            except Exception:
                pass

        # Fallback: extract RHEL version and Pacemaker version from crm_report/sysinfo.txt
        # This file contains os-release data and package list when installed-rpms is absent
        if not status["rhel_version"] or not status["pacemaker_version"]:
            sysinfo_candidates = (
                list((sos_path / "sos_commands/pacemaker/crm_report").glob("sysinfo.txt"))
                if (sos_path / "sos_commands/pacemaker/crm_report").exists()
                else []
            )
            for sysinfo in sysinfo_candidates:
                try:
                    content = sysinfo.read_text()
                    if not status["rhel_version"]:
                        match = re.search(r'VERSION_ID="(\d+\.?\d*)"', content)
                        if match:
                            status["rhel_version"] = f"RHEL {match.group(1)}"
                    if not status["pacemaker_version"]:
                        match = re.search(r"Pacemaker\s+(\d+\.\d+\.\d+)", content)
                        if match:
                            status["pacemaker_version"] = match.group(1)
                    break
                except Exception:
                    pass

        # Check if corosync.conf exists
        corosync_conf = sos_path / "etc/corosync/corosync.conf"
        status["corosync_conf_exists"] = corosync_conf.exists()

        # Check if cib.xml exists (cluster configuration exists)
        cib_xml = sos_path / "var/lib/pacemaker/cib/cib.xml"
        status["cib_exists"] = cib_xml.exists()

        # Extract cluster name from corosync.conf
        if corosync_conf.exists():
            try:
                content = corosync_conf.read_text()
                match = re.search(r"cluster_name:\s*(\S+)", content)
                if match:
                    status["cluster_name"] = match.group(1)
            except Exception:
                pass

        # Check packages from installed-rpms or sysinfo.txt fallback
        pkg_content = None
        installed_rpms = sos_path / "installed-rpms"
        if installed_rpms.exists():
            try:
                pkg_content = installed_rpms.read_text()
            except Exception:
                pass

        # Fallback: sysinfo.txt has full package list when installed-rpms is absent
        if not pkg_content:
            sysinfo_path = sos_path / "sos_commands/pacemaker/crm_report/sysinfo.txt"
            if sysinfo_path.exists():
                try:
                    pkg_content = sysinfo_path.read_text()
                except Exception:
                    pass

        if pkg_content:
            try:
                required_packages = ["pacemaker", "corosync", "pcs"]
                sap_packages = [
                    "sap-hana-ha",
                    "resource-agents-sap-hana",
                    "resource-agents-sap-hana-scaleout",
                ]

                missing = []
                for pkg in required_packages:
                    if pkg not in pkg_content:
                        missing.append(pkg)

                sap_found = any(pkg in pkg_content for pkg in sap_packages)
                if not sap_found:
                    missing.append("sap-hana-ha")

                status["missing_packages"] = missing
                status["packages_installed"] = len(missing) == 0
            except Exception:
                pass

        # Check pcs status output (try different filename variants)
        pcs_status = sos_path / "sos_commands/pacemaker/pcs_status_--full"
        if not pcs_status.exists():
            pcs_status = sos_path / "sos_commands/pacemaker/pcs_status"
        if pcs_status.exists():
            try:
                content = pcs_status.read_text()
                status["cluster_configured"] = (
                    "Cluster name:" in content or "nodes configured" in content
                )

                # Check for online nodes - handle both formats:
                # Old format: "Online: [ node1 node2 ]"
                # New format: "Node nodename (id): online"
                if "Online:" in content:
                    status["cluster_online"] = True
                    match = re.search(r"Online:\s*\[\s*(.*?)\s*\]", content)
                    if match:
                        status["cluster_nodes"] = [
                            n.strip() for n in match.group(1).split() if n.strip()
                        ]

                # New pcs status format: "Node nodename (id): online"
                node_matches = re.findall(
                    r"Node\s+(\S+)\s+\(\d+\):\s+online", content, re.IGNORECASE
                )
                if node_matches:
                    status["cluster_online"] = True
                    status["cluster_nodes"] = node_matches

                # Check STONITH - look for stonith resources running
                if "stonith:" in content.lower() and "Started" in content:
                    status["stonith_enabled"] = True
                elif (
                    "stonith-enabled=true" in content.lower()
                    or "stonith-enabled: true" in content.lower()
                ):
                    status["stonith_enabled"] = True
                elif (
                    "stonith-enabled=false" in content.lower()
                    or "stonith-enabled: false" in content.lower()
                ):
                    status["stonith_enabled"] = False

                # Check HANA resources
                if "SAPHana" in content:
                    status["hana_resources"] = True
            except Exception:
                pass

        # Check systemctl output for service status
        systemctl_output = sos_path / "sos_commands/systemd/systemctl_list-units_--all"
        if systemctl_output.exists():
            try:
                content = systemctl_output.read_text()
                # Match pacemaker.service line specifically for 'running'
                for line in content.splitlines():
                    line_lower = line.lower()
                    if "corosync.service" in line and "running" in line_lower:
                        status["corosync_running"] = True
                    if "pacemaker.service" in line and "running" in line_lower:
                        status["pacemaker_running"] = True
                    if "pcsd.service" in line and "running" in line_lower:
                        status["pcsd_running"] = True
            except Exception:
                pass

        # Fallback: if systemd check didn't determine pacemaker_running,
        # infer from cluster_online (if nodes are online, pacemaker is running)
        if status.get("pacemaker_running") is None and status.get("cluster_online"):
            status["pacemaker_running"] = True
            status["corosync_running"] = True

        # Check for HANA installation
        hana_check = sos_path / "usr/sap"
        if not hana_check.exists():
            # Try alternative location in sos data
            proc_mounts = sos_path / "proc/mounts"
            if proc_mounts.exists():
                try:
                    content = proc_mounts.read_text()
                    status["hana_installed"] = "/usr/sap/" in content or "/hana/" in content.lower()
                except Exception:
                    pass

        return status

    def _execute_check_cmd(self, cmd: str, node: str, method: str, user: str = None) -> tuple:
        """Execute a command on a node and return (success, output)."""
        try:
            if method == "local":
                full_cmd = cmd
            elif method == "ssh":
                ssh_user = user or "root"
                escaped_cmd = cmd.replace("'", "'\"'\"'")
                full_cmd = (
                    f"ssh -o BatchMode=yes -o ConnectTimeout=10 {ssh_user}@{node} '{escaped_cmd}'"
                )
            else:
                return False, "Unsupported method"

            if self.debug:
                print(f"  [DEBUG] Executing: {full_cmd[:100]}...")

            result = subprocess.run(
                full_cmd,
                shell=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=30,
                check=False,
            )

            if self.debug:
                print(
                    f"  [DEBUG] Return code: {result.returncode}, Output: {result.stdout.strip()[:50]}..."
                )

            return result.returncode == 0, result.stdout.strip()
        except Exception as e:
            if self.debug:
                print(f"  [DEBUG] Exception: {e}")
            return False, str(e)

    def check_install_status(self, node: str = None, method: str = "ssh", user: str = None) -> dict:
        """
        Check installation status on a node.
        Returns dict with status of each installation step.
        """
        status = make_status_dict(node, method)

        if not node and not self.access_config:
            return status

        # Use first accessible node if not specified
        if not node and self.access_config:
            for n, info in self.access_config.nodes.items():
                if info.get("preferred_method"):
                    node = n
                    method = info.get("preferred_method", "ssh")
                    user = info.get("ssh_user", "root")
                    break

        if not node:
            return status

        status["node"] = node

        # For sosreport mode, use the sosreport-specific method
        if method == "sosreport" and self.access_config:
            node_info = self.access_config.nodes.get(node, {})
            sosreport_path = node_info.get("sosreport_path")
            if sosreport_path:
                return self.check_install_status_sosreport(node, sosreport_path)

        # Check packages FIRST (if installed, subscription/repos don't matter)
        # Note: rpm -q returns exit code 1 if ANY package is missing, but still outputs info
        # SAP resource agent packages (any one is OK):
        #   - sap-hana-ha: RHEL 9/10, Scale-Up & Scale-Out (recommended, required for RHEL 10)
        #   - resource-agents-sap-hana: legacy Scale-Up (RHEL 8/9)
        #   - resource-agents-sap-hana-scaleout: legacy Scale-Out (RHEL 8/9)
        required_packages = ["pacemaker", "corosync", "pcs"]
        sap_packages = [
            "sap-hana-ha",
            "resource-agents-sap-hana",
            "resource-agents-sap-hana-scaleout",
        ]
        success, output = self._execute_check_cmd(
            "rpm -q pacemaker corosync pcs sap-hana-ha resource-agents-sap-hana resource-agents-sap-hana-scaleout 2>/dev/null",
            node,
            method,
            user,
        )
        # Parse output even if exit code is non-zero (rpm returns 1 if any package missing)
        if output:
            for pkg in required_packages:
                if (
                    f"{pkg} is not installed" in output
                    or f"package {pkg} is not installed" in output
                ):
                    status["missing_packages"].append(pkg)
                elif pkg not in output:
                    status["missing_packages"].append(pkg)
            # Check if at least one SAP package is installed
            sap_pkg_found = any(
                pkg in output and f"{pkg} is not installed" not in output for pkg in sap_packages
            )
            if not sap_pkg_found:
                status["missing_packages"].append("sap-hana-ha")  # Recommend newer package
            status["packages_installed"] = len(status["missing_packages"]) == 0
        else:
            status["packages_installed"] = False
            status["missing_packages"] = required_packages + ["sap-hana-ha"]

        # Detect RHEL version from /etc/redhat-release
        success, output = self._execute_check_cmd(
            "cat /etc/redhat-release 2>/dev/null", node, method, user
        )
        if success and output:
            # Extract version like "Red Hat Enterprise Linux release 9.5 (Plow)" -> "RHEL 9.5"
            match = re.search(r"release\s+(\d+\.?\d*)", output)
            if match:
                status["rhel_version"] = f"RHEL {match.group(1)}"
            else:
                status["rhel_version"] = output.strip()[:50]  # Fallback to raw output

        # Detect Pacemaker version
        success, output = self._execute_check_cmd(
            "rpm -q pacemaker 2>/dev/null | head -1", node, method, user
        )
        if success and output and "not installed" not in output:
            # Extract version like "pacemaker-2.1.8-3.el9.x86_64" -> "2.1.8"
            match = re.search(r"pacemaker-(\d+\.\d+\.\d+)", output)
            if match:
                status["pacemaker_version"] = match.group(1)
            else:
                status["pacemaker_version"] = output.strip()[:30]

        # If packages are installed, subscription/repos are OK (could be local repo)
        if status["packages_installed"]:
            status["subscription_registered"] = True
            status["repos_enabled"] = True
        else:
            # Check subscription status
            success, output = self._execute_check_cmd(
                "subscription-manager identity 2>/dev/null | grep -qE 'system identity|org ID' && echo 'registered' || "
                "subscription-manager status 2>/dev/null | grep -qE 'Overall Status:' && echo 'registered' || "
                "test -f /etc/yum.repos.d/*.repo && echo 'registered'",
                node,
                method,
                user,
            )
            status["subscription_registered"] = success and "registered" in output

            # Check required repos
            success, output = self._execute_check_cmd(
                "subscription-manager repos --list-enabled 2>/dev/null | grep -E 'highavailability|sap' || "
                "dnf repolist 2>/dev/null | grep -iE 'highavailability|ha|sap'",
                node,
                method,
                user,
            )
            status["repos_enabled"] = success and output.strip() != ""
            if not status["repos_enabled"]:
                status["missing_repos"] = ["highavailability", "sap-solutions"]

        # Check firewall configuration
        success, output = self._execute_check_cmd(
            "firewall-cmd --list-services 2>/dev/null | grep -q high-availability && echo 'configured' || "
            "systemctl is-active firewalld 2>/dev/null | grep -q inactive && echo 'configured'",
            node,
            method,
            user,
        )
        status["firewall_configured"] = success and "configured" in output

        # Check hacluster user password is set (can login)
        success, output = self._execute_check_cmd(
            "getent shadow hacluster 2>/dev/null | grep -v '!' | grep -q ':' && echo 'password_set'",
            node,
            method,
            user,
        )
        status["hacluster_password"] = success and "password_set" in output

        # Check pcsd service running
        success, output = self._execute_check_cmd(
            "systemctl is-active pcsd 2>/dev/null", node, method, user
        )
        status["pcsd_running"] = success and "active" in output

        # Check pcsd service enabled
        success, output = self._execute_check_cmd(
            "systemctl is-enabled pcsd 2>/dev/null", node, method, user
        )
        status["pcsd_enabled"] = success and "enabled" in output

        # Check if nodes are authenticated (known-hosts has multiple nodes)
        # pcs host auth stores tokens in /var/lib/pcsd/known-hosts
        success, output = self._execute_check_cmd(
            "cat /var/lib/pcsd/known-hosts 2>/dev/null | grep -c '\"token\"' || echo '0'",
            node,
            method,
            user,
        )
        try:
            token_count = int(output.strip())
            status["nodes_authenticated"] = token_count >= 2  # At least 2 nodes authenticated
        except (ValueError, AttributeError):
            status["nodes_authenticated"] = False

        # Check if corosync.conf exists (cluster was set up)
        success, output = self._execute_check_cmd(
            "test -f /etc/corosync/corosync.conf && echo 'exists'", node, method, user
        )
        status["corosync_conf_exists"] = success and "exists" in output

        # Check if cib.xml exists (cluster configuration exists - even if not running)
        success, output = self._execute_check_cmd(
            "test -f /var/lib/pacemaker/cib/cib.xml && echo 'exists'", node, method, user
        )
        status["cib_exists"] = success and "exists" in output

        # Check cluster configured and get cluster name
        success, output = self._execute_check_cmd(
            "pcs cluster status 2>/dev/null | head -10", node, method, user
        )
        status["cluster_configured"] = success and "Cluster" in output
        if success:
            # Try to extract cluster name
            match = re.search(r"Cluster name:\s*(\S+)", output)
            if match:
                status["cluster_name"] = match.group(1)

        # Check corosync service
        success, output = self._execute_check_cmd(
            "systemctl is-active corosync 2>/dev/null", node, method, user
        )
        status["corosync_running"] = success and "active" in output

        # Check pacemaker service
        success, output = self._execute_check_cmd(
            "systemctl is-active pacemaker 2>/dev/null", node, method, user
        )
        status["pacemaker_running"] = success and "active" in output

        # Check cluster enabled (auto-start on boot)
        success, output = self._execute_check_cmd(
            "systemctl is-enabled corosync pacemaker 2>/dev/null | grep -q enabled && echo 'enabled'",
            node,
            method,
            user,
        )
        status["cluster_enabled"] = success and "enabled" in output

        # Check if nodes are online
        success, output = self._execute_check_cmd(
            "pcs status nodes 2>/dev/null", node, method, user
        )
        if success:
            status["cluster_online"] = "Online:" in output and output.strip() != ""
            # Extract online nodes - handles both "Online: [ node1 node2 ]" and "Online: node1 node2"
            # Try bracket format first
            match = re.search(r"Online:\s*\[\s*(.*?)\s*\]", output)
            if match:
                status["cluster_nodes"] = [n.strip() for n in match.group(1).split() if n.strip()]
            else:
                # Try space-separated format: "Online: node1 node2"
                match = re.search(r"Online:\s*(.+?)(?:\n|$)", output)
                if match:
                    nodes = match.group(1).strip()
                    # Filter out empty strings and common non-node words
                    status["cluster_nodes"] = [
                        n.strip()
                        for n in nodes.split()
                        if n.strip() and n.strip() not in ["Standby:", "Offline:", "Maintenance:"]
                    ]

        # Check STONITH enabled (default is true if not explicitly set in modern pacemaker)
        success, output = self._execute_check_cmd(
            "pcs property show stonith-enabled 2>/dev/null", node, method, user
        )
        # If stonith-enabled is explicitly set to false, it's disabled
        # If not set or set to true, it's enabled
        if success:
            if "false" in output.lower():
                status["stonith_enabled"] = False
            else:
                # Check if stonith devices exist (if they do, stonith is effectively enabled)
                stonith_check, stonith_out = self._execute_check_cmd(
                    "pcs stonith status 2>/dev/null | grep -E 'Started|Stopped'", node, method, user
                )
                status["stonith_enabled"] = stonith_check and stonith_out.strip() != ""

        # Check STONITH configured and running
        success, output = self._execute_check_cmd(
            "pcs stonith status 2>/dev/null", node, method, user
        )
        if success:
            if "NO stonith" in output or "no stonith" in output.lower():
                status["stonith_configured"] = False
                status["stonith_enabled"] = False
            elif "Started" in output:
                status["stonith_configured"] = True
            elif "Stopped" in output or "disabled" in output.lower():
                # STONITH device exists but is disabled/stopped
                status["stonith_configured"] = False
                status["stonith_disabled"] = True
            else:
                status["stonith_configured"] = False

        # Check HANA installed
        success, output = self._execute_check_cmd(
            "ls -d /usr/sap/*/HDB[0-9][0-9] 2>/dev/null | head -1", node, method, user
        )
        status["hana_installed"] = success and "/usr/sap/" in output

        # Check HANA resources
        success, output = self._execute_check_cmd(
            "pcs resource status 2>/dev/null | grep -i saphana", node, method, user
        )
        status["hana_resources"] = success and "SAPHana" in output

        return status
