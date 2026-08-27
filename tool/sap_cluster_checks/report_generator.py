"""
PDF Report Generator for SAP HANA Cluster Health Check
Following Red Hat Documentation Style Guidelines
"""

import re
import sys
from datetime import datetime
from typing import Dict, List

from .lib.installation import get_redhat_doc_urls

# fpdf2 is optional - PDF generation will be skipped if not available
FPDF_AVAILABLE = False
try:
    from fpdf import FPDF

    FPDF_AVAILABLE = True
except ImportError:
    # Create a dummy base class so the module can be imported
    class FPDF:
        """Dummy FPDF class when fpdf2 is not installed."""

        def __init__(self, *args, **kwargs):  # pylint: disable=redefined-outer-name
            raise ImportError("PDF generation requires fpdf2. Install with: pip install fpdf2")


def is_pdf_available() -> bool:
    """Check if PDF generation is available (fpdf2 installed)."""
    return FPDF_AVAILABLE


def is_valid_ip(value: str) -> bool:
    """Check if a string is a valid IPv4 or IPv6 address."""
    if not value:
        return False

    # IPv4 pattern: x.x.x.x where x is 0-255
    ipv4_pattern = r"^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$"
    # IPv6 simplified check (colons present, hex chars)
    ipv6_pattern = r"^(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}$|^::1$|^::$"
    return bool(re.match(ipv4_pattern, value) or re.match(ipv6_pattern, value))


class RedHatColors:
    """Red Hat brand colors"""

    RED = (204, 0, 0)  # Red Hat Red
    DARK_RED = (163, 0, 0)  # Darker red for headers
    BLACK = (21, 21, 21)  # Near black
    GRAY = (102, 102, 102)  # Medium gray
    LIGHT_GRAY = (240, 240, 240)  # Background gray
    WHITE = (255, 255, 255)
    GREEN = (63, 156, 53)  # Success green
    YELLOW = (236, 178, 0)  # Warning yellow
    ORANGE = (255, 140, 0)  # Incomplete/in-progress orange
    BLUE = (0, 102, 204)  # Link blue


class NeutralColors:
    """Corporate-neutral blue/gray palette (non-branded alternative)"""

    RED = (0, 90, 156)  # Primary blue (replaces red accents)
    DARK_RED = (0, 62, 110)  # Darker blue for headers
    BLACK = (33, 37, 41)  # Near black
    GRAY = (108, 117, 125)  # Medium gray
    LIGHT_GRAY = (241, 243, 245)  # Background gray
    WHITE = (255, 255, 255)
    GREEN = (40, 167, 69)  # Success green
    YELLOW = (255, 193, 7)  # Warning yellow
    ORANGE = (253, 126, 20)  # Incomplete/in-progress orange
    BLUE = (0, 123, 255)  # Link blue


# --- Color scheme selection ---
# Change this alias to switch the PDF report color scheme.
# Default: RedHatColors (Red Hat brand colors)
# Alternative: NeutralColors (corporate-neutral blue/gray palette)
PdfColors = RedHatColors


class HealthCheckPDF(FPDF):
    """PDF Report Generator following Red Hat style guidelines"""

    def __init__(self, cluster_name: str = "Unknown", report_date: str = None):
        super().__init__()
        self.cluster_name = cluster_name
        self.report_date = report_date or datetime.now().strftime("%Y-%m-%d %H:%M")
        self.set_auto_page_break(auto=True, margin=20)

    def header(self):
        """Page header with Red Hat styling"""
        # Check if cluster name is long (needs two-line header)
        show_cluster = (
            self.cluster_name
            and self.cluster_name != "Unknown"
            and self.cluster_name != "Unknown Cluster"
        )
        cluster_name_long = show_cluster and len(self.cluster_name) > 20

        # Red header bar (taller if two-line)
        header_height = 18 if cluster_name_long else 12
        self.set_fill_color(*PdfColors.RED)
        self.rect(0, 0, 210, header_height, "F")

        # Header text - first line
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*PdfColors.WHITE)
        self.set_xy(10, 3)
        self.cell(0, 6, "SAP HANA Cluster Health Check Report", align="L")

        # Cluster name in the middle (if short enough for one line)
        if show_cluster and not cluster_name_long:
            self.set_xy(70, 3)
            self.set_font("Helvetica", "B", 9)
            self.cell(70, 6, f"Cluster: {self.cluster_name}", align="C")

        # Date on the right
        self.set_xy(-60, 3)
        self.set_font("Helvetica", "", 8)
        self.cell(50, 6, self.report_date, align="R")

        # Second line for long cluster name
        if cluster_name_long:
            self.set_xy(10, 10)
            self.set_font("Helvetica", "B", 9)
            self.cell(0, 6, f"Cluster: {self.cluster_name}", align="C")

        self.ln(header_height + 3)

    def footer(self):
        """Page footer"""
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(*PdfColors.GRAY)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

    def chapter_title(self, title: str):
        """Section header with Red Hat styling"""
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(*PdfColors.DARK_RED)
        self.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")
        # Underline
        self.set_draw_color(*PdfColors.RED)
        self.set_line_width(0.5)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(5)

    def sub_section(self, title: str):
        """Subsection header"""
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(*PdfColors.BLACK)
        self.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")

    def body_text(self, text: str):
        """Regular body text"""
        self.set_font("Helvetica", "", 10)
        self.set_text_color(*PdfColors.BLACK)
        self.multi_cell(0, 5, text)
        self.ln(2)

    def status_badge(self, status: str, x: float = None, y: float = None):
        """Draw a colored status badge"""
        if x is not None:
            self.set_x(x)
        if y is not None:
            self.set_y(y)

        colors = {
            "PASSED": PdfColors.GREEN,
            "FAILED": PdfColors.RED,
            "WARNING": PdfColors.YELLOW,
            "SKIPPED": PdfColors.GRAY,
            "ERROR": PdfColors.RED,
            "OK": PdfColors.GREEN,
            "CRITICAL": PdfColors.RED,
            "CRITICAL - INCOMPLETE": PdfColors.RED,
            "FAILED - INCOMPLETE": PdfColors.ORANGE,
            "INCOMPLETE": PdfColors.ORANGE,
            "NEEDS ATTENTION": PdfColors.YELLOW,
            "HEALTHY": PdfColors.GREEN,
        }

        color = colors.get(status.upper(), PdfColors.GRAY)

        self.set_fill_color(*color)
        self.set_text_color(*PdfColors.WHITE)
        self.set_font("Helvetica", "B", 8)

        width = self.get_string_width(status) + 6
        self.cell(width, 6, status, fill=True, align="C")
        self.set_text_color(*PdfColors.BLACK)

    def info_table(self, data: Dict[str, str]):
        """Draw an info table with key-value pairs"""
        self.set_font("Helvetica", "", 10)
        col_width = 50

        for key, value in data.items():
            self.set_font("Helvetica", "B", 10)
            self.set_text_color(*PdfColors.GRAY)
            self.cell(col_width, 7, f"{key}:", align="L")

            self.set_font("Helvetica", "", 10)
            self.set_text_color(*PdfColors.BLACK)
            self.cell(0, 7, str(value), new_x="LMARGIN", new_y="NEXT")

    def check_result_row(
        self, check_id: str, description: str, status: str, message: str = "", node: str = ""
    ):
        """Draw a check result row"""
        # Background for alternating rows
        y_start = self.get_y()

        # Status badge
        self.status_badge(status)
        self.set_xy(30, y_start)

        # Check ID
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*PdfColors.BLACK)
        self.cell(45, 6, check_id, ln=False)

        # Description
        self.set_font("Helvetica", "", 9)
        self.cell(
            0,
            6,
            description[:50] + ("..." if len(description) > 50 else ""),
            new_x="LMARGIN",
            new_y="NEXT",
        )

        # Message and node if present
        if message or node:
            self.set_x(30)
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(*PdfColors.GRAY)
            prefix = f"Node: {node} | " if node else ""
            if message:
                # Show full message with word-wrap (multi_cell handles line breaks)
                self.multi_cell(170, 4, prefix + message)
            else:
                label = prefix[:-3] if prefix.endswith(" | ") else prefix
                self.cell(0, 5, label, new_x="LMARGIN", new_y="NEXT")

        self.ln(2)

    def command_block(self, command: str, description: str = ""):
        """Draw a command block (code style)"""
        if description:
            self.set_font("Helvetica", "I", 9)
            self.set_text_color(*PdfColors.GRAY)
            self.cell(0, 5, f"# {description}", new_x="LMARGIN", new_y="NEXT")

        # Command background
        self.set_fill_color(*PdfColors.LIGHT_GRAY)
        self.set_font("Courier", "", 9)
        self.set_text_color(*PdfColors.BLACK)

        # Handle multi-line commands
        lines = command.split("\n")
        for line in lines:
            self.cell(0, 6, f"  {line}", fill=True, new_x="LMARGIN", new_y="NEXT")
        self.ln(3)

    def recommendation_box(
        self, priority: str, title: str, description: str, commands: List[str] = None
    ):
        """Draw a recommendation box"""
        y_start = self.get_y()

        # Priority indicator
        priority_colors = {
            "1": PdfColors.RED,
            "2": PdfColors.YELLOW,
            "3": PdfColors.BLUE,
        }
        color = priority_colors.get(priority, PdfColors.GRAY)

        # Left border
        self.set_draw_color(*color)
        self.set_line_width(2)
        self.line(10, y_start, 10, y_start + 20)

        # Title
        self.set_x(15)
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*PdfColors.BLACK)
        self.cell(0, 6, f"Priority {priority}: {title}", new_x="LMARGIN", new_y="NEXT")

        # Description
        self.set_x(15)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*PdfColors.GRAY)
        self.multi_cell(180, 5, description)

        # Commands
        if commands:
            self.ln(2)
            for cmd in commands:
                self.set_x(15)
                self.command_block(cmd)

        self.ln(5)


