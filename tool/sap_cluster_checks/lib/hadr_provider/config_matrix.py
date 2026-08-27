"""Configuration matrix for SAP HANA HA/DR provider hooks.

Determines the expected configuration based on:
- RHEL major version (8, 9, 10)
- Cluster topology (Scale-Up, Scale-Out)
- Resource agent package (ANGI: sap-hana-ha, Legacy: resource-agents-sap-hana[-scaleout])

Compatibility:
- RHEL 8:  Legacy only
- RHEL 9:  Both Legacy and ANGI
- RHEL 10: ANGI only
"""

from typing import List, Optional, Tuple

from .models import (
    ArchType,
    Topology,
    HookConfig,
    TraceConfig,
    SudoersEntry,
    ExpectedConfig,
)

# ---------------------------------------------------------------------------
# ANGI configuration (sap-hana-ha package, RHEL 9+)
# ---------------------------------------------------------------------------


def _angi_hooks() -> List[HookConfig]:
    return [
        HookConfig(
            section_name="ha_dr_provider_hanasr",
            provider="HanaSR",
            path="/usr/share/sap-hana-ha/",
            execution_order=1,
        ),
        HookConfig(
            section_name="ha_dr_provider_chksrv",
            provider="ChkSrv",
            path="/usr/share/sap-hana-ha/",
            execution_order=2,
            action_on_lost="stop",
            is_optional=True,
        ),
    ]


def _angi_trace() -> TraceConfig:
    return TraceConfig(
        entries={
            "ha_dr_hanasr": "info",
            "ha_dr_chksrv": "info",  # relevant when ChkSrv is configured
        }
    )


def _angi_sudoers(sid: str) -> List[SudoersEntry]:
    sid_lower = sid.lower()
    return [
        SudoersEntry(
            # Match both styles:
            #   Defaults:rh1adm !requiretty          (direct user style)
            #   Defaults!ALIAS1, ALIAS2 !requiretty  (Cmnd_Alias style)
            line_pattern=r"Defaults[:!].*!requiretty",
            description="Disable requiretty for sidadm",
            example_line=f"Defaults:{sid_lower}adm !requiretty",
        ),
        SudoersEntry(
            # Match multiple sudoers styles:
            #   rh1adm ALL=(ALL) NOPASSWD: /usr/sbin/crm_attribute -n hana_*
            #   rh1adm ALL=(ALL) NOPASSWD: ..., /usr/sbin/crm_attribute -n hana_rh1_*
            #   (ALL) NOPASSWD: /usr/sbin/crm_attribute -n hana_rh1_site_srHook_* (SAP HA role)
            #   Cmnd_Alias ... crm_attribute ... hana_
            line_pattern=rf"(NOPASSWD:.*(/usr/sbin/)?crm_attribute\s+-n\s+hana_|Cmnd_Alias.*crm_attribute.*hana_)",
            description="Allow crm_attribute for hana_* attributes",
            example_line=f"{sid_lower}adm ALL=(ALL) NOPASSWD: /usr/sbin/crm_attribute -n hana_*",
        ),
        SudoersEntry(
            # Match both styles:
            #   rh1adm ALL=(ALL) NOPASSWD: /usr/bin/SAPHanaSR-hookHelper
            #   Cmnd_Alias FENCE = /usr/bin/SAPHanaSR-hookHelper --sid=RH1 --case=*
            line_pattern=rf"({sid_lower}adm\s+ALL=\(ALL\)\s+NOPASSWD:.*SAPHanaSR-hookHelper|Cmnd_Alias\s+\w+\s*=\s*/usr/bin/SAPHanaSR-hookHelper)",
            description="Allow SAPHanaSR-hookHelper (required when action_on_lost=fence)",
            example_line=f"{sid_lower}adm ALL=(ALL) NOPASSWD: /usr/bin/SAPHanaSR-hookHelper",
            is_optional=True,
        ),
    ]


_ANGI_PROVIDER_FILES = [
    "/usr/share/sap-hana-ha/HanaSR.py",
]


# ---------------------------------------------------------------------------
# Legacy configuration (resource-agents-sap-hana[-scaleout], RHEL 8/9)
# ---------------------------------------------------------------------------


def _legacy_hooks() -> List[HookConfig]:
    return [
        HookConfig(
            section_name="ha_dr_provider_SAPHanaSR",
            provider="SAPHanaSR",
            path="/usr/share/SAPHanaSR",
            execution_order=1,
        ),
        HookConfig(
            section_name="ha_dr_provider_suschksrv",
            provider="susChkSrv",
            path="/usr/share/SAPHanaSR",
            execution_order=3,
            action_on_lost="stop",
            is_optional=True,
        ),
    ]


def _legacy_trace() -> TraceConfig:
    return TraceConfig(
        entries={
            "ha_dr_saphanasr": "info",
        }
    )


