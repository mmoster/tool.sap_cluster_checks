"""Tests for standby node handling: severity adjustment, report context, engine skip."""

import pytest

from tool.sap_cluster_checks.rules.engine import (
    CheckResult, CheckStatus, RuleDefinition, RulesEngine, Severity,
)


# ---------------------------------------------------------------------------
# Helpers -- build a minimal ClusterHealthCheck with only the fields needed
# ---------------------------------------------------------------------------

class _FakeHealthCheck:
    """Minimal stand-in for ClusterHealthCheck with standby-related methods."""

    def __init__(self, standby_nodes, check_results, debug=False):
        self._standby_nodes = list(standby_nodes)
        self.check_results = list(check_results)
        self._debug = debug

    def _debug_print(self, msg):
        if self._debug:
            print(msg)

    # Import the real method so we test the actual implementation
    from tool.sap_cluster_checks.cli import ClusterHealthCheck as _CHC
    _adjust_severity_for_standby = _CHC._adjust_severity_for_standby


# ---------------------------------------------------------------------------
# _adjust_severity_for_standby
# ---------------------------------------------------------------------------

class TestAdjustSeverityForStandby:
    """Test severity downgrade when standby nodes explain the failure."""

    def _make_result(self, check_id, message, status=CheckStatus.FAILED,
                     severity=Severity.CRITICAL):
        return CheckResult(
            check_id=check_id,
            description=f"desc for {check_id}",
            status=status,
            severity=severity,
            message=message,
        )

    def test_promoted_roles_downgraded_when_standby(self):
        result = self._make_result(
            "CHK_PROMOTED_ROLES",
            "No unpromoted instance found - no failover target",
        )
        hc = _FakeHealthCheck(["dc2hana2"], [result])
        hc._adjust_severity_for_standby()

        assert result.severity == Severity.WARNING
        assert "dc2hana2" in result.message
        assert "standby" in result.message

    def test_site_roles_downgraded_when_standby(self):
        result = self._make_result(
            "CHK_SITE_ROLES",
            "No secondary/unpromoted node detected - no failover target",
        )
        hc = _FakeHealthCheck(["node2"], [result])
        hc._adjust_severity_for_standby()

        assert result.severity == Severity.WARNING
        assert "node2" in result.message

    def test_no_downgrade_without_standby_nodes(self):
        result = self._make_result(
            "CHK_PROMOTED_ROLES",
            "No unpromoted instance found - no failover target",
        )
        hc = _FakeHealthCheck([], [result])
        hc._adjust_severity_for_standby()

        assert result.severity == Severity.CRITICAL

    def test_no_downgrade_for_unrelated_checks(self):
        result = self._make_result(
            "CHK_STONITH_CONFIG",
            "No failover possible without STONITH",
        )
        hc = _FakeHealthCheck(["dc2hana2"], [result])
        hc._adjust_severity_for_standby()

        assert result.severity == Severity.CRITICAL

    def test_no_downgrade_for_passed_checks(self):
        result = self._make_result(
            "CHK_PROMOTED_ROLES",
            "No unpromoted instance found",
            status=CheckStatus.PASSED,
        )
        hc = _FakeHealthCheck(["dc2hana2"], [result])
        hc._adjust_severity_for_standby()

        assert result.severity == Severity.CRITICAL

    def test_no_downgrade_for_warning_severity(self):
        result = self._make_result(
            "CHK_PROMOTED_ROLES",
            "No unpromoted instance found - no failover target",
            severity=Severity.WARNING,
        )
        hc = _FakeHealthCheck(["dc2hana2"], [result])
        hc._adjust_severity_for_standby()

        # Already WARNING, should stay WARNING (not double-downgraded)
        assert result.severity == Severity.WARNING

    def test_no_downgrade_for_unrelated_message(self):
        """CRITICAL failure without standby keywords is not downgraded."""
        result = self._make_result(
            "CHK_PROMOTED_ROLES",
            "Some completely different error",
        )
        hc = _FakeHealthCheck(["dc2hana2"], [result])
        hc._adjust_severity_for_standby()

        assert result.severity == Severity.CRITICAL

    def test_multiple_standby_nodes_listed(self):
        result = self._make_result(
            "CHK_PROMOTED_ROLES",
            "No unpromoted instance found - no failover target",
        )
        hc = _FakeHealthCheck(["node1", "node2"], [result])
        hc._adjust_severity_for_standby()

        assert result.severity == Severity.WARNING
        assert "node1, node2" in result.message

    def test_both_checks_downgraded_simultaneously(self):
        r1 = self._make_result(
            "CHK_PROMOTED_ROLES",
            "No unpromoted instance found - no failover target",
        )
        r2 = self._make_result(
            "CHK_SITE_ROLES",
            "No secondary/unpromoted node detected - no failover target",
        )
        hc = _FakeHealthCheck(["dc2hana2"], [r1, r2])
        hc._adjust_severity_for_standby()

        assert r1.severity == Severity.WARNING
        assert r2.severity == Severity.WARNING

    def test_no_primary_message_downgraded(self):
        result = self._make_result(
            "CHK_SITE_ROLES",
            "No primary/promoted node detected",
        )
        hc = _FakeHealthCheck(["dc1hana1"], [result])
        hc._adjust_severity_for_standby()

        # "no primary" is in standby_keywords
        assert result.severity == Severity.WARNING


