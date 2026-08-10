# zenduty-terraform-scripts

Tooling for managing Zenduty with Terraform.

## Layout

| Folder | Purpose |
|---|---|
| [`import/`](import/) | Export an entire Zenduty account into Terraform config + state (see below) |
| [`smoke-test/`](smoke-test/) | Smoke tests for the `terraform-provider-zenduty` provider — see its [README](smoke-test/README.md) |
| `eu-migration/`, `zenduty-terraform/` | Historical one-offs, kept for reference only |

## Importing a Zenduty account into Terraform

`import/automate.py` walks the Zenduty API and, for every resource it finds, runs
`terraform import` and generates the matching `.tf` config. The result is a
self-contained Terraform workspace (config + state + `mapping.json`) in an export
folder.

### Prerequisites

- `terraform` on PATH
- Python 3.10+ with dependencies: `pip install -r requirements.txt`
- A Zenduty API key: `export ZENDUTY_API_KEY=<key>`

### Usage

```bash
cd import
export ZENDUTY_API_KEY=<key>

python automate.py              # exports into ./export/
python findreplace.py           # rewrites raw UUIDs into terraform references

cd export
terraform plan                  # acceptance check: expect "No changes"
```

Both scripts accept an alternative export folder, either as the first argument
(`python automate.py prod-backup`) or via `ZENDUTY_EXPORT_DIR`. Use the same
folder for both scripts.

### What gets imported

All 22 importable resource types: users, teams, team members, schedules,
escalation policies, SLAs, incident roles, tags, priorities, services,
integrations, alert rules, outgoing rules, maintenance windows, post-incident
tasks, task templates (+ tasks), team permissions, user notification rules,
account custom roles, global routers, and global routing rules.

Not imported:

- `zenduty_invite`, `zenduty_assign_account_role` — the provider has no importer for these.
- `zenduty_incidents` — importable but deliberately skipped; incidents are runtime data, not configuration.

### Re-running

Runs are incremental. Every imported resource is recorded in
`<export-folder>/mapping.json`; on re-run those are skipped and only resources
created in Zenduty since the last run are imported. Failed imports are not
recorded, so they are retried on the next run. Endpoints unavailable on your
account plan are skipped with a warning instead of aborting.

### Caveats

- `terraform.tfstate` in the export folder contains secrets (integration keys,
  webhook URLs). It is gitignored — don't share it through other channels. The
  generated `.tf` files are scrubbed of these fields and are safe to commit.
- Keep **one** export workspace per Zenduty account. Applying from two state
  files that manage the same resources will cause conflicting changes.
- User/member listings are fetched in a single call (no pagination). On very
  large accounts, verify imported counts against the Zenduty UI.
