"""
CIB (Cluster Information Base) Parser

Unified library for parsing cluster configuration from cib.xml.
Works with both:
- Live clusters where cluster is stopped but cib.xml exists
- SOSreport analysis

Usage:
    from lib.cib_parser import CIBParser

    # From SOSreport
    parser = CIBParser.from_sosreport('/path/to/sosreport')

    # From live system cib.xml
    parser = CIBParser.from_file('/var/lib/pacemaker/cib/cib.xml')

    # Get resources, constraints, etc.
    resources = parser.get_resources()
    constraints = parser.get_constraints()
    config = parser.get_full_config()

Requires: pcs package installed locally (dnf install pcs)
"""

import subprocess
import shutil
from pathlib import Path
from typing import Dict, Any, Optional, Tuple


class CIBParser:
    """Parser for Pacemaker CIB (cib.xml) files."""

    # Default cib.xml location on live systems
    DEFAULT_CIB_PATH = "/var/lib/pacemaker/cib/cib.xml"

    # SOSreport paths to search for cib.xml
    SOSREPORT_CIB_PATTERNS = [
        "sos_commands/pacemaker/crm_report/*/cib.xml",
        "var/lib/pacemaker/cib/cib.xml",
    ]

    def __init__(self, cib_path: str):
        """Initialize with path to cib.xml file.

        Args:
            cib_path: Full path to cib.xml file
        """
        self.cib_path = cib_path
        self._pcs_available = shutil.which("pcs") is not None
        self._cache: Dict[str, Any] = {}

    @classmethod
    def from_file(cls, cib_path: str) -> Optional["CIBParser"]:
        """Create parser from a specific cib.xml file path.

        Args:
            cib_path: Path to cib.xml file

        Returns:
            CIBParser instance or None if file doesn't exist
        """
        if Path(cib_path).exists():
            return cls(cib_path)
        return None

    @classmethod
    def from_sosreport(cls, sosreport_path: str) -> Optional["CIBParser"]:
        """Create parser from a SOSreport directory.

        Args:
            sosreport_path: Path to extracted SOSreport directory

        Returns:
            CIBParser instance or None if cib.xml not found
        """
        sos_path = Path(sosreport_path)

        for pattern in cls.SOSREPORT_CIB_PATTERNS:
            matches = list(sos_path.glob(pattern))
            if matches:
                return cls(str(matches[0]))

        return None

    @classmethod
    def from_live_system(cls) -> Optional["CIBParser"]:
        """Create parser from live system's cib.xml.

        Returns:
            CIBParser instance or None if cib.xml doesn't exist
        """
        return cls.from_file(cls.DEFAULT_CIB_PATH)

    @classmethod
    def find_cib(cls, sosreport_path: str = None) -> Optional["CIBParser"]:
        """Find and create parser from best available source.

        Tries in order:
        1. SOSreport path if provided
        2. Live system cib.xml

        Args:
            sosreport_path: Optional path to SOSreport

        Returns:
            CIBParser instance or None if no cib.xml found
        """
        if sosreport_path:
            parser = cls.from_sosreport(sosreport_path)
            if parser:
                return parser

        return cls.from_live_system()

    def is_available(self) -> bool:
        """Check if parser can execute pcs commands."""
        return self._pcs_available and Path(self.cib_path).exists()

    def _run_pcs(self, subcommand: str, cache_key: str = None) -> Tuple[bool, str]:
        """Run a pcs command against the cib.xml file.

        Args:
            subcommand: pcs subcommand (e.g., 'resource', 'constraint')
            cache_key: Optional key for caching results

        Returns:
            Tuple of (success, output)
        """
        if cache_key and cache_key in self._cache:
            return True, self._cache[cache_key]

        if not self._pcs_available:
            return False, "pcs command not found. Install with: dnf install pcs"

        if not Path(self.cib_path).exists():
            return False, f"cib.xml not found: {self.cib_path}"

        cmd = f"pcs -f {self.cib_path} {subcommand} 2>/dev/null"

        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=30, check=False
            )
            output = result.stdout.strip()

            if cache_key:
                self._cache[cache_key] = output

            return result.returncode == 0, output

        except subprocess.TimeoutExpired:
            return False, "Command timed out"
        except Exception as e:
            return False, str(e)

    def get_resources(self) -> Dict[str, Any]:
        """Get cluster resources.

        Returns:
            Dict with:
            - success: bool
            - resources: list of resource names/types
            - raw_output: full pcs output
            - clones: list of clone resources
            - primitives: list of primitive resources
        """
        result = {
            "success": False,
            "resources": [],
            "clones": [],
            "primitives": [],
            "raw_output": "",
        }

        success, output = self._run_pcs("resource", "resources")
        if not success:
            result["error"] = output
            return result

        result["success"] = True
        result["raw_output"] = output

        for line in output.split("\n"):
            line = line.strip()
            if line.startswith("*"):
                resource = line.lstrip("* ").strip()
                result["resources"].append(resource)

                if "Clone Set:" in line or "(promotable)" in line:
                    result["clones"].append(resource)
                elif "(ocf:" in line or "(stonith:" in line:
                    result["primitives"].append(resource)

        return result

    def get_resource_config(self) -> Dict[str, Any]:
        """Get detailed resource configuration.

        Returns:
            Dict with:
            - success: bool
            - raw_output: full pcs resource config output
            - sap_hana: SAP HANA specific config if found
        """
        result = {"success": False, "raw_output": "", "sap_hana": {}}

        success, output = self._run_pcs("resource config", "resource_config")
        if not success:
            result["error"] = output
            return result

        result["success"] = True
        result["raw_output"] = output

        # Extract SAP HANA configuration
        import re

        lines = output.split("\n")
        current_resource = None
        current_agent_type = None

        for i, line in enumerate(lines):
            stripped = line.strip()

            # Only trigger on Clone:/Resource: definition lines, not on
            # Meta Attributes/Operations lines that reference the resource name
            if stripped.startswith("Clone:") and "SAPHana" in line:
                if "Filesystem" in line:
                    # SAPHanaFilesystem - stop capturing
                    current_resource = None
                    current_agent_type = None
                else:
                    # Clone definition - set current_resource, type comes later
                    current_resource = stripped.split(":")[1].strip().split()[0]
                    current_agent_type = None
            elif stripped.startswith("Resource:") and "SAPHana" in line:
                # Resource definition - get agent type from type= parameter
                type_match = re.search(r"type=(SAPHana\w*)", line)
                if type_match and "Filesystem" not in type_match.group(1):
                    t = type_match.group(1)
                    if "Topology" in t:
                        current_agent_type = "SAPHanaTopology"
                    elif "Controller" in t:
                        current_agent_type = "SAPHanaController"
                    else:
                        current_agent_type = "SAPHana"
                    # Keep clone name if already set (attributes span both levels)
                    if current_resource is None:
                        current_resource = stripped.split(":")[1].strip().split()[0]
                    # Update agent type on existing entry
                    if current_resource in result["sap_hana"]:
                        result["sap_hana"][current_resource]["_agent_type"] = current_agent_type

            # Extract key attributes
            if current_resource and "=" in line:
                line = line.strip()
                if any(
                    attr in line
                    for attr in [
                        "SID=",
                        "InstanceNumber=",
                        "AUTOMATED_REGISTER=",
                        "PREFER_SITE_TAKEOVER=",
                        "DUPLICATE_PRIMARY_TIMEOUT=",
                        "clone-max=",
                        "promotable=",
                    ]
                ):
                    if current_resource not in result["sap_hana"]:
                        result["sap_hana"][current_resource] = {
                            "_agent_type": current_agent_type
                        }

                    # Parse key=value
                    if "=" in line:
                        parts = line.split("=", 1)
                        if len(parts) == 2:
                            key = parts[0].strip()
                            value = parts[1].strip()
                            result["sap_hana"][current_resource][key] = value

        return result

    def get_constraints(self) -> Dict[str, Any]:
        """Get cluster constraints.

        Returns:
            Dict with:
            - success: bool
            - raw_output: full pcs constraint output
            - location: location constraints
            - colocation: colocation constraints
            - order: order constraints
            - resource_discovery: resource-discovery settings with context
            - majority_maker: identified majority maker node (if any)
            - majority_maker_info: details about majority maker constraints
        """
        import re

        result = {
            "success": False,
            "raw_output": "",
            "location": [],
            "colocation": [],
            "order": [],
            "resource_discovery": [],
            "majority_maker": None,
            "majority_maker_info": {},
        }

        success, output = self._run_pcs("constraint", "constraints")
        if not success:
            result["error"] = output
            return result

        result["success"] = True
        result["raw_output"] = output

        current_section = None
        previous_line = ""

        # Track majority maker constraints per node
        # A majority maker must have constraints avoiding:
        # 1. SAPHanaTopology (required)
        # 2. SAPHanaController OR SAPHana (required)
        # resource-discovery=never is optional but recommended
        mm_constraints = (
            {}
        )  # node -> {'topology': bool, 'controller': bool, 'resource_discovery': bool}

        for line in output.split("\n"):
            line_stripped = line.strip()

            # Detect sections
            if line_stripped.startswith("Location Constraints:"):
                current_section = "location"
            elif line_stripped.startswith("Colocation Constraints:"):
                current_section = "colocation"
            elif line_stripped.startswith("Ordering Constraints:"):
                current_section = "order"
            elif line_stripped and current_section:
                # Add to appropriate section
                if current_section == "location":
                    result["location"].append(line_stripped)
                elif current_section == "colocation":
                    result["colocation"].append(line_stripped)
                elif current_section == "order":
                    result["order"].append(line_stripped)

            # Track resource-discovery settings with context (previous line has node info)
            if "resource-discovery=never" in line.lower():
                # Previous line contains the node: "resource 'X' avoids node 'nodename'"
                context = f"{previous_line} | {line_stripped}"
                result["resource_discovery"].append(context)

                # Extract node name and resource for majority maker detection
                match = re.search(r"avoids node '([^']+)'", previous_line)
                if match:
                    node = match.group(1)
                    if node not in mm_constraints:
                        mm_constraints[node] = {
                            "topology": False,
                            "controller": False,
                            "resource_discovery": True,
                        }
                    else:
                        mm_constraints[node]["resource_discovery"] = True

            # Check for location constraints that avoid a node (with or without resource-discovery)
            # Pattern: resource 'X' avoids node 'nodename' with score INFINITY
            if current_section == "location" and "avoids node" in line_stripped:
                match = re.search(r"resource '([^']+)'.*avoids node '([^']+)'", line_stripped)
                if match:
                    resource = match.group(1)
                    node = match.group(2)

                    if node not in mm_constraints:
                        mm_constraints[node] = {
                            "topology": False,
                            "controller": False,
                            "resource_discovery": False,
                        }

                    # Check if this is a SAPHanaTopology constraint
                    if "SAPHanaTopology" in resource:
                        mm_constraints[node]["topology"] = True

                    # Check if this is a SAPHanaController or SAPHana constraint
                    if "SAPHanaController" in resource or (
                        "SAPHana" in resource
                        and "Topology" not in resource
                        and "FS" not in resource
                    ):
                        mm_constraints[node]["controller"] = True

            previous_line = line_stripped

        # Identify node excluded from both SAPHanaTopology AND SAPHanaController
        # This could be a majority maker (Scale-Out, clone-max>=4) or an app server (Scale-Up)
        # The actual determination depends on clone-max, checked at a higher level
        for node, constraints in mm_constraints.items():
            if constraints["topology"] and constraints["controller"]:
                result["hana_excluded_node"] = node
                result["majority_maker"] = node  # Legacy field - consumer must check clone-max
                result["majority_maker_info"] = {
                    "node": node,
                    "has_topology_constraint": constraints["topology"],
                    "has_controller_constraint": constraints["controller"],
                    "has_resource_discovery": constraints["resource_discovery"],
                }
                break

        return result

    def get_properties(self) -> Dict[str, Any]:
        """Get cluster properties.

        Returns:
            Dict with:
            - success: bool
            - raw_output: full pcs property output
            - properties: dict of property name -> value
        """
        result = {"success": False, "raw_output": "", "properties": {}}

        success, output = self._run_pcs("property config", "properties")
        if not success:
            result["error"] = output
            return result

        result["success"] = True
        result["raw_output"] = output

        for line in output.split("\n"):
            if ":" in line and "=" in line:
                # Parse property lines like "stonith-enabled: true"
                parts = line.split(":", 1)
                if len(parts) == 2:
                    key = parts[0].strip()
                    value = parts[1].strip()
                    result["properties"][key] = value

        return result

    def get_stonith(self) -> Dict[str, Any]:
        """Get STONITH/fencing configuration.

        Returns:
            Dict with:
            - success: bool
            - raw_output: full pcs stonith output
            - devices: list of STONITH devices
            - enabled: whether STONITH is enabled
        """
        result = {"success": False, "raw_output": "", "devices": [], "enabled": None}

        # Get STONITH config
        success, output = self._run_pcs("stonith config", "stonith_config")
        if success:
            result["success"] = True
            result["raw_output"] = output

            import re

            for line in output.split("\n"):
                line = line.strip()
                # Extract just the device name from "Resource: device_name (class=stonith ...)"
                if line.startswith("Resource:"):
                    match = re.match(r"Resource:\s*(\S+)", line)
                    if match:
                        result["devices"].append(match.group(1))
                elif line.startswith("*"):
                    # Handle "* device_name" format
                    match = re.match(r"\*\s*(\S+)", line)
                    if match:
                        result["devices"].append(match.group(1))

        # Get STONITH enabled status from properties
        props = self.get_properties()
        if props["success"]:
            stonith_enabled = props["properties"].get("stonith-enabled", "")
            if stonith_enabled.lower() == "true":
                result["enabled"] = True
            elif stonith_enabled.lower() == "false":
                result["enabled"] = False

        return result

    def get_nodes(self) -> Dict[str, Any]:
        """Get cluster nodes status.

        Returns:
            Dict with:
            - success: bool
            - raw_output: full pcs status nodes output
            - nodes: list of node names
        """
        result = {"success": False, "raw_output": "", "nodes": []}

        success, output = self._run_pcs("status nodes", "nodes")
        if not success:
            result["error"] = output
            return result

        result["success"] = True
        result["raw_output"] = output

        # Parse node names from various formats
        for line in output.split("\n"):
            line = line.strip()
            # Match patterns like "node1 node2 node3" after Online:/Offline:
            if ":" in line:
                parts = line.split(":", 1)
                if len(parts) == 2:
                    nodes = parts[1].strip().split()
                    result["nodes"].extend(nodes)

        return result

    def get_full_config(self) -> Dict[str, Any]:
        """Get complete cluster configuration.

        Returns:
            Dict combining all configuration sections
        """
        return {
            "cib_path": self.cib_path,
            "pcs_available": self._pcs_available,
            "resources": self.get_resources(),
            "resource_config": self.get_resource_config(),
            "constraints": self.get_constraints(),
            "properties": self.get_properties(),
            "stonith": self.get_stonith(),
            "nodes": self.get_nodes(),
        }

    def get_report_summary(self) -> Dict[str, Any]:
        """Get configuration summary suitable for PDF report.

        Returns:
            Dict with formatted data for report generation
        """
        config = self.get_full_config()

        summary = {
            "available": config["resources"].get("success", False),
            "source": self.cib_path,
            "resources": {
                "total": len(config["resources"].get("resources", [])),
                "clones": len(config["resources"].get("clones", [])),
                "primitives": len(config["resources"].get("primitives", [])),
                "list": config["resources"].get("resources", []),
                "raw": config["resources"].get("raw_output", ""),
            },
            "constraints": {
                "location": config["constraints"].get("location", []),
                "colocation": config["constraints"].get("colocation", []),
                "order": config["constraints"].get("order", []),
                "resource_discovery": config["constraints"].get("resource_discovery", []),
                "raw": config["constraints"].get("raw_output", ""),
            },
            "sap_hana": config["resource_config"].get("sap_hana", {}),
            "stonith": {
                "enabled": config["stonith"].get("enabled"),
                "devices": config["stonith"].get("devices", []),
            },
            "properties": config["properties"].get("properties", {}),
            # Node excluded from HANA resources (app server in Scale-Up, majority maker in Scale-Out)
            "hana_excluded_node": config["constraints"].get("hana_excluded_node"),
            "majority_maker": config["constraints"].get(
                "majority_maker"
            ),  # Legacy - check clone-max
            "majority_maker_info": config["constraints"].get("majority_maker_info", {}),
        }

        return summary
