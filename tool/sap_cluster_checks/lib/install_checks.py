"""
SAP Pacemaker Cluster Health Check - Installation Check Primitives

Reusable functions for checking installation status of cluster components.
Provides make_status_dict() for creating the standard status dictionary template.
"""


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
        "standby_nodes": [],
        "offline_nodes": [],
        # Version info
        "rhel_version": None,
        "pacemaker_version": None,
    }
