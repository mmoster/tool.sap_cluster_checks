# SAP Cluster Health Check Tool

## Architecture
- Main CLI entry: `tool/sap_cluster_checks/cli.py` (ClusterHealthCheck class)
- Access discovery: `tool/sap_cluster_checks/access/discover_access.py` (also has standalone CLI)
- Config extraction: `tool/sap_cluster_checks/lib/config_extractor.py`
- PDF reports: `tool/sap_cluster_checks/report_generator.py`
- Rules engine: `tool/sap_cluster_checks/rules/engine.py`

## Output files
- Default output directory: `./results/` (gitignored) — contains reports, configs, and access state
- Report YAMLs and PDFs always have timestamps in filenames
- State files (`cluster_access_config.yaml`, `last_run_status.yaml`) need stable names — the tool reads them back on subsequent runs
- Config reference files (`{cluster}_config.yaml`) are write-only snapshots and include timestamps

## Conventions
- User communicates in German, commit messages in English
- Commit messages: do NOT add Co-Authored-By lines
