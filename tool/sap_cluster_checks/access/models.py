"""
SAP Pacemaker Cluster Health Check - Access Data Models

Dataclass definitions for access discovery:
- NodeAccess: access information for a single cluster node
- AccessConfig: configuration for cluster access discovery
"""

from typing import Dict, Optional

from ..lib.compat import dataclass, asdict  # noqa: F401 - asdict re-exported for discover_access


@dataclass
class NodeAccess:
    """Represents access information for a single node."""

    hostname: str = None
    ssh_reachable: bool = False
    ssh_user: Optional[str] = None
    ansible_reachable: bool = False
    ansible_host: Optional[str] = None
    ansible_user: Optional[str] = None
    sosreport_path: Optional[str] = None
    preferred_method: Optional[str] = None  # 'ssh', 'ansible', 'sosreport'
    last_checked: Optional[str] = None
    machine_id: Optional[str] = None  # Unique host identifier from /etc/machine-id


@dataclass
class AccessConfig:
    """Configuration for cluster access discovery."""

    ansible_inventory_source: Optional[str] = None
    ansible_inventory_path: Optional[str] = None
    sosreport_directory: Optional[str] = None
    hosts_file: Optional[str] = None
    nodes: Dict[str, dict] = None
    clusters: Dict[str, dict] = None  # cluster_name -> {nodes: [], discovered_from: host}
    discovery_timestamp: Optional[str] = None
    discovery_complete: bool = False

    def __post_init__(self):
        if self.nodes is None:
            self.nodes = {}
        if self.clusters is None:
            self.clusters = {}
