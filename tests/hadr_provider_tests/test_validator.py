"""Tests for the validation engine."""

from pathlib import Path

from hadr_provider.models import ArchType, Topology
from hadr_provider.config_matrix import get_expected_config
from hadr_provider.collector import parse_collected_output
from hadr_provider.validator import HadrValidator


FIXTURES = Path(__file__).parent / 'fixtures'


def _build_raw(global_ini='', sudoers='', provider_files='',
               packages='', rhel=''):
    parts = []
    parts.append('=== GLOBAL_INI ===')
    parts.append(global_ini)
    parts.append('=== SUDOERS ===')
    parts.append(sudoers)
    parts.append('=== PROVIDER_FILES ===')
    parts.append(provider_files)
    parts.append('=== PACKAGES ===')
    parts.append(packages)
    parts.append('=== RHEL ===')
    parts.append(rhel)
    return '\n'.join(parts)


class TestAngiAllGood:
    """ANGI config with everything correct -- should produce no findings."""

    def test_no_findings(self):
        global_ini = (FIXTURES / 'global_ini_angi_ok.txt').read_text()
        sudoers = (FIXTURES / 'sudoers_angi.txt').read_text()
        raw = _build_raw(
            global_ini=global_ini,
            sudoers=sudoers,
            provider_files='/usr/share/sap-hana-ha/HanaSR.py',
        )
        actual = parse_collected_output(raw, 'node1', 'S4D')
        expected = get_expected_config(9, Topology.SCALE_UP, ArchType.ANGI, 'S4D')

        findings = HadrValidator().validate(actual, expected)
        assert len(findings) == 0


class TestLegacyAllGood:
    """Legacy config with everything correct."""

    def test_no_findings(self):
        global_ini = (FIXTURES / 'global_ini_legacy_ok.txt').read_text()
        sudoers = (FIXTURES / 'sudoers_legacy.txt').read_text()
        raw = _build_raw(
            global_ini=global_ini,
            sudoers=sudoers,
            provider_files='/usr/share/SAPHanaSR/SAPHanaSR.py',
        )
        actual = parse_collected_output(raw, 'node1', 'S4D')
        expected = get_expected_config(8, Topology.SCALE_UP, ArchType.LEGACY, 'S4D')

        findings = HadrValidator().validate(actual, expected)
        # Only finding should be the migration hint (RHEL 8 doesn't trigger it)
        assert len(findings) == 0

    def test_rhel9_legacy_migration_hint(self):
        """RHEL 9 with legacy should produce an INFO migration hint."""
        global_ini = (FIXTURES / 'global_ini_legacy_ok.txt').read_text()
        sudoers = (FIXTURES / 'sudoers_legacy.txt').read_text()
        raw = _build_raw(
            global_ini=global_ini,
            sudoers=sudoers,
            provider_files='/usr/share/SAPHanaSR/SAPHanaSR.py',
        )
        actual = parse_collected_output(raw, 'node1', 'S4D')
        expected = get_expected_config(9, Topology.SCALE_UP, ArchType.LEGACY, 'S4D')

        findings = HadrValidator().validate(actual, expected)
        info_findings = [f for f in findings if f.severity == 'INFO']
        assert len(info_findings) == 1
        assert 'migration' in info_findings[0].what_is_wrong.lower() or \
               'migrating' in info_findings[0].fix_description.lower()


class TestMissingHooks:
    """No HA/DR hooks configured at all."""

    def test_missing_main_hook_critical(self):
        global_ini = (FIXTURES / 'global_ini_missing.txt').read_text()
        raw = _build_raw(global_ini=global_ini)
        actual = parse_collected_output(raw, 'node1', 'S4D')
        expected = get_expected_config(9, Topology.SCALE_UP, ArchType.ANGI, 'S4D')

        findings = HadrValidator().validate(actual, expected)
        critical = [f for f in findings if f.severity == 'CRITICAL']
        # At least the main hook should be CRITICAL
        main_hook = [f for f in critical
                     if 'ha_dr_provider_hanasr' in f.what_is_wrong]
        assert len(main_hook) >= 1

    def test_missing_chksrv_warning(self):
        global_ini = (FIXTURES / 'global_ini_missing.txt').read_text()
        raw = _build_raw(global_ini=global_ini)
        actual = parse_collected_output(raw, 'node1', 'S4D')
        expected = get_expected_config(9, Topology.SCALE_UP, ArchType.ANGI, 'S4D')

        findings = HadrValidator().validate(actual, expected)
        chksrv = [f for f in findings
                  if 'chksrv' in f.what_is_wrong.lower()
                  and f.category == 'global_ini']
        assert len(chksrv) >= 1
        assert chksrv[0].severity == 'WARNING'


