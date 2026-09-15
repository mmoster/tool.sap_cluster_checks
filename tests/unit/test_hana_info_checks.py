"""Tests for HANA information checks: version, process status, SR detail, landscape."""

from tool.sap_cluster_checks.rules.engine import RulesEngine


def _parse(output, parser_config):
    """Helper: call _parse_output on a fresh engine."""
    engine = RulesEngine()
    return engine._parse_output(output, parser_config)


# ── Fixtures: actual sosreport data formats ──────────────────────────

VERSION_OUTPUT = """\
HDB version info:
  version:             2.00.089.03.1779443507
  branch:              fa/hana2sp08
  machine config:      linuxx86_64
  git hash:            81aec2acc04cdc81b95eb027f147977fbadb9e6e
  git merge time:      2026-05-22 09:51:47
  weekstone:           0000.00.0
  cloud edition:       0000.00.00
  compile date:        2026-05-22 10:19:51
  compile host:        unknown
  compile type:        rel
"""

PROCESS_STATUS_ALL_GREEN = """\
26.08.2026 14:43:12
GetProcessList
OK
name, description, dispstatus, textstatus, starttime, elapsedtime, pid
hdbdaemon, HDB Daemon, GREEN, Running, 2026 08 26 08:06:24, 6:36:48, 189537
hdbcompileserver, HDB Compileserver, GREEN, Running, 2026 08 26 08:06:43, 6:36:29, 189785
hdbindexserver, HDB Indexserver-MZ, GREEN, Running, 2026 08 26 08:06:49, 6:36:23, 189849
hdbindexserver, HDB Indexserver-RH1, GREEN, Running, 2026 08 26 08:06:49, 6:36:23, 189852
hdbnameserver, HDB Nameserver, GREEN, Running, 2026 08 26 08:06:25, 6:36:47, 189559
hdbpreprocessor, HDB Preprocessor, GREEN, Running, 2026 08 26 08:06:43, 6:36:29, 189788
hdbwebdispatcher, HDB Web Dispatcher, GREEN, Running, 2026 08 26 08:07:40, 6:35:32, 190474
hdbxsengine, HDB XSEngine-RH1, GREEN, Running, 2026 08 26 08:06:49, 6:36:23, 189855
"""

PROCESS_STATUS_WITH_PROBLEM = """\
26.08.2026 14:43:12
GetProcessList
OK
name, description, dispstatus, textstatus, starttime, elapsedtime, pid
hdbdaemon, HDB Daemon, GREEN, Running, 2026 08 26 08:06:24, 6:36:48, 189537
hdbnameserver, HDB Nameserver, RED, Stopped, , , 0
hdbindexserver, HDB Indexserver, YELLOW, Starting, 2026 08 26 14:43:10, 0:00:02, 200100
"""

REPLICAINFO_OUTPUT = """\
SAP HANA DB Management Client Console
System Replication Primary Information
======================================
System Replication Primary Configuration
 [system_replication] logshipping_timeout = 30s
 [system_replication] enable_full_sync = false
 [system_replication_communication] enable_ssl = on

Primary Statistics
 - ReplicationMode                 : syncmem
 - OperationMode                   : logreplay

 - ReplicationStatus               : ReplicationStatus_Active
 - ReplicationStatusDetails        :
 - ReplicationFullSync             : DISABLED

 - replayBacklog                   : 0 microseconds
 - replayBacklogSize               : 0 bytes

 - SSLActive           : true
"""

REPLICAINFO_WITH_BACKLOG = """\
Primary Statistics
 - ReplicationMode                 : sync
 - OperationMode                   : logreplay
 - ReplicationStatus               : ReplicationStatus_Active
 - replayBacklog                   : 5000000 microseconds
 - replayBacklogSize               : 15048704 bytes
 - SSLActive           : true
"""

