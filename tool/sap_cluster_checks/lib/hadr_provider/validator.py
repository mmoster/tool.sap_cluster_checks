"""Validation engine: compare actual configuration against expected.

Produces a list of Finding objects with severity and remediation guidance.
"""

import re
from typing import Dict, List

from .models import (
    ActualConfig,
    ExpectedConfig,
    Finding,
    HookConfig,
)
from .suggestions import (
    generate_fix_for_missing_section,
    generate_fix_for_wrong_value,
    generate_fix_for_missing_sudoers,
    generate_fix_for_missing_trace,
    generate_fix_for_missing_provider_file,
    generate_fix_for_wrong_arch_hooks,
    generate_migration_hint,
)


class HadrValidator:
    """Validate HA/DR provider hook configuration."""

    @staticmethod
    def _ci_get(sections: Dict[str, Dict[str, str]], key: str):
        """Case-insensitive lookup in a section dictionary.

        HANA global.ini section names are case-insensitive, so
        [ha_dr_provider_HanaSR] and [ha_dr_provider_hanasr] are equivalent.
        Returns (actual_key, value) or (None, None).
        """
        key_lower = key.lower()
        for k, v in sections.items():
            if k.lower() == key_lower:
                return k, v
        return None, None

    def validate(self, actual: ActualConfig, expected: ExpectedConfig) -> List[Finding]:
        """Compare actual vs expected config and return all findings.

        An empty list means the configuration is correct.
        """
        findings: List[Finding] = []
        findings.extend(self._validate_global_ini(actual, expected))
        findings.extend(self._validate_trace(actual, expected))
        findings.extend(self._validate_sudoers(actual, expected))
        findings.extend(self._validate_provider_files(actual, expected))
        findings.extend(self._check_wrong_arch_hooks(actual, expected))
        findings.extend(self._check_migration_hint(actual, expected))
        return findings

    # ------------------------------------------------------------------
    # global.ini validation
    # ------------------------------------------------------------------

    def _validate_global_ini(
        self,
        actual: ActualConfig,
        expected: ExpectedConfig,
    ) -> List[Finding]:
        findings: List[Finding] = []

        for hook in expected.hooks:
            _actual_name, section = self._ci_get(
                actual.global_ini_sections, hook.section_name
            )

            if section is None:
                severity = "WARNING" if hook.is_optional else "CRITICAL"
                desc, cmd = generate_fix_for_missing_section(hook, actual.sid, expected.arch_type)
                findings.append(
                    Finding(
                        category="global_ini",
                        severity=severity,
                        what_is_wrong=f"Section [{hook.section_name}] missing from global.ini",
                        expected_value=f"[{hook.section_name}] with provider={hook.provider}",
                        actual_value="missing",
                        fix_description=desc,
                        fix_command=cmd,
                        node=actual.node,
                        section=hook.section_name,
                    )
                )
                continue

            # Section exists -- validate individual keys
            findings.extend(self._check_hook_values(actual, hook, section))

        return findings

    def _check_hook_values(
        self,
        actual: ActualConfig,
        hook: HookConfig,
        section: Dict[str, str],
    ) -> List[Finding]:
        findings: List[Finding] = []

        # Valid alternatives for action_on_lost: stop (default), fence (requires hookHelper)
        _ACTION_ON_LOST_VALID = {"stop", "fence"}

        checks = [
            ("provider", hook.provider),
            ("path", hook.path),
            ("execution_order", str(hook.execution_order)),
        ]
        if hook.action_on_lost is not None:
            checks.append(("action_on_lost", hook.action_on_lost))

        for key, expected_val in checks:
            actual_val = section.get(key)
            if actual_val is None:
                severity = "WARNING" if key == "execution_order" else "CRITICAL"
                desc, cmd = generate_fix_for_wrong_value(
                    hook.section_name, key, expected_val, "missing", actual.sid
                )
                findings.append(
                    Finding(
                        category="global_ini",
                        severity=severity,
                        what_is_wrong=f"Key '{key}' missing in [{hook.section_name}]",
                        expected_value=f"{key} = {expected_val}",
                        actual_value="missing",
                        fix_description=desc,
                        fix_command=cmd,
                        node=actual.node,
                        section=hook.section_name,
                    )
                )
            elif actual_val != expected_val:
                # action_on_lost accepts multiple valid values (stop, fence)
                if key == "action_on_lost" and actual_val in _ACTION_ON_LOST_VALID:
                    continue
                severity = "WARNING" if key == "execution_order" else "CRITICAL"
                desc, cmd = generate_fix_for_wrong_value(
                    hook.section_name, key, expected_val, actual_val, actual.sid
                )
                findings.append(
                    Finding(
                        category="global_ini",
                        severity=severity,
                        what_is_wrong=(
                            f"Wrong '{key}' in [{hook.section_name}]: "
                            f"'{actual_val}' (expected '{expected_val}')"
                        ),
                        expected_value=f"{key} = {expected_val}",
                        actual_value=f"{key} = {actual_val}",
                        fix_description=desc,
                        fix_command=cmd,
                        node=actual.node,
                        section=hook.section_name,
                    )
                )

        return findings

    # ------------------------------------------------------------------
    # trace validation
    # ------------------------------------------------------------------

    def _validate_trace(
        self,
        actual: ActualConfig,
        expected: ExpectedConfig,
    ) -> List[Finding]:
        findings: List[Finding] = []

        for key, expected_val in expected.trace.entries.items():
            # Skip ChkSrv trace if ChkSrv hook is optional and not configured
            chksrv_hook = next((h for h in expected.hooks if h.is_optional), None)
            if chksrv_hook and key.endswith("chksrv"):
                # Only check this trace entry if the optional hook is present
                _name, _sec = self._ci_get(
                    actual.global_ini_sections, chksrv_hook.section_name
                )
                if _sec is None:
                    continue

            actual_val = actual.trace_settings.get(key)
            if actual_val is None:
                desc, cmd = generate_fix_for_missing_trace(key, expected_val, actual.sid)
                findings.append(
                    Finding(
                        category="trace",
                        severity="WARNING",
                        what_is_wrong=f"Trace entry '{key}' missing in [trace] section",
                        expected_value=f"{key} = {expected_val}",
                        actual_value="missing",
                        fix_description=desc,
                        fix_command=cmd,
                        node=actual.node,
                        section="trace",
                    )
                )

        return findings

    # ------------------------------------------------------------------
    # sudoers validation
    # ------------------------------------------------------------------

    def _validate_sudoers(
        self,
        actual: ActualConfig,
        expected: ExpectedConfig,
    ) -> List[Finding]:
        findings: List[Finding] = []

        all_sudoers = "\n".join(actual.sudoers_lines)

        # Check if any hook uses action_on_lost=fence (makes hookHelper required)
        uses_fence = any(
            section.get("action_on_lost") == "fence"
            for section in actual.global_ini_sections.values()
        )

        for entry in expected.sudoers_entries:
            pattern = entry.line_pattern
            if not re.search(pattern, all_sudoers, re.IGNORECASE):
                is_optional = entry.is_optional
                # hookHelper becomes required when action_on_lost=fence
                if "hookhelper" in entry.description.lower() and uses_fence:
                    is_optional = False
                # hookHelper is unnecessary when action_on_lost!=fence
                elif "hookhelper" in entry.description.lower() and not uses_fence:
                    continue
                severity = "WARNING" if is_optional else "CRITICAL"
                desc, cmd = generate_fix_for_missing_sudoers(entry, actual.sid)
                findings.append(
                    Finding(
                        category="sudoers",
                        severity=severity,
                        what_is_wrong=f"Sudoers entry missing: {entry.description}",
                        expected_value=entry.example_line,
                        actual_value="not found in /etc/sudoers.d/",
                        fix_description=desc,
                        fix_command=cmd,
                        node=actual.node,
                    )
                )

        return findings

    # ------------------------------------------------------------------
    # provider files validation
    # ------------------------------------------------------------------

    def _validate_provider_files(
        self,
        actual: ActualConfig,
        expected: ExpectedConfig,
    ) -> List[Finding]:
        findings: List[Finding] = []

        for expected_file in expected.provider_files:
            if expected_file not in actual.provider_files_found:
                desc, cmd = generate_fix_for_missing_provider_file(
                    expected_file, expected.arch_type
                )
                findings.append(
                    Finding(
                        category="provider_file",
                        severity="CRITICAL",
                        what_is_wrong=f"Provider file not found: {expected_file}",
                        expected_value=expected_file,
                        actual_value="missing",
                        fix_description=desc,
                        fix_command=cmd,
                        node=actual.node,
                    )
                )

        return findings

    # ------------------------------------------------------------------
    # wrong architecture detection
    # ------------------------------------------------------------------

    def _check_wrong_arch_hooks(
        self,
        actual: ActualConfig,
        expected: ExpectedConfig,
    ) -> List[Finding]:
        """Detect hooks from the wrong architecture generation."""
        findings: List[Finding] = []

        from .models import ArchType

        if expected.arch_type == ArchType.ANGI:
            # Check for legacy hook sections that shouldn't be there
            legacy_sections = [
                "ha_dr_provider_SAPHanaSR",
                "ha_dr_provider_suschksrv",
            ]
            for legacy_name in legacy_sections:
                actual_name, _sec = self._ci_get(
                    actual.global_ini_sections, legacy_name
                )
                if actual_name is not None:
                    desc, cmd = generate_fix_for_wrong_arch_hooks(
                        expected.arch_type, legacy_name, actual.sid
                    )
                    findings.append(
                        Finding(
                            category="global_ini",
                            severity="CRITICAL",
                            what_is_wrong=(
                                f"Legacy hook [{legacy_name}] found but ANGI "
                                f"(sap-hana-ha) package is installed"
                            ),
                            expected_value="ANGI hooks: [ha_dr_provider_hanasr]",
                            actual_value=f"Legacy hook: [{legacy_name}]",
                            fix_description=desc,
                            fix_command=cmd,
                            node=actual.node,
                            section=legacy_name,
                        )
                    )

        elif expected.arch_type == ArchType.LEGACY:
            # Check for ANGI hook sections that shouldn't be there
            angi_sections = [
                "ha_dr_provider_hanasr",
                "ha_dr_provider_chksrv",
            ]
            for angi_name in angi_sections:
                actual_name, _sec = self._ci_get(
                    actual.global_ini_sections, angi_name
                )
                if actual_name is not None:
                    desc, cmd = generate_fix_for_wrong_arch_hooks(
                        expected.arch_type, angi_name, actual.sid
                    )
                    findings.append(
                        Finding(
                            category="global_ini",
                            severity="CRITICAL",
                            what_is_wrong=(
                                f"ANGI hook [{angi_name}] found but legacy "
                                f"resource-agents package is installed"
                            ),
                            expected_value="Legacy hooks: [ha_dr_provider_SAPHanaSR]",
                            actual_value=f"ANGI hook: [{angi_name}]",
                            fix_description=desc,
                            fix_command=cmd,
                            node=actual.node,
                            section=angi_name,
                        )
                    )

        return findings

    # ------------------------------------------------------------------
    # migration hint (Legacy on RHEL 9)
    # ------------------------------------------------------------------

    def _check_migration_hint(
        self,
        actual: ActualConfig,
        expected: ExpectedConfig,
    ) -> List[Finding]:
        from .models import ArchType

        if expected.arch_type == ArchType.LEGACY and expected.rhel_major == 9:
            desc, cmd = generate_migration_hint()
            return [
                Finding(
                    category="compatibility",
                    severity="INFO",
                    what_is_wrong=(
                        "Legacy resource agents on RHEL 9 -- "
                        "consider migrating to sap-hana-ha (ANGI)"
                    ),
                    expected_value="sap-hana-ha (ANGI) package",
                    actual_value="resource-agents-sap-hana (legacy)",
                    fix_description=desc,
                    fix_command=cmd,
                    node=actual.node,
                )
            ]
        return []
