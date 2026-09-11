"""Tests for CheckDispatch - dispatch manifest loading and querying."""

from pathlib import Path

import pytest

from tool.sap_cluster_checks.rules.engine import (
    CheckDispatch,
    DispatchStep,
    RuleDefinition,
)

FIXTURES = Path(__file__).parent / "fixtures"
TEST_MANIFEST = str(FIXTURES / "test_dispatch.yaml")


@pytest.fixture
def dispatch():
    """Return a loaded CheckDispatch from the test fixture manifest."""
    d = CheckDispatch(manifest_path=TEST_MANIFEST)
    d.load()
    return d


class TestLoad:
    def test_load_valid_manifest(self):
        d = CheckDispatch(manifest_path=TEST_MANIFEST)
        result = d.load()
        assert result is True
        assert d.loaded is True

    def test_load_missing_file(self):
        d = CheckDispatch(manifest_path="/nonexistent/path.yaml")
        result = d.load()
        assert result is False
        assert d.loaded is False

    def test_load_invalid_yaml(self, tmp_path):
        bad_file = tmp_path / "bad.yaml"
        bad_file.write_text(": : : invalid yaml [[[\n")
        d = CheckDispatch(manifest_path=str(bad_file))
        assert d.load() is False

    def test_load_empty_file(self, tmp_path):
        empty_file = tmp_path / "empty.yaml"
        empty_file.write_text("")
        d = CheckDispatch(manifest_path=str(empty_file))
        assert d.load() is False


class TestGetStep:
    def test_returns_dispatch_step(self, dispatch):
        step = dispatch.get_step("test_step")
        assert step is not None
        assert isinstance(step, DispatchStep)
        assert step.name == "Test Step"
        assert step.step_number == 1

    def test_returns_none_for_unknown(self, dispatch):
        assert dispatch.get_step("nonexistent") is None


class TestGetPhases:
    def test_without_topology_returns_all(self, dispatch):
        phases = dispatch.get_phases("test_step")
        assert len(phases) == 2
        assert len(phases[0].checks) == 3

    def test_with_scale_up_topology_filters(self, dispatch):
        phases = dispatch.get_phases("test_step", detected_topology="Scale-Up")
        # Phase 1: CHK_A (all), CHK_B (Scale-Up) - CHK_C (Scale-Out) removed
        check_ids = [c.check_id for c in phases[0].checks]
        assert check_ids == ["CHK_A", "CHK_B"]

    def test_with_scale_out_topology_filters(self, dispatch):
        phases = dispatch.get_phases("test_step", detected_topology="Scale-Out")
        check_ids = [c.check_id for c in phases[0].checks]
        assert "CHK_A" in check_ids
        assert "CHK_C" in check_ids
        assert "CHK_B" not in check_ids

    def test_unknown_step_returns_empty(self, dispatch):
        assert dispatch.get_phases("nonexistent") == []

    def test_filtered_phases_preserve_phase_metadata(self, dispatch):
        phases = dispatch.get_phases("test_step", detected_topology="Scale-Up")
        assert phases[1].gate == "some_gate"
        assert phases[1].parallel is False


class TestGetAllCheckIds:
    def test_returns_deduplicated_ids(self, dispatch):
        ids = dispatch.get_all_check_ids("second_step")
        assert "CHK_E" in ids
        assert "CHK_A" in ids
        assert len(ids) == 2

    def test_no_duplicates_across_phases(self, dispatch):
        ids = dispatch.get_all_check_ids("test_step")
        assert len(ids) == len(set(ids))

    def test_unknown_step_returns_empty(self, dispatch):
        assert dispatch.get_all_check_ids("nonexistent") == []


class TestAccessors:
    def test_get_step_name(self, dispatch):
        assert dispatch.get_step_name("test_step") == "Test Step"
        assert dispatch.get_step_name("second_step") == "Second Step"

    def test_get_step_name_unknown(self, dispatch):
        assert dispatch.get_step_name("unknown") == "unknown"

    def test_get_step_number(self, dispatch):
        assert dispatch.get_step_number("test_step") == 1
        assert dispatch.get_step_number("second_step") == 2

    def test_get_step_number_unknown(self, dispatch):
        assert dispatch.get_step_number("unknown") == 0


class TestValidateAgainstRules:
    def test_detects_missing_rules(self, dispatch):
        rules = [
            RuleDefinition(check_id="CHK_A"),
            RuleDefinition(check_id="CHK_B"),
        ]
        warnings = dispatch.validate_against_rules(rules)
        missing = [w for w in warnings if "no matching YAML rule" in w]
        assert len(missing) >= 3

    def test_detects_unreferenced_rules(self, dispatch):
        rules = [
            RuleDefinition(check_id=cid)
            for cid in ["CHK_A", "CHK_B", "CHK_C", "CHK_D", "CHK_E", "CHK_EXTRA"]
        ]
        warnings = dispatch.validate_against_rules(rules)
        unreferenced = [w for w in warnings if "not referenced" in w]
        assert any("CHK_EXTRA" in w for w in unreferenced)

    def test_no_warnings_when_matched(self, dispatch):
        rules = [
            RuleDefinition(check_id=cid)
            for cid in ["CHK_A", "CHK_B", "CHK_C", "CHK_D", "CHK_E"]
        ]
        assert dispatch.validate_against_rules(rules) == []