LANDSCAPE_SCALE_UP = """\
| Host       | Host   | Host   | Failover | Remove | Storage   | Storage   | Failover | Failover | NameServer | NameServer | IndexServer | IndexServer | Host   | Host   | Worker  | Worker  |
|            | Active | Status | Status   | Status | Config    | Actual    | Config   | Actual   | Config     | Actual     | Config      | Actual      | Config | Actual | Config  | Actual  |
|            |        |        |          |        | Partition | Partition | Group    | Group    | Role       | Role       | Role        | Role        | Roles  | Roles  | Groups  | Groups  |
| ---------- | ------ | ------ | -------- | ------ | --------- | --------- | -------- | -------- | ---------- | ---------- | ----------- | ----------- | ------ | ------ | ------- | ------- |
| rhel100-h1 | yes    | ok     |          |        |         1 |         1 | default  | default  | master 1   | master     | worker      | master      | worker | worker | default | default |

overall host status: ok
"""

LANDSCAPE_SCALE_OUT = """\
| Host       | Host   | Host   | Failover | Remove | Storage   | Storage   | Failover | Failover | NameServer | NameServer | IndexServer | IndexServer | Host   | Host   | Worker  | Worker  |
|            | Active | Status | Status   | Status | Config    | Actual    | Config   | Actual   | Config     | Actual     | Config      | Actual      | Config | Actual | Config  | Actual  |
|            |        |        |          |        | Partition | Partition | Group    | Group    | Role       | Role       | Role        | Role        | Roles  | Roles  | Groups  | Groups  |
| ---------- | ------ | ------ | -------- | ------ | --------- | --------- | -------- | -------- | ---------- | ---------- | ----------- | ----------- | ------ | ------ | ------- | ------- |
| hana-node1 | yes    | ok     |          |        |         1 |         1 | default  | default  | master 1   | master     | worker      | master      | worker | worker | default | default |
| hana-node2 | yes    | ok     |          |        |         2 |         2 | default  | default  | master 2   | slave      | worker      | slave       | worker | worker | default | default |
| hana-node3 | yes    | ok     |          |        |         3 |         3 | default  | default  | master 3   | slave      | worker      | slave       | worker | worker | default | default |

overall host status: ok
"""


# ── CHK_HANA_VERSION tests ───────────────────────────────────────────

class TestHanaVersion:
    PARSER = {
        "type": "regex",
        "multiline": True,
        "search_patterns": [
            {"name": "hana_version", "regex": r"version:\s+(\d+\.\d+\.\d+\.\d+)", "group": 1},
            {"name": "hana_branch", "regex": r"branch:\s+(\S+)", "group": 1},
            {"name": "hana_sp", "regex": r"hana2sp(\d+)", "group": 1},
            {"name": "hana_compile_date", "regex": r"compile date:\s+(\d{4}-\d{2}-\d{2})", "group": 1},
            {"name": "not_hana_node", "regex": r"(NOT_HANA_NODE)", "group": 1},
        ],
    }

    def test_parse_version_from_sosreport(self):
        result = _parse(VERSION_OUTPUT, self.PARSER)
        assert result["hana_version"] == "2.00.089.03"
        assert result["hana_branch"] == "fa/hana2sp08"
        assert result["hana_sp"] == "08"
        assert result["hana_compile_date"] == "2026-05-22"

    def test_not_hana_node(self):
        result = _parse("NOT_HANA_NODE", self.PARSER)
        assert result["not_hana_node"] == "NOT_HANA_NODE"
        assert result["hana_version"] is None

    def test_older_hana_version(self):
        output = """\
HDB version info:
  version:             2.00.070.00.1629794244
  branch:              fa/hana2sp07
  compile date:        2021-08-24 09:30:00
"""
        result = _parse(output, self.PARSER)
        assert result["hana_version"] == "2.00.070.00"
        assert result["hana_sp"] == "07"


# ── CHK_HANA_PROCESS_STATUS tests ────────────────────────────────────

