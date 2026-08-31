"""
SAP Pacemaker Cluster Health Check - HANA Database Status

Mixin class providing HANA database status gathering and
System Replication topology analysis for ClusterHealthCheck.
"""

import re


class HanaStatusMixin:
    """Mixin providing HANA status methods for ClusterHealthCheck."""

    def _gather_hana_db_status(self, install_results: list, hana_nodes: dict):
        """
        Gather comprehensive HANA database status and replication info.

        Determines:
        - Whether the HANA database is running on each node
        - Whether HANA is managed by the cluster (resource running) or not
        - Replication status via the appropriate source:
          * DB running + resource running: already gathered by CHK_HANA_SR_STATUS
          * DB running + resource stopped/disabled: hdbnsutil -sr_state (direct)
          * DB NOT running: SAPHanaSR-stateConfiguration (last known config from CIB)

        Results are stored in self._hana_db_status for report generation.
        """
        from ..rules.engine import CheckStatus  # lazy import to avoid circular dependency

        cluster_running = True
        if self.access_config and hasattr(self.access_config, "clusters"):
            for cinfo in self.access_config.clusters.values():
                if cinfo.get("cluster_running") is False:
                    cluster_running = False
                    break

        hana_resource_active = self._hana_resource_state == "running"

        # Determine HANA managed state:
        # Managed = cluster is running AND resource is started/running
        hana_managed = cluster_running and hana_resource_active

        # Find nodes where HANA is installed and their running status
        hana_running_nodes = []
        hana_stopped_nodes = []
        sidadm = None

        for result in install_results:
            if result.status != CheckStatus.PASSED or not result.details:
                continue
            parsed = result.details.get("parsed", {})
            node_sidadm = parsed.get("sidadm")
            if node_sidadm:
                sidadm = node_sidadm  # Keep last valid sidadm for offline queries

            if parsed.get("hana_running") == "yes" and node_sidadm:
                hana_running_nodes.append(
                    {
                        "node": result.node,
                        "sidadm": node_sidadm,
                        "sid": parsed.get("sid"),
                    }
                )
            elif parsed.get("hana_installed") == "HANA_INSTALLED":
                hana_stopped_nodes.append(
                    {
                        "node": result.node,
                        "sidadm": node_sidadm,
                        "sid": parsed.get("sid"),
                    }
                )

        db_running = len(hana_running_nodes) > 0
        running_nodes = [n["node"] for n in hana_running_nodes]
        stopped_nodes = [n["node"] for n in hana_stopped_nodes]

        # Store status for report generation
        self._hana_db_status = {
            "db_running": db_running,
            "hana_managed": hana_managed,
            "running_nodes": running_nodes,
            "stopped_nodes": stopped_nodes,
            "hana_resource_state": self._hana_resource_state,
            "sr_source": None,
            "sr_info": None,
        }

        if db_running:
            print(f"  [INFO] HANA database running on: {', '.join(running_nodes)}")
        else:
            print("  [INFO] HANA database NOT running on any node")

        # Always gather SR topology when DB is running (for the report table)
        if db_running:
            self._query_sr_topology(hana_running_nodes, hana_nodes)

        if hana_managed:
            print(f"  [INFO] HANA is managed by the cluster (resource {self._hana_resource_state})")
            self._hana_db_status["sr_source"] = "hdbnsutil -sr_state"
            return

        state_detail = {
            "disabled": "resource disabled via target-role=Stopped",
            "stopped": "resource stopped",
            "unmanaged": "resource set to is-managed=false",
            "absent": "no HANA resource configured",
        }.get(self._hana_resource_state, f"resource {self._hana_resource_state}")
        print(f"  [INFO] HANA is NOT managed by the cluster ({state_detail})")

        # --- Gather replication info for non-managed scenarios ---

        if db_running:
            # Topology already gathered above; add maintenance check result
            self._add_maintenance_sr_result(hana_running_nodes[0]["node"])
        else:
            # DB not running: try offline config queries and global.ini
            self._query_sr_state_configuration(hana_nodes, sidadm)
            self._query_sr_topology_offline(hana_nodes, sidadm)

    def _query_sr_topology(self, hana_running_nodes: list, hana_nodes: dict):
        """Query hdbnsutil -sr_state from running nodes and parse SR topology."""
        for node_info in hana_running_nodes:
            sidadm = node_info["sidadm"]
            node_name = node_info["node"]

            if not re.match(r"^[a-z0-9]+adm$", sidadm):
                self._debug_print(f"Invalid sidadm user: {sidadm}")
                continue

            node_access = hana_nodes.get(node_name, {})
            method = node_access.get("preferred_method", "ssh")
            user = node_access.get("ssh_user")

            sr_cmd = f"su - {sidadm} -c 'hdbnsutil -sr_state' 2>/dev/null"
            self._debug_print(f"Running: {sr_cmd} on {node_name}")

            success, output = (
                self.rules_engine._execute_command_raw(  # pylint: disable=protected-access
                    sr_cmd, node_name, method, user
                )
            )

            if success and output and output.strip():
                self._hana_db_status["sr_source"] = "hdbnsutil -sr_state"
                self._hana_db_status["sr_info"] = output.strip()
                topology = self._parse_sr_topology(output)
                if topology:
                    self._hana_db_status["sr_topology"] = topology
                    print(f"  [OK] SR topology retrieved from {node_name}")
                    return  # Got what we need from one node
                self._debug_print(f"Could not parse SR topology from {node_name}")
            else:
                self._debug_print(f"hdbnsutil query failed on {node_name}")

    def _parse_sr_topology(self, output: str) -> dict:
        """Parse hdbnsutil -sr_state output into structured topology data.

        Supports multi-target replication (e.g., DC1 -> DC2, DC1 -> DC3).

        Returns dict with:
            mapping: str       - e.g. "DC1 -> DC2, DC1 -> DC3"
            sites: list[dict]  - [{name, role, op_mode, tier, hosts}, ...]
        """
        topology = {"mapping": None, "sites": []}

        # Extract all site mapping directions (multi-target has multiple lines)
        # "Mapping: DC1 -> DC2"
        mappings = re.findall(r"^Mapping:\s*(.+?)\s*$", output, re.MULTILINE)
        if mappings:
            topology["mapping"] = ", ".join(m.strip() for m in mappings)

        # Extract host-to-site mappings: "hostname -> [SiteName] hostname"
        host_site_map = {}  # {site_name: [hostname, ...]}
        for hm in re.finditer(r"(\S+)\s+->\s+\[(\S+)\]\s+(\S+)", output):
            site_name = hm.group(2)
            host = hm.group(3)
            if site_name not in host_site_map:
                host_site_map[site_name] = []
            if host not in host_site_map[site_name]:
                host_site_map[site_name].append(host)

        # Extract replication/operation modes per site
        site_repl_mode = {}
        site_op_mode = {}
        for rm in re.finditer(r"Replication mode of (\S+):\s*(\S+)", output):
            site_repl_mode[rm.group(1)] = rm.group(2)
        for om in re.finditer(r"Operation mode of (\S+):\s*(\S+)", output):
            site_op_mode[om.group(1)] = om.group(2)

        # Extract tiers: "Tier of DC1: 1"
        site_tiers = {}
        for tm in re.finditer(r"Tier of (\S+):\s*(\d+)", output):
            site_tiers[tm.group(1)] = int(tm.group(2))

        # Build sites list, ordered by tier (primary first)
        for site_name in sorted(host_site_map, key=lambda s: site_tiers.get(s, 99)):
            role = site_repl_mode.get(site_name, "unknown")
            op_mode = site_op_mode.get(site_name, "")
            tier = site_tiers.get(site_name)
            hosts = host_site_map[site_name]

            topology["sites"].append(
                {
                    "name": site_name,
                    "role": role,
                    "op_mode": op_mode,
                    "tier": tier,
                    "hosts": hosts,
                }
            )

        return topology if topology["sites"] else None

    def _add_maintenance_sr_result(self, node_name: str):
        """Add a CHK_HANA_SR_STATUS check result for maintenance (not-managed) scenarios."""
        from ..rules.engine import CheckResult, CheckStatus, Severity  # lazy import

        sr_info = self._hana_db_status.get("sr_info", "")
        self._hana_db_status["sr_source"] = "hdbnsutil -sr_state (direct query)"

        self.check_results.append(
            CheckResult(
                check_id="CHK_HANA_SR_STATUS",
                description="HANA System Replication status (direct query - resource not managed)",
                status=CheckStatus.PASSED,
                severity=Severity.WARNING,
                message=(
                    f"Replication info gathered directly from HANA (NOT via Pacemaker). "
                    f"HANA resource is {self._hana_resource_state}."
                ),
                details={
                    "maintenance_mode": True,
                    "hana_resource_state": self._hana_resource_state,
                    "sr_state_output": sr_info[:1000],
                    "source": "hdbnsutil -sr_state",
                    "note": "HANA is NOT managed by Pacemaker in this state",
                },
                node=node_name,
            )
        )
        # Remove the SKIPPED result we added earlier for CHK_HANA_SR_STATUS
        self.check_results = [
            r
            for r in self.check_results
            if not (
                r.check_id == "CHK_HANA_SR_STATUS"
                and r.status == CheckStatus.SKIPPED
                and "HANA resource is" in (r.message or "")
            )
        ]
        print(f"  [OK] Replication status retrieved from {node_name} (via hdbnsutil)")

    def _query_sr_topology_offline(self, hana_nodes: dict, sidadm: str = None):
        """Parse global.ini from each node to build SR topology when DB is stopped."""
        if not sidadm or not re.match(r"^[a-z0-9]+adm$", sidadm):
            return
        sid = sidadm[:-3].upper()

        sites = []
        for node_name, node_access in hana_nodes.items():
            method = node_access.get("preferred_method", "ssh")
            user = node_access.get("ssh_user")

            ini_cmd = (
                f"su - {sidadm} -c "
                f'\'grep -E "^(mode|site_id|site_name)" '
                f"/usr/sap/{sid}/SYS/global/hdb/custom/config/global.ini' "
                f"2>/dev/null"
            )
            self._debug_print(f"Reading global.ini from {node_name}")

            success, output = (
                self.rules_engine._execute_command_raw(  # pylint: disable=protected-access
                    ini_cmd, node_name, method, user
                )
            )

            if success and output:
                site_name = None
                role = None
                for line in output.strip().split("\n"):
                    line = line.strip()
                    if line.startswith("site_name"):
                        site_name = line.split("=", 1)[1].strip()
                    elif line.startswith("mode") and "=" in line:
                        role = line.split("=", 1)[1].strip()
                if site_name:
                    # Check if site already in list
                    existing = next((s for s in sites if s["name"] == site_name), None)
                    if existing:
                        if node_name not in existing["hosts"]:
                            existing["hosts"].append(node_name)
                    else:
                        sites.append(
                            {
                                "name": site_name,
                                "role": role or "unknown",
                                "op_mode": "",
                                "tier": None,
                                "hosts": [node_name],
                            }
                        )

        if sites:
            # Sort: primary first
            sites.sort(key=lambda s: 0 if s["role"] == "primary" else 1)
            primary = next((s["name"] for s in sites if s["role"] == "primary"), None)
            secondary = next((s["name"] for s in sites if s["role"] != "primary"), None)
            mapping = f"{primary} -> {secondary}" if primary and secondary else None

            self._hana_db_status["sr_topology"] = {
                "mapping": mapping,
                "sites": sites,
            }
            print(f"  [OK] SR topology from global.ini ({len(sites)} site(s))")

    def _query_sr_state_configuration(self, hana_nodes: dict, sidadm: str = None):
        """
        Query last known SR configuration when HANA database is NOT running.

        Tries in order:
        1. hdbnsutil -sr_stateConfiguration (works even when DB is down, requires sidadm)
        2. SAPHanaSR-stateConfiguration (ANGI/sap-hana-ha packages only, not on legacy Scale-Up)
        3. SAPHanaSR-showAttr (ANGI/sap-hana-ha packages only, not on legacy Scale-Up)
        4. crm_mon -A1 (works on all setups including legacy resource-agents-sap-hana)
        """
        # Try on any accessible node
        for node_name, node_access in hana_nodes.items():
            method = node_access.get("preferred_method", "ssh")
            if not method:
                continue
            user = node_access.get("ssh_user")

            # 1. Try hdbnsutil -sr_stateConfiguration via sidadm (primary method)
            if sidadm and re.match(r"^[a-z0-9]+adm$", sidadm):
                sr_cmd = f"su - {sidadm} -c 'hdbnsutil -sr_stateConfiguration' 2>/dev/null"
                self._debug_print(f"Running: {sr_cmd} on {node_name}")

                success, output = (
                    self.rules_engine._execute_command_raw(  # pylint: disable=protected-access
                        sr_cmd, node_name, method, user
                    )
                )

                if success and output and output.strip() and "not found" not in output.lower():
                    self._hana_db_status["sr_source"] = "hdbnsutil -sr_stateConfiguration"
                    self._hana_db_status["sr_info"] = output.strip()
                    print(f"  [OK] SR configuration retrieved via hdbnsutil on {node_name}")
                    return

            # 2. Try SAPHanaSR-stateConfiguration (ANGI packages only)
            sr_cmd = "SAPHanaSR-stateConfiguration 2>/dev/null"
            self._debug_print(f"Trying: {sr_cmd} on {node_name}")

            success, output = (
                self.rules_engine._execute_command_raw(  # pylint: disable=protected-access
                    sr_cmd, node_name, method, user
                )
            )

            if success and output and output.strip() and "not found" not in output.lower():
                self._hana_db_status["sr_source"] = "SAPHanaSR-stateConfiguration (CIB attributes)"
                self._hana_db_status["sr_info"] = output.strip()
                print(f"  [OK] SR configuration retrieved from CIB via {node_name}")
                return

            # 3. Try SAPHanaSR-showAttr (ANGI packages only)
            sr_cmd = "SAPHanaSR-showAttr 2>/dev/null"
            self._debug_print(f"Trying: {sr_cmd} on {node_name}")

            success, output = (
                self.rules_engine._execute_command_raw(  # pylint: disable=protected-access
                    sr_cmd, node_name, method, user
                )
            )

            if success and output and output.strip() and "not found" not in output.lower():
                self._hana_db_status["sr_source"] = "SAPHanaSR-showAttr (CIB attributes)"
                self._hana_db_status["sr_info"] = output.strip()
                print(f"  [OK] SR attributes retrieved from CIB via {node_name}")
                return

            # 4. Fallback for legacy Scale-Up (resource-agents-sap-hana):
            #    crm_mon -A1 shows node attributes including SR state from CIB
            sr_cmd = "crm_mon -A1 2>/dev/null | grep -iE 'hana|srmode|sync|site|sra|srah|lss|srr'"
            self._debug_print(f"Legacy fallback: crm_mon -A1 on {node_name}")

            success, output = (
                self.rules_engine._execute_command_raw(  # pylint: disable=protected-access
                    sr_cmd, node_name, method, user
                )
            )

            if success and output and output.strip():
                self._hana_db_status["sr_source"] = "crm_mon -A1 (CIB node attributes)"
                self._hana_db_status["sr_info"] = output.strip()
                print(f"  [OK] SR attributes retrieved from CIB node attributes via {node_name}")
                return

        self._debug_print("Could not retrieve SR configuration from any node")
