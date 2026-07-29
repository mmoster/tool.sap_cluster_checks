"""
Installation and guidance functions for SAP Pacemaker Cluster Health Check.

This module contains functions for displaying:
- Installation guides
- Step descriptions
- Troubleshooting suggestions
"""


def get_redhat_doc_urls(rhel_major: int = 9) -> dict:
    """Return Red Hat documentation URLs for the given RHEL major version.

    Args:
        rhel_major: RHEL major version (8, 9, 10, ...). Defaults to 9.

    Returns:
        Dict with keys: ha_clusters, ha_quorum, ha_fencing, sap_solutions,
        sap_scale_up, sap_scale_out
    """
    v = rhel_major
    base = "https://docs.redhat.com/en/documentation"
    ha = f"{base}/red_hat_enterprise_linux/{v}/html/configuring_and_managing_high_availability_clusters"
    sap_base = f"{base}/red_hat_enterprise_linux_for_sap_solutions/{v}"
    # SAP-specific guides: RHEL 8 uses "Automating...", RHEL 9+ uses "Deploying..."
    # RHEL 10+ SAP deployment guides not yet published — use RHEL 9 URLs as fallback
    # TODO: When RHEL 10 SAP guides are published, bump cap to 10:
    #       sap_ver = v if v <= 10 else 10
    #       RHEL 10 is expected to use the same "Deploying..." slug as RHEL 9.
    sap_ver = v if v <= 9 else 9
    sap_guide_base = f"{base}/red_hat_enterprise_linux_for_sap_solutions/{sap_ver}"
    if v <= 8:
        scale_up = f"{sap_guide_base}/html/automating_sap_hana_scale-up_system_replication_using_the_rhel_ha_add-on/"
        scale_out = f"{sap_guide_base}/html/automating_sap_hana_scale-out_system_replication_using_the_rhel_ha_add-on/"
    else:
        scale_up = f"{sap_guide_base}/html/deploying_sap_hana_scale-up_system_replication_high_availability/"
        scale_out = f"{sap_guide_base}/html/deploying_sap_hana_scale-out_system_replication_high_availability/"
    multitarget_dr = f"{sap_guide_base}/html/configuring_sap_hana_scale-up_multitarget_system_replication_for_disaster_recovery/"
    return {
        "ha_clusters": f"{ha}/",
        "ha_quorum": f"{ha}/assembly_configuring-cluster-quorum-configuring-and-managing-high-availability-clusters",
        "ha_fencing": f"{ha}/assembly_configuring-fencing-configuring-and-managing-high-availability-clusters",
        "sap_solutions": f"{sap_base}/",
        "sap_scale_up": scale_up,
        "sap_scale_out": scale_out,
        "sap_multitarget_dr": multitarget_dr,
    }


