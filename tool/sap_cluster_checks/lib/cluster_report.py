"""
Unified Cluster Report Data Model

This module defines the ClusterReportData dataclass that captures ALL data
needed for generating consistent reports, regardless of data source
(SSH live, SOSreport, local execution).

Usage:
    from lib.cluster_report import ClusterReportData

    # Build report data from health check
    report_data = ClusterReportData(
        cluster_name="my_cluster",
        nodes=["node1", "node2"],
        ...
    )

    # Serialize to dict for YAML
    from dataclasses import asdict
    yaml_data = asdict(report_data)

    # Load from YAML
    report_data = ClusterReportData.from_dict(yaml_data)
"""

from datetime import datetime
from typing import Dict, List, Any

# Python 3.6 compatibility for dataclasses
try:
    from dataclasses import dataclass, asdict
except ImportError:
    # Fallback for Python < 3.7
    def field(default=None, default_factory=None):
        return default_factory() if default_factory else default

    def asdict(obj):
        """Convert dataclass to dict."""
        result = {}
        for key in obj.__annotations__:
            value = getattr(obj, key, None)
            if hasattr(value, "__annotations__"):
                result[key] = asdict(value)
            elif isinstance(value, list):
                result[key] = [asdict(v) if hasattr(v, "__annotations__") else v for v in value]
            elif isinstance(value, dict):
                result[key] = {
                    k: asdict(v) if hasattr(v, "__annotations__") else v for k, v in value.items()
                }
            else:
                result[key] = value
        return result

    def dataclass(cls):
        """Simple dataclass decorator fallback."""
        original_annotations = getattr(cls, "__annotations__", {})

        def __init__(self, **kwargs):
            # Set defaults from class annotations first
            for name in original_annotations:
                default = getattr(cls, name, None)
                if callable(default) and not isinstance(default, type):
                    default = default()
                setattr(self, name, default)
            # Override with provided kwargs
            for key, value in kwargs.items():
                setattr(self, key, value)
            # Call __post_init__ if defined
            if hasattr(self, "__post_init__"):
                self.__post_init__()

        cls.__init__ = __init__
        cls.__annotations__ = original_annotations
        return cls


# Report format version for backwards compatibility
REPORT_VERSION = "1.0.0"


