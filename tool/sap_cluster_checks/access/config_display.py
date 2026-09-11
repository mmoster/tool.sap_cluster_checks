"""
SAP Pacemaker Cluster Health Check - Configuration Display Module

Provides functions for displaying, exporting, and managing the
cluster configuration:
- show_config: Display configuration in a user-friendly format
- export_ansible_vars: Export cluster config as Ansible group_vars YAML
- delete_config: Delete health check reports and status files
"""

import os
from pathlib import Path

import yaml


def show_config(config_path: Path, cluster_or_node: str = None, config_only: bool = False):
    """Display the current configuration in a user-friendly format.

    Args:
        config_path: Path to the configuration file
        cluster_or_node: Optional cluster name or hostname to filter output.
                         If a cluster name is provided, shows that cluster.
                         If a hostname is provided, shows the cluster containing that node.
        config_only: If True, show only cluster configuration (no node summary,
                     quick commands, or other sections)
    """
    if not config_path.exists():
        print(f"No configuration file found at {config_path}")
        print("\nRun discovery first:")
        print("  ./sap_cluster_checks.py hana01")
        return False

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    clusters = config.get("clusters", {})
    all_nodes = config.get("nodes", {})

    # Resolve cluster_or_node to a cluster name
    cluster_name = None
    if cluster_or_node:
        # First, check if it's a cluster name
        if cluster_or_node in clusters:
            cluster_name = cluster_or_node
        else:
            # Check if it's a hostname - find which cluster contains it
            for cname, cinfo in clusters.items():
                if cluster_or_node in cinfo.get("nodes", []):
                    cluster_name = cname
                    print(f"[INFO] Node '{cluster_or_node}' belongs to cluster '{cluster_name}'")
                    break

            if not cluster_name:
                # Not found as cluster or node in any cluster
                if cluster_or_node in all_nodes:
                    # It's a known node but not in any cluster
                    print(
                        f"\n[INFO] Node '{cluster_or_node}' found but not assigned to any cluster"
                    )
                else:
                    print(f"\n[ERROR] '{cluster_or_node}' not found as cluster or node")

                if clusters:
                    print(f"\nAvailable clusters: {', '.join(clusters.keys())}")
                    print(f"Available nodes: {', '.join(list(all_nodes.keys())[:10])}", end="")
                    if len(all_nodes) > 10:
                        print(f" ... and {len(all_nodes) - 10} more")
                    else:
                        print()
                    print("\nTo show all configuration:")
                    print("  ./sap_cluster_checks.py --show-config")
                else:
                    print("\nNo clusters discovered yet. Run discovery first:")
                    print("  ./sap_cluster_checks.py hana01")
                return False

    print("\n" + "=" * 60)
    if cluster_name:
        print(f" SAP Cluster Health Check - Cluster: {cluster_name}")
    else:
        print(" SAP Cluster Health Check - Configuration")
    print("=" * 60)
    print(f"Config file: {config_path}")

    # Show clusters (filtered if cluster_name specified)
    if clusters:
        clusters_to_show = {cluster_name: clusters[cluster_name]} if cluster_name else clusters

        if cluster_name:
            print(f"\n--- Cluster: {cluster_name} ---")
        else:
            print("\n--- Discovered Clusters ---")

        for name, info in clusters_to_show.items():
            cluster_nodes = info.get("nodes", [])
            discovered_from = info.get("discovered_from", "unknown")
            if not cluster_name:
                print(f"\n  Cluster: {name}")
            print(f"    Nodes: {', '.join(cluster_nodes)}")
            print(f"    Discovered from: {discovered_from}")

            # Always show basic cluster info
            discovered_at = info.get("discovered_at", "")
            cluster_running = info.get("cluster_running")
            rhel_version = info.get("rhel_version", "")
            pacemaker_version = info.get("pacemaker_version", "")

            if discovered_at:
                print(f"    Discovered at: {discovered_at[:19]}")  # Trim microseconds
            if cluster_running is not None:
                status = "Running" if cluster_running else "Stopped"
                print(f"    Cluster status: {status}")
            if rhel_version:
                print(f"    RHEL version: {rhel_version}")
            if pacemaker_version:
                print(f"    Pacemaker version: {pacemaker_version}")

            # Show SAP HANA info if available (Ansible-compatible parameters)
            sid = info.get("sid")
            if sid:
                inst = info.get("instance_number", "??")
                resource_type = info.get("resource_type", "SAPHana")

                print("\n    SAP HANA HA Configuration (Ansible-compatible):")
                print("    " + "-" * 40)

                # Cluster name and nodes
                print(f"      cluster_name: {name}")
                print(f"      cluster_nodes: [{', '.join(cluster_nodes)}]")

                # Core Parameters
                print(f"      hana_sid: {sid}")
                print(f'      hana_instance_number: "{inst}"')

                # HANA landscape
                cluster_type = "Scale-Up" if resource_type == "SAPHana" else "Scale-Out"
                print(f"      hana_landscape: {cluster_type}")

                # Node Information
                node1_hostname = info.get("node1_hostname", "")
                node1_fqdn = info.get("node1_fqdn", "")
                node1_ip = info.get("node1_ip", "")
                node2_hostname = info.get("node2_hostname", "")
                node2_fqdn = info.get("node2_fqdn", "")
                node2_ip = info.get("node2_ip", "")
                if node1_hostname or node1_fqdn or node1_ip:
                    print("\n      # Node 1 (Primary Site)")
                    if node1_hostname:
                        print(f"      node1_hostname: {node1_hostname}")
                    if node1_fqdn:
                        print(f"      node1_fqdn: {node1_fqdn}")
                    if node1_ip:
                        print(f"      node1_ip: {node1_ip}")
                if node2_hostname or node2_fqdn or node2_ip:
                    print("\n      # Node 2 (Secondary Site)")
                    if node2_hostname:
                        print(f"      node2_hostname: {node2_hostname}")
                    if node2_fqdn:
                        print(f"      node2_fqdn: {node2_fqdn}")
                    if node2_ip:
                        print(f"      node2_ip: {node2_ip}")

                # Virtual IP
                virtual_ip = info.get("virtual_ip", "")
                vip_resource = info.get("vip_resource", "")
                secondary_vip = info.get("secondary_vip", "")
                secondary_vip_resource = info.get("secondary_vip_resource", "")
                if virtual_ip or secondary_vip:
                    print("\n      # Virtual IP Configuration")
                    if virtual_ip:
                        print(f"      vip: {virtual_ip}")
                    if vip_resource:
                        print(f"      vip_resource: {vip_resource}")
                    if secondary_vip:
                        print(f"      secondary_vip: {secondary_vip}")
                        if secondary_vip_resource:
                            print(f"      secondary_vip_resource: {secondary_vip_resource}")

                # System Replication
                repl_mode = info.get("replication_mode", "")
                op_mode = info.get("operation_mode", "")
                sites = info.get("sites", [])
                site1 = info.get("site1_name", "")
                site2 = info.get("site2_name", "")
                if repl_mode or op_mode or sites:
                    print("\n      # System Replication")
                    if repl_mode:
                        print(f"      replication_mode: {repl_mode}")
                    if op_mode:
                        print(f"      operation_mode: {op_mode}")
                    if site1:
                        print(f"      site1_name: {site1}")
                    if site2:
                        print(f"      site2_name: {site2}")
                    elif sites:
                        print(f"      sites: {', '.join(sites)}")

                # Resource Names
                resource_name = info.get("resource_name", "")
                topology_resource = info.get("topology_resource", "")
                if resource_name or topology_resource:
                    print("\n      # Pacemaker Resources")
                    if resource_name:
                        print(f"      hana_resource: {resource_name}")
                    if topology_resource:
                        print(f"      topology_resource: {topology_resource}")

                # STONITH
                stonith_device = info.get("stonith_device", "")
                stonith_type = info.get("stonith_type", "")
                stonith_params = info.get("stonith_params", {})
                if stonith_device or stonith_params:
                    print("\n      # STONITH/Fencing")
                    if stonith_device:
                        print(f"      stonith_device: {stonith_device}")
                    if stonith_type:
                        print(f"      stonith_type: {stonith_type}")
                    if stonith_params:
                        pcmk_host_map = stonith_params.get("pcmk_host_map", "")
                        if pcmk_host_map:
                            print(f"      pcmk_host_map: {pcmk_host_map}")
                        for key, value in stonith_params.items():
                            if key != "pcmk_host_map":
                                print(f"      {key}: {value}")

                # Cluster Properties
                stickiness = info.get("resource_stickiness")
                migration = info.get("migration_threshold")
                auto_reg = info.get("automated_register")
                prefer_takeover = info.get("prefer_site_takeover")
                dup_primary_timeout = info.get("duplicate_primary_timeout")
                secondary_read = info.get("secondary_read")

                has_props = (
                    stickiness
                    or migration
                    or auto_reg is not None
                    or prefer_takeover is not None
                    or dup_primary_timeout
                    or secondary_read is not None
                )
                if has_props:
                    print("\n      # Cluster Properties")
                    if stickiness:
                        print(f"      resource_stickiness: {stickiness}")
                    if migration is not None:
                        print(f"      migration_threshold: {migration}")
                    if auto_reg is not None:
                        print(f"      automated_register: {str(auto_reg).lower()}")
                    if prefer_takeover is not None:
                        print(f"      prefer_site_takeover: {str(prefer_takeover).lower()}")
                    if dup_primary_timeout:
                        print(f"      duplicate_primary_timeout: {dup_primary_timeout}")
                    if secondary_read is not None:
                        print(f"      secondary_read: {str(secondary_read).lower()}")
            else:
                # Show whatever info we have even without SID
                # This can still be quite comprehensive
                print("\n    Cluster Configuration (SID not stored):")
                print("    " + "-" * 44)

                # Resource type and HANA landscape
                resource_type = info.get("resource_type", "")
                if resource_type:
                    cluster_type = "Scale-Up" if resource_type == "SAPHana" else "Scale-Out"
                    print(f"      hana_landscape: {cluster_type}")
                    print(f"      resource_type: {resource_type}")

                # Node Information
                node1_hostname = info.get("node1_hostname", "")
                node1_fqdn = info.get("node1_fqdn", "")
                node1_ip = info.get("node1_ip", "")
                node2_hostname = info.get("node2_hostname", "")
                node2_fqdn = info.get("node2_fqdn", "")
                node2_ip = info.get("node2_ip", "")
                if node1_hostname or node1_fqdn or node1_ip:
                    print("\n      # Node 1")
                    if node1_hostname:
                        print(f"      node1_hostname: {node1_hostname}")
                    if node1_fqdn:
                        print(f"      node1_fqdn: {node1_fqdn}")
                    if node1_ip:
                        print(f"      node1_ip: {node1_ip}")
                if node2_hostname or node2_fqdn or node2_ip:
                    print("\n      # Node 2")
                    if node2_hostname:
                        print(f"      node2_hostname: {node2_hostname}")
                    if node2_fqdn:
                        print(f"      node2_fqdn: {node2_fqdn}")
                    if node2_ip:
                        print(f"      node2_ip: {node2_ip}")

                # Virtual IP
                virtual_ip = info.get("virtual_ip", "")
                vip_resource = info.get("vip_resource", "")
                secondary_vip = info.get("secondary_vip", "")
                secondary_vip_resource = info.get("secondary_vip_resource", "")
                if virtual_ip or secondary_vip:
                    print("\n      # Virtual IP Configuration")
                    if virtual_ip:
                        print(f"      vip: {virtual_ip}")
                    if vip_resource:
                        print(f"      vip_resource: {vip_resource}")
                    if secondary_vip:
                        print(f"      secondary_vip: {secondary_vip}")
                        if secondary_vip_resource:
                            print(f"      secondary_vip_resource: {secondary_vip_resource}")

                # System Replication
                repl_mode = info.get("replication_mode", "")
                op_mode = info.get("operation_mode", "")
                sites = info.get("sites", [])
                site1 = info.get("site1_name", "")
                site2 = info.get("site2_name", "")
                if repl_mode or op_mode or sites or site1:
                    print("\n      # System Replication")
                    if repl_mode:
                        print(f"      replication_mode: {repl_mode}")
                    if op_mode:
                        print(f"      operation_mode: {op_mode}")
                    if site1:
                        print(f"      site1_name: {site1}")
                    if site2:
                        print(f"      site2_name: {site2}")
                    elif sites:
                        print(f"      sites: {', '.join(sites)}")

                # STONITH
                stonith_device = info.get("stonith_device", "")
                stonith_params = info.get("stonith_params", {})
                if stonith_device or stonith_params:
                    print("\n      # STONITH/Fencing")
                    if stonith_device:
                        print(f"      stonith_device: {stonith_device}")
                    if stonith_params:
                        pcmk_host_map = stonith_params.get("pcmk_host_map", "")
                        if pcmk_host_map:
                            print(f"      pcmk_host_map: {pcmk_host_map}")
                        for key, value in stonith_params.items():
                            if key != "pcmk_host_map":
                                print(f"      {key}: {value}")

                # Cluster Properties
                auto_reg = info.get("automated_register")
                prefer_takeover = info.get("prefer_site_takeover")
                dup_primary_timeout = info.get("duplicate_primary_timeout")
                secondary_read = info.get("secondary_read")
                has_props = (
                    auto_reg is not None
                    or prefer_takeover is not None
                    or dup_primary_timeout
                    or secondary_read is not None
                )
                if has_props:
                    print("\n      # Cluster Properties")
                    if auto_reg is not None:
                        print(f"      automated_register: {str(auto_reg).lower()}")
                    if prefer_takeover is not None:
                        print(f"      prefer_site_takeover: {str(prefer_takeover).lower()}")
                    if dup_primary_timeout:
                        print(f"      duplicate_primary_timeout: {dup_primary_timeout}")
                    if secondary_read is not None:
                        print(f"      secondary_read: {str(secondary_read).lower()}")

            if not config_only:
                print("\n    To check this cluster:")
                print(f"      ./sap_cluster_checks.py -C {name}")
    else:
        print("\n[INFO] No clusters discovered yet")
        print("  Run: ./sap_cluster_checks.py hana01")

    # Skip remaining sections if config_only mode
    if config_only:
        return True

    # Show node summary (filtered to cluster nodes if cluster_name specified)
    if cluster_name and cluster_name in clusters:
        cluster_node_names = clusters[cluster_name].get("nodes", [])
        nodes = {n: all_nodes[n] for n in cluster_node_names if n in all_nodes}
        node_label = f"Nodes in Cluster '{cluster_name}'"
    else:
        nodes = all_nodes
        node_label = "All Discovered Nodes"

    if nodes:
        print(f"\n--- {node_label} ({len(nodes)}) ---")
        accessible = [n for n, info in nodes.items() if info.get("preferred_method")]
        no_access = [n for n, info in nodes.items() if not info.get("preferred_method")]

        if accessible:
            print(f"\n  Accessible ({len(accessible)}):")
            for name in sorted(accessible)[:10]:  # Show first 10
                info = nodes[name]
                method = info.get("preferred_method", "none")
                machine_id = info.get("machine_id", "")
                machine_id_short = f" [{machine_id[:8]}]" if machine_id else ""
                print(f"    {name}: {method}{machine_id_short}")
            if len(accessible) > 10:
                print(f"    ... and {len(accessible) - 10} more")

        if no_access:
            print(f"\n  No access ({len(no_access)}): {', '.join(sorted(no_access)[:5])}", end="")
            if len(no_access) > 5:
                print(f" ... and {len(no_access) - 5} more")
            else:
                print()

    # Show other config (only in full view, not cluster-specific)
    if not cluster_name:
        if config.get("sosreport_directory"):
            print("\n--- SOSreport Directory ---")
            print(f"  {config['sosreport_directory']}")

        if config.get("ansible_inventory_path"):
            print("\n--- Ansible Inventory ---")
            print(f"  Path: {config['ansible_inventory_path']}")
            print(f"  Source: {config.get('ansible_inventory_source', 'unknown')}")

    print("\n--- Quick Commands ---")
    if cluster_name:
        print(f"  Check cluster:    ./sap_cluster_checks.py -C {cluster_name}")
        print("  Show all config:  ./sap_cluster_checks.py --show-config")
    elif clusters:
        first_cluster = list(clusters.keys())[0]
        print(f"  Check cluster:    ./sap_cluster_checks.py -C {first_cluster}")
        print(f"  Show one cluster: ./sap_cluster_checks.py --show-config {first_cluster}")
    print("  Force rediscover: ./sap_cluster_checks.py -f hana01")
    print("  Delete config:    ./sap_cluster_checks.py -D")
    print("  Show guide:       ./sap_cluster_checks.py --guide")

    return True


