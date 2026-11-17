"""Tests for interactive tips/suggestion feature."""

import pytest
from pathlib import Path

from tool.sap_cluster_checks.lib.interactive import _show_suggest_tips, _SUGGEST_INPUTS


class TestSuggestInputs:
    """Verify which inputs are recognized as suggestion requests."""

    def test_t_recognized(self):
        assert "t" in _SUGGEST_INPUTS

    def test_tips_recognized(self):
        assert "tips" in _SUGGEST_INPUTS

    def test_suggest_recognized(self):
        assert "suggest" in _SUGGEST_INPUTS

    def test_suggestion_recognized(self):
        assert "suggestion" in _SUGGEST_INPUTS

    def test_flag_recognized(self):
        assert "--suggest" in _SUGGEST_INPUTS


class TestShowSuggestTips:
    """Test _show_suggest_tips output for various scenarios."""

    def test_no_previous_run(self, tmp_path, capsys):
        """When no last_run_status.yaml exists, show helpful message."""
        config_path = tmp_path / "cluster_access_config.yaml"
        _show_suggest_tips(config_path)
        out = capsys.readouterr().out
        assert "No previous health check run found" in out
        assert "--suggest config" in out

    def test_all_steps_passed(self, tmp_path, capsys):
        """When last run had no failures, explain that no tips are needed."""
        import yaml

        status_file = tmp_path / "last_run_status.yaml"
        status_file.write_text(yaml.dump({
            "steps": {"access": "passed", "config": "passed"},
            "failed_steps": [],
        }))
        config_path = tmp_path / "cluster_access_config.yaml"
        _show_suggest_tips(config_path)
        out = capsys.readouterr().out
        assert "All steps passed" in out
        assert "--suggest all" in out

    def test_failed_step_shows_suggestions(self, tmp_path, capsys):
        """When last run had failures, show suggestions for first failing step."""
        import yaml

        status_file = tmp_path / "last_run_status.yaml"
        status_file.write_text(yaml.dump({
            "steps": {"access": "passed", "config": "failed"},
            "failed_steps": ["config"],
        }))
        config_path = tmp_path / "cluster_access_config.yaml"
        _show_suggest_tips(config_path)
        out = capsys.readouterr().out
        assert "First failing step: config" in out
        # Should include actual suggestions from print_suggestions
        assert "CLUSTER CONFIGURATION" in out

    def test_multiple_failures_lists_others(self, tmp_path, capsys):
        """When multiple steps failed, list the other failing steps."""
        import yaml

        status_file = tmp_path / "last_run_status.yaml"
        status_file.write_text(yaml.dump({
            "steps": {"config": "failed", "pacemaker": "failed"},
            "failed_steps": ["config", "pacemaker"],
        }))
        config_path = tmp_path / "cluster_access_config.yaml"
        _show_suggest_tips(config_path)
        out = capsys.readouterr().out
        assert "First failing step: config" in out
        assert "Other failing steps: pacemaker" in out