def _strip_pkg_prefix(version_str, _pkg_key):
    """Strip the package name prefix from an RPM version string.

    RPM versions follow the pattern 'name-version-release.dist'.
    Since the package name is shown in the row label, we only need
    the version-release part in the table cells.

    Examples:
        corosync-3.1.5-2.el8        -> 3.1.5-2.el8
        pacemaker-2.1.5-9.el8_9.3   -> 2.1.5-9.el8_9.3
        sap-hana-ha-1.0.2-3.el9     -> 1.0.2-3.el9
        not installed                -> not installed
    """
    if not version_str or version_str in ("not installed", "N/A"):
        return version_str
    # Match: everything up to the first hyphen followed by a digit
    m = re.match(r"^[a-zA-Z][a-zA-Z0-9_-]*?-(\d.*)$", version_str)
    if m:
        return m.group(1)
    return version_str


def _render_version_table(pdf, check):
    """Render a version comparison table for CHK_PACKAGE_CONSISTENCY results.

    Shows a table with package names as rows and nodes as columns,
    displaying version differences side-by-side for easy comparison.
    Only renders when the check has a version_table in its details.
    """
    if check.get("check_id") != "CHK_PACKAGE_CONSISTENCY":
        return
    details = check.get("details", {})
    version_table = details.get("version_table")
    if not version_table:
        return

    # Collect all nodes from the version_table values
    all_nodes = []
    for pkg_versions in version_table.values():
        for node in pkg_versions:
            if node not in all_nodes:
                all_nodes.append(node)

    if not all_nodes:
        return

    # Friendly display names for package keys
    pkg_display_names = {
        "pacemaker_version": "pacemaker",
        "corosync_version": "corosync",
        "sap_hana_ha_version": "sap-hana-ha",
        "resource_agents_sap_hana": "resource-agents-sap-hana",
        "resource_agents_sap_hana_scaleout": "res-agents-sap-hana-scaleout",
        "saphanasr_version": "SAPHanaSR",
    }

    # Adapt layout for node count
    num_nodes = len(all_nodes)
    pkg_col_width = 52
    remaining = 186 - pkg_col_width  # 186 = usable width (14mm left margin)
    node_col_width = remaining / num_nodes
    # Use smaller font when many nodes make columns tight
    version_font_size = 7 if node_col_width >= 28 else 6

    # Check if we need a page break (estimate table height)
    row_height = 5
    table_height = (len(version_table) + 1) * row_height + 8
    if pdf.get_y() + table_height > 270:
        pdf.add_page()

    pdf.ln(2)
    pdf.set_x(14)

    # Table header
    pdf.set_fill_color(240, 240, 240)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(*PdfColors.BLACK)
    pdf.cell(pkg_col_width, row_height, "Package", border=1, fill=True)
    for node in all_nodes:
        display_node = node[:12] if len(node) > 12 else node
        pdf.cell(node_col_width, row_height, display_node, border=1, fill=True, align="C")
    pdf.ln()

    # Table rows — show version-release only (strip package name prefix)
    for pkg_key, node_versions in version_table.items():
        pdf.set_x(14)
        display_name = pkg_display_names.get(pkg_key, pkg_key)
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_text_color(*PdfColors.BLACK)
        pdf.cell(pkg_col_width, row_height, display_name, border=1)

        # Determine if values differ (to highlight)
        values = list(node_versions.values())
        has_diff = len(set(str(v) for v in values)) > 1

        ref_value = node_versions.get(all_nodes[0], "N/A")
        pdf.set_font("Courier", "", version_font_size)
        for node in all_nodes:
            version = node_versions.get(node, "N/A")
            version_str = str(version) if version else "not installed"
            # Strip package name prefix — row label already shows it
            version_str = _strip_pkg_prefix(version_str, pkg_key)
            # Highlight cells that differ from the reference node
            if has_diff and str(version) != str(ref_value):
                pdf.set_text_color(*PdfColors.RED)
            else:
                pdf.set_text_color(*PdfColors.BLACK)
            pdf.cell(node_col_width, row_height, version_str, border=1, align="C")
        pdf.ln()

    pdf.set_text_color(*PdfColors.BLACK)
    pdf.ln(2)