# ---------------------------------------------------------------------------
# Report recommendations context (unit-testable logic)
# ---------------------------------------------------------------------------

class TestReportRecommendationContext:
    """Test that report recommendation logic correctly detects standby context."""

    def _make_check_dict(self, check_id, message, severity="CRITICAL",
                         status="FAILED"):
        return {
            "check_id": check_id,
            "message": message,
            "severity": severity,
            "status": status,
        }

    def test_standby_detected_from_node_status_message(self):
        """CHK_NODE_STATUS with 'standby' in message flags has_standby."""
        checks = [
            self._make_check_dict(
                "CHK_NODE_STATUS",
                "One or more cluster nodes are in STANDBY mode: dc2hana2",
                severity="WARNING",
            ),
        ]
        has_standby = any(
            c.get("check_id") == "CHK_NODE_STATUS"
            and "standby" in (c.get("message") or "").lower()
            for c in checks
        )
        assert has_standby is True

    def test_no_standby_without_keyword(self):
        """CHK_NODE_STATUS without 'standby' does not flag has_standby."""
        checks = [
            self._make_check_dict(
                "CHK_NODE_STATUS",
                "One or more cluster nodes are OFFLINE",
                severity="CRITICAL",
            ),
        ]
        has_standby = any(
            c.get("check_id") == "CHK_NODE_STATUS"
            and "standby" in (c.get("message") or "").lower()
            for c in checks
        )
        assert has_standby is False

    def test_has_no_failover_detected(self):
        """CHK_PROMOTED_ROLES in failed checks flags has_no_failover."""
        checks = [
            self._make_check_dict(
                "CHK_PROMOTED_ROLES",
                "No unpromoted instance found",
                severity="WARNING",
            ),
        ]
        has_no_failover = any(
            c.get("check_id") in ["CHK_PROMOTED_ROLES", "CHK_SITE_ROLES"]
            for c in checks
        )
        assert has_no_failover is True

    def test_standby_plus_no_failover_triggers_linked_recommendation(self):
        """When both standby and no-failover are present, both flags are True."""
        all_failed = [
            self._make_check_dict(
                "CHK_NODE_STATUS",
                "One or more cluster nodes are in STANDBY mode: dc2hana2",
                severity="WARNING",
            ),
            self._make_check_dict(
                "CHK_PROMOTED_ROLES",
                "No unpromoted instance found (expected: node dc2hana2 is in standby)",
                severity="WARNING",
            ),
        ]
        has_standby = any(
            c.get("check_id") == "CHK_NODE_STATUS"
            and "standby" in (c.get("message") or "").lower()
            for c in all_failed
        )
        has_no_failover = any(
            c.get("check_id") in ["CHK_PROMOTED_ROLES", "CHK_SITE_ROLES"]
            for c in all_failed
        )
        assert has_standby is True
        assert has_no_failover is True


