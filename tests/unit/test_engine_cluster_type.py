"""Tests for RulesEngine._detect_cluster_type()."""

from tool.sap_cluster_checks.rules.engine import RulesEngine, RuleDefinition, CheckStatus, Severity


def _parsed(**overrides):
    """Build a parsed data dict with sensible defaults for a Scale-Up cluster."""
    defaults = {
        "node_count": "2",
        "saphana_resource": None,
        "saphana_controller": "SAPHanaController_S4D_HDB00",
        "saphana_topology": None,
        "majority_maker": None,
        "majority_maker_node": None,
        "clone_max": "2",
        "site_hosts_count": None,
        "sidadm_user": None,
        "hdbnsutil_failed": None,
    }
    defaults.update(overrides)
    return defaults


def _detect(parsed, rule=None):
    """Helper: call _detect_cluster_type on a fresh engine."""
    engine = RulesEngine()
    if rule is None:
        rule = RuleDefinition(
            check_id="CHK_CLUSTER_TYPE",
            description="Detect cluster type",
            severity="INFO",
        )
    return engine._detect_cluster_type(rule, parsed, "node1")


class TestScaleUp:
    def test_basic_scale_up(self):
        result = _detect(_parsed())
        assert result.status == CheckStatus.PASSED
        assert result.details["cluster_type"] == "Scale-Up"
        assert "Scale-Up" in result.message

    def test_scale_up_with_legacy_saphana_resource(self):
        result = _detect(_parsed(
            saphana_resource="SAPHana_S4D_HDB00",
            saphana_controller=None,
        ))
        assert result.details["cluster_type"] == "Scale-Up"

    def test_scale_up_with_app_server(self):
        """Scale-Up with 3 nodes where one has HANA exclusion constraints (app server)."""
        result = _detect(_parsed(
            node_count="3",
            majority_maker="has_constraint",
            majority_maker_node="appserver1",
        ))
        assert result.details["cluster_type"] == "Scale-Up"
        assert "app server" in result.message

    def test_scale_up_default_clone_max(self):
        """When clone_max is not available, defaults to 2 (Scale-Up)."""
        result = _detect(_parsed(clone_max=None))
        assert result.details["cluster_type"] == "Scale-Up"
        assert result.details["clone_max"] == 2

    def test_scale_up_topology_only(self):
        """SAPHanaTopology alone (no SAPHana/Controller) should detect Scale-Up."""
        result = _detect(_parsed(
            saphana_resource=None,
            saphana_controller=None,
            saphana_topology="SAPHanaTopology_S4D_HDB00",
            clone_max="2",
        ))
        assert result.details["cluster_type"] == "Scale-Up"
        assert "Scale-Up" in result.message


class TestScaleOut:
    def test_basic_scale_out(self):
        result = _detect(_parsed(
            node_count="5",
            majority_maker="has_constraint",
            majority_maker_node="mm1",
            clone_max="4",
        ))
        assert result.details["cluster_type"] == "Scale-Out"
        assert result.details["has_majority_maker"] is True
        assert "Scale-Out" in result.message

    def test_scale_out_large_cluster(self):
        result = _detect(_parsed(
            node_count="7",
            majority_maker="has_constraint",
            majority_maker_node="mm1",
            clone_max="6",
            site_hosts_count="3",
            sidadm_user="s4dadm",
        ))
        assert result.details["cluster_type"] == "Scale-Out"
        assert result.details["hdbnsutil_confirms_scaleout"] is True
        assert "verified" in result.message
        assert "3 HANA instances per site" in result.message

    def test_scale_out_without_majority_maker_warning(self):
        result = _detect(_parsed(
            node_count="4",
            clone_max="4",
        ))
        assert result.details["cluster_type"] == "Scale-Out"
        assert result.details["has_majority_maker"] is False
        assert "no majority maker" in result.message.lower()

    def test_scale_out_hdbnsutil_failed(self):
        result = _detect(_parsed(
            node_count="5",
            majority_maker="has_constraint",
            majority_maker_node="mm1",
            clone_max="4",
            hdbnsutil_failed="command not found",
        ))
        assert result.details["cluster_type"] == "Scale-Out"
        assert "could not verify" in result.message.lower()

    def test_scale_out_hdbnsutil_shows_single_host(self):
        result = _detect(_parsed(
            node_count="5",
            majority_maker="has_constraint",
            majority_maker_node="mm1",
            clone_max="4",
            site_hosts_count="1",
        ))
        assert result.details["cluster_type"] == "Scale-Out"
        assert "WARNING" in result.message
        assert "only 1 HANA instance" in result.message