def export_ansible_vars(config_path: Path, cluster_name: str, output_file: str = None):
    """
    Export cluster configuration as Ansible group_vars YAML file.

    Args:
        config_path: Path to the configuration file
        cluster_name: Name of the cluster to export
        output_file: Optional output file path. If not provided, prints to stdout.
    """
    if not config_path.exists():
        print(f"No configuration file found at {config_path}")
        return False

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    clusters = config.get("clusters", {})

    if cluster_name not in clusters:
        print(f"Cluster '{cluster_name}' not found in configuration.")
        print(f"Available clusters: {', '.join(clusters.keys())}")
        return False

    info = clusters[cluster_name]
    cluster_nodes = info.get("nodes", [])
    sid = info.get("sid", "")

    if not sid:
        print(f"No SAP HANA configuration found for cluster '{cluster_name}'.")
        print("Run discovery with: ./sap_cluster_checks.py -f <node>")
        return False

    # Build Ansible vars dictionary
    ansible_vars = {
        "# SAP HANA HA Pacemaker Configuration": None,
        f"# Cluster: {cluster_name}": None,
        f"# Generated from: {config_path}": None,
        "": None,
        "# Core SAP HANA Parameters": None,
        "sap_hana_ha_pacemaker_hana_sid": sid,
        "sap_hana_ha_pacemaker_hana_instance_number": f'"{info.get("instance_number", "00")}"',
    }

    # Cluster name
    ansible_vars["sap_hana_ha_pacemaker_cluster_name"] = cluster_name

    # Node information
    if len(cluster_nodes) >= 2:
        ansible_vars["\n# Cluster Node Information"] = None
        node1_fqdn = info.get("node1_fqdn", cluster_nodes[0])
        node1_ip = info.get("node1_ip", "")
        node2_fqdn = info.get("node2_fqdn", cluster_nodes[1])
        node2_ip = info.get("node2_ip", "")

        ansible_vars["sap_hana_ha_pacemaker_node1_fqdn"] = node1_fqdn
        if node1_ip:
            ansible_vars["sap_hana_ha_pacemaker_node1_ip"] = node1_ip
        ansible_vars["sap_hana_ha_pacemaker_node2_fqdn"] = node2_fqdn
        if node2_ip:
            ansible_vars["sap_hana_ha_pacemaker_node2_ip"] = node2_ip

    # Virtual IP
    vip = info.get("virtual_ip", "")
    if vip:
        ansible_vars["\n# Virtual IP Configuration"] = None
        ansible_vars["sap_hana_ha_pacemaker_vip"] = vip
        secondary_vip = info.get("secondary_vip", "")
        if secondary_vip:
            ansible_vars["sap_hana_ha_pacemaker_secondary_vip"] = secondary_vip
            ansible_vars["sap_hana_ha_pacemaker_secondary_read"] = "true"

    # Cluster password placeholder
    ansible_vars["\n# Pacemaker & HA Service Setup"] = None
    ansible_vars["sap_hana_ha_pacemaker_hacluster_password"] = (
        '"{{ vault_hacluster_password }}"  # Store in Ansible Vault'
    )

    # System Replication
    repl_mode = info.get("replication_mode", "")
    op_mode = info.get("operation_mode", "")
    site1 = info.get("site1_name", "")
    site2 = info.get("site2_name", "")
    if repl_mode or op_mode or site1:
        ansible_vars["\n# SAP HANA System Replication"] = None
        if repl_mode:
            ansible_vars["sap_hana_ha_pacemaker_replication_mode"] = repl_mode
        if op_mode:
            ansible_vars["sap_hana_ha_pacemaker_operation_mode"] = op_mode
        if site1:
            ansible_vars["sap_hana_ha_pacemaker_site1_name"] = site1
        if site2:
            ansible_vars["sap_hana_ha_pacemaker_site2_name"] = site2

    # Cluster Properties
    auto_reg = info.get("automated_register")
    prefer_takeover = info.get("prefer_site_takeover")
    stickiness = info.get("resource_stickiness")
    migration = info.get("migration_threshold")
    if auto_reg is not None or prefer_takeover is not None or stickiness or migration:
        ansible_vars["\n# Cluster Properties"] = None
        if auto_reg is not None:
            ansible_vars["sap_hana_ha_pacemaker_automated_register"] = str(auto_reg).lower()
        if prefer_takeover is not None:
            ansible_vars["sap_hana_ha_pacemaker_prefer_site_takeover"] = str(
                prefer_takeover
            ).lower()
        if stickiness:
            ansible_vars["sap_hana_ha_pacemaker_resource_stickiness"] = stickiness
        if migration:
            ansible_vars["sap_hana_ha_pacemaker_migration_threshold"] = migration

    # STONITH
    stonith = info.get("stonith_device", "")
    stonith_type = info.get("stonith_type", "")
    if stonith:
        ansible_vars["\n# STONITH/Fencing Configuration"] = None
        ansible_vars["sap_hana_ha_pacemaker_stonith_device"] = stonith
        if stonith_type:
            ansible_vars["sap_hana_ha_pacemaker_stonith_type"] = stonith_type
        ansible_vars["# Add fencing credentials as needed:"] = None
        ansible_vars["# sap_hana_ha_pacemaker_fence_user"] = '"{{ vault_fence_user }}"'
        ansible_vars["# sap_hana_ha_pacemaker_fence_password"] = '"{{ vault_fence_password }}"'

    # Format output
    output_lines = ["---"]
    for key, value in ansible_vars.items():
        if key.startswith("#") or key.startswith("\n#"):
            output_lines.append(key.lstrip("\n"))
        elif key == "":
            output_lines.append("")
        elif value is None:
            continue
        else:
            output_lines.append(f"{key}: {value}")

    yaml_content = "\n".join(output_lines)

    if output_file:
        output_path = Path(output_file)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(yaml_content + "\n")
        print(f"Ansible vars exported to: {output_path}")
        print("\nUsage:")
        print(f"  1. Move to your Ansible inventory: group_vars/{cluster_name}.yml")
        print("  2. Store passwords in Ansible Vault")
        print("  3. Run playbook: ansible-playbook -i inventory sap_hana_ha.yml --ask-vault-pass")
    else:
        print(yaml_content)

    return True