def print_guide(rhel_major: int = 9):
    """Print detailed usage guide.

    Args:
        rhel_major: RHEL major version for documentation URLs. Defaults to 9.
    """
    urls = get_redhat_doc_urls(rhel_major)
    print("""
===============================================================================
                    SAP Pacemaker Cluster Health Check - Guide
===============================================================================

QUICK START
-----------
  1. Run directly on a cluster node (auto-detects local mode):
     ./sap_cluster_checks.py

  2. Check a live cluster remotely (auto-discovers all members):
     ./sap_cluster_checks.py hana01

  3. Analyze SOSreports offline:
     ./sap_cluster_checks.py -s /path/to/sosreports/

  4. Show current configuration:
     ./sap_cluster_checks.py --show-config

WORKFLOW
--------
  Step 1: ACCESS DISCOVERY
    The tool first discovers how to access your nodes:
    - SSH direct access (preferred)
    - Ansible inventory
    - SOSreport directories

    Example: ./sap_cluster_checks.py --access-only hana01

  Step 2: CLUSTER DISCOVERY
    From the first reachable node, discovers all cluster members:
    - Uses: crm_node -l, pcs status nodes, corosync-cmapctl
    - Saves cluster name for future runs

    Example: ./sap_cluster_checks.py -C mycluster  # Use saved cluster

  Step 3: HEALTH CHECKS
    Runs all CHK_*.yaml rules against discovered nodes:
    - Cluster configuration (quorum, fencing, resources)
    - Pacemaker status (nodes, resources, failures)
    - SAP-specific (HANA SR status, hooks, systemd)

    Example: ./sap_cluster_checks.py --list-rules  # See all checks

  Step 4: REPORT GENERATION
    Generates YAML report with all findings:
    - Critical failures (must fix)
    - Warnings (should review)
    - Passed checks

COMMON USE CASES
----------------
  Run on the cluster node itself (local mode):
    ./sap_cluster_checks.py              # Auto-detects local mode
    ./sap_cluster_checks.py --local      # Explicit local mode

  Live cluster check from remote:
    ./sap_cluster_checks.py hana01 hana02

  SOSreport analysis (auto-extracts .tar.xz):
    ./sap_cluster_checks.py -s /path/to/sosreports/

  Debug mode (verbose output):
    ./sap_cluster_checks.py -d hana01

  Use saved cluster:
    ./sap_cluster_checks.py -C production_cluster

  Skip specific steps:
    ./sap_cluster_checks.py --skip sap report hana01

  Force re-discovery:
    ./sap_cluster_checks.py -f hana01

  Ansible inventory group:
    ./sap_cluster_checks.py -g sap_hana_cluster

OPTIONS REFERENCE
-----------------
  Input Sources:
    (none)            Auto-detect local mode (on cluster node)
    <hosts>           Hostnames to check (auto-discovers cluster)
    -H, --hosts-file  File with hostnames (one per line)
    -s, --sosreport   Directory with SOSreport archives
    -g, --group       Ansible inventory group filter
    -C, --cluster     Use saved cluster name
    -l, --local       Explicit local mode (on cluster node)

  Actions:
    -a, --access-only  Only run access discovery
    -S, --show-config  Show current configuration
    -D, --delete-reports Delete report files (keeps node config)
    -L, --list-rules   List available health checks
    -G, --guide        Show this guide

  Modifiers:
    -d, --debug       Debug mode (verbose)
    -f, --force       Force re-discovery
    -w, --workers     Parallel workers (default: 10)
    -r, --rules-path  Custom rules directory

AUTOMATION & CRONJOB SUPPORT
----------------------------
  The tool is designed to run unattended (cron, pipelines, scripts):

  - Auto-timeout prompts: All interactive prompts auto-skip after 20s
    (e.g., version update prompt auto-declines if no response)
  - Non-TTY detection: When stdin is not a terminal (piped/cron), all
    interactive prompts are skipped automatically
  - Spinner suppression: Progress animations are disabled when stdout
    is not a terminal (redirected to file/pipe)
  - Exit codes: Returns non-zero on critical failures for scripting
  - YAML output: Machine-readable results in last_run_status.yaml
  - PDF can be skipped: --no-pdf avoids PDF generation overhead

  Example cronjob:
    0 6 * * 1 /opt/sap-cluster-checks/sap_cluster_checks.py --local \
        --no-update-check --no-pdf >> /var/log/sap_healthcheck.log 2>&1

AUTO-DETECTION & INTELLIGENCE
-----------------------------
  The tool automatically detects and adapts to your environment:

  - RHEL version: Reads /etc/redhat-release (supports RHEL 8, 9, 10)
  - Pacemaker version: Detects from installed RPM
  - Cluster type: Scale-Up vs Scale-Out (based on clone-max value)
  - Architecture type: ANGI (sap-hana-ha) vs legacy (resource-agents-sap-hana)
  - HANA SID & instance: Discovers sidadm user, SID, instance number
  - HANA running status: Detects if database is running or stopped
  - Cluster status: Warns if Pacemaker/Corosync not running, falls back
    to static corosync.conf analysis
  - Hostname aliases: Resolves mismatches between corosync node names
    and system hostnames via /etc/hosts IP matching (SOSreport mode)
  - Majority maker: Identifies majority maker nodes in Scale-Out clusters

SOSREPORT SPECIAL FEATURES
--------------------------
  SOSreport analysis works entirely offline (no SSH to cluster nodes):

  Supported formats:
    .tar.xz, .tar.gz, .tar.bz2, .tar (plain uncompressed)

  Features:
  - Auto-extraction: Archives extracted automatically in parallel
  - SOSreport-only mode: With -s flag, skips all SSH access attempts
    and works purely from SOSreport data
  - Hostname alias resolution: Matches corosync node names to SOSreport
    hostnames via /etc/hosts IP cross-referencing
  - Cluster name detection: Extracts cluster name from corosync.conf
    inside the SOSreport
  - Complete SOSreport workflow (-R): Discover cluster from seed node,
    configure SAP extensions, create and fetch SOSreports in one step

  SOSreport collection examples:
    ./sap_cluster_checks.py -R hana01                  # Full workflow
    ./sap_cluster_checks.py -R hana01 --configure-extensions  # Auto-config
    ./sap_cluster_checks.py -F mycluster               # Fetch existing
    ./sap_cluster_checks.py -F mycluster --create-sosreports  # Create & fetch

PERFORMANCE FEATURES
--------------------
  - TCP port pre-check: Before SSH login, checks if port 22 is open
    (2s timeout). Unreachable nodes are skipped immediately instead of
    waiting for SSH timeout (~14s per node)
  - Parallel execution: Health checks run in parallel within phases
    (configurable: -w/--workers, default 10)
  - Parallel extraction: SOSreport archives extracted in parallel
  - Config caching: Cluster topology saved in cluster_access_config.yaml,
    subsequent runs reuse discovery results (use -f to force re-discovery)

VERSION & UPDATE CHECK
----------------------
  - Automatic check: On startup, compares local git HEAD with remote
  - Shows: "[INFO] A newer version is available (N commit(s) behind)"
  - Auto-timeout: Prompt auto-skips after 20 seconds
  - Disable: --no-update-check skips the check entirely
  - Auto-restart: If updated, restarts the script with same arguments

LESS-KNOWN FLAGS
----------------
  -a, --access-only      Only discover cluster access, skip health checks
  -E, --export-ansible   Export cluster config as Ansible group_vars YAML
  --strict               Strict mode - all checks required, no soft-skip
  --skip STEPS           Skip steps: access, config, pacemaker, sap, report
  --suggest STEP         Show troubleshooting suggestions for a step
  --suggest auto         Auto-detect failing step and show suggestions
  -w N, --workers N      Parallel workers for rule execution (default: 10)
  -r PATH                Custom rules directory (CHK_*.yaml files)
  -c DIR, --config-dir   Custom config directory
  --create-sosreports    Auto-create missing SOSreports (with -F)
  --configure-extensions Auto-configure SAP extensions (with -R)

TROUBLESHOOTING
---------------
  No SSH access:
    - Check SSH keys: ssh-copy-id root@hana01
    - Try: ./sap_cluster_checks.py -d hana01  # Debug output

  Commands timing out:
    - Some SAP commands are slow, tool uses 15s timeout
    - Use SOSreports for offline analysis

  Wrong nodes discovered:
    - Specify nodes explicitly: ./sap_cluster_checks.py hana01 hana02
    - Use hosts file: ./sap_cluster_checks.py -H my_hosts.txt

DOCUMENTATION
-------------""")
    print(f"""  SAP HANA Platform:
    https://help.sap.com/docs/SAP_HANA_PLATFORM

  SAP HANA System Replication:
    https://help.sap.com/docs/SAP_HANA_PLATFORM/6b94445c94ae495c83a19646e7c3fd56

  SAP HANA Administration Guide:
    https://help.sap.com/docs/SAP_HANA_PLATFORM/6b94445c94ae495c83a19646e7c3fd56/330e5550b09d4f0f8b6cceb14a1f956d.html

  Red Hat HA Clusters:
    {urls['ha_clusters']}

  Red Hat SAP HANA HA:
    {urls['sap_solutions']}

  Red Hat SAP HANA Scale-Up HA:
    {urls['sap_scale_up']}

  Red Hat SAP HANA Scale-Out HA:
    {urls['sap_scale_out']}

  Red Hat SAP HANA Multitarget DR:
    {urls['sap_multitarget_dr']}

  Pacemaker Documentation:
    https://clusterlabs.org/pacemaker/doc/

  ClusterLabs Wiki:
    https://wiki.clusterlabs.org/

HEALTH CHECK RULES
------------------
  Rules are defined in YAML files (CHK_*.yaml). Each rule specifies:
    - Command to run (live_cmd) or SOSreport path (sos_path)
    - Parser to extract values (regex patterns)
    - Validation logic (expectations)

  Custom rules: ./sap_cluster_checks.py -r /path/to/my_rules/

===============================================================================
""")


