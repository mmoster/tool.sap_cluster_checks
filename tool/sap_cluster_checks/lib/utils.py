"""
Utility functions for SAP Pacemaker Cluster Health Check.

This module contains helper functions for:
- Resource scanning
- SOSreport extraction
- Software update checking
"""

import os
import sys
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Get script directory for git operations
SCRIPT_DIR = Path(__file__).parent.parent.resolve()


def scan_for_resources(base_dir: str = ".") -> dict:
    """
    Scan current directory and subdirectories for sosreports, inventory files, and former results.
    Returns dict with found resources.
    """
    base_path = Path(base_dir).resolve()
    results = {
        "sosreports_compressed": [],  # .tar.xz, .tar.gz files
        "sosreports_extracted": [],  # sosreport-* directories
        "inventory_files": [],  # ansible inventory files (hosts, inventory.*)
        "hosts_files": [],  # hosts.txt, *_hosts.txt
        "former_results": [],  # health_check_report_*.yaml
        "config_files": [],  # cluster_access_config.yaml
        "pdf_reports": [],  # *.pdf reports
    }

    # Scan for sosreports (compressed)
    archive_patterns = [
        "**/sosreport-*.tar.xz",
        "**/sosreport-*.tar.gz",
        "**/sosreport-*.tar.bz2",
        "**/sosreport-*.tgz",
        "**/sosreport-*.txz",
    ]
    for pattern in archive_patterns:
        for f in base_path.glob(pattern):
            results["sosreports_compressed"].append(str(f))

    # Scan for sosreports (extracted directories)
    for d in base_path.glob("**/sosreport-*"):
        if d.is_dir() and (d / "etc").exists():
            results["sosreports_extracted"].append(str(d))

    # Scan for inventory files
    inventory_patterns = [
        "**/inventory",
        "**/inventory.ini",
        "**/inventory.yaml",
        "**/inventory.yml",
        "**/ansible/hosts",
        "**/hosts.ini",
    ]
    for pattern in inventory_patterns:
        for f in base_path.glob(pattern):
            if f.is_file():
                results["inventory_files"].append(str(f))

    # Scan for hosts files
    for f in base_path.glob("**/hosts.txt"):
        results["hosts_files"].append(str(f))
    for f in base_path.glob("**/*_hosts.txt"):
        results["hosts_files"].append(str(f))

    # Scan for former results
    for f in base_path.glob("**/health_check_report_*.yaml"):
        results["former_results"].append(str(f))
    for f in base_path.glob("**/health_check_report_*.pdf"):
        results["pdf_reports"].append(str(f))
    for f in base_path.glob("**/*_health_check_report_*.pdf"):
        results["pdf_reports"].append(str(f))

    # Scan for config files
    for f in base_path.glob("**/cluster_access_config.yaml"):
        results["config_files"].append(str(f))
    for f in base_path.glob("**/last_run_status.yaml"):
        results["former_results"].append(str(f))

    # Remove duplicates and sort
    for key in results:
        results[key] = sorted(list(set(results[key])))

    return results


def extract_sosreports_parallel(archives: list, max_workers: int = 4) -> list:
    """
    Extract multiple sosreport archives in parallel.
    Returns list of extracted directories.
    """
    extracted = []

    def extract_one(archive_path: str) -> tuple:
        """Extract single archive. Returns (success, path_or_error)."""
        archive_name = os.path.basename(archive_path)
        base_dir = os.path.dirname(archive_path)

        # Determine expected directory name
        dir_name = archive_name
        for ext in [".tar.xz", ".tar.gz", ".tar.bz2", ".tgz", ".txz"]:
            if dir_name.endswith(ext):
                dir_name = dir_name[: -len(ext)]
                break

        expected_dir = os.path.join(base_dir, dir_name)

        # Check if already extracted
        if os.path.isdir(expected_dir):
            return (True, expected_dir, "already extracted")

        # Determine extraction command
        if archive_path.endswith((".tar.xz", ".txz")):
            cmd = ["tar", "xJf", archive_path, "-C", base_dir]
        elif archive_path.endswith((".tar.gz", ".tgz")):
            cmd = ["tar", "xzf", archive_path, "-C", base_dir]
        elif archive_path.endswith(".tar.bz2"):
            cmd = ["tar", "xjf", archive_path, "-C", base_dir]
        else:
            return (False, archive_path, "unknown format")

        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=300)
            return (True, expected_dir, "extracted")
        except subprocess.TimeoutExpired:
            return (False, archive_path, "timeout")
        except subprocess.CalledProcessError as e:
            return (
                False,
                archive_path,
                e.stderr.decode()[:100] if e.stderr else "extraction failed",
            )
        except Exception as e:
            return (False, archive_path, str(e))

    if not archives:
        return extracted

    print(f"\n  Extracting {len(archives)} sosreport archive(s) in parallel...")

    with ThreadPoolExecutor(max_workers=min(len(archives), max_workers)) as executor:
        futures = {executor.submit(extract_one, arch): arch for arch in archives}
        for future in as_completed(futures):
            archive = futures[future]
            archive_name = os.path.basename(archive)
            try:
                success, path, status = future.result()
                if success:
                    print(f"    [OK] {archive_name} ({status})")
                    extracted.append(path)
                else:
                    print(f"    [FAIL] {archive_name}: {status}")
            except Exception as e:
                print(f"    [ERROR] {archive_name}: {e}")

    return extracted


def check_for_updates(script_dir: Path = None):
    """Check if a newer version is available via git and offer to update."""
    if script_dir is None:
        script_dir = SCRIPT_DIR

    try:
        # Check if we're in a git repository
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=script_dir,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0:
            return  # Not a git repo

        # Fetch latest from remote (quietly)
        subprocess.run(
            ["git", "fetch", "--quiet"],
            cwd=script_dir,
            capture_output=True,
            timeout=30,
            check=False,
        )

        # Get local and remote HEAD
        local_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=script_dir,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout.strip()

        remote_head = subprocess.run(
            ["git", "rev-parse", "@{u}"],
            cwd=script_dir,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout.strip()

        if local_head != remote_head:
            # Check how many commits behind
            behind_count = subprocess.run(
                ["git", "rev-list", "--count", f"{local_head}..{remote_head}"],
                cwd=script_dir,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout.strip()

            # Only show update notice if actually behind (not if ahead with local commits)
            try:
                behind_int = int(behind_count)
            except ValueError:
                behind_int = 0

            if behind_int > 0:
                print(f"\n[INFO] A newer version is available ({behind_count} commit(s) behind).")
                print("  To update, run: git pull")
    except Exception:
        pass  # Silently ignore any errors in update check
