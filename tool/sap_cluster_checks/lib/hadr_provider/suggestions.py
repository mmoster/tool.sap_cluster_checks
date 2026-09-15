"""Generate actionable remediation suggestions for each finding type.

Every function returns (fix_description, fix_command) where fix_command is
a shell command or configuration text the operator can apply directly.
"""

from typing import Tuple

from .models import HookConfig, SudoersEntry

# ---------------------------------------------------------------------------
# global.ini fixes
# ---------------------------------------------------------------------------


def generate_fix_for_missing_section(
    hook: HookConfig,
    sid: str,
) -> Tuple[str, str]:
    """Fix for a missing [ha_dr_provider_*] section in global.ini."""
    sid_upper = sid.upper()
    section_text = (
        f"[{hook.section_name}]\n"
        f"provider = {hook.provider}\n"
        f"path = {hook.path}\n"
        f"execution_order = {hook.execution_order}"
    )
    if hook.action_on_lost:
        section_text += f"\naction_on_lost = {hook.action_on_lost}"

    kind = "recommended" if hook.is_optional else "required"

    desc = (
        f"Add {kind} [{hook.section_name}] section to global.ini "
        f"on this node, then restart HANA for the hook to take effect."
    )
    cmd = (
        f"# Add to /hana/shared/{sid_upper}/global/hdb/custom/config/global.ini:\n"
        f"{section_text}\n\n"
        f"# Restart HANA to load the hook (as {sid.lower()}adm):\n"
        f"HDB stop && HDB start"
    )
    return desc, cmd


def generate_fix_for_wrong_value(
    section_name: str,
    key: str,
    expected: str,
    actual: str,
    sid: str,
) -> Tuple[str, str]:
    """Fix for an incorrect value in a global.ini section."""
    sid_upper = sid.upper()
    desc = f"Change '{key}' in [{section_name}] from '{actual}' to '{expected}'."
    cmd = (
        f"# In /hana/shared/{sid_upper}/global/hdb/custom/config/global.ini\n"
        f"# section [{section_name}], set:\n"
        f"{key} = {expected}\n\n"
        f"# Restart HANA to apply (as {sid.lower()}adm):\n"
        f"HDB stop && HDB start"
    )
    return desc, cmd


# ---------------------------------------------------------------------------
# sudoers fixes
# ---------------------------------------------------------------------------


def generate_fix_for_missing_sudoers(
    entry: SudoersEntry,
) -> Tuple[str, str]:
    """Fix for a missing sudoers entry."""
    desc = f"Add sudoers entry: {entry.description}"
    cmd = (
        f"# Add to /etc/sudoers.d/20-saphana (as root):\n"
        f"echo '{entry.example_line}' >> /etc/sudoers.d/20-saphana\n"
        f"chmod 440 /etc/sudoers.d/20-saphana\n"
        f"visudo -cf /etc/sudoers.d/20-saphana   # verify syntax"
    )
    return desc, cmd


# ---------------------------------------------------------------------------
# trace fixes
# ---------------------------------------------------------------------------


def generate_fix_for_missing_trace(
    key: str,
    value: str,
    sid: str,
) -> Tuple[str, str]:
    """Fix for a missing [trace] entry."""
    sid_upper = sid.upper()
    desc = f"Add trace entry '{key} = {value}' to [trace] section in global.ini."
    cmd = (
        f"# In /hana/shared/{sid_upper}/global/hdb/custom/config/global.ini\n"
        f"# under [trace] section, add:\n"
        f"{key} = {value}\n\n"
        f"# No HANA restart required -- trace changes take effect dynamically."
    )
    return desc, cmd


# ---------------------------------------------------------------------------
# provider file fixes
# ---------------------------------------------------------------------------


def generate_fix_for_missing_provider_file(
    path: str,
    arch_type: ArchType,
) -> Tuple[str, str]:
    """Fix for a missing provider Python file (package not installed)."""
    if arch_type == ArchType.ANGI:
        pkg = "sap-hana-ha"
    else:
        pkg = "resource-agents-sap-hana"

    desc = (
        f"Provider file {path} not found. "
        f"The {pkg} package may not be installed or is incomplete."
    )
    cmd = (
        f"# Install the resource agent package:\n"
        f"yum install -y {pkg}\n\n"
        f"# Verify the file exists:\n"
        f"ls -la {path}"
    )
    return desc, cmd


