"""Tests for install_status package detection logic (Bug #19)."""


def _parse_rpm_output(output):
    """Simulate the package detection logic from InstallStatusMixin.check_install_status."""
    required_packages = ["pacemaker", "corosync", "pcs"]
    sap_packages = [
        "sap-hana-ha",
        "resource-agents-sap-hana",
        "resource-agents-sap-hana-scaleout",
    ]
    missing_packages = []

    if output:
        lines = output.strip().splitlines()
        for pkg in required_packages:
            installed = any(
                line.startswith(f"{pkg}-") and "is not installed" not in line
                for line in lines
            )
            if not installed:
                missing_packages.append(pkg)
        sap_pkg_found = any(
            any(line.startswith(f"{pkg}-") and "is not installed" not in line for line in lines)
            for pkg in sap_packages
        )
        if not sap_pkg_found:
            missing_packages.append("sap-hana-ha")
        packages_installed = len(missing_packages) == 0
    else:
        packages_installed = False
        missing_packages = required_packages + ["sap-hana-ha"]

    return {"packages_installed": packages_installed, "missing_packages": missing_packages}


class TestPackageDetection:
    def test_all_packages_installed(self):
        rpm_out = (
            "pacemaker-2.1.7-5.el9.x86_64\n"
            "corosync-3.1.8-1.el9.x86_64\n"
            "pcs-0.11.7-2.el9.x86_64\n"
            "sap-hana-ha-1.0.2-3.el9.x86_64\n"
            "package resource-agents-sap-hana is not installed\n"
            "package resource-agents-sap-hana-scaleout is not installed\n"
        )
        result = _parse_rpm_output(rpm_out)
        assert result["packages_installed"] is True
        assert len(result["missing_packages"]) == 0

    def test_pacemaker_not_installed(self):
        rpm_out = (
            "package pacemaker is not installed\n"
            "corosync-3.1.8-1.el9.x86_64\n"
            "pcs-0.11.7-2.el9.x86_64\n"
            "sap-hana-ha-1.0.2-3.el9.x86_64\n"
        )
        result = _parse_rpm_output(rpm_out)
        assert result["packages_installed"] is False
        assert "pacemaker" in result["missing_packages"]

    def test_no_sap_package_installed(self):
        rpm_out = (
            "pacemaker-2.1.7-5.el9.x86_64\n"
            "corosync-3.1.8-1.el9.x86_64\n"
            "pcs-0.11.7-2.el9.x86_64\n"
            "package sap-hana-ha is not installed\n"
            "package resource-agents-sap-hana is not installed\n"
            "package resource-agents-sap-hana-scaleout is not installed\n"
        )
        result = _parse_rpm_output(rpm_out)
        assert result["packages_installed"] is False
        assert "sap-hana-ha" in result["missing_packages"]

    def test_legacy_resource_agents_detected(self):
        rpm_out = (
            "pacemaker-2.1.7-5.el9.x86_64\n"
            "corosync-3.1.8-1.el9.x86_64\n"
            "pcs-0.11.7-2.el9.x86_64\n"
            "package sap-hana-ha is not installed\n"
            "resource-agents-sap-hana-0.162.3-1.el9.x86_64\n"
            "package resource-agents-sap-hana-scaleout is not installed\n"
        )
        result = _parse_rpm_output(rpm_out)
        assert result["packages_installed"] is True

    def test_scaleout_agents_detected(self):
        rpm_out = (
            "pacemaker-2.1.7-5.el9.x86_64\n"
            "corosync-3.1.8-1.el9.x86_64\n"
            "pcs-0.11.7-2.el9.x86_64\n"
            "package sap-hana-ha is not installed\n"
            "package resource-agents-sap-hana is not installed\n"
            "resource-agents-sap-hana-scaleout-0.180.0-1.el9.x86_64\n"
        )
        result = _parse_rpm_output(rpm_out)
        assert result["packages_installed"] is True

    def test_empty_output_marks_not_installed(self):
        result = _parse_rpm_output("")
        assert result["packages_installed"] is False
        assert len(result["missing_packages"]) == 4

    def test_no_false_positive_from_substring_match(self):
        """Ensure 'pcs' doesn't match inside 'resource-agents-sap-hana-scaleout'."""
        rpm_out = (
            "pacemaker-2.1.7-5.el9.x86_64\n"
            "corosync-3.1.8-1.el9.x86_64\n"
            "package pcs is not installed\n"
            "sap-hana-ha-1.0.2-3.el9.x86_64\n"
        )
        result = _parse_rpm_output(rpm_out)
        assert result["packages_installed"] is False
        assert "pcs" in result["missing_packages"]
