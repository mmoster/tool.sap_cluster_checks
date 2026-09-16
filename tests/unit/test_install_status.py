"""Tests for install_status package detection and node status parsing."""

import re

from tool.sap_cluster_checks.lib.install_checks import make_status_dict


def _parse_node_status(output):
    """Simulate the node status parsing from InstallStatusMixin (SSH path)."""
    status = make_status_dict("test", "ssh")
    for category, key in [
        ("Online", "cluster_nodes"),
        ("Standby", "standby_nodes"),
        ("Offline", "offline_nodes"),
    ]:
        match = re.search(rf"{category}:\s*\[\s*(.*?)\s*\]", output)
        if match:
            status[key] = [n.strip() for n in match.group(1).split() if n.strip()]
        else:
            match = re.search(rf"{category}:[ \t]*(.+?)(?:\n|$)", output)
            if match:
                status[key] = [
                    n.strip()
                    for n in match.group(1).split()
                    if n.strip() and ":" not in n
                ]
    status["cluster_online"] = (
        bool(status["cluster_nodes"])
        and not status["standby_nodes"]
        and not status["offline_nodes"]
    )
    return status


class TestNodeStatusParsing:
    """Test node status parsing handles standby/offline nodes correctly."""

    def test_all_nodes_online_bracket_format(self):
        output = "Pacemaker Nodes:\n Online: [ node1 node2 node3 ]\n"
        result = _parse_node_status(output)
        assert result["cluster_online"] is True
        assert result["cluster_nodes"] == ["node1", "node2", "node3"]
        assert result["standby_nodes"] == []

    def test_standby_node_bracket_format(self):
        output = (
            "Pacemaker Nodes:\n"
            " Online: [ node1 node2 ]\n"
            " Standby: [ node3 ]\n"
        )
        result = _parse_node_status(output)
        assert result["cluster_online"] is False
        assert result["cluster_nodes"] == ["node1", "node2"]
        assert result["standby_nodes"] == ["node3"]

    def test_standby_node_space_format(self):
        output = (
            "Pacemaker Nodes:\n"
            " Online: node1 node2 node3\n"
            " Standby: node4 node5\n"
        )
        result = _parse_node_status(output)
        assert result["cluster_online"] is False
        assert result["cluster_nodes"] == ["node1", "node2", "node3"]
        assert result["standby_nodes"] == ["node4", "node5"]

    def test_offline_node(self):
        output = (
            "Pacemaker Nodes:\n"
            " Online: [ node1 ]\n"
            " Offline: [ node2 ]\n"
        )
        result = _parse_node_status(output)
        assert result["cluster_online"] is False
        assert result["offline_nodes"] == ["node2"]

    def test_no_nodes_online(self):
        output = "Pacemaker Nodes:\n Offline: [ node1 node2 ]\n"
        result = _parse_node_status(output)
        assert result["cluster_online"] is False
        assert result["cluster_nodes"] == []

    def test_empty_standby_not_parsed_as_nodes(self):
        """Regression: empty Standby/Offline must not cross newlines into
        'Standby with resource(s)...' or 'Pacemaker Remote Nodes:' headers."""
        output = (
            "Pacemaker Nodes:\n"
            " Online: dc1hana1 dc1hana2 dc2hana1 dc2hana2 dc3mm\n"
            " Standby:\n"
            " Standby with resource(s) running on them:\n"
            " Maintenance:\n"
            " Offline:\n"
            "Pacemaker Remote Nodes:\n"
            " Online:\n"
            " Standby:\n"
            " Standby with resource(s) running on them:\n"
            " Maintenance:\n"
            " Offline:\n"
        )
        result = _parse_node_status(output)
        assert result["cluster_online"] is True
        assert result["cluster_nodes"] == ["dc1hana1", "dc1hana2", "dc2hana1", "dc2hana2", "dc3mm"]
        assert result["standby_nodes"] == []
        assert result["offline_nodes"] == []


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