# ---------------------------------------------------------------------------
# Engine-level standby node skipping (skip_standby flag)
# ---------------------------------------------------------------------------

class TestEngineStandbySkip:
    """Test that the engine skips standby nodes for checks with skip_standby=True."""

    def test_rule_definition_defaults_skip_standby_false(self):
        rule = RuleDefinition(check_id="CHK_TEST")
        assert rule.skip_standby is False

    def test_rule_definition_accepts_skip_standby_true(self):
        rule = RuleDefinition(check_id="CHK_TEST", skip_standby=True)
        assert rule.skip_standby is True

    def test_set_standby_nodes(self):
        engine = RulesEngine()
        engine.set_standby_nodes({"node1", "node2"})
        assert engine._standby_nodes == {"node1", "node2"}

    def test_set_standby_nodes_empty(self):
        engine = RulesEngine()
        engine.set_standby_nodes(set())
        assert engine._standby_nodes == set()

    def test_skip_standby_skips_node_in_run_check(self):
        """A check with skip_standby=True should SKIP on standby nodes."""
        engine = RulesEngine()
        engine.set_standby_nodes({"dc2hana2"})

        rule = RuleDefinition(
            check_id="CHK_TEST_SKIP",
            severity="WARNING",
            description="Test check",
            skip_standby=True,
            source_definitions={"live_cmd": "echo test"},
            parser={"type": "regex", "search_patterns": []},
            validation_logic={"expectations": []},
        )

        nodes = {
            "dc1hana1": {"preferred_method": "ssh", "ssh_user": "root"},
            "dc2hana2": {"preferred_method": "ssh", "ssh_user": "root"},
        }

        results = engine.run_check(rule, nodes)

        # dc2hana2 should be SKIPPED
        standby_results = [r for r in results if r.node == "dc2hana2"]
        assert len(standby_results) == 1
        assert standby_results[0].status == CheckStatus.SKIPPED
        assert "standby" in standby_results[0].message

    def test_no_skip_when_flag_false(self):
        """A check with skip_standby=False should NOT skip standby nodes."""
        engine = RulesEngine()
        engine.set_standby_nodes({"dc2hana2"})

        rule = RuleDefinition(
            check_id="CHK_TEST_NOSKIP",
            severity="WARNING",
            description="Test check",
            skip_standby=False,
            source_definitions={"live_cmd": "echo test"},
            parser={"type": "regex", "search_patterns": []},
            validation_logic={"expectations": []},
        )

        nodes = {
            "dc2hana2": {"preferred_method": "ssh", "ssh_user": "root"},
        }

        results = engine.run_check(rule, nodes)

        # Should NOT be skipped (may fail due to no SSH, but not SKIPPED for standby)
        standby_results = [r for r in results if r.node == "dc2hana2"]
        assert len(standby_results) == 1
        assert "standby" not in (standby_results[0].message or "")

    def test_no_skip_when_node_not_standby(self):
        """Non-standby nodes should not be skipped even if skip_standby=True."""
        engine = RulesEngine()
        engine.set_standby_nodes({"dc2hana2"})

        rule = RuleDefinition(
            check_id="CHK_TEST_OTHER",
            severity="WARNING",
            description="Test check",
            skip_standby=True,
            source_definitions={"live_cmd": "echo test"},
            parser={"type": "regex", "search_patterns": []},
            validation_logic={"expectations": []},
        )

        nodes = {
            "dc1hana1": {"preferred_method": "ssh", "ssh_user": "root"},
        }

        results = engine.run_check(rule, nodes)

        # dc1hana1 is not in standby, should not be skipped for standby reasons
        assert len(results) == 1
        if results[0].status == CheckStatus.SKIPPED:
            assert "standby" not in (results[0].message or "")