class TestWrongArchitecture:
    """Legacy hooks present when ANGI is expected."""

    def test_wrong_arch_critical(self):
        global_ini = (FIXTURES / 'global_ini_wrong_arch.txt').read_text()
        raw = _build_raw(global_ini=global_ini)
        actual = parse_collected_output(raw, 'node1', 'S4D')
        expected = get_expected_config(9, Topology.SCALE_UP, ArchType.ANGI, 'S4D')

        findings = HadrValidator().validate(actual, expected)
        wrong_arch = [f for f in findings
                      if 'legacy' in f.what_is_wrong.lower()
                      or 'Legacy' in f.what_is_wrong]
        assert len(wrong_arch) >= 1
        assert all(f.severity == 'CRITICAL' for f in wrong_arch)

    def test_wrong_arch_has_fix_command(self):
        global_ini = (FIXTURES / 'global_ini_wrong_arch.txt').read_text()
        raw = _build_raw(global_ini=global_ini)
        actual = parse_collected_output(raw, 'node1', 'S4D')
        expected = get_expected_config(9, Topology.SCALE_UP, ArchType.ANGI, 'S4D')

        findings = HadrValidator().validate(actual, expected)
        wrong_arch = [f for f in findings
                      if 'legacy' in f.what_is_wrong.lower()
                      or 'Legacy' in f.what_is_wrong]
        for f in wrong_arch:
            assert f.fix_command  # Must have fix suggestion
            assert 'HanaSR' in f.fix_command  # Should suggest ANGI config


class TestMissingSudoers:
    """Sudoers entries missing."""

    def test_missing_crm_attribute_critical(self):
        global_ini = (FIXTURES / 'global_ini_angi_ok.txt').read_text()
        raw = _build_raw(
            global_ini=global_ini,
            sudoers='',  # empty sudoers
            provider_files='/usr/share/sap-hana-ha/HanaSR.py',
        )
        actual = parse_collected_output(raw, 'node1', 'S4D')
        expected = get_expected_config(9, Topology.SCALE_UP, ArchType.ANGI, 'S4D')

        findings = HadrValidator().validate(actual, expected)
        sudoers_findings = [f for f in findings if f.category == 'sudoers']
        # requiretty and crm_attribute should both be CRITICAL
        critical_sudoers = [f for f in sudoers_findings if f.severity == 'CRITICAL']
        assert len(critical_sudoers) >= 2


class TestMissingProviderFiles:
    """Provider files not found on disk."""

    def test_missing_provider_file_critical(self):
        global_ini = (FIXTURES / 'global_ini_angi_ok.txt').read_text()
        sudoers = (FIXTURES / 'sudoers_angi.txt').read_text()
        raw = _build_raw(
            global_ini=global_ini,
            sudoers=sudoers,
            provider_files='',  # no files found
        )
        actual = parse_collected_output(raw, 'node1', 'S4D')
        expected = get_expected_config(9, Topology.SCALE_UP, ArchType.ANGI, 'S4D')

        findings = HadrValidator().validate(actual, expected)
        file_findings = [f for f in findings if f.category == 'provider_file']
        assert len(file_findings) == 1
        assert file_findings[0].severity == 'CRITICAL'
        assert 'sap-hana-ha' in file_findings[0].fix_command