@dataclass
class ClusterReportData:
    """
    Unified data model for SAP HANA cluster health check reports.

    This dataclass captures ALL data needed to generate consistent reports,
    regardless of whether the data came from:
    - Live SSH access
    - SOSreport analysis
    - Local execution on cluster node
    - Ansible

    The data can be serialized to YAML and later deserialized to regenerate
    reports with full fidelity.
    """

    # =========================================================================
    # METADATA
    # =========================================================================
    version: str = REPORT_VERSION
    timestamp: str = None

    # =========================================================================
    # DATA SOURCE INFORMATION
    # =========================================================================
    data_source: str = "Unknown"  # Human-readable description
    access_method: str = "unknown"  # ssh, sosreport, local, ansible
    used_cib_xml: bool = False  # True if parsed cib.xml (cluster was stopped)
    cluster_running: bool = True  # False if cluster services not running
    hana_resource_state: str = None  # running/stopped/disabled/unmanaged/absent/unknown
    hana_db_status: Dict[str, Any] = None  # HANA DB running state, managed flag, SR info

    # =========================================================================
    # CLUSTER INFORMATION
    # =========================================================================
    cluster_name: str = "Unknown"
    cluster_type: str = "Scale-Up"  # Scale-Up or Scale-Out
    nodes: List[str] = None
    majority_makers: List[str] = None

    # OS/Software versions
    rhel_version: str = None
    pacemaker_version: str = None
    resource_agent: str = None  # e.g. 'resource-agents-sap-hana-0.162.1 (legacy)'

    # =========================================================================
    # SAP HANA CONFIGURATION
    # =========================================================================
    sid: str = None
    instance_number: str = None
    virtual_ip: str = None
    secondary_vip: str = None
    replication_mode: str = None  # sync, syncmem, async
    operation_mode: str = None  # logreplay, delta_datashipping
    secondary_read: bool = None

    # Node configuration
    node1_hostname: str = None
    node1_ip: str = None
    node2_hostname: str = None
    node2_ip: str = None
    sites: Dict[str, Any] = None

    # =========================================================================
    # HA PARAMETERS
    # =========================================================================
    prefer_site_takeover: bool = None
    automated_register: bool = None
    duplicate_primary_timeout: int = None
    migration_threshold: int = None

    # =========================================================================
    # RESOURCE CONFIGURATION
    # =========================================================================
    resource_type: str = None  # SAPHana or SAPHanaController
    resource_name: str = None
    topology_resource: str = None
    vip_resource: str = None
    secondary_vip_resource: str = None

    # STONITH/Fencing
    stonith_device: str = None
    stonith_params: Dict[str, Any] = None

    # Full resource configuration from CIB (for detailed report section)
    resource_config: Dict[str, Any] = None

    # =========================================================================
    # INSTALLATION STATUS
    # =========================================================================
    install_status: Dict[str, Any] = None

    # =========================================================================
    # CHECK RESULTS
    # =========================================================================
    results: List[Dict[str, Any]] = None
    summary: Dict[str, Any] = None

    def __post_init__(self):
        """Initialize default values for mutable fields."""
        if self.timestamp is None:
            self.timestamp = datetime.now().isoformat()
        if self.nodes is None:
            self.nodes = []
        if self.majority_makers is None:
            self.majority_makers = []
        if self.sites is None:
            self.sites = {}
        if self.stonith_params is None:
            self.stonith_params = {}
        if self.resource_config is None:
            self.resource_config = {}
        if self.hana_db_status is None:
            self.hana_db_status = {}
        if self.install_status is None:
            self.install_status = {}
        if self.results is None:
            self.results = []
        if self.summary is None:
            self.summary = {}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ClusterReportData":
        """
        Create ClusterReportData from a dictionary (e.g., loaded from YAML).

        Args:
            data: Dictionary containing report data

        Returns:
            ClusterReportData instance
        """
        # Filter to only known fields
        known_fields = set(cls.__annotations__.keys())
        filtered_data = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered_data)

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to dictionary for YAML serialization.

        Returns:
            Dictionary representation suitable for YAML export
        """
        return asdict(self)

    def to_cluster_info(self) -> Dict[str, Any]:
        """
        Convert to cluster_info dict format for PDF generator compatibility.

        This provides backward compatibility with the existing
        generate_health_check_report() function.

        Returns:
            cluster_info dict as expected by report_generator.py
        """
        cluster_info = {
            # Core cluster info
            "cluster_name": self.cluster_name,
            "nodes": self.nodes,
            "cluster_type": self.cluster_type,
            "majority_makers": self.majority_makers,
            # Data source
            "data_source": self.data_source,
            "access_method": self.access_method,
            "used_cib_xml": self.used_cib_xml,
            "cluster_running": self.cluster_running,
            "hana_resource_state": self.hana_resource_state,
            "hana_db_status": self.hana_db_status,
            # OS/Software versions
            "rhel_version": self.rhel_version,
            "pacemaker_version": self.pacemaker_version,
            "resource_agent": self.resource_agent,
            # SAP HANA config
            "sid": self.sid,
            "instance_number": self.instance_number,
            "virtual_ip": self.virtual_ip,
            "secondary_vip": self.secondary_vip,
            "replication_mode": self.replication_mode,
            "operation_mode": self.operation_mode,
            "secondary_read": self.secondary_read,
            # Node configuration
            "node1_hostname": self.node1_hostname,
            "node1_ip": self.node1_ip,
            "node2_hostname": self.node2_hostname,
            "node2_ip": self.node2_ip,
            "sites": self.sites,
            # HA parameters
            "prefer_site_takeover": self.prefer_site_takeover,
            "automated_register": self.automated_register,
            "duplicate_primary_timeout": self.duplicate_primary_timeout,
            "migration_threshold": self.migration_threshold,
            # Resource configuration
            "resource_type": self.resource_type,
            "resource_name": self.resource_name,
            "topology_resource": self.topology_resource,
            "vip_resource": self.vip_resource,
            "secondary_vip_resource": self.secondary_vip_resource,
            # STONITH
            "stonith_device": self.stonith_device,
            "stonith_params": self.stonith_params,
            # CIB resource config
            "resource_config": self.resource_config,
        }

        return cluster_info

    def get_summary_dict(self) -> Dict[str, Any]:
        """
        Get summary statistics dict for PDF generator.

        Returns:
            Summary dict as expected by report_generator.py
        """
        return self.summary.copy() if self.summary else {}

    def get_results_list(self) -> List[Dict[str, Any]]:
        """
        Get results list for PDF generator.

        Returns:
            Results list as expected by report_generator.py
        """
        return self.results.copy() if self.results else []

    def get_install_status(self) -> Dict[str, Any]:
        """
        Get installation status dict for PDF generator.

        Returns:
            Install status dict as expected by report_generator.py
        """
        return self.install_status.copy() if self.install_status else {}