class TestHanaProcessStatus:
    PARSER = {
        "type": "regex",
        "multiline": True,
        "search_patterns": [
            {"name": "getprocesslist", "regex": r"GetProcessList\s+(OK|FAIL)", "group": 1},
            {"name": "process_problem", "regex": r"(hdb\w+),\s*[^,]+,\s*(YELLOW|RED|GRAY)", "group": 0},
            {"name": "process_green", "regex": r"(hdb\w+),\s*[^,]+,\s*GREEN", "group": 0},
            {"name": "process_summary", "regex": r"PROCESS_SUMMARY=(.*)", "group": 1},
            {"name": "not_hana_node", "regex": r"(NOT_HANA_NODE)", "group": 1},
        ],
    }

    def test_all_green(self):
        result = _parse(PROCESS_STATUS_ALL_GREEN, self.PARSER)
        assert result["getprocesslist"] == "OK"
        assert result["process_green"] is not None
        assert result["process_problem"] is None

    def test_process_problem_detected(self):
        result = _parse(PROCESS_STATUS_WITH_PROBLEM, self.PARSER)
        assert result["getprocesslist"] == "OK"
        assert result["process_problem"] is not None
        # First non-GREEN process should be detected
        assert "RED" in result["process_problem"] or "YELLOW" in result["process_problem"]

    def test_not_hana_node(self):
        result = _parse("NOT_HANA_NODE", self.PARSER)
        assert result["not_hana_node"] == "NOT_HANA_NODE"
        assert result["process_green"] is None

    def test_empty_output(self):
        result = _parse("", self.PARSER)
        assert result["getprocesslist"] is None
        assert result["process_green"] is None


# ── CHK_HANA_SR_DETAIL tests ─────────────────────────────────────────

class TestHanaSrDetail:
    PARSER = {
        "type": "regex",
        "multiline": True,
        "search_patterns": [
            {"name": "replication_mode", "regex": r"ReplicationMode\s+:\s+(\w+)", "group": 1},
            {"name": "operation_mode", "regex": r"OperationMode\s+:\s+(\w+)", "group": 1},
            {"name": "replication_status", "regex": r"ReplicationStatus\s+:\s+(\S+)", "group": 1},
            {"name": "replay_backlog_us", "regex": r"replayBacklog\s+:\s+(\d+)\s+microseconds", "group": 1},
            {"name": "replay_backlog_bytes", "regex": r"replayBacklogSize\s+:\s+(\d+)\s+bytes", "group": 1},
            {"name": "ssl_active", "regex": r"SSLActive\s+:\s+(true|false)", "group": 1},
            {"name": "full_sync", "regex": r"enable_full_sync\s*=\s*(true|false)", "group": 1},
            {"name": "logshipping_timeout", "regex": r"logshipping_timeout\s*=\s*(\S+)", "group": 1},
            {"name": "not_hana_node", "regex": r"(NOT_HANA_NODE)", "group": 1},
        ],
    }

    def test_healthy_replication(self):
        result = _parse(REPLICAINFO_OUTPUT, self.PARSER)
        assert result["replication_mode"] == "syncmem"
        assert result["operation_mode"] == "logreplay"
        assert result["replication_status"] == "ReplicationStatus_Active"
        assert result["replay_backlog_us"] == "0"
        assert result["replay_backlog_bytes"] == "0"
        assert result["ssl_active"] == "true"
        assert result["full_sync"] == "false"
        assert result["logshipping_timeout"] == "30s"

    def test_replication_with_backlog(self):
        result = _parse(REPLICAINFO_WITH_BACKLOG, self.PARSER)
        assert result["replication_mode"] == "sync"
        assert result["replication_status"] == "ReplicationStatus_Active"
        assert result["replay_backlog_us"] == "5000000"
        assert result["replay_backlog_bytes"] == "15048704"

    def test_not_hana_node(self):
        result = _parse("NOT_HANA_NODE", self.PARSER)
        assert result["not_hana_node"] == "NOT_HANA_NODE"
        assert result["replication_mode"] is None

    def test_no_replication_configured(self):
        result = _parse("this system is not a system replication site", self.PARSER)
        assert result["replication_status"] is None
        assert result["replication_mode"] is None


# ── CHK_HANA_LANDSCAPE tests ─────────────────────────────────────────

