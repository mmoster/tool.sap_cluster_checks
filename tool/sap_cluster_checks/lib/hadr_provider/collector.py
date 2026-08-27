"""Collect and parse actual HA/DR provider configuration from command output.

The live_cmd in CHK_HADR_HOOKS.yaml produces a combined output with sections
delimited by "=== SECTION ===" markers.  This module parses that output into
an ActualConfig dataclass.
"""

import re
from typing import Dict, List

from .models import ActualConfig

# ---------------------------------------------------------------------------
# Section markers emitted by the live_cmd
# ---------------------------------------------------------------------------
_MARKER_GLOBAL_INI = "=== GLOBAL_INI ==="
_MARKER_SUDOERS = "=== SUDOERS ==="
_MARKER_PROVIDER_FILES = "=== PROVIDER_FILES ==="
_MARKER_PACKAGES = "=== PACKAGES ==="
_MARKER_RHEL = "=== RHEL ==="

_ALL_MARKERS = [
    _MARKER_GLOBAL_INI,
    _MARKER_SUDOERS,
    _MARKER_PROVIDER_FILES,
    _MARKER_PACKAGES,
    _MARKER_RHEL,
]


def _split_sections(raw: str) -> Dict[str, str]:
    """Split raw command output by === MARKER === lines into a dict."""
    sections: Dict[str, str] = {}
    current_marker = None
    current_lines: List[str] = []

    for line in raw.splitlines():
        stripped = line.strip()
        if stripped in _ALL_MARKERS:
            if current_marker is not None:
                sections[current_marker] = "\n".join(current_lines)
            current_marker = stripped
            current_lines = []
        else:
            current_lines.append(line)

    if current_marker is not None:
        sections[current_marker] = "\n".join(current_lines)

    return sections


def _parse_ini_sections(text: str) -> Dict[str, Dict[str, str]]:
    """Parse INI-style text into {section_name: {key: value}}.

    Only returns sections whose name starts with ``ha_dr_provider_`` or
    equals ``trace``.
    """
    sections: Dict[str, Dict[str, str]] = {}
    current_section = None

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        section_match = re.match(r"^\[(.+)\]$", line)
        if section_match:
            name = section_match.group(1).strip()
            if name.startswith("ha_dr_provider_") or name.lower() == "trace":
                current_section = name
                sections.setdefault(current_section, {})
            else:
                current_section = None
            continue

        if current_section is not None:
            kv_match = re.match(r"^(\S+)\s*=\s*(.*)$", line)
            if kv_match:
                sections[current_section][kv_match.group(1).strip()] = kv_match.group(2).strip()

    return sections


def _extract_trace(ini_sections: Dict[str, Dict[str, str]]) -> Dict[str, str]:
    """Pull trace entries relevant to HA/DR from parsed INI sections."""
    trace_section = {}
    for name, entries in ini_sections.items():
        if name.lower() == "trace":
            for key, val in entries.items():
                if key.lower().startswith("ha_dr_"):
                    trace_section[key] = val
    return trace_section


def _parse_sudoers(text: str) -> List[str]:
    """Return non-empty, non-comment lines from sudoers output."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            lines.append(stripped)
    return lines


def _parse_provider_files(text: str) -> List[str]:
    """Extract file paths from ls output (lines that are file paths)."""
    found = []
    for line in text.splitlines():
        line = line.strip()
        # Skip "No such file" errors from ls
        if not line or "No such file" in line or "cannot access" in line:
            continue
        # ls -la output: permissions ... path  or just the path
        parts = line.split()
        if parts:
            path = parts[-1]
            if path.startswith("/"):
                found.append(path)
    return found


def _parse_packages(text: str) -> List[str]:
    """Extract installed package names from rpm -q output."""
    pkgs = []
    for line in text.splitlines():
        line = line.strip()
        if line and "not installed" not in line:
            pkgs.append(line)
    return pkgs


def _parse_rhel_version(text: str) -> str:
    """Extract RHEL version string from /etc/redhat-release."""
    return text.strip()


def has_required_data(raw_output: str) -> bool:
    """Check whether the raw output contains the section markers from live_cmd.

    Returns False when running from SOSreport or any other mode where the
    full data collection command could not be executed.  In that case the
    check should be SKIPPED rather than producing misleading findings.
    """
    return _MARKER_GLOBAL_INI in raw_output


def parse_collected_output(raw_output: str, node: str, sid: str) -> ActualConfig:
    """Parse the combined command output into an ActualConfig.

    Args:
        raw_output: Full output from the CHK_HADR_HOOKS live_cmd.
        node: Hostname of the node this output was collected from.
        sid: SAP SID (e.g. "S4D").

    Returns:
        ActualConfig populated with parsed data.
    """
    sections = _split_sections(raw_output)

    global_ini_raw = sections.get(_MARKER_GLOBAL_INI, "")
    ini_sections = _parse_ini_sections(global_ini_raw)
    trace_settings = _extract_trace(ini_sections)
    sudoers_raw = sections.get(_MARKER_SUDOERS, "")
    sudoers_lines = _parse_sudoers(sudoers_raw)
    provider_files = _parse_provider_files(sections.get(_MARKER_PROVIDER_FILES, ""))
    packages = _parse_packages(sections.get(_MARKER_PACKAGES, ""))
    rhel_version = _parse_rhel_version(sections.get(_MARKER_RHEL, ""))

    return ActualConfig(
        node=node,
        sid=sid,
        sidadm=f"{sid.lower()}adm",
        global_ini_raw=global_ini_raw,
        global_ini_sections=ini_sections,
        trace_settings=trace_settings,
        sudoers_raw=sudoers_raw,
        sudoers_lines=sudoers_lines,
        provider_files_found=provider_files,
        installed_packages=packages,
        rhel_version=rhel_version,
    )
