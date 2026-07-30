"""
SAP Pacemaker Cluster Health Check - Installation Check Primitives

Reusable functions for checking installation status of cluster components.
Provides CommandExecutor for running commands on nodes and make_status_dict()
for creating the standard status dictionary template.
"""

import subprocess


class CommandExecutor:
    """Wraps SSH/local command execution for installation checks."""

    def __init__(self, debug: bool = False):
        self.debug = debug

    def execute(self, cmd: str, node: str, method: str, user: str = None) -> tuple:
        """Execute a command on a node and return (success, output)."""
        # Copy the EXACT logic from _execute_check_cmd (lines 811-850 of cli.py)
        # but use self.debug instead of self.debug (same thing)
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


def make_status_dict(node: str = None, method: str = None) -> dict:
    """Create the standard installation status dictionary template.

    This template is shared between live checks and SOSreport checks.
    """
    return {
        # Phase 1: Prerequisites
        "subscription_registered": None,
        "repos_enabled": None,
        "firewall_configured": None,
        "packages_installed": None,
        "hacluster_password": None,
        "pcsd_running": None,
        "pcsd_enabled": None,
        # Phase 2: Cluster Creation
        "nodes_authenticated": None,
        "corosync_conf_exists": None,
        "cib_exists": None,
        "cluster_configured": None,
        "corosync_running": None,
        "pacemaker_running": None,
        "cluster_enabled": None,
        "cluster_online": None,
        # Phase 3: Fencing & Resources
        "stonith_enabled": None,
        "stonith_configured": None,
        "stonith_disabled": False,
        "hana_installed": None,
        "hana_resources": None,
        # Details
        "missing_packages": [],
        "missing_repos": [],
        "node": node,
        "method": method,
        "cluster_name": None,
        "cluster_nodes": [],
        "offline_nodes": [],
        # Version info
        "rhel_version": None,
        "pacemaker_version": None,
    }