class TestHanaLandscape:
    PARSER = {
        "type": "regex",
        "multiline": True,
        "search_patterns": [
            {"name": "landscape_host", "regex": r"^\|\s+(\S+)\s+\|\s+(yes|no)\s+\|\s+(\w+)\s+\|", "group": 1},
            {"name": "host_status", "regex": r"^\|\s+\S+\s+\|\s+(?:yes|no)\s+\|\s+(\w+)\s+\|", "group": 1},
            {"name": "second_host", "regex": r"^\|\s+\S+\s+\|\s+(?:yes|no)\s+\|[^\n]+\n\|\s+(\S+)\s+\|\s+(?:yes|no)\s+\|", "group": 1},
            {"name": "overall_status", "regex": r"overall host status:\s+(\w+)", "group": 1},
            {"name": "hana_host_count", "regex": r"HANA_HOST_COUNT=(\d+)", "group": 1},
            {"name": "hana_topology", "regex": r"HANA_TOPOLOGY=(\S+)", "group": 1},
            {"name": "not_hana_node", "regex": r"(NOT_HANA_NODE)", "group": 1},
        ],
    }

    def test_scale_up_from_sosreport(self):
        result = _parse(LANDSCAPE_SCALE_UP, self.PARSER)
        assert result["landscape_host"] == "rhel100-h1"
        assert result["host_status"] == "ok"
        assert result["overall_status"] == "ok"
        # Only 1 host → no second_host match → Scale-Up
        assert result["second_host"] is None

    def test_scale_out_from_sosreport(self):
        result = _parse(LANDSCAPE_SCALE_OUT, self.PARSER)
        assert result["landscape_host"] == "hana-node1"
        assert result["second_host"] == "hana-node2"
        assert result["overall_status"] == "ok"

    def test_scale_up_from_live_cmd(self):
        live_output = LANDSCAPE_SCALE_UP + "\nHANA_HOST_COUNT=1\nHANA_TOPOLOGY=Scale-Up\n"
        result = _parse(live_output, self.PARSER)
        assert result["hana_host_count"] == "1"
        assert result["hana_topology"] == "Scale-Up"

    def test_scale_out_from_live_cmd(self):
        live_output = LANDSCAPE_SCALE_OUT + "\nHANA_HOST_COUNT=3\nHANA_TOPOLOGY=Scale-Out\n"
        result = _parse(live_output, self.PARSER)
        assert result["hana_host_count"] == "3"
        assert result["hana_topology"] == "Scale-Out"

    def test_not_hana_node(self):
        result = _parse("NOT_HANA_NODE", self.PARSER)
        assert result["not_hana_node"] == "NOT_HANA_NODE"
        assert result["landscape_host"] is None

    def test_host_error_status(self):
        error_landscape = LANDSCAPE_SCALE_UP.replace("| ok     |", "| error  |").replace("overall host status: ok", "overall host status: error")
        result = _parse(error_landscape, self.PARSER)
        assert result["host_status"] == "error"
        assert result["overall_status"] == "error"

    # ── Validation-level tests (in_if_exists on overall_status) ──

    STATUS_EXPECTATION = {
        "key": "overall_status",
        "operator": "in_if_exists",
        "value": ["ok"],
        "severity": "WARNING",
        "message": "HANA landscape overall status: ${overall_status}",
        "pass_message": "HANA landscape status: ok",
    }

    def test_validation_ok_status_passes(self):
        engine = RulesEngine()
        parsed = _parse(LANDSCAPE_SCALE_UP, self.PARSER)
        passed, msg, pass_msg = engine._evaluate_expectation(parsed, self.STATUS_EXPECTATION)
        assert passed is True

    def test_validation_error_status_fails(self):
        engine = RulesEngine()
        error_landscape = LANDSCAPE_SCALE_UP.replace("| ok     |", "| error  |").replace("overall host status: ok", "overall host status: error")
        parsed = _parse(error_landscape, self.PARSER)
        passed, msg, pass_msg = engine._evaluate_expectation(parsed, self.STATUS_EXPECTATION)
        assert passed is False
        assert pass_msg is None

    def test_validation_non_hana_node_passes(self):
        engine = RulesEngine()
        parsed = _parse("NOT_HANA_NODE", self.PARSER)
        passed, msg, pass_msg = engine._evaluate_expectation(parsed, self.STATUS_EXPECTATION)
        assert passed is True
        assert pass_msg is None


# ── CHK_ALERT_FENCING tests ─────────────────────────────────────────