def print_steps():
    """Print all health check steps with descriptions."""
    print("""
===============================================================================
                    SAP Cluster Health Check - Steps
===============================================================================

STEP        DESCRIPTION                              SUGGESTIONS
----        -----------                              -----------
install     Full installation guide for SAP HANA     --suggest install
            HA cluster (packages, setup, config)

access      Discover access to cluster nodes         --suggest access
            (SSH, Ansible, SOSreports)

config      Check cluster configuration              --suggest config
            (quorum, corosync, node status)

pacemaker   Check Pacemaker/Corosync                 --suggest pacemaker
            (STONITH, resources, fencing)

sap         Check SAP HANA configuration             --suggest sap
            (System Replication, HA/DR hooks)

report      Generate health check report             (no suggestions)

===============================================================================

USAGE EXAMPLES
--------------
  # Show full installation guide
  ./sap_cluster_checks.py --suggest install

  # Run all steps
  ./sap_cluster_checks.py hana01

  # Skip specific steps
  ./sap_cluster_checks.py --skip sap report hana01

  # Only run access discovery
  ./sap_cluster_checks.py --access-only hana01

  # Get suggestions for a step
  ./sap_cluster_checks.py --suggest config

  # Get suggestions for all steps
  ./sap_cluster_checks.py --suggest all

===============================================================================
""")


