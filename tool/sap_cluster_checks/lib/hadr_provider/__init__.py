"""SAP HANA HA/DR Provider Hook Validator.

Architecture-aware validation of HA/DR provider hooks for SAP HANA
Pacemaker clusters on RHEL 8/9/10.
"""

from .models import (
    ArchType as ArchType,
    Topology as Topology,
    HookConfig as HookConfig,
    TraceConfig as TraceConfig,
    SudoersEntry as SudoersEntry,
    ExpectedConfig as ExpectedConfig,
    ActualConfig as ActualConfig,
    Finding as Finding,
)
from .config_matrix import (
    get_expected_config as get_expected_config,
    detect_arch_type as detect_arch_type,
    validate_rhel_arch_compatibility as validate_rhel_arch_compatibility,
)
from .collector import parse_collected_output as parse_collected_output, has_required_data as has_required_data
from .validator import HadrValidator as HadrValidator
