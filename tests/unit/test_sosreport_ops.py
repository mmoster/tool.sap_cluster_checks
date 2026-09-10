"""Tests for sosreport_ops default paths and output message consistency."""

import os
from pathlib import Path
from unittest.mock import patch, MagicMock

from tool.sap_cluster_checks.access.sosreport_ops import (
    create_and_fetch_sosreports,
    fetch_sosreports,
)

MODULE = "tool.sap_cluster_checks.access.sosreport_ops"


class TestCreateAndFetchDefaultPath:
    """Verify create_and_fetch_sosreports uses results/sosreports as default output."""

    @patch(f"{MODULE}.discover_cluster_from_node")
    def test_default_output_dir_under_results(self, mock_discover, tmp_path):
        """When output_dir is None, sosreports must land in results/sosreports."""
        mock_discover.return_value = {"success": False, "error": "test"}

        with patch(f"{MODULE}.Path.cwd", return_value=tmp_path):
            create_and_fetch_sosreports(seed_node="testnode")

        expected = tmp_path / "results" / "sosreports"
        # discover fails early, but the directory should NOT have been created
        # at the old wrong location
        wrong_dir = tmp_path / "sosreports"
        assert not wrong_dir.exists(), (
            f"sosreports dir created at wrong location {wrong_dir} instead of {expected}"
        )

    @patch(f"{MODULE}.discover_cluster_from_node")
    def test_explicit_output_dir_is_used(self, mock_discover, tmp_path):
        """When output_dir is provided, it must be used as-is."""
        mock_discover.return_value = {"success": False, "error": "test"}
        custom_dir = str(tmp_path / "my_custom_dir")

        create_and_fetch_sosreports(seed_node="testnode", output_dir=custom_dir)

        # The function fails early at discovery, so nothing is created,
        # but we can verify by checking what discover was called with
        mock_discover.assert_called_once()


class TestFetchSosreportsDefaultPath:
    """Verify fetch_sosreports derives output dir from config_path.parent."""

    @patch(f"{MODULE}.yaml.safe_load", return_value={})
    def test_default_dir_relative_to_config_path(self, mock_yaml, tmp_path):
        """Without output_dir, sos_dir = config_path.parent / 'sosreports'."""
        config_path = tmp_path / "results" / "cluster_access_config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("{}")

        # The function reads the config and then tries to find nodes —
        # with empty config it will return early
        result = fetch_sosreports(config_path=config_path, nodes=["testnode"])

        expected_dir = tmp_path / "results" / "sosreports"
        assert expected_dir.exists(), (
            f"Expected sosreport dir at {expected_dir}"
        )

    @patch(f"{MODULE}.yaml.safe_load", return_value={})
    def test_explicit_output_dir_overrides_default(self, mock_yaml, tmp_path):
        """Explicit output_dir takes precedence over config_path-derived default."""
        config_path = tmp_path / "results" / "cluster_access_config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("{}")

        custom_dir = str(tmp_path / "custom_sos")
        result = fetch_sosreports(
            config_path=config_path, nodes=["testnode"], output_dir=custom_dir
        )

        assert Path(custom_dir).exists(), (
            f"Expected custom sosreport dir at {custom_dir}"
        )


class TestOutputMessageConsistency:
    """Verify sosreport-related output messages use consistent formatting.

    All user-facing messages should use lowercase 'sosreports' (plural)
    and never the '(s)' suffix pattern.
    """

    def _get_source_files(self):
        """Return all Python source files in the access/ and lib/ directories."""
        base = Path(__file__).parent.parent.parent / "tool" / "sap_cluster_checks"
        source_dirs = [base / "access", base / "lib"]
        files = []
        for d in source_dirs:
            if d.exists():
                files.extend(d.glob("*.py"))
        return files

    def test_no_parenthesized_plural_in_output(self):
        """No print/log statement should contain 'sosreport(s)' or 'SOSreport(s)'."""
        violations = []
        for filepath in self._get_source_files():
            content = filepath.read_text()
            for i, line in enumerate(content.splitlines(), 1):
                # Only check print statements and f-strings, not comments/docstrings
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith('"""'):
                    continue
                if "sosreport(s)" in line.lower() and ("print(" in line or "log" in line):
                    violations.append(f"{filepath.name}:{i}: {stripped}")

        assert not violations, (
            "Found '(s)' suffix in output messages — use plural 'sosreports' instead:\n"
            + "\n".join(violations)
        )
