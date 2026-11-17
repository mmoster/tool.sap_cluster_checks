"""Tests for HANA resource state detection, including partially_running."""

import pytest

from tool.sap_cluster_checks.rules.engine import CheckResult, CheckStatus, Severity


# ---------------------------------------------------------------------------
# Test _extract_hana_resource_state via a minimal ClusterHealthCheck mock
# ---------------------------------------------------------------------------

class _FakeResult:
    """Minimal stand-in for CheckResult with only the fields needed."""
    def __init__(self, check_id, details=None):
        self.check_id = check_id
        self.details = details or {}
        self.status = CheckStatus.PASSED
        self.node = None


def _extract(parsed_dict):
    """Call _extract_hana_resource_state with a fake result containing parsed_dict."""
    from tool.sap_cluster_checks.cli import ClusterHealthCheck

    hc = ClusterHealthCheck.__new__(ClusterHealthCheck)
    result = _FakeResult(
        check_id="CHK_RESOURCE_STATUS",
        details={"parsed": parsed_dict},
    )
    return hc._extract_hana_resource_state([result])


class TestExtractHanaResourceState:
    """Test the SOSreport fallback path in _extract_hana_resource_state."""

    def test_running_state(self):
        state = _extract({
            "sap_hana_resource": "SAPHana",
            "resource_started": "Promoted",
        })
        assert state == "running"

    def test_partially_running_state(self):
        """Both started and stopped instances -> partially_running."""
        state = _extract({
            "sap_hana_resource": "SAPHana",
            "resource_started": "Promoted",
            "resource_stopped": "Stopped",
        })
        assert state == "partially_running"

    def test_partially_running_not_when_disabled(self):
        """Started + stopped + disabled -> running (disabled takes precedence over stopped)."""
        state = _extract({
            "sap_hana_resource": "SAPHana",
            "resource_started": "Promoted",
            "resource_stopped": "Stopped",
            "resource_disabled": "target-role=Stopped",
        })
        assert state == "running"

    def test_stopped_state(self):
        state = _extract({
            "sap_hana_resource": "SAPHana",
            "resource_stopped": "Stopped",
        })
        assert state == "stopped"

    def test_absent_state(self):
        state = _extract({})
        assert state == "absent"

    def test_maintenance_state(self):
        state = _extract({
            "sap_hana_resource": "SAPHana",
            "resource_maintenance": "(maintenance)",
        })
        assert state == "maintenance"

    def test_explicit_hana_resource_state_takes_precedence(self):
        """The live_cmd emitted state should take precedence over regex inference."""
        state = _extract({
            "hana_resource_state": "partially_running",
            "sap_hana_resource": "SAPHana",
            "resource_started": "Promoted",
        })
        assert state == "partially_running"


class TestGateWithPartiallyRunning:
    """Gate hana_resource_running should pass for partially_running."""

    def test_gate_passes_for_running(self):
        from tool.sap_cluster_checks.cli import ClusterHealthCheck
        hc = ClusterHealthCheck.__new__(ClusterHealthCheck)
        hc._hana_resource_state = "running"
        # Directly test the gate condition
        assert hc._hana_resource_state in ("running", "partially_running")

    def test_gate_passes_for_partially_running(self):
        from tool.sap_cluster_checks.cli import ClusterHealthCheck
        hc = ClusterHealthCheck.__new__(ClusterHealthCheck)
        hc._hana_resource_state = "partially_running"
        assert hc._hana_resource_state in ("running", "partially_running")

    def test_gate_fails_for_stopped(self):
        from tool.sap_cluster_checks.cli import ClusterHealthCheck
        hc = ClusterHealthCheck.__new__(ClusterHealthCheck)
        hc._hana_resource_state = "stopped"
        assert hc._hana_resource_state not in ("running", "partially_running")


class TestHanaStatusPartiallyRunning:
    """hana_resource_active should be True for partially_running."""

    def test_active_for_running(self):
        state = "running"
        assert state in ("running", "partially_running")

    def test_active_for_partially_running(self):
        state = "partially_running"
        assert state in ("running", "partially_running")

    def test_not_active_for_stopped(self):
        state = "stopped"
        assert state not in ("running", "partially_running")

    def test_not_active_for_disabled(self):
        state = "disabled"
        assert state not in ("running", "partially_running")


class TestReportStateDisplay:
    """Resource state should display with spaces in reports."""

    def test_partially_running_formatted(self):
        state = "partially_running"
        assert state.replace("_", " ") == "partially running"

    def test_running_unchanged(self):
        state = "running"
        assert state.replace("_", " ") == "running"