ALERT_FENCING_LIVE_OK = """\
=== ALERTS ===
Alerts:
 Alert: alert-1 (path=/usr/share/pacemaker/alerts/SAPHanaSR-alert-fencing.sh)
  Recipients:
   Recipient: alert-1-rec1 (value=fence_aws)
=== FENCING ===
  * fence_aws	(stonith:fence_aws):	Started node1
"""

ALERT_FENCING_NO_ALERTS = """\
=== ALERTS ===
NO_ALERTS
=== FENCING ===
  * fence_aws	(stonith:fence_aws):	Started node1
"""

ALERT_FENCING_NO_FENCING = """\
=== ALERTS ===
Alerts:
 Alert: alert-1 (path=/usr/share/pacemaker/alerts/SAPHanaSR-alert-fencing.sh)
=== FENCING ===
"""

ALERT_FENCING_PCS_CONFIG = """\
Alerts:
 Alert: alert-1 (path=/usr/share/pacemaker/alerts/SAPHanaSR-alert-fencing.sh)

Stonith Devices:
  Resource: fence_sbd (class=stonith type=fence_sbd)
   Attributes: fence_sbd-instance_attributes
     devices=/dev/vdb
   Operations:
     monitor: fence_sbd-monitor-interval-600s
       interval=600s timeout=15s
"""


class TestAlertFencing:
    PARSER = {
        "type": "regex",
        "multiline": True,
        "search_patterns": [
            {"name": "alert_config", "regex": "(Alerts:|alert|SAPHanaSR-alert)", "group": 0},
            {"name": "fence_config", "regex": "(stonith|fence_|fencing-topology|Started)", "group": 0},
            {"name": "no_alerts", "regex": "NO_ALERTS", "group": 0},
        ],
    }

    FENCE_EXPECTATION = {
        "key": "fence_config",
        "operator": "exists",
        "message": "No STONITH/fencing configuration found",
        "pass_message": "Fencing: ${fence_config}",
    }

    NO_ALERTS_EXPECTATION = {
        "key": "no_alerts",
        "operator": "not_exists",
        "severity": "WARNING",
        "message": "No SAPHanaSR alerts configured (recommended for automatic fence reaction)",
    }

    def test_parse_live_with_alerts_and_fencing(self):
        result = _parse(ALERT_FENCING_LIVE_OK, self.PARSER)
        assert result["alert_config"] is not None
        assert result["fence_config"] is not None
        assert result["no_alerts"] is None

    def test_parse_no_alerts(self):
        result = _parse(ALERT_FENCING_NO_ALERTS, self.PARSER)
        assert result["no_alerts"] == "NO_ALERTS"
        assert result["fence_config"] is not None

    def test_parse_no_fencing(self):
        result = _parse(ALERT_FENCING_NO_FENCING, self.PARSER)
        assert result["fence_config"] is None

    def test_parse_sosreport_pcs_config(self):
        result = _parse(ALERT_FENCING_PCS_CONFIG, self.PARSER)
        assert result["alert_config"] is not None
        assert result["fence_config"] is not None
        assert result["no_alerts"] is None

    def test_validation_fencing_present_passes(self):
        engine = RulesEngine()
        parsed = _parse(ALERT_FENCING_LIVE_OK, self.PARSER)
        passed, msg, pass_msg = engine._evaluate_expectation(parsed, self.FENCE_EXPECTATION)
        assert passed is True

    def test_validation_fencing_absent_fails(self):
        engine = RulesEngine()
        parsed = _parse(ALERT_FENCING_NO_FENCING, self.PARSER)
        passed, msg, pass_msg = engine._evaluate_expectation(parsed, self.FENCE_EXPECTATION)
        assert passed is False

    def test_validation_no_alerts_warns(self):
        engine = RulesEngine()
        parsed = _parse(ALERT_FENCING_NO_ALERTS, self.PARSER)
        passed, msg, pass_msg = engine._evaluate_expectation(parsed, self.NO_ALERTS_EXPECTATION)
        assert passed is False

    def test_validation_alerts_configured_passes(self):
        engine = RulesEngine()
        parsed = _parse(ALERT_FENCING_LIVE_OK, self.PARSER)
        passed, msg, pass_msg = engine._evaluate_expectation(parsed, self.NO_ALERTS_EXPECTATION)
        assert passed is True
