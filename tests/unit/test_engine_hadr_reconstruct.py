"""Tests for _reconstruct_hadr_from_extras in the rules engine.

Verifies that individual sos_extras files (new-style SOSreports without the
sap-ha-collect-hadr script) are reassembled into marker-delimited output
compatible with the existing HADR hooks parser.
"""

import textwrap
from pathlib import Path

from tool.sap_cluster_checks.rules.engine import RulesEngine


def _create_extras_files(extras_dir: Path, files: dict):
    """Helper to create mock sos_extras files.

    Args:
        extras_dir: Path to sos_commands/sos_extras/sap_hana_ha/
        files: Dict mapping filename -> content
    """
    extras_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (extras_dir / name).write_text(content)


class TestReconstructHadrFromExtras:
    """Test _reconstruct_hadr_from_extras with mocked SOSreport structure."""

    def _make_engine(self, tmp_path: Path, node: str = "node1") -> RulesEngine:
        """Create engine with access_config pointing to tmp_path SOSreport."""
        sos_path = str(tmp_path / "sosreport-node1-12345")
        # Create minimal sosreport structure so directory resolution works
        (tmp_path / "sosreport-node1-12345" / "etc").mkdir(parents=True)
        return RulesEngine(
            access_config={
                "nodes": {
                    node: {
                        "sosreport_path": sos_path,
                        "preferred_method": "sosreport",
                    }
                }
            }
        )

    def test_full_reconstruction(self, tmp_path):
        """All individual files present -> full marker-delimited output."""
        sos_dir = tmp_path / "sosreport-node1-12345"
        extras_dir = sos_dir / "sos_commands" / "sos_extras" / "sap_hana_ha"

        global_ini = textwrap.dedent("""\
            [ha_dr_provider_SAPHanaSR]
            provider = SAPHanaSR
            path = /usr/share/SAPHanaSR/SAPHanaSR.py
            execution_order = 1
        """)
        sudoers = "s4dadm ALL=(ALL) NOPASSWD: /usr/sbin/crm_attribute -n *\n"
        provider_ls = "/usr/share/SAPHanaSR/SAPHanaSR.py\n"
        packages = "resource-agents-sap-hana-0.162.3-1.el9.noarch\n"
        rhel = "Red Hat Enterprise Linux release 9.4 (Plow)\n"

        _create_extras_files(extras_dir, {
            "cat_.hana.shared.S4D.global.hdb.custom.config.global.ini": global_ini,
            "cat_.etc.sudoers.d.20-saphana_.etc.sudoers.d.*sap*_.etc.sudoers.d.*hana*": sudoers,
            "ls_.usr.share.sap-hana-ha.HanaSR.py_.usr.share.SAPHanaSR.SAPHanaSR.py": provider_ls,
            "rpm_-q_sap-hana-ha_resource-agents-sap-hana_resource-agents-sap-hana-scaleout": packages,
            "cat_.etc.redhat-release": rhel,
        })

        engine = self._make_engine(tmp_path)
        result = engine._reconstruct_hadr_from_extras("node1")

        assert result is not None
        assert "=== GLOBAL_INI ===" in result
        assert "=== SUDOERS ===" in result
        assert "=== PROVIDER_FILES ===" in result
        assert "=== PACKAGES ===" in result
        assert "=== RHEL ===" in result
        assert "[ha_dr_provider_SAPHanaSR]" in result
        assert "s4dadm" in result
        assert "resource-agents-sap-hana" in result
        assert "Red Hat Enterprise Linux" in result

    def test_has_required_data_after_reconstruction(self, tmp_path):
        """Reconstructed output must pass has_required_data()."""
        from tool.sap_cluster_checks.lib.hadr_provider import has_required_data

        sos_dir = tmp_path / "sosreport-node1-12345"
        extras_dir = sos_dir / "sos_commands" / "sos_extras" / "sap_hana_ha"

        _create_extras_files(extras_dir, {
            "cat_.hana.shared.S4D.global.hdb.custom.config.global.ini": "[trace]\nha_dr = info\n",
            "cat_.etc.redhat-release": "Red Hat Enterprise Linux release 9.4\n",
        })

        engine = self._make_engine(tmp_path)
        result = engine._reconstruct_hadr_from_extras("node1")

        assert result is not None
        assert has_required_data(result)

    def test_no_extras_dir_returns_none(self, tmp_path):
        """Missing sos_extras directory -> None."""
        engine = self._make_engine(tmp_path)
        result = engine._reconstruct_hadr_from_extras("node1")

        assert result is None

    def test_empty_extras_dir_returns_none(self, tmp_path):
        """Empty sos_extras directory (no global.ini) -> None."""
        sos_dir = tmp_path / "sosreport-node1-12345"
        extras_dir = sos_dir / "sos_commands" / "sos_extras" / "sap_hana_ha"
        extras_dir.mkdir(parents=True)
        # Only non-global.ini files present
        (extras_dir / "cat_.etc.redhat-release").write_text("RHEL 9.4\n")

        engine = self._make_engine(tmp_path)
        result = engine._reconstruct_hadr_from_extras("node1")

        assert result is None  # No global.ini data -> not useful

    def test_unknown_node_returns_none(self, tmp_path):
        """Unknown node (not in access_config) -> None."""
        engine = self._make_engine(tmp_path)
        result = engine._reconstruct_hadr_from_extras("unknown_node")

        assert result is None

    def test_multiple_global_ini_files_combined(self, tmp_path):
        """Multiple global.ini matches are concatenated."""
        sos_dir = tmp_path / "sosreport-node1-12345"
        extras_dir = sos_dir / "sos_commands" / "sos_extras" / "sap_hana_ha"

        _create_extras_files(extras_dir, {
            "cat_.hana.shared.S4D.global.hdb.custom.config.global.ini": "[trace]\nfirst = yes\n",
            "cat_.usr.sap.S4D.SYS.global.hdb.custom.config.global.ini": "[trace]\nsecond = yes\n",
        })

        engine = self._make_engine(tmp_path)
        result = engine._reconstruct_hadr_from_extras("node1")

        assert result is not None
        assert "first = yes" in result
        assert "second = yes" in result


class TestReconstructWithSosreportDirectory:
    """Test reconstruction when access_config uses sosreport_directory (not per-node path)."""

    def test_sosreport_directory_fallback(self, tmp_path):
        """When node has no sosreport_path, fall back to sosreport_directory."""
        sos_dir = tmp_path / "sosreport-node1-12345"
        extras_dir = sos_dir / "sos_commands" / "sos_extras" / "sap_hana_ha"
        (sos_dir / "etc").mkdir(parents=True)

        _create_extras_files(extras_dir, {
            "cat_.hana.shared.S4D.global.hdb.custom.config.global.ini": "[trace]\ntest = ok\n",
        })

        engine = RulesEngine(
            access_config={
                "sosreport_directory": str(tmp_path),
                "nodes": {
                    "node1": {
                        "preferred_method": "sosreport",
                    }
                },
            }
        )

        result = engine._reconstruct_hadr_from_extras("node1")
        assert result is not None
        assert "=== GLOBAL_INI ===" in result
        assert "test = ok" in result