# ---------------------------------------------------------------------------
# wrong architecture fixes
# ---------------------------------------------------------------------------


def generate_fix_for_wrong_arch_hooks(
    expected_arch: ArchType,
    found_section: str,
    sid: str,
) -> Tuple[str, str]:
    """Fix for hooks from the wrong resource agent generation."""
    sid_upper = sid.upper()

    if expected_arch == ArchType.ANGI:
        desc = (
            f"Remove legacy hook [{found_section}] and replace with ANGI hooks. "
            f"The sap-hana-ha package uses different provider names and paths."
        )
        cmd = (
            f"# In /hana/shared/{sid_upper}/global/hdb/custom/config/global.ini:\n"
            f"# 1. Remove the legacy section [{found_section}]\n"
            f"# 2. Add ANGI hooks:\n\n"
            f"[ha_dr_provider_hanasr]\n"
            f"provider = HanaSR\n"
            f"path = /usr/share/sap-hana-ha/\n"
            f"execution_order = 1\n\n"
            f"[ha_dr_provider_chksrv]\n"
            f"provider = ChkSrv\n"
            f"path = /usr/share/sap-hana-ha/\n"
            f"execution_order = 2\n"
            f"action_on_lost = stop\n\n"
            f"[trace]\n"
            f"ha_dr_hanasr = info\n"
            f"ha_dr_chksrv = info\n\n"
            f"# Restart HANA (as {sid.lower()}adm):\n"
            f"HDB stop && HDB start"
        )
    else:
        desc = (
            f"Remove ANGI hook [{found_section}] and replace with legacy hooks. "
            f"The resource-agents-sap-hana package uses different provider names."
        )
        cmd = (
            f"# In /hana/shared/{sid_upper}/global/hdb/custom/config/global.ini:\n"
            f"# 1. Remove the ANGI section [{found_section}]\n"
            f"# 2. Add legacy hooks:\n\n"
            f"[ha_dr_provider_SAPHanaSR]\n"
            f"provider = SAPHanaSR\n"
            f"path = /usr/share/SAPHanaSR\n"
            f"execution_order = 1\n\n"
            f"[ha_dr_provider_suschksrv]\n"
            f"provider = susChkSrv\n"
            f"path = /usr/share/SAPHanaSR\n"
            f"execution_order = 3\n"
            f"action_on_lost = stop\n\n"
            f"[trace]\n"
            f"ha_dr_saphanasr = info\n\n"
            f"# Restart HANA (as {sid.lower()}adm):\n"
            f"HDB stop && HDB start"
        )

    return desc, cmd


# ---------------------------------------------------------------------------
# migration hint
# ---------------------------------------------------------------------------


def generate_migration_hint() -> Tuple[str, str]:
    """Informational hint about migrating from Legacy to ANGI on RHEL 9."""
    desc = (
        "RHEL 9 supports the modern sap-hana-ha (ANGI) package which "
        "provides unified Scale-Up and Scale-Out support. Consider "
        "migrating from legacy resource-agents-sap-hana to sap-hana-ha."
    )
    cmd = (
        "# Migration reference:\n"
        "# https://docs.redhat.com/en/documentation/red_hat_enterprise_linux_for_sap_solutions/9/\n"
        "#\n"
        "# Steps (planned maintenance window required):\n"
        "# 1. Put cluster in maintenance mode\n"
        "# 2. Remove legacy resource-agents-sap-hana package\n"
        "# 3. Install sap-hana-ha package\n"
        "# 4. Update global.ini hooks (provider names and paths change)\n"
        "# 5. Update sudoers entries\n"
        "# 6. Restart HANA on both nodes\n"
        "# 7. Recreate pacemaker resources with SAPHanaController\n"
        "# 8. Exit maintenance mode"
    )
    return desc, cmd


# ---------------------------------------------------------------------------
# message formatter
# ---------------------------------------------------------------------------


