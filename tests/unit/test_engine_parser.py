"""Tests for RulesEngine._parse_output()."""

from tool.sap_cluster_checks.rules.engine import RulesEngine


def _parse(output, parser_config):
    """Helper: call _parse_output on a fresh engine."""
    engine = RulesEngine()
    return engine._parse_output(output, parser_config)


class TestNonRegexType:
    def test_returns_raw_for_non_regex(self):
        result = _parse("hello world", {"type": "text"})
        assert result == {"raw": "hello world"}

    def test_returns_raw_when_type_missing(self):
        result = _parse("hello world", {})
        assert result == {"raw": "hello world"}


class TestRegexPatterns:
    def test_named_group_extraction(self):
        output = "version: 1.2.3"
        config = {
            "type": "regex",
            "search_patterns": [
                {"name": "version", "regex": r"version:\s+(\S+)", "group": 1},
            ],
        }
        result = _parse(output, config)
        assert result["version"] == "1.2.3"

    def test_group_zero_full_match(self):
        output = "status: active"
        config = {
            "type": "regex",
            "search_patterns": [
                {"name": "status_line", "regex": r"status:\s+\w+", "group": 0},
            ],
        }
        result = _parse(output, config)
        assert result["status_line"] == "status: active"

    def test_no_match_returns_none(self):
        output = "no version here"
        config = {
            "type": "regex",
            "search_patterns": [
                {"name": "version", "regex": r"version:\s+(\S+)", "group": 1},
            ],
        }
        result = _parse(output, config)
        assert result["version"] is None

    def test_multiple_patterns(self):
        output = "name: test\nversion: 2.0\nstatus: ok"
        config = {
            "type": "regex",
            "search_patterns": [
                {"name": "name", "regex": r"name:\s+(\S+)", "group": 1},
                {"name": "version", "regex": r"version:\s+(\S+)", "group": 1},
                {"name": "status", "regex": r"status:\s+(\S+)", "group": 1},
            ],
        }
        result = _parse(output, config)
        assert result["name"] == "test"
        assert result["version"] == "2.0"
        assert result["status"] == "ok"

    def test_invalid_regex_stores_error(self):
        output = "test"
        config = {
            "type": "regex",
            "search_patterns": [
                {"name": "bad", "regex": r"[invalid", "group": 0},
            ],
        }
        result = _parse(output, config)
        assert result["bad"] is None
        assert "bad_error" in result

    def test_multiline_flag(self):
        output = "line1\nversion: 3.0\nline3"
        config = {
            "type": "regex",
            "multiline": True,
            "search_patterns": [
                {"name": "version", "regex": r"^version:\s+(\S+)", "group": 1},
            ],
        }
        result = _parse(output, config)
        assert result["version"] == "3.0"

    def test_missing_name_skipped(self):
        output = "test"
        config = {
            "type": "regex",
            "search_patterns": [
                {"regex": r"test", "group": 0},
            ],
        }
        result = _parse(output, config)
        assert len(result) == 0

    def test_missing_regex_skipped(self):
        output = "test"
        config = {
            "type": "regex",
            "search_patterns": [
                {"name": "test", "group": 0},
            ],
        }
        result = _parse(output, config)
        assert len(result) == 0

    def test_group_exceeds_captures_returns_none(self):
        output = "hello"
        config = {
            "type": "regex",
            "search_patterns": [
                {"name": "val", "regex": r"(hello)", "group": 5},
            ],
        }
        result = _parse(output, config)
        assert result["val"] is None

    def test_empty_output(self):
        config = {
            "type": "regex",
            "search_patterns": [
                {"name": "version", "regex": r"version:\s+(\S+)", "group": 1},
            ],
        }
        result = _parse("", config)
        assert result["version"] is None

    def test_empty_search_patterns(self):
        result = _parse("hello", {"type": "regex", "search_patterns": []})
        assert result == {}

    def test_default_group_zero(self):
        output = "status: active"
        config = {
            "type": "regex",
            "search_patterns": [
                {"name": "status_line", "regex": r"status:\s+\w+"},
            ],
        }
        result = _parse(output, config)
        assert result["status_line"] == "status: active"


