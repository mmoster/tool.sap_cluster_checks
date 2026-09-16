"""
SAP Pacemaker Cluster Health Check - Dynamic Installation Guide

Mixin class providing the dynamic installation guide that shows
only the steps still needed based on current installation status.
"""

from .installation import print_suggestions


class InstallGuideMixin:
    """Mixin providing installation guide printing for ClusterHealthCheck."""

    def print_dynamic_install_guide(self, node: str = None):
        """Print installation guide showing only steps that are still needed."""
        print("\n" + "=" * 63)
        print(" Checking current installation status...")
        print("=" * 63)

        # Get first accessible node
        method = "ssh"
        user = "root"
        if not node and self.access_config:
            for n, info in self.access_config.nodes.items():
                if info.get("preferred_method"):
                    node = n
                    method = info.get("preferred_method", "ssh")
                    user = info.get("ssh_user", "root")
                    break

        if not node:
            print("\n[WARNING] No accessible nodes found. Showing full guide.")
            print_suggestions("install", self._get_rhel_major())
            return

        if method == "local":
            print("  Checking: LOCAL execution (this machine)")
        else:
            print(f"  Checking: {node} via {method.upper()} (user={user})")
        status = self.check_install_status(node, method, user)

        # Print status summary
        print("\n" + "-" * 63)
        print(" Current Installation Status")
        print("-" * 63)

        def status_icon(val):
            if val is None:
                return "[?]"
            return "[OK]" if val else "[--]"

        # Phase 1: Prerequisites
        print("\n  PHASE 1 - PREREQUISITES:")
        print(f"    {status_icon(status['subscription_registered'])} Subscription/repos available")
        print(
            f"    {status_icon(status['firewall_configured'])} Firewall ports open (high-availability)"
        )
        print(f"    {status_icon(status['packages_installed'])} Cluster packages installed")
        if status["missing_packages"]:
            print(f"        Missing: {', '.join(status['missing_packages'])}")
        print(f"    {status_icon(status['hacluster_password'])} hacluster user password set")
        print(f"    {status_icon(status['pcsd_running'])} PCSD daemon running")
        print(f"    {status_icon(status['pcsd_enabled'])} PCSD enabled on boot")

        # Phase 2: Cluster Creation
        print("\n  PHASE 2 - CLUSTER CREATION:")
        print(
            f"    {status_icon(status['nodes_authenticated'])} Nodes authenticated (pcs host auth)"
        )
        cluster_info = f" ({status['cluster_name']})" if status["cluster_name"] else ""
        print(
            f"    {status_icon(status.get('corosync_conf_exists'))} Cluster created (corosync.conf)"
        )
        print(f"    {status_icon(status.get('cib_exists'))} Cluster configured (cib.xml)")
        print(f"    {status_icon(status['cluster_configured'])} Cluster running{cluster_info}")
        print(f"    {status_icon(status['corosync_running'])} Corosync running (messaging)")
        print(f"    {status_icon(status['pacemaker_running'])} Pacemaker running (resource mgr)")
        # Cluster enabled on boot is optional - show warning if not enabled
        if status["cluster_enabled"]:
            print(f"    {status_icon(True)} Cluster enabled on boot")
        elif status["cluster_enabled"] is False:
            print("    [~] Cluster enabled on boot (optional)")
        else:
            print("    [?] Cluster enabled on boot (optional)")
        print(f"    {status_icon(status['cluster_online'])} All nodes online")
        if status["cluster_nodes"]:
            print(f"        Online: {', '.join(status['cluster_nodes'])}")
        if status.get("standby_nodes"):
            print(f"        Standby: {', '.join(status['standby_nodes'])}")
        if status.get("offline_nodes"):
            print(f"        Offline: {', '.join(status['offline_nodes'])}")

        # Warning if cluster is configured but not running
        if (status.get("corosync_conf_exists") or status.get("cib_exists")) and not status[
            "pacemaker_running"
        ]:
            print("""
  ╔═════════════════════════════════════════════════════════════╗
  ║  [!] CLUSTER NOT RUNNING                                    ║
  ╠═════════════════════════════════════════════════════════════╣
  ║  Run:  pcs cluster start --all                              ║
  ╚═════════════════════════════════════════════════════════════╝""")

        # Phase 3: Fencing & Resources
        print("\n  PHASE 3 - FENCING & RESOURCES:")
        print(f"    {status_icon(status['stonith_enabled'])} STONITH enabled")
        print(f"    {status_icon(status['stonith_configured'])} STONITH device running")
        print(f"    {status_icon(status['hana_installed'])} SAP HANA installed")
        print(f"    {status_icon(status['hana_resources'])} SAP HANA cluster resources")

        # Determine what steps are needed based on phases
        steps_needed = []

        # If cluster is running, prerequisites must have been completed
        cluster_running = status["cluster_configured"] or status["pacemaker_running"]

        # Phase 1: Prerequisites - skip if cluster is already running
        if not cluster_running:
            if status["subscription_registered"] is False:
                steps_needed.append("subscription")
            if status["firewall_configured"] is False:
                steps_needed.append("firewall")
            if status["packages_installed"] is False:
                steps_needed.append("packages")
            if status["hacluster_password"] is False:
                steps_needed.append("hacluster")
            if status["pcsd_running"] is False:
                steps_needed.append("pcsd")

        # Phase 2: Cluster Creation - skip auth/setup if cluster already exists
        if not cluster_running:
            if status["pcsd_running"] and status["nodes_authenticated"] is False:
                steps_needed.append("authenticate")
            if status["nodes_authenticated"] and not status["cluster_configured"]:
                steps_needed.append("cluster_setup")
        if status["cluster_configured"] and not status["corosync_running"]:
            steps_needed.append("cluster_start")
        # Note: cluster_enable is optional - cluster works without being enabled on boot

        # Phase 3: Fencing & Resources
        if status["cluster_online"] and status["stonith_enabled"] is False:
            steps_needed.append("stonith")
        if status["hana_installed"] and status["hana_resources"] is False:
            steps_needed.append("hana")

        if not steps_needed:
            print("\n" + "=" * 63)
            print(" All installation steps completed!")
            print("=" * 63)
            print("\n  Run health check to verify configuration:")
            print("    ./sap_cluster_checks.py")
            return

        # Determine the immediate next step
        next_step = steps_needed[0] if steps_needed else None

        # Print summary and next step with prominent separator
        print("\n")
        print("=" * 63)
        print("=" * 63)
        print(f" NEXT STEP: {next_step.upper().replace('_', ' ') if next_step else 'DONE'}")
        print("=" * 63)
        print("=" * 63)

        # Print only the needed steps
        print(f"\n  Remaining steps ({len(steps_needed)}): {', '.join(steps_needed)}")

        step_num = 1

        if "subscription" in steps_needed:
            print(f"""
STEP {step_num}: REGISTER SUBSCRIPTION (both nodes)
---------------------------------------------------------------
  # Register system and attach SAP subscription
  subscription-manager register
  subscription-manager attach --pool=<SAP_POOL_ID>

  # Enable High Availability repository (RHEL 9)
  subscription-manager repos --enable=rhel-9-for-x86_64-highavailability-rpms
""")
            step_num += 1

        if "firewall" in steps_needed:
            print(f"""
STEP {step_num}: CONFIGURE FIREWALL (both nodes)
---------------------------------------------------------------
  # Allow High Availability traffic through the firewall
  firewall-cmd --permanent --add-service=high-availability
  firewall-cmd --reload

  # Verify
  firewall-cmd --list-services | grep high-availability
""")
            step_num += 1

        if "packages" in steps_needed:
            missing = status["missing_packages"]
            pkg_list = (
                " ".join(missing) if missing else "pacemaker pcs fence-agents-all sap-hana-ha"
            )
            print(f"""
STEP {step_num}: INSTALL CLUSTER PACKAGES (both nodes)
---------------------------------------------------------------
  # Install required packages
  dnf install -y {pkg_list}

  # SAP resource agent package (install ONE):
  dnf install -y sap-hana-ha  # RHEL 9/10, Scale-Up & Scale-Out (required for RHEL 10)
  # Legacy alternatives (RHEL 8/9 only):
  #   dnf install -y resource-agents-sap-hana           # legacy Scale-Up
  #   dnf install -y resource-agents-sap-hana-scaleout  # legacy Scale-Out

  # Verify installation
  rpm -q pacemaker corosync pcs sap-hana-ha
""")
            step_num += 1

        if "hacluster" in steps_needed:
            print(f"""
STEP {step_num}: SET HACLUSTER PASSWORD (both nodes)
---------------------------------------------------------------
  # Set password for hacluster user (use SAME password on all nodes)
  passwd hacluster

  # Verify the user exists
  id hacluster
""")
            step_num += 1

        if "pcsd" in steps_needed:
            print(f"""
STEP {step_num}: START PCSD DAEMON (both nodes)
---------------------------------------------------------------
  # Enable and start pcsd service
  systemctl enable --now pcsd.service

  # Verify pcsd is running
  systemctl status pcsd
""")
            step_num += 1

        if "authenticate" in steps_needed:
            print(f"""
STEP {step_num}: AUTHENTICATE NODES (one node only)
---------------------------------------------------------------
  # Authenticate cluster nodes (RHEL 9 syntax: pcs host auth)
  pcs host auth node1 node2 -u hacluster

  # Enter the hacluster password when prompted
  # This creates /etc/corosync/corosync.conf on successful auth
""")
            step_num += 1

        if "cluster_setup" in steps_needed:
            print(f"""
STEP {step_num}: CREATE CLUSTER (one node only)
---------------------------------------------------------------
  # Create the cluster (replace my_cluster with your cluster name)
  pcs cluster setup my_cluster node1 node2

  # This generates /etc/corosync/corosync.conf on all nodes
""")
            step_num += 1

        if "cluster_start" in steps_needed:
            print(f"""
STEP {step_num}: START CLUSTER (one node only)
---------------------------------------------------------------
  NOTE: If 'pcs status' shows "Connection to cluster failed: Connection
        refused" - the cluster needs to be STARTED or CREATED first!

  # Start the cluster on all nodes
  pcs cluster start --all

  # Verify cluster is running
  pcs cluster status
  pcs status

  # If cluster doesn't exist yet, create it first:
  pcs cluster setup <cluster_name> <node1> <node2>

  # Monitor in real-time
  watch pcs status
""")
            step_num += 1

        if "cluster_enable" in steps_needed:
            print(f"""
STEP {step_num}: ENABLE CLUSTER ON BOOT (one node only)
---------------------------------------------------------------
  # Enable cluster to start automatically on boot
  pcs cluster enable --all

  # Verify
  systemctl is-enabled corosync pacemaker
""")
            step_num += 1

        if "stonith" in steps_needed:
            print(f"""
STEP {step_num}: CONFIGURE STONITH/FENCING (one node only)
---------------------------------------------------------------

  OPTION A: Production cluster - Configure real STONITH device
  ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  IMPORTANT: STONITH is REQUIRED for production SAP HANA clusters!

  # Example: IPMI/iLO fencing
  pcs stonith create fence_node1 fence_ipmilan \\
      ipaddr=<IPMI_IP> login=<USER> passwd=<PASS> \\
      lanplus=1 pcmk_host_list=node1

  # Example: Cloud fencing (Azure)
  pcs stonith create fence_azure fence_azure_arm ...

  # Verify fencing
  pcs stonith status

  OPTION B: Test/Dev cluster - Disable STONITH (NOT for production!)
  ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  # Disable STONITH for non-production/test clusters only
  sudo pcs property set stonith-enabled=false

  # Verify STONITH is disabled
  pcs property show stonith-enabled

  # To re-enable STONITH later (before going to production):
  sudo pcs property set stonith-enabled=true
""")
            step_num += 1

        if "hana" in steps_needed:
            print(f"""
STEP {step_num}: CONFIGURE SAP HANA RESOURCES (one node only)
---------------------------------------------------------------
  # Ensure HANA System Replication is configured first!
  # Run as <sid>adm: hdbnsutil -sr_state

  # Create SAPHanaTopology resource
  pcs resource create SAPHanaTopology_<SID>_<INST> SAPHanaTopology \\
      SID=<SID> InstanceNumber=<INST> \\
      op start timeout=600 op stop timeout=300 op monitor interval=10 timeout=600 \\
      clone clone-max=2 clone-node-max=1 interleave=true

  # Create SAPHana resource
  pcs resource create SAPHana_<SID>_<INST> SAPHana \\
      SID=<SID> InstanceNumber=<INST> \\
      PREFER_SITE_TAKEOVER=true DUPLICATE_PRIMARY_TIMEOUT=7200 AUTOMATED_REGISTER=true \\
      op start timeout=3600 op stop timeout=3600 \\
      op monitor interval=61 role=Slave timeout=700 \\
      op monitor interval=59 role=Master timeout=700 \\
      op promote timeout=3600 op demote timeout=3600 \\
      promotable meta notify=true clone-max=2 clone-node-max=1 interleave=true

  # Create virtual IP
  pcs resource create vip_<SID>_<INST> IPaddr2 ip=<VIP> cidr_netmask=24 \\
      op monitor interval=10 timeout=20

  # Add constraints
  pcs constraint colocation add vip_<SID>_<INST> with master SAPHana_<SID>_<INST>-clone 4000
  pcs constraint order SAPHanaTopology_<SID>_<INST>-clone then SAPHana_<SID>_<INST>-clone
""")
            step_num += 1

        print("-" * 63)
        print(" After completing these steps, rerun the health check:")
        print("   ./sap_cluster_checks.py")
        print("-" * 63)