def generate_health_check_report(  # pylint: disable=redefined-outer-name
    results: List[Dict],
    summary: Dict,
    cluster_info: Dict,
    output_path: str = None,
    install_status: Dict = None,
    verbose: bool = False,
) -> str:
    """
    Generate a PDF health check report.

    Args:
        results: List of check results
        summary: Summary statistics
        cluster_info: Cluster information (name, nodes, etc.)
        output_path: Output file path (optional)
        install_status: Installation status dict (optional)
        verbose: If True, show all checks in detail (not just failed/warnings)

    Returns:
        Path to generated PDF file

    Raises:
        ImportError: If fpdf2 is not installed
    """
    if not FPDF_AVAILABLE:
        raise ImportError("PDF generation requires fpdf2. Install with: pip install fpdf2")

    cluster_name = cluster_info.get("cluster_name", "Unknown Cluster")
    nodes = cluster_info.get("nodes", [])

    pdf = HealthCheckPDF(cluster_name=cluster_name)
    pdf.alias_nb_pages()
    pdf.add_page()

    # =========================================================================
    # EXECUTIVE SUMMARY
    # =========================================================================
    pdf.chapter_title("Executive Summary")

    # Overall status
    total = summary.get("total", 0)
    passed = summary.get("passed", 0)
    failed = summary.get("failed", 0)
    warnings = summary.get("warning_count", 0)
    critical = summary.get("critical_count", 0)

    # Check installation completeness
    install_complete = True
    steps_done = 0
    steps_total = 7
    missing_steps = []
    if install_status:
        steps_done = sum(
            1
            for v in [
                install_status.get("subscription_registered"),
                install_status.get("repos_enabled"),
                install_status.get("packages_installed"),
                install_status.get("pcsd_running"),
                install_status.get("cluster_configured"),
                install_status.get("stonith_configured"),
                install_status.get("hana_resources"),
            ]
            if v
        )
        install_complete = steps_done >= steps_total

        # Build list of missing steps
        if not install_status.get("stonith_configured"):
            missing_steps.append("stonith")
        if not install_status.get("hana_resources"):
            missing_steps.append("hana_resources")
        if not install_status.get("cluster_configured"):
            missing_steps.append("cluster")

    # Determine overall status considering both checks and installation
    hana_resource_state = cluster_info.get("hana_resource_state")
    resources_not_managed = hana_resource_state in ("stopped", "disabled", "unmanaged")

    status_descriptions = {
        "CRITICAL - INCOMPLETE": "Critical issues found and installation incomplete",
        "FAILED - INCOMPLETE": "Failed checks and installation incomplete",
        "CRITICAL": "Critical issues found, installation complete",
        "NEEDS ATTENTION": "Failed checks found, installation complete",
        "INCOMPLETE": "No failures, but installation incomplete",
        "WARNING": "Warnings only, no critical issues",
        "HEALTHY": "All checks passed, cluster fully configured",
    }

    if critical > 0 and not install_complete:
        overall_status = "CRITICAL - INCOMPLETE"
    elif critical > 0:
        overall_status = "CRITICAL"
    elif failed > 0 and not install_complete:
        overall_status = "FAILED - INCOMPLETE"
    elif failed > 0:
        overall_status = "NEEDS ATTENTION"
    elif not install_complete:
        overall_status = "INCOMPLETE"
    elif resources_not_managed:
        overall_status = "WARNING"
        status_descriptions["WARNING"] = (
            f"HANA resource is {hana_resource_state} - not managed by Pacemaker"
        )
    elif warnings > 0:
        overall_status = "WARNING"
    else:
        overall_status = "HEALTHY"

    # Status summary table
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(40, 8, "Overall Status: ")
    pdf.status_badge(overall_status)
    pdf.ln(6)
    # Add status description in smaller font
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(*PdfColors.GRAY)
    pdf.cell(0, 5, status_descriptions.get(overall_status, ""), ln=True)
    pdf.set_text_color(*PdfColors.BLACK)
    pdf.ln(4)

    # Build info table with data source information
    data_source = cluster_info.get("data_source", "Unknown")
    used_cib_xml = cluster_info.get("used_cib_xml", False)

    info_data = {
        "Cluster Name": cluster_name,
        "Nodes": ", ".join(nodes) if nodes else "N/A",
        "RHEL Version": cluster_info.get("rhel_version", "N/A"),
        "Resource Agent": cluster_info.get("resource_agent", "N/A"),
        "Data Source": data_source,
        "Report Date": datetime.now().strftime("%d %B %Y, %H:%M"),
        "Total Checks": str(total),
        "Passed": str(passed),
        "Failed": str(failed),
        "Critical Issues": str(critical),
        "Warnings": str(warnings),
    }

    pdf.info_table(info_data)

    pdf.ln(5)

    # =========================================================================
    # DATA SOURCE INFO BOX (SOSreport / cib.xml usage)
    # =========================================================================
    access_method = cluster_info.get("access_method", "unknown")
    cluster_running = cluster_info.get("cluster_running", True)

    if access_method == "sosreport":
        # Info box for sosreport analysis
        # Only show "cluster was stopped" when cib.xml was used AND cluster was not running
        if used_cib_xml and not cluster_running:
            # Yellow warning - cluster was stopped when sosreport was taken
            pdf.set_fill_color(255, 243, 205)  # Light yellow background
            pdf.set_draw_color(255, 193, 7)  # Yellow border
            pdf.set_text_color(133, 100, 4)  # Dark yellow/brown text
            box_title = "INFO: Analyzing SOSreport (cluster was stopped)"
            box_text = (
                "This analysis is based on SOSreport data. The cluster was not running when "
                "the SOSreport was collected, so cluster configuration was read from cib.xml. "
                "Some checks (node status, quorum, resource status) reflect the offline state."
            )
        else:
            # Blue info - sosreport analysis
            pdf.set_fill_color(217, 237, 247)  # Light blue background
            pdf.set_draw_color(49, 112, 143)  # Blue border
            pdf.set_text_color(31, 78, 121)  # Dark blue text
            box_title = "INFO: Analyzing SOSreport (offline data)"
            box_text = (
                "This analysis is based on SOSreport data collected from cluster nodes. "
                "No live SSH access was used. Results reflect the cluster state at the time "
                "the SOSreport was collected."
            )

        pdf.set_line_width(0.5)
        pdf.set_font("Helvetica", "B", 11)

        y_start = pdf.get_y()
        pdf.rect(10, y_start, 190, 24, "DF")
        pdf.set_xy(15, y_start + 3)
        pdf.cell(0, 6, box_title, ln=True)

        pdf.set_xy(15, y_start + 10)
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(180, 4, box_text)

        pdf.set_text_color(*PdfColors.BLACK)
        pdf.set_line_width(0.2)
        pdf.ln(8)

    # =========================================================================
    # CLUSTER NOT RUNNING WARNING (for live access AND SOSreports)
    # =========================================================================
    cluster_running = cluster_info.get("cluster_running", True)

    # Also check install_status for live systems
    if install_status and access_method != "sosreport":
        # Check if cluster is configured but not running
        has_config = install_status.get("corosync_conf_exists") or install_status.get("cib_exists")
        pacemaker_running = install_status.get("pacemaker_running")
        if has_config and not pacemaker_running:
            cluster_running = False

    if not cluster_running:
        # Determine warning message based on access method
        if access_method == "sosreport":
            warning_title = "WARNING: Cluster Was Not Running When SOSreport Was Captured"
            warning_text = (
                "The SOSreport was collected while Pacemaker was not running. Health check results "
                "may be incomplete or inaccurate. Checks that require live cluster data (quorum, "
                "node status, resource status, replication status) will report ERROR status. "
                "Consider creating new SOSreports with the cluster running."
            )
        else:
            warning_title = "WARNING: Cluster Services Not Running"
            warning_text = (
                "The cluster is configured but Pacemaker is not running. Health check results "
                "may be incomplete or inaccurate. Checks that require live cluster data (quorum, "
                "node status, resource status, replication status) will report ERROR status."
            )

        # Add prominent warning box
        pdf.set_fill_color(255, 243, 205)  # Light yellow background
        pdf.set_draw_color(255, 193, 7)  # Yellow border
        pdf.set_line_width(0.5)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(133, 100, 4)  # Dark yellow/brown text

        # Warning box
        y_start = pdf.get_y()
        pdf.rect(10, y_start, 190, 38, "DF")
        pdf.set_xy(15, y_start + 3)
        pdf.cell(0, 6, warning_title, ln=True)

        pdf.set_xy(15, y_start + 10)
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(180, 4, warning_text)
        pdf.set_xy(15, y_start + 22)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 4, "To start the cluster and rerun the health check:")
        pdf.set_xy(15, y_start + 28)
        pdf.set_font("Courier", "", 9)
        pdf.set_fill_color(245, 245, 245)
        pdf.cell(180, 5, "  pcs cluster start --all", fill=True)

        pdf.set_text_color(*PdfColors.BLACK)
        pdf.set_line_width(0.2)
        pdf.ln(20)

    # =========================================================================
    # HANA RESOURCE NOT MANAGED WARNING
    # =========================================================================
    hana_resource_state = cluster_info.get("hana_resource_state")
    if hana_resource_state and hana_resource_state in ("stopped", "disabled", "unmanaged"):
        state_descriptions = {
            "stopped": "HANA resource is stopped in Pacemaker",
            "disabled": "HANA resource is disabled in Pacemaker (target-role=Stopped)",
            "unmanaged": "HANA resource is in unmanaged state",
        }

        warning_title = f"WARNING: {state_descriptions.get(hana_resource_state)}"
        warning_text = (
            f"The SAP HANA cluster resource is {hana_resource_state}. "
            "HANA is NOT managed by Pacemaker in this state. "
            "Checks that depend on Pacemaker resource status (master/slave roles, site roles, "
            "SR status via Pacemaker) have been skipped. "
        )

        # Include DB running status if available
        hana_db_status_info = cluster_info.get("hana_db_status") or {}
        db_running = hana_db_status_info.get("db_running", False)

        if db_running:
            running_on = hana_db_status_info.get("running_nodes", [])
            warning_text += (
                f"The HANA database is still running ({', '.join(running_on)}). "
                "Replication info was gathered directly from HANA. "
            )
        else:
            warning_text += "The HANA database is NOT running. "

        if hana_resource_state == "disabled":
            warning_text += "To re-enable: pcs resource enable <resource_name>"
        elif hana_resource_state == "stopped":
            warning_text += "To start the resource: pcs resource start <resource_name>"
        elif hana_resource_state == "unmanaged":
            warning_text += "To restore management: pcs resource manage <resource_name>"

        # Yellow warning box (same style as "Cluster Not Running")
        pdf.set_fill_color(255, 243, 205)  # Light yellow background
        pdf.set_draw_color(255, 193, 7)  # Yellow border
        pdf.set_line_width(0.5)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(133, 100, 4)  # Dark yellow/brown text

        y_start = pdf.get_y()
        # Dynamic height: disabled state has longer text
        box_height = 36 if hana_resource_state == "disabled" else 30
        pdf.rect(10, y_start, 190, box_height, "DF")
        pdf.set_xy(15, y_start + 3)
        pdf.cell(0, 5, warning_title, ln=True)
        pdf.set_xy(15, y_start + 10)
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(180, 4, warning_text)

        pdf.set_text_color(*PdfColors.BLACK)
        pdf.set_line_width(0.2)
        pdf.ln(8)

    # =========================================================================
    # CLUSTER CONFIGURATION
    # =========================================================================
    pdf.chapter_title("Cluster Configuration")

    pdf.info_table(
        {
            "Cluster Type": cluster_info.get("cluster_type", "Scale-Up"),
            "Node Count": str(len(nodes)),
            "Pacemaker": cluster_info.get("pacemaker_version", "N/A"),
        }
    )

    pdf.ln(5)

    # Node list
    pdf.sub_section("Cluster Nodes")
    # Majority makers are ONLY applicable for Scale-Out clusters
    is_scale_out = cluster_info.get("cluster_type") == "Scale-Out"
    majority_makers = cluster_info.get("majority_makers", []) if is_scale_out else []
    # Nodes excluded from HANA by location constraints (app servers or majority makers)
    resource_config = cluster_info.get("resource_config") or {}
    hana_excluded_node = resource_config.get("hana_excluded_node")
    excluded_nodes = set(majority_makers)
    if hana_excluded_node:
        excluded_nodes.add(hana_excluded_node)
    for node in nodes:
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(5, 6, "-")  # Bullet point
        if node in majority_makers:
            pdf.set_font("Helvetica", "I", 10)
            pdf.cell(0, 6, f"{node} (MajorityMaker)", new_x="LMARGIN", new_y="NEXT")
        elif node in excluded_nodes:
            pdf.set_font("Helvetica", "I", 10)
            pdf.cell(
                0,
                6,
                f"{node} (no HANA resources - excluded by constraints)",
                new_x="LMARGIN",
                new_y="NEXT",
            )
        else:
            pdf.cell(0, 6, node, new_x="LMARGIN", new_y="NEXT")

    # Note about constraint-excluded nodes
    if excluded_nodes:
        pdf.ln(2)
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(100, 100, 100)
        if majority_makers:
            pdf.multi_cell(
                0,
                4,
                "Note: MajorityMaker node(s) have location constraints with resource-discovery=never "
                "to prevent SAPHanaTopology and SAPHanaController from running on these nodes.",
            )
        else:
            non_mm_excluded = excluded_nodes - set(majority_makers)
            if non_mm_excluded:
                pdf.multi_cell(
                    0,
                    4,
                    f"Note: {', '.join(sorted(non_mm_excluded))} excluded from SAPHana resources by "
                    f"location constraints (resource-discovery=never).",
                )
        pdf.set_text_color(*PdfColors.BLACK)

    pdf.ln(5)

    # Node Details (in verbose mode or when available)
    node1_hostname = cluster_info.get("node1_hostname", "")
    node1_fqdn = cluster_info.get("node1_fqdn", "")
    node1_ip = cluster_info.get("node1_ip", "")
    node2_hostname = cluster_info.get("node2_hostname", "")
    node2_fqdn = cluster_info.get("node2_fqdn", "")
    node2_ip = cluster_info.get("node2_ip", "")

    # Only display IP if it's actually a valid IP address (not a hostname)
    node1_ip_valid = node1_ip if is_valid_ip(node1_ip) else ""
    node2_ip_valid = node2_ip if is_valid_ip(node2_ip) else ""

    if verbose or node1_hostname or node1_ip_valid:
        if node1_hostname or node1_fqdn or node1_ip_valid:
            pdf.sub_section("Node 1 (Primary Site)")
            node1_info = {}
            if node1_hostname:
                node1_info["Hostname"] = node1_hostname
            if node1_fqdn:
                node1_info["FQDN"] = node1_fqdn
            if node1_ip_valid:
                node1_info["IP Address"] = node1_ip_valid
            if node1_info:
                pdf.info_table(node1_info)
            pdf.ln(3)

        if node2_hostname or node2_fqdn or node2_ip_valid:
            pdf.sub_section("Node 2 (Secondary Site)")
            node2_info = {}
            if node2_hostname:
                node2_info["Hostname"] = node2_hostname
            if node2_fqdn:
                node2_info["FQDN"] = node2_fqdn
            if node2_ip_valid:
                node2_info["IP Address"] = node2_ip_valid
            if node2_info:
                pdf.info_table(node2_info)
            pdf.ln(3)

    # SAP HANA HA Parameters (Ansible-compatible)
    sid = cluster_info.get("sid")
    # Show HANA config in verbose mode even without SID
    show_hana_config = sid or verbose
    if show_hana_config:
        pdf.sub_section("SAP HANA Configuration")
        hana_config = {}
        if sid:
            hana_config["SID"] = sid
        if cluster_info.get("instance_number"):
            hana_config["Instance Number"] = cluster_info.get("instance_number")
        if cluster_info.get("virtual_ip"):
            hana_config["Virtual IP (Primary)"] = cluster_info.get("virtual_ip")
        if cluster_info.get("secondary_vip"):
            hana_config["Virtual IP (Secondary)"] = cluster_info.get("secondary_vip")
        if cluster_info.get("replication_mode"):
            hana_config["Replication Mode"] = cluster_info.get("replication_mode")
        if cluster_info.get("operation_mode"):
            hana_config["Operation Mode"] = cluster_info.get("operation_mode")
        if cluster_info.get("secondary_read") is not None:
            hana_config["Secondary Read Enabled"] = str(cluster_info.get("secondary_read"))
        # Site names
        site1 = cluster_info.get("site1_name", "")
        site2 = cluster_info.get("site2_name", "")
        sites = cluster_info.get("sites", [])
        if site1:
            hana_config["Site 1 Name"] = site1
        if site2:
            hana_config["Site 2 Name"] = site2
        elif sites and not site1:
            hana_config["Sites"] = ", ".join(sites)
        if hana_config:
            pdf.info_table(hana_config)
        pdf.ln(3)

        # HA Parameters
        ha_config = {}
        if cluster_info.get("prefer_site_takeover") is not None:
            ha_config["Prefer Site Takeover"] = str(cluster_info.get("prefer_site_takeover"))
        if cluster_info.get("automated_register") is not None:
            ha_config["Automated Register"] = str(cluster_info.get("automated_register"))
        if cluster_info.get("duplicate_primary_timeout") is not None:
            ha_config["Duplicate Primary Timeout"] = str(
                cluster_info.get("duplicate_primary_timeout")
            )
        if cluster_info.get("migration_threshold") is not None:
            ha_config["Migration Threshold"] = str(cluster_info.get("migration_threshold"))
        if ha_config:
            pdf.sub_section("HA Parameters")
            pdf.info_table(ha_config)
        pdf.ln(3)

        # SAPHanaTopology Resource
        topology_resource = cluster_info.get("topology_resource")
        if topology_resource:
            pdf.sub_section("SAPHanaTopology Resource")
            # Determine which nodes the clone actually runs on
            hana_nodes = [n for n in nodes if n not in excluded_nodes]
            clone_desc = f"clone (runs on: {', '.join(hana_nodes)})"
            topo_config = {
                "Resource Name": topology_resource,
                "Resource Agent": "SAPHanaTopology",
                "Clone Type": clone_desc,
                "interleave": "true",
            }
            if excluded_nodes:
                topo_config["Excluded Nodes"] = (
                    ", ".join(sorted(excluded_nodes)) + " (resource-discovery=never)"
                )
            pdf.info_table(topo_config)
            pdf.ln(3)

        # SAPHanaController Resource / SAPHana Resource
        res_config = {}
        resource_type = cluster_info.get("resource_type")
        resource_name = cluster_info.get("resource_name")
        if resource_type and resource_name:
            if resource_type == "SAPHanaController":
                pdf.sub_section("SAPHanaController Resource")
                res_config["Resource Name"] = resource_name
                res_config["Resource Agent"] = "SAPHanaController"
                res_config["Clone Type"] = "promotable (master/slave)"
                res_config["interleave"] = "true"
                if excluded_nodes:
                    res_config["Excluded Nodes"] = (
                        ", ".join(sorted(excluded_nodes)) + " (resource-discovery=never)"
                    )
            else:
                pdf.sub_section("SAPHana Resource")
                res_config["Resource Name"] = resource_name
                res_config["Resource Agent"] = "SAPHana"
                res_config["Clone Type"] = "promotable (master/slave)"
            pdf.info_table(res_config)
            pdf.ln(3)

        # VIP Resources
        vip_config = {}
        if cluster_info.get("vip_resource"):
            vip_config["Primary VIP Resource"] = cluster_info.get("vip_resource")
        if cluster_info.get("secondary_vip_resource"):
            vip_config["Secondary VIP Resource"] = cluster_info.get("secondary_vip_resource")
        if vip_config:
            pdf.sub_section("Virtual IP Resources")
            pdf.info_table(vip_config)
        pdf.ln(3)

        # STONITH Configuration
        stonith_config = {}
        if cluster_info.get("stonith_device"):
            stonith_config["STONITH Device"] = cluster_info.get("stonith_device")
        stonith_params = cluster_info.get("stonith_params")
        if stonith_params:
            if stonith_params.get("ssl"):
                stonith_config["SSL Enabled"] = "Yes" if stonith_params.get("ssl") == "1" else "No"
            if stonith_params.get("ssl_insecure"):
                stonith_config["SSL Insecure"] = (
                    "Yes" if stonith_params.get("ssl_insecure") == "1" else "No"
                )
        if stonith_config:
            pdf.sub_section("STONITH/Fencing Configuration")
            pdf.info_table(stonith_config)
            # Show pcmk_host_map in a formatted table
            if stonith_params and stonith_params.get("pcmk_host_map"):
                pdf.ln(3)
                pdf.set_font("Helvetica", "B", 9)
                pdf.cell(0, 5, "STONITH Host Mapping (pcmk_host_map):", ln=True)
                pdf.ln(1)
                # Table header
                pdf.set_fill_color(240, 240, 240)
                pdf.set_font("Helvetica", "B", 8)
                pdf.cell(60, 5, "Cluster Node", border=1, fill=True)
                pdf.cell(80, 5, "STONITH Target", border=1, fill=True, ln=True)
                # Table rows
                pdf.set_font("Helvetica", "", 8)
                host_map = stonith_params.get("pcmk_host_map", "")
                hosts = host_map.split(";")
                for host in hosts:
                    if host.strip() and ":" in host:
                        node, target = host.strip().split(":", 1)
                        pdf.cell(60, 5, node, border=1)
                        pdf.cell(80, 5, target, border=1, ln=True)

        pdf.ln(5)

    # =========================================================================
    # HANA DATABASE STATUS & REPLICATION
    # =========================================================================
    hana_db_status = cluster_info.get("hana_db_status")
    if hana_db_status:
        pdf.sub_section("HANA Database Status")

        db_running = hana_db_status.get("db_running", False)
        hana_managed = hana_db_status.get("hana_managed", False)
        running_nodes = hana_db_status.get("running_nodes", [])
        stopped_nodes = hana_db_status.get("stopped_nodes", [])
        resource_state = hana_db_status.get("hana_resource_state", "unknown")

        # Build status info table
        db_status_info = {}
        hana_version = hana_db_status.get("hana_version")
        if hana_version:
            hana_sp = hana_db_status.get("hana_sp", "")
            version_display = hana_version
            if hana_sp:
                version_display += f" (SPS{hana_sp})"
            db_status_info["HANA Version"] = version_display
        if db_running:
            db_status_info["Database Running"] = f"Yes ({', '.join(running_nodes)})"
        else:
            db_status_info["Database Running"] = "No"
        if stopped_nodes:
            db_status_info["Database Stopped On"] = ", ".join(stopped_nodes)

        if hana_managed:
            db_status_info["Managed by Cluster"] = f"Yes (resource {resource_state})"
        else:
            reason = (
                f"resource {resource_state}"
                if resource_state != "unknown"
                else "cluster not running"
            )
            db_status_info["Managed by Cluster"] = f"No ({reason})"

        db_status_info["Resource State"] = resource_state

        pdf.info_table(db_status_info)
        pdf.ln(3)

        # System Replication topology table
        sr_topology = hana_db_status.get("sr_topology")
        sr_source = hana_db_status.get("sr_source")

        if sr_topology and sr_topology.get("sites"):
            pdf.sub_section("System Replication")

            # Mapping line: "DC1 -> DC2, DC1 -> DC3"
            mapping = sr_topology.get("mapping", "")

            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(*PdfColors.BLACK)
            mapping_display = mapping if mapping else "Unknown"
            pdf.cell(0, 6, mapping_display, ln=True)

            # Source attribution
            if sr_source:
                pdf.set_font("Helvetica", "I", 8)
                pdf.set_text_color(100, 100, 100)
                pdf.cell(0, 4, f"Source: {sr_source}", ln=True)

            if not hana_managed:
                pdf.set_font("Helvetica", "B", 8)
                pdf.set_text_color(133, 100, 4)
                pdf.cell(0, 4, "Note: HANA is NOT managed by Pacemaker", ln=True)

            pdf.set_text_color(*PdfColors.BLACK)
            pdf.ln(2)

            # Site topology table
            row_h = 5
            pdf.set_fill_color(240, 240, 240)
            pdf.set_font("Helvetica", "B", 8)
            pdf.cell(30, row_h, "Site", border=1, fill=True)
            pdf.cell(25, row_h, "Role", border=1, fill=True)
            pdf.cell(30, row_h, "Mode", border=1, fill=True, align="C")
            pdf.cell(105, row_h, "HANA Instances", border=1, fill=True)
            pdf.ln()

            pdf.set_font("Helvetica", "", 8)
            for site in sr_topology["sites"]:
                pdf.set_font("Helvetica", "B", 8)
                pdf.cell(30, row_h, site.get("name", ""), border=1)

                role = site.get("role", "")
                if role == "primary":
                    pdf.set_text_color(*PdfColors.GREEN)
                else:
                    pdf.set_text_color(0, 100, 180)  # Blue for secondary
                pdf.set_font("Helvetica", "B", 8)
                pdf.cell(25, row_h, role, border=1)
                pdf.set_text_color(*PdfColors.BLACK)

                pdf.set_font("Helvetica", "", 8)
                op_mode_str = site.get("op_mode", "") or ""
                pdf.cell(30, row_h, op_mode_str, border=1, align="C")

                # List hosts (coordinator first if detectable)
                hosts = site.get("hosts", [])
                hosts_str = ", ".join(hosts) if hosts else "N/A"
                pdf.set_font("Courier", "", 8)
                pdf.cell(105, row_h, hosts_str, border=1)
                pdf.ln()

            pdf.set_font("Helvetica", "", 10)
            pdf.ln(3)

        elif sr_source:
            pdf.sub_section("System Replication")
            pdf.set_font("Helvetica", "I", 9)
            pdf.cell(0, 5, f"Source: {sr_source}", ln=True)
            pdf.ln(3)

    pdf.ln(5)

    # =========================================================================
    # CONFIGURED RESOURCES (from cib.xml)
    # =========================================================================
    resource_config = cluster_info.get("resource_config")
    if resource_config and resource_config.get("available"):
        pdf.add_page()
        pdf.chapter_title("Configured Resources (from cib.xml)")

        # Resources summary
        resources = resource_config.get("resources", {})
        if resources.get("list"):
            pdf.sub_section("Cluster Resources")
            pdf.set_font("Courier", "", 8)
            for resource in resources.get("list", [])[:20]:  # Limit to 20 resources
                pdf.set_x(10)  # Reset to left margin
                pdf.multi_cell(0, 4, f"- {resource[:90]}")  # Truncate long lines
            if len(resources.get("list", [])) > 20:
                pdf.set_font("Helvetica", "I", 8)
                pdf.set_x(10)
                pdf.cell(0, 4, f"  ... and {len(resources['list']) - 20} more resources", ln=True)
            pdf.ln(3)

        # SAP HANA specific configuration
        sap_hana = resource_config.get("sap_hana", {})
        if sap_hana:
            pdf.sub_section("SAP HANA Resource Configuration")
            for resource_name, attrs in sap_hana.items():
                pdf.set_font("Helvetica", "B", 9)
                pdf.cell(0, 5, resource_name[:60], ln=True)
                pdf.set_font("Courier", "", 8)
                for key, value in attrs.items():
                    # Truncate long values to prevent layout issues
                    val_str = str(value)[:80]
                    pdf.cell(0, 4, f"  {key}={val_str}", ln=True)
                pdf.ln(2)

        # Constraints summary
        constraints = resource_config.get("constraints", {})

        # Location constraints with resource-discovery
        resource_discovery = constraints.get("resource_discovery", [])
        if resource_discovery:
            pdf.sub_section("Resource Discovery Settings")
            pdf.set_font("Courier", "", 8)
            for rd in resource_discovery[:15]:
                pdf.set_x(10)
                pdf.multi_cell(0, 4, rd[:100])
            if len(resource_discovery) > 15:
                pdf.set_font("Helvetica", "I", 8)
                pdf.set_x(10)
                pdf.cell(0, 4, f"  ... and {len(resource_discovery) - 15} more", ln=True)
            pdf.ln(3)

        # Location constraints
        location = constraints.get("location", [])
        if location:
            pdf.sub_section("Location Constraints")
            pdf.set_font("Courier", "", 7)
            shown = 0
            for loc in location:
                if shown >= 20:
                    break
                if loc.startswith("resource") or loc.startswith("Resource"):
                    pdf.set_x(10)
                    pdf.multi_cell(0, 3.5, loc[:100])
                    shown += 1
            if (
                len(
                    [
                        ln
                        for ln in location
                        if ln.startswith("resource") or ln.startswith("Resource")
                    ]
                )
                > 20
            ):
                pdf.set_font("Helvetica", "I", 8)
                pdf.set_x(10)
                pdf.cell(0, 4, "  ... more constraints in full output", ln=True)
            pdf.ln(3)

        # Colocation constraints
        colocation = constraints.get("colocation", [])
        if colocation:
            pdf.sub_section("Colocation Constraints")
            pdf.set_font("Courier", "", 8)
            for col in colocation[:10]:
                pdf.set_x(10)
                pdf.multi_cell(0, 4, col[:100])
            pdf.ln(3)

        # Order constraints
        order = constraints.get("order", [])
        if order:
            pdf.sub_section("Order Constraints")
            pdf.set_font("Courier", "", 8)
            for ord_c in order[:10]:
                pdf.set_x(10)
                pdf.multi_cell(0, 4, ord_c[:100])
            pdf.ln(3)

        # STONITH info from cib
        stonith = resource_config.get("stonith", {})
        if stonith.get("devices"):
            pdf.sub_section("STONITH Devices (from cib.xml)")
            pdf.set_font("Courier", "", 8)
            for device in stonith.get("devices", [])[:10]:
                pdf.set_x(10)
                pdf.multi_cell(0, 4, device[:100])
            pdf.ln(3)

        pdf.ln(5)

    # =========================================================================
    # CHECK RESULTS
    # =========================================================================
    pdf.add_page()
    pdf.chapter_title("Health Check Results")

    # Group results by status
    passed_checks = [r for r in results if r.get("status") == "PASSED"]
    failed_checks = [
        r for r in results if r.get("status") == "FAILED" and r.get("severity") != "WARNING"
    ]
    warning_checks = [
        r for r in results if r.get("severity") == "WARNING" and r.get("status") == "FAILED"
    ]
    error_checks = [r for r in results if r.get("status") == "ERROR"]
    skipped_checks = [r for r in results if r.get("status") == "SKIPPED"]

    # Critical/Failed checks first
    if failed_checks or error_checks:
        pdf.sub_section("Failed Checks")

        # Add prominent note when cluster is stopped and there are errors
        if error_checks and not cluster_running:
            pdf.set_fill_color(255, 243, 205)  # Light yellow background
            pdf.set_draw_color(255, 193, 7)  # Yellow border
            pdf.set_line_width(0.3)
            y_note = pdf.get_y()
            pdf.rect(10, y_note, 190, 16, "DF")
            pdf.set_xy(15, y_note + 2)
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_text_color(133, 100, 4)
            pdf.cell(0, 5, "Cluster Not Running - Some checks cannot retrieve live data", ln=True)
            pdf.set_xy(15, y_note + 8)
            pdf.set_font("Helvetica", "", 8)
            pdf.multi_cell(
                180,
                4,
                "ERROR status below may be caused by the stopped cluster. Start the cluster "
                "with 'pcs cluster start --all' and rerun the health check for accurate results.",
            )
            pdf.set_text_color(*PdfColors.BLACK)
            pdf.set_line_width(0.2)
            pdf.ln(5)

        for check in failed_checks + error_checks:
            pdf.check_result_row(
                check.get("check_id", "N/A"),
                check.get("description", ""),
                check.get("status", "FAILED"),
                check.get("message", ""),
                check.get("node", ""),
            )
            # Render version comparison table for package consistency checks
            _render_version_table(pdf, check)

        # Add note about errors when cluster is stopped (for running cluster case)
        if error_checks and cluster_running:
            pdf.ln(3)
            pdf.set_font("Helvetica", "I", 9)
            pdf.set_text_color(*PdfColors.GRAY)
            pdf.multi_cell(
                0,
                5,
                "Note: Some checks report ERROR status when data could not be retrieved. "
                "This may indicate a problem with the cluster configuration or services.",
            )
            pdf.set_text_color(0, 0, 0)
            pdf.ln(3)

    # Warnings
    if warning_checks:
        pdf.sub_section("Warnings")
        for check in warning_checks:
            pdf.check_result_row(
                check.get("check_id", "N/A"),
                check.get("description", ""),
                "WARNING",
                check.get("message", ""),
                check.get("node", ""),
            )
            # Render version comparison table for package consistency checks
            _render_version_table(pdf, check)

    # Passed checks
    if passed_checks:
        pdf.sub_section(f"Passed Checks ({len(passed_checks)})")

        if verbose:
            # Verbose mode: show all passed checks in detail
            for check in passed_checks:
                pdf.check_result_row(
                    check.get("check_id", "N/A"),
                    check.get("description", ""),
                    check.get("status", "PASSED"),
                    check.get("message", ""),
                    check.get("node", ""),
                )
                # Render version comparison table for package consistency checks
                _render_version_table(pdf, check)
        else:
            # Compact mode: list passed checks in 3-column format
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(*PdfColors.GREEN)
            for i, check in enumerate(passed_checks):
                if i > 0 and i % 3 == 0:
                    pdf.ln(5)
                pdf.cell(
                    60,
                    5,
                    f"[OK] {check.get('check_id', 'N/A')}",
                    new_x="LMARGIN" if (i % 3 == 2) else "RIGHT",
                    new_y="NEXT" if (i % 3 == 2) else "TOP",
                )
            pdf.ln(8)

    # Skipped checks
    if skipped_checks:
        pdf.sub_section(f"Skipped Checks ({len(skipped_checks)})")
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(*PdfColors.GRAY)
        # Group skip reasons for a meaningful summary
        skip_reasons = set()
        for c in skipped_checks:
            msg = c.get("message", "")
            if "not installed" in msg.lower():
                skip_reasons.add("SAP HANA not installed")
            elif "legacy scale-up" in msg.lower():
                skip_reasons.add("not applicable (legacy scale-up)")
            elif "majority maker" in msg.lower():
                skip_reasons.add("not applicable (Majority Maker)")
            elif "scale-out" in msg.lower() or "scale-up" in msg.lower():
                skip_reasons.add("not applicable (cluster type)")
            elif "not applicable" in msg.lower():
                skip_reasons.add("not applicable")
            else:
                skip_reasons.add("not applicable")
        reason_text = ", ".join(sorted(skip_reasons)) if skip_reasons else "not applicable"
        pdf.body_text(f"Skipped {len(skipped_checks)} checks ({reason_text})")

    # =========================================================================
    # RECOMMENDATIONS
    # =========================================================================
    pdf.add_page()
    pdf.chapter_title("Recommendations")

    priority = 1

    # Generate recommendations based on failed checks
    if any(c.get("check_id") == "CHK_STONITH_CONFIG" for c in failed_checks):
        pdf.recommendation_box(
            str(priority),
            "Configure STONITH/Fencing",
            "STONITH is required for production SAP HANA clusters to ensure data integrity.",
            [
                (
                    "# Option A: Configure real STONITH (production)\n"
                    "pcs stonith create fence_node1 fence_ipmilan \\\n"
                    "    ipaddr=<IPMI_IP> login=<USER> passwd=<PASS> \\\n"
                    "    lanplus=1 pcmk_host_list=node1"
                ),
                "# Option B: Disable STONITH (test/dev only)\npcs property set stonith-enabled=false",
            ],
        )
        priority += 1

    if any(c.get("check_id") == "CHK_HANA_INSTALLED" for c in failed_checks):
        pdf.recommendation_box(
            str(priority),
            "Install SAP HANA",
            "SAP HANA database is not installed on one or more cluster nodes.",
            ["# Run SAP HANA installation\n./hdblcm --action=install --sid=<SID> --number=<INST>"],
        )
        priority += 1

    if any(c.get("check_id") in ["CHK_CLONE_CONFIG", "CHK_RESOURCE_STATUS"] for c in failed_checks):
        pdf.recommendation_box(
            str(priority),
            "Configure SAP HANA Cluster Resources",
            "SAP HANA cluster resources are not configured. Configure SAPHana and SAPHanaTopology resources.",
            [
                (
                    "# Create SAPHanaTopology resource\n"
                    "pcs resource create SAPHanaTopology_<SID>_<INST> SAPHanaTopology \\\n"
                    "    SID=<SID> InstanceNumber=<INST> \\\n"
                    "    clone clone-max=2 clone-node-max=1 interleave=true"
                ),
                (
                    "# Create SAPHana resource\n"
                    "pcs resource create SAPHana_<SID>_<INST> SAPHana \\\n"
                    "    SID=<SID> InstanceNumber=<INST> \\\n"
                    "    PREFER_SITE_TAKEOVER=true DUPLICATE_PRIMARY_TIMEOUT=7200 \\\n"
                    "    promotable notify=true clone-max=2 clone-node-max=1"
                ),
            ],
        )
        priority += 1

    if priority == 1:  # No specific recommendations
        pdf.body_text("No critical issues found. The cluster appears to be properly configured.")
        pdf.ln(5)

    # =========================================================================
    # BEST PRACTICES CHECKLIST
    # =========================================================================
    pdf.chapter_title("Best Practices Checklist")

    best_practices = [
        ("STONITH/Fencing enabled", "Ensures data integrity during split-brain scenarios"),
        ("Two-node quorum configured", "Proper quorum settings for 2-node clusters"),
        ("HANA System Replication active", "Synchronous or memory-sync replication mode"),
        ("HA/DR hooks configured", "SAPHanaSR hooks for cluster awareness"),
        ("Resource timeouts set", "Appropriate timeouts for SAP HANA operations"),
        ("Monitoring enabled", "Integration with monitoring systems"),
        ("Backup strategy defined", "Regular backups and tested recovery"),
        ("Documentation updated", "Runbooks and procedures documented"),
    ]

    for practice, description in best_practices:
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(8, 6, "[ ]")  # Checkbox
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(70, 6, practice)
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(*PdfColors.GRAY)
        pdf.cell(0, 6, description, new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*PdfColors.BLACK)

    # =========================================================================
    # DOCUMENTATION REFERENCES
    # =========================================================================
    pdf.ln(10)
    pdf.chapter_title("Documentation References")

    # Extract RHEL major version for version-specific documentation URLs
    rhel_ver_str = cluster_info.get("rhel_version", "")
    rhel_major = 9  # default
    match = re.search(r"(\d+)", str(rhel_ver_str))
    if match:
        rhel_major = int(match.group(1))
    doc_urls = get_redhat_doc_urls(rhel_major)

    references = [
        ("SAP HANA Administration Guide", "https://help.sap.com/docs/SAP_HANA_PLATFORM"),
        (
            "SAP HANA System Replication",
            "https://help.sap.com/docs/SAP_HANA_PLATFORM/6b94445c94ae495c83a19646e7c3fd56",
        ),
        ("Red Hat HA Clusters", doc_urls["ha_clusters"]),
        ("Red Hat SAP Solutions", doc_urls["sap_solutions"]),
        ("Red Hat SAP HANA Scale-Up HA", doc_urls["sap_scale_up"]),
        ("Red Hat SAP HANA Scale-Out HA", doc_urls["sap_scale_out"]),
        ("Red Hat SAP HANA Multitarget DR", doc_urls["sap_multitarget_dr"]),
        ("Pacemaker Documentation", "https://clusterlabs.org/pacemaker/doc/"),
    ]

    for title, url in references:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(60, 6, title)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*PdfColors.BLUE)
        pdf.cell(0, 6, url, new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*PdfColors.BLACK)

    # =========================================================================
    # SAVE PDF
    # =========================================================================
    if output_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"health_check_report_{timestamp}.pdf"

    pdf.output(output_path)
    return output_path