def _legacy_sudoers(sid: str) -> List[SudoersEntry]:
    sid_lower = sid.lower()
    return [
        SudoersEntry(
            # Match both styles:
            #   Defaults:rh1adm !requiretty          (direct user style)
            #   Defaults!ALIAS1, ALIAS2 !requiretty  (Cmnd_Alias style)
            line_pattern=r"Defaults[:!].*!requiretty",
            description="Disable requiretty for sidadm",
            example_line=f"Defaults:{sid_lower}adm !requiretty",
        ),
        SudoersEntry(
            # Match multiple sudoers styles:
            #   rh1adm ALL=(ALL) NOPASSWD: /usr/sbin/crm_attribute -n hana_rh1_*
            #   rh1adm ALL=(ALL) NOPASSWD: ..., /usr/sbin/crm_attribute -n hana_rh1_*
            #   (ALL) NOPASSWD: /usr/sbin/crm_attribute -n hana_rh1_site_srHook_* (SAP HA role)
            #   Cmnd_Alias ... crm_attribute ... hana_
            line_pattern=rf"(NOPASSWD:.*(/usr/sbin/)?crm_attribute\s+-n\s+hana_|Cmnd_Alias.*crm_attribute.*hana_)",
            description=f"Allow crm_attribute for hana_{sid_lower}_* attributes",
            example_line=f"{sid_lower}adm ALL=(ALL) NOPASSWD: /usr/sbin/crm_attribute -n hana_{sid_lower}_*",
        ),
        SudoersEntry(
            # Match both styles:
            #   rh1adm ALL=(ALL) NOPASSWD: /usr/sbin/SAPHanaSR-hookHelper
            #   Cmnd_Alias FENCE = /usr/sbin/SAPHanaSR-hookHelper --sid=RH1 --case=*
            line_pattern=rf"({sid_lower}adm\s+ALL=\(ALL\)\s+NOPASSWD:.*SAPHanaSR-hookHelper|Cmnd_Alias\s+\w+\s*=\s*/usr/sbin/SAPHanaSR-hookHelper)",
            description="Allow SAPHanaSR-hookHelper",
            example_line=f"{sid_lower}adm ALL=(ALL) NOPASSWD: /usr/sbin/SAPHanaSR-hookHelper *",
            is_optional=True,
        ),
    ]


_LEGACY_PROVIDER_FILES = [
    "/usr/share/SAPHanaSR/SAPHanaSR.py",
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_rhel_arch_compatibility(rhel_major: int, arch_type: ArchType) -> Tuple[bool, str]:
    """Check whether the RHEL version supports the detected resource agent type.

    Returns (is_valid, message).
    """
    if rhel_major <= 8 and arch_type == ArchType.ANGI:
        return False, (
            f"RHEL {rhel_major} does not support the ANGI resource agent (sap-hana-ha). "
            "ANGI is available starting with RHEL 9. "
            "Use the legacy resource-agents-sap-hana package on RHEL 8."
        )
    if rhel_major >= 10 and arch_type == ArchType.LEGACY:
        return False, (
            f"RHEL {rhel_major} does not support legacy resource agents "
            "(resource-agents-sap-hana / resource-agents-sap-hana-scaleout). "
            "Install the sap-hana-ha (ANGI) package on RHEL 10."
        )
    return True, ""


def detect_arch_type(installed_packages: List[str]) -> Optional[ArchType]:
    """Detect the resource agent architecture from installed package names.

    Args:
        installed_packages: List of package name strings (may include version suffixes).

    Returns:
        ArchType.ANGI if sap-hana-ha is found,
        ArchType.LEGACY if resource-agents-sap-hana[-scaleout] is found,
        None if neither is detected.
    """
    for pkg in installed_packages:
        if pkg.startswith("sap-hana-ha"):
            return ArchType.ANGI
    for pkg in installed_packages:
        if pkg.startswith("resource-agents-sap-hana"):
            return ArchType.LEGACY
    return None


def get_expected_config(
    rhel_major: int,
    topology: Topology,
    arch_type: ArchType,
    sid: str,
) -> ExpectedConfig:
    """Return the expected HA/DR provider configuration for a given environment.

    Args:
        rhel_major: RHEL major version (8, 9, 10).
        topology: Scale-Up or Scale-Out.
        arch_type: ANGI or Legacy.
        sid: SAP SID (e.g. "S4D").

    Returns:
        ExpectedConfig with all expected hooks, trace, sudoers, and provider files.

    Raises:
        ValueError: If the RHEL/arch combination is unsupported.
    """
    valid, msg = validate_rhel_arch_compatibility(rhel_major, arch_type)
    if not valid:
        raise ValueError(msg)

    if arch_type == ArchType.ANGI:
        return ExpectedConfig(
            arch_type=arch_type,
            topology=topology,
            rhel_major=rhel_major,
            hooks=_angi_hooks(),
            trace=_angi_trace(),
            sudoers_entries=_angi_sudoers(sid),
            provider_files=list(_ANGI_PROVIDER_FILES),
        )

    # Legacy -- hooks are identical for Scale-Up and Scale-Out
    return ExpectedConfig(
        arch_type=arch_type,
        topology=topology,
        rhel_major=rhel_major,
        hooks=_legacy_hooks(),
        trace=_legacy_trace(),
        sudoers_entries=_legacy_sudoers(sid),
        provider_files=list(_LEGACY_PROVIDER_FILES),
    )
