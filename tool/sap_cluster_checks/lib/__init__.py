"""
SAP Pacemaker Cluster Health Check - Library modules.

This package contains modular components:
- utils: Utility functions (resource scanning, SOSreport extraction, update checking)
- installation: Installation guides and step suggestions
- interactive: Interactive startup and usage scanning
- cib_parser: Unified CIB (cib.xml) parser for cluster configuration
- cluster_report: Unified data model for report generation
"""

from .utils import (
    scan_for_resources,
    extract_sosreports_parallel,
    check_for_updates,
)

from .installation import (
    get_redhat_doc_urls,
    print_guide,
    print_steps,
    print_suggestions,
)

from .interactive import (
    interactive_startup,
    run_usage_scan,
)

from .cib_parser import CIBParser

from .cluster_report import (
    ClusterReportData,
    REPORT_VERSION,
)

from .install_status import InstallStatusMixin
from .install_guide import InstallGuideMixin
from .hana_status import HanaStatusMixin

__all__ = [
    # utils
    "scan_for_resources",
    "extract_sosreports_parallel",
    "check_for_updates",
    # installation
    "get_redhat_doc_urls",
    "print_guide",
    "print_steps",
    "print_suggestions",
    # interactive
    "interactive_startup",
    "run_usage_scan",
    # cib_parser
    "CIBParser",
    # cluster_report
    "ClusterReportData",
    "REPORT_VERSION",
    # install_status
    "InstallStatusMixin",
    # install_guide
    "InstallGuideMixin",
    # hana_status
    "HanaStatusMixin",
]