def load_yaml_report(yaml_path: str) -> tuple:
    """Load results from YAML report file (legacy format)"""
    import yaml

    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    results = data.get("results", [])  # pylint: disable=redefined-outer-name
    summary = data.get("summary", {})  # pylint: disable=redefined-outer-name

    return results, summary


def load_unified_yaml_report(yaml_path: str) -> tuple:
    """
    Load report data from unified YAML format.

    This function supports both the new unified format (with version field)
    and the legacy format (only results and summary).

    Args:
        yaml_path: Path to YAML report file

    Returns:
        Tuple of (results, summary, cluster_info, install_status)
        - results: List of check result dicts
        - summary: Summary statistics dict
        - cluster_info: Cluster information dict for PDF generation
        - install_status: Installation status dict (or None)
    """
    import yaml

    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # Check if this is the unified format (has version field)
    if "version" in data:
        # Unified format - extract all components
        results = data.get("results", [])  # pylint: disable=redefined-outer-name
        summary = data.get("summary", {})  # pylint: disable=redefined-outer-name

        # Build cluster_info from unified data
        cluster_info = {  # pylint: disable=redefined-outer-name
            "cluster_name": data.get("cluster_name", "Unknown"),
            "nodes": data.get("nodes", []),
            "cluster_type": data.get("cluster_type", "Scale-Up"),
            "majority_makers": data.get("majority_makers", []),
            # Data source
            "data_source": data.get("data_source", "Unknown"),
            "access_method": data.get("access_method", "unknown"),
            "used_cib_xml": data.get("used_cib_xml", False),
            "cluster_running": data.get("cluster_running", True),
            "hana_resource_state": data.get("hana_resource_state"),
            "hana_db_status": data.get("hana_db_status"),
            # OS/Software versions
            "rhel_version": data.get("rhel_version"),
            "pacemaker_version": data.get("pacemaker_version"),
            # SAP HANA config
            "sid": data.get("sid"),
            "instance_number": data.get("instance_number"),
            "virtual_ip": data.get("virtual_ip"),
            "secondary_vip": data.get("secondary_vip"),
            "replication_mode": data.get("replication_mode"),
            "operation_mode": data.get("operation_mode"),
            "secondary_read": data.get("secondary_read"),
            # Node config
            "node1_hostname": data.get("node1_hostname"),
            "node1_ip": data.get("node1_ip"),
            "node2_hostname": data.get("node2_hostname"),
            "node2_ip": data.get("node2_ip"),
            "sites": data.get("sites"),
            # HA parameters
            "prefer_site_takeover": data.get("prefer_site_takeover"),
            "automated_register": data.get("automated_register"),
            "duplicate_primary_timeout": data.get("duplicate_primary_timeout"),
            "migration_threshold": data.get("migration_threshold"),
            # Resource config
            "resource_type": data.get("resource_type"),
            "resource_name": data.get("resource_name"),
            "topology_resource": data.get("topology_resource"),
            "vip_resource": data.get("vip_resource"),
            "secondary_vip_resource": data.get("secondary_vip_resource"),
            # STONITH
            "stonith_device": data.get("stonith_device"),
            "stonith_params": data.get("stonith_params"),
            # CIB resource config
            "resource_config": data.get("resource_config"),
        }

        install_status = data.get("install_status")  # pylint: disable=redefined-outer-name

        return results, summary, cluster_info, install_status

    # Legacy format - only has results and summary
    results = data.get("results", [])
    summary = data.get("summary", {})

    # Build minimal cluster_info
    cluster_info = {
        "cluster_name": "Unknown",
        "nodes": [],
        "cluster_type": "Scale-Up",
        "data_source": "Legacy YAML report",
    }

    return results, summary, cluster_info, None


