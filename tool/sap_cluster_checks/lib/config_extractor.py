"""
Cluster Configuration Extractor

Extracts SAP HANA cluster configuration from pcs config output.
Works with:
- SOSreport: reads sos_commands/pacemaker/pcs_config file
- Offline cluster (SSH): runs pcs -f /var/lib/pacemaker/cib/cib.xml config
- Running cluster: runs pcs config (active configuration)

Output: YAML file with standardized cluster configuration for PDF report generation.
"""

import re
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime

import yaml


class ConfigExtractor:
    """Extract cluster configuration from pcs config output."""

    # Default cib.xml location
    DEFAULT_CIB_PATH = "/var/lib/pacemaker/cib/cib.xml"

    def __init__(self):
        self.config = {}
        self._raw_output = ""
        self._source = None

    @classmethod
    def from_sosreport(cls, sosreport_path: str) -> Optional["ConfigExtractor"]:
        """Create extractor from SOSreport directory.

        Args:
            sosreport_path: Path to extracted SOSreport directory

        Returns:
            ConfigExtractor instance or None if pcs_config not found
        """
        sos_path = Path(sosreport_path)  # pylint: disable=redefined-outer-name
        pcs_config_path = sos_path / "sos_commands/pacemaker/pcs_config"

        if not pcs_config_path.exists():
            return None

        extractor = cls()  # pylint: disable=redefined-outer-name
        extractor._raw_output = pcs_config_path.read_text()
        extractor._source = f"sosreport:{sosreport_path}"
        extractor._parse_pcs_config()
        return extractor

    @classmethod
    def from_running_cluster(
        cls, host: str = None, user: str = "root"
    ) -> Optional["ConfigExtractor"]:
        """Create extractor from running cluster.

        Args:
            host: Remote host (None for local)
            user: SSH user for remote access

        Returns:
            ConfigExtractor instance or None if failed
        """
        try:
            if host:
                cmd = f"ssh {user}@{host} 'pcs config'"
            else:
                cmd = "pcs config"

            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=30, check=False
            )
            if result.returncode != 0:
                return None

            extractor = cls()  # pylint: disable=redefined-outer-name
            extractor._raw_output = result.stdout
            extractor._source = f"running_cluster:{host or 'local'}"
            extractor._parse_pcs_config()
            return extractor
        except Exception:
            return None

    @classmethod
    def from_ssh_offline(
        cls, host: str, user: str = "root", cib_path: str = None
    ) -> Optional["ConfigExtractor"]:
        """Create extractor from offline cluster via SSH.

        Args:
            host: Remote host
            user: SSH user
            cib_path: Path to cib.xml on remote host

        Returns:
            ConfigExtractor instance or None if failed
        """
        cib_path = cib_path or cls.DEFAULT_CIB_PATH

        try:
            cmd = f"ssh {user}@{host} 'pcs -f {cib_path} config'"
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=30, check=False
            )
            if result.returncode != 0:
                return None

            extractor = cls()  # pylint: disable=redefined-outer-name
            extractor._raw_output = result.stdout
            extractor._source = f"ssh_offline:{host}"
            extractor._parse_pcs_config()
            return extractor
        except Exception:
            return None

    def _parse_pcs_config(self):
        """Parse pcs config output and extract all configuration."""
        self.config = {
            "extracted_at": datetime.now().isoformat(),
            "source": getattr(self, "_source", "unknown"),
            "cluster": {},
            "sap_hana": {},
            "resources": {},
            "stonith": {},
            "constraints": {},
            "properties": {},
        }

        self._clone_type_map = {}  # Maps clone name -> resource type category

        self._parse_cluster_info()
        self._parse_resources()
        self._parse_stonith()
        self._parse_constraints()
        self._parse_properties()

    def _parse_cluster_info(self):
        """Extract cluster name and basic info."""
        # Cluster Name: from "Cluster Name:" line
        match = re.search(r"Cluster Name:\s*(\S+)", self._raw_output)
        if match:
            self.config["cluster"]["name"] = match.group(1)

    def _parse_resources(self):
        """Extract SAP HANA and related resources."""
        lines = self._raw_output.split("\n")

        # Track current resource being parsed
        current_clone = None
        current_resource = None
        current_section = None  # 'attributes', 'meta', 'operations'
        pending_vips = []  # Store VIP resources to update IPs later

        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            # Detect Clone/Promotable resources
            clone_match = re.match(r"\s*Clone:\s*(\S+)", line)
            if clone_match:
                current_clone = clone_match.group(1)
                current_resource = None
                current_section = None
                i += 1
                continue

            # Detect Group (reset clone context)
            group_match = re.match(r"\s*Group:\s*(\S+)", line)
            if group_match:
                current_clone = None
                current_resource = None
                current_section = None
                i += 1
                continue

            # Detect Resource (standalone or within Clone/Group)
            resource_match = re.match(
                r"\s*Resource:\s*(\S+)\s*\(class=(\S+)\s+(?:provider=(\S+)\s+)?type=(\S+)\)", line
            )
            if resource_match:
                res_name = resource_match.group(1)
                res_class = resource_match.group(2)
                res_provider = resource_match.group(3) or ""
                res_type = resource_match.group(4)

                current_resource = {
                    "name": res_name,
                    "class": res_class,
                    "provider": res_provider,
                    "type": res_type,
                    "clone": current_clone,
                    "attributes": {},
                    "meta_attributes": {},
                    "operations": [],
                }

                # Categorize resource
                if "SAPHanaController" in res_type:
                    self._store_saphana_resource("controller", current_resource, current_clone)
                elif "SAPHanaTopology" in res_type:
                    self._store_saphana_resource("topology", current_resource, current_clone)
                elif "SAPHanaFilesystem" in res_type:
                    self._store_saphana_resource("filesystem", current_resource, current_clone)
                elif (
                    "SAPHana" in res_type
                    and "Controller" not in res_type
                    and "Topology" not in res_type
                ):
                    self._store_saphana_resource("saphana", current_resource, current_clone)
                elif "IPaddr2" in res_type or "IPaddr" in res_type:
                    # Store VIP resource - will update IP when attributes are parsed
                    pending_vips.append(current_resource)

                # Record clone-to-type mapping for _parse_clone_meta()
                if current_clone:
                    if "SAPHanaController" in res_type or (
                        "SAPHana" in res_type
                        and "Controller" not in res_type
                        and "Topology" not in res_type
                        and "Filesystem" not in res_type
                    ):
                        self._clone_type_map[current_clone] = "saphana"
                    elif "SAPHanaTopology" in res_type:
                        self._clone_type_map[current_clone] = "topology"

                current_section = None
                i += 1
                continue

            # Detect Attributes section
            if "Attributes:" in stripped and current_resource:
                current_section = "attributes"
                i += 1
                continue

            # Detect Meta Attributes section
            if "Meta Attributes:" in stripped:
                current_section = "meta"
                # Check if this is clone meta or resource meta
                if current_resource is None and current_clone:
                    # Clone-level meta attributes
                    self._parse_clone_meta(lines, i, current_clone)
                i += 1
                continue

            # Detect Operations section
            if "Operations:" in stripped and current_resource:
                current_section = "operations"
                i += 1
                continue

            # Parse attribute values
            if current_section == "attributes" and current_resource and "=" in stripped:
                attr_match = re.match(r"(\S+)=(.+)", stripped)
                if attr_match:
                    key = attr_match.group(1)
                    value = attr_match.group(2).strip()
                    current_resource["attributes"][key] = value

                    # Extract key SAP HANA attributes
                    if key == "SID":
                        self.config["sap_hana"]["sid"] = value
                    elif key == "InstanceNumber":
                        self.config["sap_hana"]["instance_number"] = value
                    elif key == "AUTOMATED_REGISTER":
                        self.config["sap_hana"]["automated_register"] = value.lower() == "true"
                    elif key == "PREFER_SITE_TAKEOVER":
                        self.config["sap_hana"]["prefer_site_takeover"] = value.lower() == "true"
                    elif key == "DUPLICATE_PRIMARY_TIMEOUT":
                        try:
                            self.config["sap_hana"]["duplicate_primary_timeout"] = int(value)
                        except ValueError:
                            pass

            i += 1

        # Process pending VIP resources (now that attributes are parsed)
        for vip_resource in pending_vips:
            self._store_vip_resource(vip_resource)

    def _store_saphana_resource(self, res_type: str, resource: dict, clone_name: str):
        """Store SAP HANA resource configuration."""
        self.config["sap_hana"][res_type] = {
            "resource_name": resource["name"],
            "clone_name": clone_name,
            "type": resource["type"],
            "attributes": resource["attributes"],
        }

        # Set main resource info
        if res_type in ("controller", "saphana"):
            self.config["sap_hana"]["resource_type"] = (
                "SAPHanaController" if res_type == "controller" else "SAPHana"
            )
            self.config["sap_hana"]["resource_name"] = resource["name"]

    def _store_vip_resource(self, resource: dict):
        """Store VIP resource configuration."""
        if "vips" not in self.config["resources"]:
            self.config["resources"]["vips"] = []

        vip_info = {
            "resource_name": resource["name"],
            "ip": resource["attributes"].get("ip", ""),
            "cidr_netmask": resource["attributes"].get("cidr_netmask", ""),
            "nic": resource["attributes"].get("nic", ""),
        }
        self.config["resources"]["vips"].append(vip_info)

        # Detect SAP HANA VIPs - look for vip_<SID>_<Instance> or vip2_<SID>_<Instance> pattern
        # Get SID if already detected
        sid = self.config["sap_hana"].get("sid", "").lower()
        res_name_lower = resource["name"].lower()

        # Secondary VIP: vip2_<SID> pattern (must contain SID if known)
        is_secondary = "vip2" in res_name_lower
        if is_secondary:
            # If we know SID, verify this VIP is for this SID
            if not sid or sid in res_name_lower:
                self.config["sap_hana"]["secondary_vip"] = vip_info["ip"]
                self.config["sap_hana"]["secondary_vip_resource"] = resource["name"]
        # Primary HANA VIP: vip_<SID> pattern (not vip2, not ascs/ers/pas/aas)
        elif re.match(r"^vip_[a-z0-9]+_\d+$", res_name_lower):
            # Pattern: vip_<SID>_<InstanceNumber> - this is a HANA VIP
            if not sid or sid in res_name_lower:
                self.config["sap_hana"]["virtual_ip"] = vip_info["ip"]
                self.config["sap_hana"]["vip_resource"] = resource["name"]

    def _parse_clone_meta(self, lines: List[str], start_idx: int, clone_name: str):
        """Parse clone-level meta attributes."""
        clone_meta = {}
        i = start_idx + 1

        while i < len(lines):
            line = lines[i].strip()
            if not line or line.startswith("Resource:") or line.startswith("Clone:"):
                break

            match = re.match(r"(\S+)=(\S+)", line)
            if match:
                clone_meta[match.group(1)] = match.group(2)
            i += 1

        # Identify clone type by the resource agent type, not the clone name
        clone_type = self._clone_type_map.get(clone_name)
        if clone_type == "saphana":
            if "clone-max" in clone_meta:
                self.config["sap_hana"]["clone_max"] = int(clone_meta["clone-max"])
            if "promotable" in clone_meta:
                self.config["sap_hana"]["promotable"] = clone_meta["promotable"].lower() == "true"
            if "interleave" in clone_meta:
                self.config["sap_hana"]["interleave"] = clone_meta["interleave"].lower() == "true"
        elif clone_type == "topology":
            if "clone-max" in clone_meta:
                self.config["sap_hana"]["topology_clone_max"] = int(clone_meta["clone-max"])

    def _parse_stonith(self):
        """Extract STONITH/fencing configuration."""
        lines = self._raw_output.split("\n")

        in_stonith = False
        current_device = None

        for line in lines:
            stripped = line.strip()

            # Detect STONITH resource
            if "class=stonith" in line:
                match = re.match(r"\s*Resource:\s*(\S+)\s*\(class=stonith\s+type=(\S+)\)", line)
                if match:
                    current_device = {
                        "name": match.group(1),
                        "type": match.group(2),
                        "attributes": {},
                    }
                    self.config["stonith"]["device"] = match.group(1)
                    self.config["stonith"]["type"] = match.group(2)
                    in_stonith = True
                continue

            # Parse STONITH attributes
            if in_stonith and current_device and "=" in stripped:
                # Stop at next section
                if (
                    stripped.startswith("Operations:")
                    or stripped.startswith("Resource:")
                    or stripped.startswith("Clone:")
                ):
                    in_stonith = False
                    continue

                match = re.match(r"(\S+)=(.+)", stripped)
                if match:
                    key = match.group(1)
                    value = match.group(2).strip()
                    current_device["attributes"][key] = value

                    # Extract key STONITH attributes
                    if key == "pcmk_host_map":
                        self.config["stonith"]["pcmk_host_map"] = value
                        # Parse host map into structured format
                        host_map = {}
                        for mapping in value.split(";"):
                            if ":" in mapping:
                                node, target = mapping.split(":", 1)
                                host_map[node.strip()] = target.strip()
                        self.config["stonith"]["host_map"] = host_map
                    elif key in ("ip", "username", "ssl", "ssl_insecure", "power_wait"):
                        self.config["stonith"][key] = value

    def _parse_constraints(self):
        """Extract location, colocation, and order constraints."""
        lines = self._raw_output.split("\n")

        current_section = None
        constraints = {
            "location": [],
            "colocation": [],
            "order": [],
            "hana_excluded_node": None,  # Node with HANA exclusion constraints
            "majority_maker": None,  # Only set if clone-max >= 4 (Scale-Out)
        }
        # Track which nodes have both SAPHanaTopology AND SAPHanaController exclusion constraints
        nodes_excluded_from_controller = set()
        nodes_excluded_from_topology = set()

        for line in lines:
            stripped = line.strip()

            if stripped.startswith("Location Constraints:"):
                current_section = "location"
                continue
            if stripped.startswith("Colocation Constraints:"):
                current_section = "colocation"
                continue
            if stripped.startswith("Ordering Constraints:"):
                current_section = "order"
                continue
            if stripped.startswith("Ticket Constraints:") or stripped.startswith("Resources:"):
                current_section = None
                continue

            if current_section and stripped:
                constraints[current_section].append(stripped)

                # Track location constraints that exclude HANA resources from nodes
                if (
                    current_section == "location"
                    and "avoids node" in stripped
                    and "INFINITY" in stripped
                ):
                    match = re.search(r"avoids node '([^']+)'", stripped)
                    if match:
                        node = match.group(1)
                        if "SAPHanaController" in stripped:
                            nodes_excluded_from_controller.add(node)
                        if "SAPHanaTopology" in stripped:
                            nodes_excluded_from_topology.add(node)

        # A node excluded from BOTH SAPHanaTopology AND SAPHanaController
        # is either an app server (Scale-Up) or majority maker (Scale-Out)
        # The distinction depends on clone-max which is checked later
        excluded_from_both = nodes_excluded_from_controller & nodes_excluded_from_topology
        if excluded_from_both:
            constraints["hana_excluded_node"] = sorted(excluded_from_both)[0]

        self.config["constraints"] = constraints

    def _parse_properties(self):
        """Extract cluster properties."""
        lines = self._raw_output.split("\n")

        in_properties = False
        properties = {}

        for line in lines:
            stripped = line.strip()

            if "Cluster Properties:" in stripped:
                in_properties = True
                continue

            if in_properties:
                # Stop at next section
                if stripped.startswith("Resource Defaults:") or stripped.startswith(
                    "Operation Defaults:"
                ):
                    break

                # Parse property=value (default) or property=value
                match = re.match(r"(\S+)=(\S+)(?:\s+\(default\))?", stripped)
                if match:
                    properties[match.group(1)] = match.group(2)

        self.config["properties"] = properties

        # Extract key properties
        if "stonith-enabled" in properties:
            self.config["stonith"]["enabled"] = properties["stonith-enabled"].lower() == "true"

        # Extract versions from dc-version (format: 2.1.9-1.el9-49aab9983)
        dc_version = properties.get("dc-version", "")
        if dc_version:
            # Extract Pacemaker version (first part before -)
            pacemaker_match = re.match(r"(\d+\.\d+\.\d+)", dc_version)
            if pacemaker_match:
                self.config["cluster"]["pacemaker_version"] = pacemaker_match.group(1)

            # Extract RHEL version from el<N> pattern
            rhel_match = re.search(r"\.el(\d+)", dc_version)
            if rhel_match:
                self.config["cluster"]["rhel_version"] = f"RHEL {rhel_match.group(1)}"

    def get_config(self) -> Dict[str, Any]:
        """Return the extracted configuration."""
        return self.config

    def write_yaml(self, output_path: str) -> str:
        """Write configuration to YAML file.

        Args:
            output_path: Path to output YAML file

        Returns:
            Path to written file
        """
        output_path = Path(output_path)

        with open(output_path, "w", encoding="utf-8") as f:
            yaml.dump(self.config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

        return str(output_path)

