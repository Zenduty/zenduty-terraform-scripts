# zenduty-terraform-scripts

Tooling for managing Zenduty with Terraform.

## Layout

| Folder | Purpose |
|---|---|
| [`MIGRATION.md`](MIGRATION.md) | **Support runbook** for migrating an account to another instance, driven by [`import/migrate.sh`](import/migrate.sh) |
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

### Applying an export to another instance (migration)

> **Just need to run a migration?** Use the guided wrapper
> `import/migrate.sh` as described in [MIGRATION.md](MIGRATION.md) — it
> prompts for credentials, chains export → findreplace → verify → apply →
> verify, logs every phase, and adds safety checks (mechanical "No changes"
> verification, typed-hostname confirmation, refusal to reuse a folder
> against a different target). This section is the technical background for
> engineers running the pieces by hand.

An export is portable across Zenduty instances: nothing instance-specific is
hardcoded in the generated config. `findreplace.py` rewrites every raw UUID
into a reference to the terraform resource that owns it, so IDs are minted
fresh on whatever instance the apply targets. The one class of IDs that
belongs to the instance rather than the account — the integration application
catalog — is emitted as `data "zenduty_applications"` name lookups that
resolve at plan time against the target. This means the target's catalog must
contain the same applications by name; lookups for applications the target
doesn't have fail at plan time until they are seeded there.

Don't apply an export to a fresh instance with plain `terraform apply`:
creating a team makes the backend auto-create a "Default Escalation Policy",
an "Incident Commander" incident role, and a membership for the API user, and
the export contains the source account's copies of the first two. A plain
apply therefore fails on every role ("incident role with the name already
exists") and silently duplicates every default escalation policy. (The
auto-added member is harmless — the API tolerates re-adding an existing
member.)

`import/apply.py` wraps the apply to handle this: it applies the
`zenduty_teams` resources first, binds each auto-created default to its
exported counterpart with terraform `import` blocks (so the full apply
updates it in place instead of recreating it), deletes auto-created defaults
the source team had renamed or deleted, and then runs the full apply.

```bash
# 1. point the provider block in <folder>/main.tf at the TARGET instance
#    (base_url + token), and remove any state bound to the source
#    (terraform.tfstate) — the migration starts from an empty state
# 2. tell apply.py where its own API calls (adopting/deleting the
#    auto-created defaults) should go — defaults to http://localhost:8000
export ZENDUTY_TARGET_BASE_URL=<target url>
python apply.py <folder>
```

Terraform itself talks to whatever the provider block in `<folder>/main.tf`
says. apply.py's direct API calls use the token from that provider block
(`ZENDUTY_TARGET_API_KEY` overrides it) and the address from
`ZENDUTY_TARGET_BASE_URL` (default `http://localhost:8000`) — keep the two
pointed at the same instance. Re-running after a partial failure is safe: teams
already in state are skipped, only unmanaged defaults are adopted, and
deletes only target objects that still exist unmatched.

A faithful export can also contain states the current create APIs no longer
accept — stored data that predates today's validation. Objects that could
never be created are skipped at export time with a warning (escalation
policies with no targeted rules, global routing rules with no actions, alert
rules whose every action lost its target object); everything else apply.py
rewrites in place before applying, printing each change: target-less rules
are stripped from escalation policies, broken actions from alert rules,
non-member users from schedule layers and overrides (the API's own advice),
HTML/URLs from user names, inconsistent collation fields are normalised, and
services pointing at an escalation policy that no longer exists fall back to
the team's default. If the apply still fails, it is retried once serially
(`-parallelism=1`): parallel user invites can exhaust the backend's row
locks, which surface as 500s with MySQL 1205 lock waits.

### Fast mode (the default)

Fast mode is used automatically whenever `terraform` ≥ 1.5 is on PATH; pass
`--slow` (or `ZENDUTY_FAST_IMPORT=0`) to force the old per-resource path, or
`--fast` (or `ZENDUTY_FAST_IMPORT=1`) to fail instead of falling back on older
terraform versions. It enumerates every resource via the API first (threaded, auto-sized to
`min(32, cores + 4)`; `ZENDUTY_ENUM_THREADS` overrides) and then imports them
all with a single
`terraform plan -generate-config-out` (import blocks), instead of two
terraform subprocesses per resource. API calls are paced by a shared adaptive
rate limiter (AIMD: starts at `ZENDUTY_API_RPS`, default 8/s; a 429 halves the
shared rate and backs all threads off together, sustained success raises it
again), so the thread count just provides concurrency while throughput
converges on whatever the API allows. Provider reads during the terraform
phase run in parallel too (`ZENDUTY_TF_PARALLELISM`, default 10). Together
this turns an hour-long export into a few minutes. Progress streams live: `queued <address>`
lines during enumeration, then terraform's own per-resource output during the
import plan. It refuses to apply anything but pure imports, so the source
account is never modified. Use it on a fresh export folder; if a fast run
aborts partway, start over with a fresh folder rather than re-running into the
same one.

### What gets imported

All 21 importable resource types: users, teams, team members, schedules,
escalation policies, SLAs, incident roles, tags, priorities, services,
integrations, alert rules, outgoing rules, maintenance windows, post-incident
tasks, task templates (+ tasks), team permissions,
account custom roles, global routers, and global routing rules.

Not imported:

- `zenduty_invite`, `zenduty_assign_account_role` — the provider has no importer for these.
- `zenduty_incidents` — importable but deliberately skipped; incidents are runtime data, not configuration.
- User notification rules — deliberately skipped; they are personal per-user
  preferences tied to contact-method UUIDs that only exist once the user
  completes signup on the target, and the invite already creates a default
  set there. (The `data "zenduty_usercontact"` lookups existed only to serve
  them, so they are no longer generated either.)
- Bot users (no email) — the provider cannot manage them.
- The account owner (role 1) is not imported as a resource; a `data "zenduty_user"`
  block is generated instead so other resources can reference the owner.

The Zenduty invite API always requires a destination team, so every imported user
gets a deterministic `team`: a team named `GLOBAL` (override with
`ZENDUTY_INVITE_TEAM`), created automatically on the first run if missing.

Provider requirement: the generated config needs registry release **2.0.0 or
newer** — it uses the `zenduty_applications` data source and relies on
optional schedule layer/override names and whitespace-tolerant user last
names, none of which exist in 1.0.6. `import/main.tf` points at the registry
provider (`zenduty/zenduty`), and `terraform init` picks up the latest
release. To test an unreleased provider build instead, install it with
`smoke-test/scripts/run.sh` (or `go build` into
`~/.terraform.d/plugins/local.zenduty.com/dev/zendutylocal/0.0.1/<os_arch>/`)
and change the `source` in `main.tf` to `local.zenduty.com/dev/zendutylocal`.

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