def delete_config(config_path: Path):
    """Delete health check reports and status files (keeps node access config)."""
    import glob

    config_dir = config_path.parent
    deleted_count = 0

    # Delete last_run_status.yaml
    status_file = config_dir / "last_run_status.yaml"
    if status_file.exists():
        try:
            os.remove(status_file)
            print(f"Deleted: {status_file.name}")
            deleted_count += 1
        except Exception:
            pass

    # Check for health check report files
    report_pattern = str(config_dir / "health_check_report_*.yaml")
    report_files = glob.glob(report_pattern)

    if report_files:
        print(f"\nFound {len(report_files)} health check report file(s):")
        for f in sorted(report_files)[-5:]:  # Show last 5
            print(f"  {Path(f).name}")
        if len(report_files) > 5:
            print(f"  ... and {len(report_files) - 5} more")

        try:
            response = input("\nDelete all health check report files? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            response = "n"

        if response == "y":
            for f in report_files:
                try:
                    os.remove(f)
                    deleted_count += 1
                except Exception:
                    pass
            print(f"Deleted {len(report_files)} report file(s)")
        else:
            print("Report files kept.")
    else:
        print("No health check report files found.")

    # Show info about config file
    if config_path.exists():
        print(f"\nNote: Node access config preserved: {config_path.name}")
        print("      Use -f (--force) to re-discover nodes.")

    if deleted_count > 0:
        print(f"\nTotal files deleted: {deleted_count}")
        return True
    print("\nNo files deleted.")
    return False
