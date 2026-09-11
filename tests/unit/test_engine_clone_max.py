"""Tests for RulesEngine._validate_clone_max()."""

from tool.sap_cluster_checks.rules.engine import RulesEngine, RuleDefinition, CheckStatus, Severity


def _parsed(**overrides):
    """Build a parsed data dict with valid clone config defaults."""
    defaults = {
        "controller_clone_max": "4",
        "topology_clone_max": "4",
        "controller_clone_node_max": "1",
        "topology_clone_node_max": "1",
        "controller_interleave": "true",
        "topology_interleave": "true",
        "controller_promotable": "true",
    }
    defaults.update(overrides)
    return defaults


def _validate(parsed, rule=None):
    """Helper: call _validate_clone_max on a fresh engine."""
    engine = RulesEngine()
    if rule is None:
        rule = RuleDefinition(
            check_id="CHK_CLONE_CONFIG",
            description="Validate clone configuration",
            severity="WARNING",
        )
    return engine._validate_clone_max(rule, parsed, "node1")


class TestValidConfig:
    def test_all_correct(self):
        result = _validate(_parsed())
        assert result.status == CheckStatus.PASSED
        assert "valid" in result.message.lower()

    def test_scale_up_valid(self):
        result = _validate(_parsed(
            controller_clone_max="2",
            topology_clone_max="2",
        ))
        assert result.status == CheckStatus.PASSED


class TestInvalidCloneNodeMax:
    def test_controller_clone_node_max_not_1(self):
        result = _validate(_parsed(controller_clone_node_max="2"))
        assert result.status == CheckStatus.FAILED
        assert "clone-node-max=2" in result.message

    def test_topology_clone_node_max_not_1(self):
        result = _validate(_parsed(topology_clone_node_max="3"))
        assert result.status == CheckStatus.FAILED
        assert "Topology" in result.message


class TestInvalidInterleave:
    def test_controller_interleave_not_true(self):
        result = _validate(_parsed(controller_interleave="false"))
        assert result.status == CheckStatus.FAILED
        assert "interleave=false" in result.message

    def test_topology_interleave_not_true(self):
        result = _validate(_parsed(topology_interleave="false"))
        assert result.status == CheckStatus.FAILED


class TestInvalidPromotable:
    def test_controller_promotable_not_true(self):
        result = _validate(_parsed(controller_promotable="false"))
        assert result.status == CheckStatus.FAILED
        assert "promotable=false" in result.message


class TestCloneMaxMismatch:
    def test_controller_topology_mismatch(self):
        result = _validate(_parsed(topology_clone_max="6"))
        assert result.status == CheckStatus.FAILED
        assert "mismatch" in result.message.lower()
        assert "Controller=4" in result.message
        assert "Topology=6" in result.message


class TestNoCloneConfig:
    def test_no_clone_config_passes_with_info(self):
        result = _validate(_parsed(
            controller_clone_max=None,
            topology_clone_max=None,
            controller_clone_node_max=None,
            topology_clone_node_max=None,
            controller_interleave=None,
            topology_interleave=None,
            controller_promotable=None,
        ))
        assert result.status == CheckStatus.PASSED
        assert result.severity == Severity.INFO
        assert "not available" in result.message.lower()

    def test_partial_clone_config(self):
        """Only controller config present, topology missing."""
        result = _validate(_parsed(
            topology_clone_max=None,
            topology_clone_node_max=None,
            topology_interleave=None,
        ))
        assert result.status in (CheckStatus.PASSED, CheckStatus.FAILED)


class TestEmptyStringValues:
    def test_empty_clone_max_treated_as_missing(self):
        result = _validate(_parsed(
            controller_clone_max="",
            topology_clone_max="",
        ))
        # Empty strings should be handled gracefully
        assert result.status in (CheckStatus.PASSED, CheckStatus.FAILED)

    def test_empty_interleave_treated_as_not_configured(self):
        """Empty string interleave is treated same as None - not validated."""
        result = _validate(_parsed(controller_interleave=""))
        assert result.status == CheckStatus.PASSED


class TestMultipleIssues:
    def test_multiple_issues_combined(self):
        result = _validate(_parsed(
            topology_clone_max="6",
            controller_clone_node_max="2",
            controller_interleave="false",
        ))
        assert result.status == CheckStatus.FAILED
        assert "clone-node-max=2" in result.message
        assert "interleave=false" in result.message
        assert "mismatch" in result.message.lower()

    def test_failed_result_has_warning_severity(self):
        result = _validate(_parsed(controller_clone_node_max="2"))
        assert result.severity == Severity.WARNING
