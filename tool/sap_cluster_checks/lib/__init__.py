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
    SCRIPT_DIR,
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
    print_usage_help,
)

from .cib_parser import CIBParser

from .cluster_report import (
    ClusterReportData,
    REPORT_VERSION,
)

from .install_checks import make_status_dict
from .install_status import InstallStatusMixin
from .install_guide import InstallGuideMixin
from .hana_status import HanaStatusMixin

__all__ = [
    # utils
    "scan_for_resources",
    "extract_sosreports_parallel",
    "check_for_updates",
    "SCRIPT_DIR",
    # installation
    "get_redhat_doc_urls",
    "print_guide",
    "print_steps",
    "print_suggestions",
    # interactive
    "interactive_startup",
    "run_usage_scan",
    "print_usage_help",
    # cib_parser
    "CIBParser",
    # cluster_report
    "ClusterReportData",
    "REPORT_VERSION",
    # install_checks
    "make_status_dict",
    # install_status
    "InstallStatusMixin",
    # install_guide
    "InstallGuideMixin",
    # hana_status
    "HanaStatusMixin",
]