class TestMissingTrace:
    """Trace entries missing."""

    def test_missing_trace_warning(self):
        # ANGI hooks present but no trace section
        raw = _build_raw(
            global_ini=(
                "[ha_dr_provider_hanasr]\n"
                "provider = HanaSR\n"
                "path = /usr/share/sap-hana-ha/\n"
                "execution_order = 1\n"
            ),
            sudoers=(FIXTURES / 'sudoers_angi.txt').read_text(),
            provider_files='/usr/share/sap-hana-ha/HanaSR.py',
        )
        actual = parse_collected_output(raw, 'node1', 'S4D')
        expected = get_expected_config(9, Topology.SCALE_UP, ArchType.ANGI, 'S4D')

        findings = HadrValidator().validate(actual, expected)
        trace_findings = [f for f in findings if f.category == 'trace']
        assert len(trace_findings) >= 1
        assert all(f.severity == 'WARNING' for f in trace_findings)


class TestCaseInsensitiveSections:
    """Section names in global.ini are case-insensitive for HANA."""

    def test_angi_mixed_case_sections(self):
        """[ha_dr_provider_HanaSR] should match expected [ha_dr_provider_hanasr]."""
        raw = _build_raw(
            global_ini=(
                "[ha_dr_provider_HanaSR]\n"
                "provider = HanaSR\n"
                "path = /usr/share/sap-hana-ha/\n"
                "execution_order = 1\n"
                "\n"
                "[ha_dr_provider_ChkSrv]\n"
                "provider = ChkSrv\n"
                "path = /usr/share/sap-hana-ha/\n"
                "execution_order = 2\n"
                "action_on_lost = stop\n"
                "\n"
                "[trace]\n"
                "ha_dr_hanasr = info\n"
                "ha_dr_chksrv = info\n"
            ),
            sudoers=(FIXTURES / 'sudoers_angi.txt').read_text(),
            provider_files='/usr/share/sap-hana-ha/HanaSR.py',
        )
        actual = parse_collected_output(raw, 'node1', 'S4D')
        expected = get_expected_config(9, Topology.SCALE_UP, ArchType.ANGI, 'S4D')

        findings = HadrValidator().validate(actual, expected)
        global_ini_findings = [f for f in findings if f.category == 'global_ini']
        assert len(global_ini_findings) == 0, (
            f"Expected no global_ini findings but got: "
            f"{[f.what_is_wrong for f in global_ini_findings]}"
        )

    def test_legacy_mixed_case_sections(self):
        """Mixed case legacy sections should still be recognized."""
        raw = _build_raw(
            global_ini=(
                "[ha_dr_provider_saphanasr]\n"
                "provider = SAPHanaSR\n"
                "path = /usr/share/SAPHanaSR\n"
                "execution_order = 1\n"
                "\n"
                "[trace]\n"
                "ha_dr_saphanasr = info\n"
            ),
            sudoers=(FIXTURES / 'sudoers_legacy.txt').read_text(),
            provider_files='/usr/share/SAPHanaSR/SAPHanaSR.py',
        )
        actual = parse_collected_output(raw, 'node1', 'S4D')
        expected = get_expected_config(8, Topology.SCALE_UP, ArchType.LEGACY, 'S4D')

        findings = HadrValidator().validate(actual, expected)
        # Main hook section should be found (no CRITICAL missing-section findings)
        missing_critical = [
            f for f in findings
            if 'missing from global.ini' in f.what_is_wrong
            and f.severity == 'CRITICAL'
        ]
        assert len(missing_critical) == 0, (
            f"Expected no CRITICAL missing section findings but got: "
            f"{[f.what_is_wrong for f in missing_critical]}"
        )


class TestFindingSuggestions:
    """Verify all findings have non-empty fix descriptions and commands."""

    def test_all_findings_have_suggestions(self):
        # Use empty config to get maximum findings
        raw = _build_raw()
        actual = parse_collected_output(raw, 'node1', 'S4D')
        expected = get_expected_config(9, Topology.SCALE_UP, ArchType.ANGI, 'S4D')

        findings = HadrValidator().validate(actual, expected)
        assert len(findings) > 0
        for f in findings:
            assert f.fix_description, f"Finding '{f.what_is_wrong}' has no fix_description"
            assert f.fix_command, f"Finding '{f.what_is_wrong}' has no fix_command"
            assert f.node == 'node1'