class TestHanaResourceTypeDetection:
    """Verify CHK_CLUSTER_TYPE parser patterns match agent types, not just resource names."""

    CLUSTER_TYPE_PATTERNS = {
        "type": "regex",
        "multiline": True,
        "search_patterns": [
            {"name": "saphana_resource", "regex": r"(SAPHana[_):\s][^A-Za-z])", "group": 0},
            {"name": "saphana_controller", "regex": r"(SAPHanaController)", "group": 0},
            {"name": "saphana_topology", "regex": r"(SAPHanaTopology)", "group": 0},
        ],
    }

    def test_standard_resource_names(self):
        """Standard naming: SAPHanaController_S4D_HDB00."""
        output = (
            "  * Clone Set: SAPHanaController_S4D_HDB00-clone [SAPHanaController_S4D_HDB00]:\n"
            "    * SAPHanaController_S4D_HDB00 (ocf:heartbeat:SAPHanaController): Promoted node1\n"
            "  * Clone Set: SAPHanaTopology_S4D_HDB00-clone [SAPHanaTopology_S4D_HDB00]:\n"
            "    * SAPHanaTopology_S4D_HDB00 (ocf:heartbeat:SAPHanaTopology): Started node1\n"
        )
        result = _parse(output, self.CLUSTER_TYPE_PATTERNS)
        assert result["saphana_controller"] is not None
        assert result["saphana_topology"] is not None

    def test_custom_resource_names(self):
        """Custom naming (SAP HA Ansible role): rsc_SAPHanaCon_RH1_HDB02."""
        output = (
            "  * Clone Set: cln_SAPHanaCon_RH1_HDB02 [rsc_SAPHanaCon_RH1_HDB02] (promotable):\n"
            "    * rsc_SAPHanaCon_RH1_HDB02 (ocf:heartbeat:SAPHanaController): Promoted node1\n"
            "    * rsc_SAPHanaCon_RH1_HDB02 (ocf:heartbeat:SAPHanaController): Unpromoted node2\n"
            "  * Clone Set: cln_SAPHanaTop_RH1_HDB02 [rsc_SAPHanaTop_RH1_HDB02]:\n"
            "    * rsc_SAPHanaTop_RH1_HDB02 (ocf:heartbeat:SAPHanaTopology): Started node1\n"
            "    * rsc_SAPHanaTop_RH1_HDB02 (ocf:heartbeat:SAPHanaTopology): Started node2\n"
        )
        result = _parse(output, self.CLUSTER_TYPE_PATTERNS)
        assert result["saphana_controller"] is not None, "SAPHanaController not detected from agent type"
        assert result["saphana_topology"] is not None, "SAPHanaTopology not detected from agent type"

    def test_legacy_saphana_type(self):
        """Legacy naming: SAPHana_S4D_HDB00 (resource agent type is SAPHana, not SAPHanaController)."""
        output = (
            "  * Clone Set: SAPHana_S4D_HDB00-clone [SAPHana_S4D_HDB00]:\n"
            "    * SAPHana_S4D_HDB00 (ocf:heartbeat:SAPHana): Master node1\n"
        )
        result = _parse(output, self.CLUSTER_TYPE_PATTERNS)
        assert result["saphana_resource"] is not None, "Legacy SAPHana not detected"
        assert result["saphana_controller"] is None, "Should not match SAPHanaController"

    def test_legacy_saphana_custom_name(self):
        """Legacy SAPHana with custom resource name - detected by agent type."""
        output = (
            "    * rsc_hana_S4D (ocf:heartbeat:SAPHana): Master node1\n"
        )
        result = _parse(output, self.CLUSTER_TYPE_PATTERNS)
        assert result["saphana_resource"] is not None, "Legacy SAPHana type not detected"

    def test_no_hana_resources(self):
        """No HANA resources in output."""
        output = "  * Clone Set: fence-clone [fence_kdump]:\n    * Started: [ node1 node2 ]\n"
        result = _parse(output, self.CLUSTER_TYPE_PATTERNS)
        assert result["saphana_resource"] is None
        assert result["saphana_controller"] is None
        assert result["saphana_topology"] is None