class TestNoHanaResources:
    def test_no_resources_no_nodes(self):
        result = _detect(_parsed(
            node_count=None,
            saphana_controller=None,
            clone_max=None,
        ))
        assert result.details["cluster_type"] == "Not detected"

    def test_single_node(self):
        result = _detect(_parsed(
            node_count="1",
            saphana_controller=None,
            clone_max=None,
        ))
        assert result.details["cluster_type"] == "Single Node"

    def test_nodes_but_no_hana(self):
        result = _detect(_parsed(
            node_count="3",
            saphana_controller=None,
            clone_max=None,
        ))
        assert result.details["cluster_type"] == "Unknown"
        assert "no SAP HANA resources" in result.message


class TestEdgeCases:
    def test_invalid_node_count(self):
        result = _detect(_parsed(node_count="abc"))
        assert result.details["node_count"] == 0
        assert result.details["cluster_type"] == "Scale-Up"

    def test_invalid_clone_max_defaults_to_2(self):
        result = _detect(_parsed(clone_max="abc"))
        assert result.details["clone_max"] == 2
        assert result.details["cluster_type"] == "Scale-Up"

    def test_majority_maker_literal_none_string_not_treated_as_detected(self):
        """The string 'none' should NOT count as having a majority maker."""
        result = _detect(_parsed(
            node_count="5",
            majority_maker="none",
            majority_maker_node="none",
            clone_max="4",
        ))
        assert result.details["cluster_type"] == "Scale-Out"
        assert result.details["has_majority_maker"] is False

    def test_result_is_always_passed(self):
        """Detection checks always return PASSED status."""
        result = _detect(_parsed(
            node_count=None,
            saphana_controller=None,
            clone_max=None,
        ))
        assert result.status == CheckStatus.PASSED
        assert result.severity == Severity.INFO


class TestInferredFromHanaTopology:
    """Tests for hdbnsutil-based inference when no CIB HANA resources exist."""

    def test_scale_out_inferred(self):
        """Multi-host-per-site hdbnsutil data → Scale-Out inference."""
        result = _detect(_parsed(
            node_count="5",
            saphana_resource=None,
            saphana_controller=None,
            clone_max=None,
            site_hosts_count="2",
            sidadm_user="s4dadm",
        ))
        assert result.details["cluster_type"] == "Scale-Out"
        assert result.details["inferred_from_hana_topology"] is True
        assert "inferred from HANA topology" in result.message
        assert "2 hosts per site" in result.message

    def test_scale_up_inferred(self):
        """Single-host-per-site hdbnsutil data → Scale-Up inference."""
        result = _detect(_parsed(
            node_count="2",
            saphana_resource=None,
            saphana_controller=None,
            clone_max=None,
            site_hosts_count="1",
            sidadm_user="s4dadm",
        ))
        assert result.details["cluster_type"] == "Scale-Up"
        assert result.details["inferred_from_hana_topology"] is True
        assert "inferred from HANA topology" in result.message
        assert "1 host per site" in result.message

    def test_large_scale_out_inferred(self):
        """3 hosts per site → Scale-Out inference."""
        result = _detect(_parsed(
            node_count="7",
            saphana_resource=None,
            saphana_controller=None,
            clone_max=None,
            site_hosts_count="3",
            sidadm_user="s4dadm",
        ))
        assert result.details["cluster_type"] == "Scale-Out"
        assert result.details["inferred_from_hana_topology"] is True
        assert "3 hosts per site" in result.message

    def test_no_hdbnsutil_data_remains_unknown(self):
        """No hdbnsutil data at all → still Unknown."""
        result = _detect(_parsed(
            node_count="3",
            saphana_resource=None,
            saphana_controller=None,
            clone_max=None,
            site_hosts_count=None,
        ))
        assert result.details["cluster_type"] == "Unknown"
        assert "no SAP HANA resources" in result.message
        assert "inferred_from_hana_topology" not in result.details

    def test_hdbnsutil_failed_remains_unknown(self):
        """hdbnsutil ran but failed → still Unknown (host count is 0)."""
        result = _detect(_parsed(
            node_count="2",
            saphana_resource=None,
            saphana_controller=None,
            clone_max=None,
            site_hosts_count=None,
            hdbnsutil_failed="command not found",
        ))
        assert result.details["cluster_type"] == "Unknown"
        assert "inferred_from_hana_topology" not in result.details

    def test_normal_detection_no_inferred_flag(self):
        """Normal CIB-based detection does NOT set inferred_from_hana_topology."""
        result = _detect(_parsed())
        assert result.details["cluster_type"] == "Scale-Up"
        assert "inferred_from_hana_topology" not in result.details

    def test_single_node_ignores_hdbnsutil(self):
        """Single-node cluster stays 'Single Node' even if hdbnsutil data exists."""
        result = _detect(_parsed(
            node_count="1",
            saphana_resource=None,
            saphana_controller=None,
            clone_max=None,
            site_hosts_count="2",
            sidadm_user="s4dadm",
        ))
        assert result.details["cluster_type"] == "Single Node"
        assert "inferred_from_hana_topology" not in result.details