def print_suggestions(step: str, rhel_major: int = 9):
    """Print detailed suggestions for a specific step.

    Args:
        step: Step name (access, config, pacemaker, sap, install, all).
        rhel_major: RHEL major version for documentation URLs. Defaults to 9.
    """
    urls = get_redhat_doc_urls(rhel_major)
    suggestions = {
        "access": """
===============================================================================
                         ACCESS DISCOVERY - Suggestions
===============================================================================

PURPOSE
-------
  Discover how to connect to cluster nodes (SSH, Ansible, or SOSreports)

COMMON ISSUES & SOLUTIONS
-------------------------

  1. SSH Connection Failed
     - Check SSH keys: ssh-copy-id root@hana01
     - Test manually: ssh -o BatchMode=yes root@hana01 hostname
     - Check firewall: firewall-cmd --list-all

  2. Permission Denied
     - Ensure root access or sudo without password
     - Check /etc/ssh/sshd_config for PermitRootLogin

  3. Host Not Found
     - Verify hostname in /etc/hosts or DNS
     - Try IP address: ./sap_cluster_checks.py 192.168.1.100

  4. Ansible Inventory Issues
     - Check inventory: ansible-inventory --list
     - Use specific group: ./sap_cluster_checks.py -g sap_cluster
     - Skip Ansible: specify hosts directly

COMMANDS TO TRY
---------------
  # Debug connection
  ./sap_cluster_checks.py -d --access-only hana01

  # Use SOSreports instead
  ./sap_cluster_checks.py -s /path/to/sosreports/

  # Specify hosts manually
  ./sap_cluster_checks.py hana01 hana02

DOCUMENTATION
-------------
  SSH: https://man.openbsd.org/ssh
  Ansible: https://docs.ansible.com/ansible/latest/inventory_guide/
""",
        "config": """
===============================================================================
                      CLUSTER CONFIGURATION - Suggestions
===============================================================================

PURPOSE
-------
  Verify cluster configuration (quorum, corosync, resources)

CHECKS PERFORMED
----------------
  CHK_NODE_STATUS        - All nodes online
  CHK_CLUSTER_QUORUM     - Quorum is established
  CHK_QUORUM_CONFIG      - Quorum settings correct (expected_votes, two_node)
  CHK_CLONE_CONFIG       - Clone resources properly configured
  CHK_SETUP_VALIDATION   - Basic setup validation
  CHK_CIB_TIME_SYNC      - CIB timestamps synchronized
  CHK_PACKAGE_CONSISTENCY - Package versions match across nodes

COMMON ISSUES & SOLUTIONS
-------------------------

  1. Expected Votes Not Configured
     - Check: grep expected_votes /etc/corosync/corosync.conf
     - Fix: Set expected_votes in quorum section
     - For 2-node: also set two_node: 1 and wait_for_all: 0

  2. Quorum Not Established
     - Check: corosync-quorumtool -s
     - Verify all nodes are online: crm_mon -1
     - Check corosync: systemctl status corosync

  3. No Designated Controller (DC)
     - Cluster may not be running: pcs status
     - Start cluster: pcs cluster start --all

COMMANDS TO CHECK
-----------------
  # Cluster status
  pcs status
  crm_mon -1

  # Quorum status
  corosync-quorumtool -s

  # Configuration
  pcs config show

DOCUMENTATION
-------------
  Red Hat HA Quorum:
    """
        + urls["ha_quorum"]
        + """

  Corosync Configuration:
    https://clusterlabs.org/pacemaker/doc/2.1/Pacemaker_Explained/html/cluster-options.html
""",
        "pacemaker": """
===============================================================================
                       PACEMAKER/COROSYNC - Suggestions
===============================================================================

PURPOSE
-------
  Check Pacemaker resources, STONITH/fencing, and cluster health

CHECKS PERFORMED
----------------
  CHK_STONITH_CONFIG     - STONITH is enabled and configured
  CHK_RESOURCE_STATUS    - All resources running
  CHK_RESOURCE_FAILURES  - No resource failures
  CHK_ALERT_FENCING      - Fencing alerts configured
  CHK_MASTER_SLAVE_ROLES - Master/slave roles correct
  CHK_MAJORITY_MAKER     - Majority maker for 2-node clusters

COMMON ISSUES & SOLUTIONS
-------------------------

  1. STONITH Not Configured
     - CRITICAL: Production clusters MUST have STONITH
     - Check: pcs property show stonith-enabled
     - Configure fencing agent for your hardware/cloud

  2. Resource Failures
     - Check: pcs resource failcount show
     - Clear failures: pcs resource cleanup <resource>
     - Check logs: journalctl -u pacemaker

  3. Resources Not Running
     - Check constraints: pcs constraint show
     - Check resource config: pcs resource show <resource>
     - Start resource: pcs resource enable <resource>

  4. Split-Brain Risk
     - Ensure STONITH is working
     - Test fencing: pcs stonith fence <node> --off

COMMANDS TO CHECK
-----------------
  # Resource status
  pcs status resources
  crm_mon -1 -rf

  # STONITH status
  pcs stonith status
  pcs property show stonith-enabled

  # Resource failures
  pcs resource failcount show

  # Fencing history
  pcs stonith history

DOCUMENTATION
-------------
  Red Hat Fencing:
    """
        + urls["ha_fencing"]
        + """

  Pacemaker Resources:
    https://clusterlabs.org/pacemaker/doc/2.1/Pacemaker_Explained/html/resources.html
""",
        "sap": """
===============================================================================
                           SAP HANA - Suggestions
===============================================================================

PURPOSE
-------
  Check SAP HANA System Replication and SAP-specific configurations

CHECKS PERFORMED
----------------
  CHK_HANA_SR_STATUS     - HANA System Replication active
  CHK_SITE_ROLES         - Primary/secondary sites correct
  CHK_REPLICATION_MODE   - Sync mode (sync/syncmem recommended)
  CHK_HADR_HOOKS         - HA/DR hooks configured
  CHK_HANA_AUTOSTART     - Autostart disabled (Pacemaker manages)
  CHK_SYSTEMD_SAP        - SAP systemd services correct

COMMON ISSUES & SOLUTIONS
-------------------------

  1. System Replication Not Active
     - Check: SAPHanaSR-showAttr
     - Verify SR status: hdbnsutil -sr_state
     - Check secondary registered: hdbnsutil -sr_register --help

  2. Wrong Replication Mode (async)
     - Risk: Data loss on failover
     - Change to sync: hdbnsutil -sr_changemode --mode=sync

  3. Multiple Primary Sites (Split-Brain)
     - CRITICAL: Immediate attention required
     - Check: SAPHanaSR-showAttr | grep -i prim
     - May need manual intervention

  4. HA/DR Hooks Not Configured
     - Required for automatic failover
     - Configure in global.ini: [ha_dr_provider_*]

  5. Autostart Enabled
     - Should be disabled when using Pacemaker
     - Check: grep Autostart /usr/sap/<SID>/SYS/profile/*

COMMANDS TO CHECK
-----------------
  # HANA SR status (run as <sid>adm)
  SAPHanaSR-showAttr
  hdbnsutil -sr_state

  # Pacemaker HANA resources
  pcs resource show SAPHana*
  crm_mon -A1 | grep -i hana

  # HANA processes
  sapcontrol -nr <instance> -function GetProcessList

DOCUMENTATION
-------------
  SAP HANA System Replication:
    https://help.sap.com/docs/SAP_HANA_PLATFORM/6b94445c94ae495c83a19646e7c3fd56

  SAP HANA HA/DR Providers:
    https://help.sap.com/docs/SAP_HANA_PLATFORM/6b94445c94ae495c83a19646e7c3fd56/1367c8fdefaa4808a7485b09f7a62949.html

  Red Hat SAP HANA HA:
    """
        + urls["sap_solutions"]
        + """
""",
        "install": """
===============================================================================
                    INSTALLATION GUIDE - SAP HANA HA Cluster
===============================================================================

This guide covers installation of SAP HANA Scale-Up System Replication with
Pacemaker HA cluster on RHEL 9/10. Run these steps on BOTH nodes unless noted.

===============================================================================
STEP 1: PREREQUISITES & SUBSCRIPTIONS (both nodes)
===============================================================================

  # Check if system is already registered
  subscription-manager status

  # If NOT registered, register and attach SAP subscription:
  # subscription-manager register
  # subscription-manager attach --pool=<SAP_POOL_ID>

  # Enable required repositories (RHEL 9) - skip if already enabled
  subscription-manager repos --list-enabled | grep -E 'highavailability|sap'

  # If HA/SAP repos are missing, enable them:
  subscription-manager repos --enable=rhel-9-for-x86_64-baseos-e4s-rpms
  subscription-manager repos --enable=rhel-9-for-x86_64-appstream-e4s-rpms
  subscription-manager repos --enable=rhel-9-for-x86_64-sap-solutions-e4s-rpms
  subscription-manager repos --enable=rhel-9-for-x86_64-sap-netweaver-e4s-rpms
  subscription-manager repos --enable=rhel-9-for-x86_64-highavailability-e4s-rpms

  # For RHEL 10, replace "9" with "10" in the above commands

===============================================================================
STEP 2: INSTALL CLUSTER PACKAGES (both nodes)
===============================================================================

  # Install Pacemaker, Corosync, and fence agents
  dnf install -y pacemaker pcs fence-agents-all

  # SAP HANA resource agent (install ONE):
  dnf install -y sap-hana-ha  # RHEL 9/10, Scale-Up & Scale-Out (required for RHEL 10)

  # Legacy alternatives (RHEL 8/9 only):
  # dnf install -y resource-agents-sap-hana           # legacy Scale-Up
  # dnf install -y resource-agents-sap-hana-scaleout  # legacy Scale-Out

  # Additional useful packages
  dnf install -y sap-cluster-connector

  # Verify installation
  rpm -q pacemaker corosync pcs sap-hana-ha

===============================================================================
STEP 3: CONFIGURE PCSD SERVICE (both nodes)
===============================================================================

  # Set password for hacluster user
  passwd hacluster
  # Or non-interactively:
  echo 'YourSecurePassword' | passwd --stdin hacluster

  # Enable and start pcsd service
  systemctl enable --now pcsd.service

  # Open firewall ports
  firewall-cmd --permanent --add-service=high-availability
  firewall-cmd --reload

===============================================================================
STEP 4: AUTHENTICATE CLUSTER NODES (one node only)
===============================================================================

  # Authenticate from first node to all cluster nodes
  pcs host auth node1 node2 -u hacluster -p 'YourSecurePassword'

  # Expected output: "node1: Authorized" and "node2: Authorized"

===============================================================================
STEP 5: CREATE CLUSTER (one node only)
===============================================================================

  # Create and start the cluster
  pcs cluster setup <cluster_name> --start node1 node2

  # Example:
  pcs cluster setup hana_cluster --start hana01 hana02

  # Enable cluster to start on boot
  pcs cluster enable --all

  # Verify cluster status
  pcs cluster status
  pcs status

===============================================================================
STEP 6: CONFIGURE STONITH/FENCING (one node only)
===============================================================================

  OPTION A: Production cluster - Configure real STONITH device
  ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  IMPORTANT: STONITH is REQUIRED for production SAP HANA clusters!

  # Example: IPMI/iLO fencing
  pcs stonith create fence_node1 fence_ipmilan \\
      ipaddr=<IPMI_IP_NODE1> login=<USER> passwd=<PASS> \\
      lanplus=1 pcmk_host_list=node1 power_timeout=240 pcmk_reboot_timeout=480

  pcs stonith create fence_node2 fence_ipmilan \\
      ipaddr=<IPMI_IP_NODE2> login=<USER> passwd=<PASS> \\
      lanplus=1 pcmk_host_list=node2 power_timeout=240 pcmk_reboot_timeout=480

  # Example: Cloud fencing (Azure)
  pcs stonith create fence_azure fence_azure_arm \\
      subscriptionId=<SUB_ID> resourceGroup=<RG> tenantId=<TENANT> \\
      login=<APP_ID> passwd=<SECRET> pcmk_host_map="node1:node1-vm;node2:node2-vm"

  # Example: SBD fencing (shared storage)
  pcs stonith create sbd fence_sbd devices=/dev/disk/by-id/<SBD_DEVICE>

  # Verify fencing
  pcs stonith status
  pcs property show stonith-enabled

  OPTION B: Test/Dev cluster - Disable STONITH (NOT for production!)
  ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  # Disable STONITH for non-production/test clusters only
  sudo pcs property set stonith-enabled=false

  # Verify STONITH is disabled
  pcs property show stonith-enabled

  # To re-enable STONITH later (before going to production):
  sudo pcs property set stonith-enabled=true

===============================================================================
STEP 7: CLUSTER PROPERTIES (one node only)
===============================================================================

  # Set cluster properties for SAP HANA
  pcs property set stonith-enabled=true
  pcs property set stonith-timeout=300s

  # Resource defaults
  pcs resource defaults update resource-stickiness=1000
  pcs resource defaults update migration-threshold=3

  # Operation defaults
  pcs resource op defaults update timeout=600s

===============================================================================
STEP 8: INSTALL SAP HANA (both nodes)
===============================================================================

  # Run SAP HANA installation (as root)
  # Primary node (SITE1):
  ./hdblcm --action=install --sid=<SID> --number=<INST_NO>

  # Secondary node (SITE2):
  ./hdblcm --action=install --sid=<SID> --number=<INST_NO>

  # Verify HANA is running (as <sid>adm)
  HDB info
  sapcontrol -nr <INST_NO> -function GetProcessList

===============================================================================
STEP 9: CONFIGURE HANA SYSTEM REPLICATION (both nodes)
===============================================================================

  # On PRIMARY node (as <sid>adm):
  # 1. Create backup (required before enabling SR)
  #    Backup SYSTEMDB and all tenant databases - SR needs a backup for each
  hdbsql -i <INST_NO> -u SYSTEM -d SYSTEMDB "BACKUP DATA USING FILE ('/hana/shared/backup/')"
  hdbsql -i <INST_NO> -u SYSTEM -d <TENANT_SID> "BACKUP DATA USING FILE ('/hana/shared/backup/')"
  # Example:
  #   hdbsql -i 10 -u system -d systemdb "Backup data using File ('/hana/shared/backup/')"
  #   hdbsql -i 10 -u system -d RH1 "Backup data using File ('/hana/shared/backup/')"

  # 2. Enable System Replication
  hdbnsutil -sr_enable --name=<SITE1_NAME>
  # Example: hdbnsutil -sr_enable --name=DC1

  # On SECONDARY node:
  # 1. Copy SSFS keys from primary (required after HANA reinstall)
  #    The secondary must have the same PKI SSFS keys as the primary,
  #    otherwise sr_register will fail to establish a trusted connection.
  #    Run as root or <sid>adm from the PRIMARY node:
  scp /usr/sap/<SID>/SYS/global/security/rsecssfs/data/SSFS_<SID>.DAT \\
      <SECONDARY_HOST>:/usr/sap/<SID>/SYS/global/security/rsecssfs/data/
  scp /usr/sap/<SID>/SYS/global/security/rsecssfs/key/SSFS_<SID>.KEY \\
      <SECONDARY_HOST>:/usr/sap/<SID>/SYS/global/security/rsecssfs/key/
  # Ensure correct ownership: chown <sid>adm:sapsys on both files

  # 2. Register as secondary (--online will stop and restart HANA automatically)
  hdbnsutil -sr_register --remoteHost=<PRIMARY_HOST> \\
      --remoteInstance=<INST_NO> --replicationMode=sync \\
      --operationMode=logreplay --name=<SITE2_NAME> --online

  # Example:
  hdbnsutil -sr_register --remoteHost=hana01 \\
      --remoteInstance=00 --replicationMode=sync \\
      --operationMode=logreplay --name=DC2 --online

  # Verify replication (on primary)
  hdbnsutil -sr_state
  # Should show: "mode: sync", "status: active"

===============================================================================
STEP 10: CONFIGURE HA/DR PROVIDER HOOKS (both nodes)
===============================================================================

  # Edit global.ini (as <sid>adm or root)
  # Path: /hana/shared/<SID>/global/hdb/custom/config/global.ini

  # Add these sections:
  [ha_dr_provider_SAPHanaSR]
  provider = SAPHanaSR
  path = /usr/share/SAPHanaSR
  execution_order = 1

  [ha_dr_provider_suschksrv]
  provider = susChkSrv
  path = /usr/share/SAPHanaSR
  execution_order = 3
  action_on_lost = stop

  [trace]
  ha_dr_saphanasr = info

  # Create sudoers entry for <sid>adm (as root)
  cat > /etc/sudoers.d/20-saphana <<EOF
  <sid>adm ALL=(ALL) NOPASSWD: /usr/sbin/crm_attribute -n hana_<sid>_*
  <sid>adm ALL=(ALL) NOPASSWD: /usr/sbin/SAPHanaSR-hookHelper *
  EOF

  # Restart HANA to load hooks (as <sid>adm)
  HDB stop && HDB start

===============================================================================
STEP 11: DISABLE HANA AUTOSTART (both nodes)
===============================================================================

  # Cluster must control HANA startup, not systemd/sapinit

  # Edit HANA profile (as <sid>adm)
  # Path: /usr/sap/<SID>/SYS/profile/<SID>_HDB<INST>_<hostname>
  # Set: Autostart = 0

  # Or via command:
  sed -i 's/Autostart = 1/Autostart = 0/' /usr/sap/<SID>/SYS/profile/<SID>_HDB*

===============================================================================
STEP 12: CREATE CLUSTER RESOURCES (one node only)
===============================================================================

  # Variables (adjust for your environment)
  SID=<SID>          # e.g., HDB
  INST=<INST_NO>     # e.g., 00

  # 1. Create SAPHanaTopology clone resource
  pcs resource create SAPHanaTopology_${SID}_${INST} SAPHanaTopology \\
      SID=${SID} InstanceNumber=${INST} \\
      op start timeout=600 \\
      op stop timeout=300 \\
      op monitor interval=10 timeout=600 \\
      clone clone-max=2 clone-node-max=1 interleave=true

  # 2. Create SAPHana promotable resource
  pcs resource create SAPHana_${SID}_${INST} SAPHana \\
      SID=${SID} InstanceNumber=${INST} \\
      PREFER_SITE_TAKEOVER=true \\
      DUPLICATE_PRIMARY_TIMEOUT=7200 \\
      AUTOMATED_REGISTER=true \\
      op start timeout=3600 \\
      op stop timeout=3600 \\
      op monitor interval=61 role=Unpromoted timeout=700 \\
      op monitor interval=59 role=Promoted timeout=700 \\
      op promote timeout=3600 \\
      op demote timeout=3600 \\
      promotable promoted-max=1 clone-max=2 clone-node-max=1 interleave=true notify=true

  # 3. Create Virtual IP resource
  pcs resource create vip_${SID}_${INST} IPaddr2 \\
      ip=<VIRTUAL_IP> cidr_netmask=<NETMASK> \\
      op monitor interval=10 timeout=20

  # 4. Create constraints
  pcs constraint order SAPHanaTopology_${SID}_${INST}-clone \\
      then SAPHana_${SID}_${INST}-clone symmetrical=false

  pcs constraint colocation add vip_${SID}_${INST} \\
      with Promoted SAPHana_${SID}_${INST}-clone 2000

===============================================================================
STEP 13: VERIFY CLUSTER (one node only)
===============================================================================

  # Check overall status
  pcs status

  # Check HANA resources
  pcs resource status

  # Check SR attributes
  SAPHanaSR-showAttr

  # Expected output:
  # - Both nodes online
  # - SAPHanaTopology running on both nodes
  # - SAPHana Promoted on primary, Unpromoted on secondary
  # - Virtual IP on primary node
  # - sync_state = SOK (sync OK)

===============================================================================
DOCUMENTATION
===============================================================================

  Red Hat SAP HANA Scale-Up HA (RHEL """
        + str(rhel_major)
        + """):
    """
        + urls["sap_scale_up"]
        + """

  Red Hat SAP HANA Scale-Out HA:
    """
        + urls["sap_scale_out"]
        + """

  SAP HANA System Replication:
    https://help.sap.com/docs/SAP_HANA_PLATFORM/6b94445c94ae495c83a19646e7c3fd56

  Pacemaker Documentation:
    https://clusterlabs.org/pacemaker/doc/

===============================================================================
""",
    }

    if step == "all":
        for s in ["install", "access", "config", "pacemaker", "sap"]:
            print(suggestions.get(s, f"No suggestions available for '{s}'"))
    else:
        print(suggestions.get(step, f"No suggestions available for '{step}'"))