if __name__ == "__main__":
    # Standalone mode - convert YAML to PDF
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate PDF health check report from YAML",
        epilog="""
Examples:
  # Generate PDF from unified YAML report
  python report_generator.py 20260410_120000_mycluster.yaml

  # Generate PDF with custom output path
  python report_generator.py report.yaml -o custom_report.pdf

  # Generate demo PDF (no YAML input)
  python report_generator.py --demo
        """,
    )
    parser.add_argument("yaml_report", nargs="?", help="YAML report file to convert to PDF")
    parser.add_argument("-o", "--output", help="Output PDF path")
    parser.add_argument(
        "--cluster", default="Test Cluster", help="Cluster name (for legacy YAML or demo)"
    )
    parser.add_argument(
        "--nodes",
        nargs="+",
        default=["node1", "node2"],
        help="Node names (for legacy YAML or demo)",
    )
    parser.add_argument("--demo", action="store_true", help="Generate a demo PDF with sample data")

    args = parser.parse_args()

    install_status = None

    if args.yaml_report:
        # Load from YAML using unified loader
        results, summary, cluster_info, install_status = load_unified_yaml_report(args.yaml_report)

        # Override cluster name and nodes if provided via CLI
        if args.cluster != "Test Cluster":
            cluster_info["cluster_name"] = args.cluster
        if args.nodes != ["node1", "node2"]:
            cluster_info["nodes"] = args.nodes

        print(f"Loaded report from: {args.yaml_report}")
        print(f"  Cluster: {cluster_info.get('cluster_name', 'Unknown')}")
        print(f"  Data source: {cluster_info.get('data_source', 'Unknown')}")
        print(
            f"  Checks: {summary.get('total', 0)} total, {summary.get('passed', 0)} passed, {summary.get('failed', 0)} failed"
        )

    elif args.demo:
        # Demo data
        results = [
            {
                "check_id": "CHK_NODE_STATUS",
                "description": "Verify all cluster nodes are online",
                "status": "PASSED",
                "severity": "CRITICAL",
                "node": "cluster",
            },
            {
                "check_id": "CHK_STONITH_CONFIG",
                "description": "Verify STONITH/fencing is configured",
                "status": "FAILED",
                "severity": "WARNING",
                "message": "No STONITH resources configured",
                "node": "cluster",
            },
            {
                "check_id": "CHK_HANA_INSTALLED",
                "description": "Check if SAP HANA is installed",
                "status": "FAILED",
                "severity": "INFO",
                "message": "SAP HANA not installed",
                "node": "node1",
            },
        ]
        summary = {"total": 3, "passed": 1, "failed": 2, "critical_count": 0, "warning_count": 1}
        cluster_info = {
            "cluster_name": args.cluster,
            "nodes": args.nodes,
            "cluster_type": "Scale-Up",
            "data_source": "Demo data",
        }
        print("Generating demo PDF report...")

    else:
        parser.print_help()
        print("\nError: Please provide a YAML report file or use --demo")
        sys.exit(1)

    output = generate_health_check_report(
        results, summary, cluster_info, args.output, install_status
    )
    print(f"PDF report generated: {output}")
