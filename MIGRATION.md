# Migrating a Zenduty account to another instance

Runbook for copying one Zenduty account onto a different Zenduty instance
(for example: production → an EU instance, or → a dev backend).

The whole migration is driven by one script, `import/migrate.sh`, run twice:
once to **export** the source account, once to **apply** it to the target. It
prompts for the API keys, verifies each phase, writes log files, and refuses
to continue when something is off — follow the two steps in order and read
what it prints.

---

## Before you start

You need:

| | |
|---|---|
| Source account API key | from the account being migrated |
| Target instance URL | e.g. `https://imr.eu.xurrent.com` |
| Target account API key | an API key on the (empty) target account |
| `terraform` ≥ 1.5 | `terraform -version` |
| Python 3.10+ | `python3 --version` |

One-time setup, from the repo root:

```bash
pip install -r requirements.txt
```

⚠️ **One rule above all: never run plain `terraform apply` against the
target.** Always go through the script. A plain apply duplicates every team's
default escalation policy and fails on every incident role.

Every phase writes a timestamped log file into `import/logs/<customer-name>/`
(one file per phase per run, nothing is overwritten). **When escalating a
problem, attach the newest log of the failing phase.** Logs can contain
customer data (names, emails, team structure) — treat them like customer
data, don't post them publicly.

---

## Step 1 — Export the source account

```bash
cd import
./migrate.sh export <customer-name>
```

It asks for the **source** API key and the source URL (press Enter to accept
`https://www.zenduty.com`), then, on its own:

1. exports the whole account into a Terraform workspace at
   `import/<customer-name>/` — takes a few minutes on a large account and
   streams progress. `WARN` lines are normal: they list objects that can
   never be recreated through the API (for example escalation policies with
   no rules) and are deliberately skipped;
2. rewrites hardcoded source-account IDs into portable references;
3. verifies the export against the source with a `terraform plan` and
   **stops with an error unless the plan is clean** — if it stops here,
   escalate with the step 3 log, don't continue.

The export never modifies the source account, and re-running it is safe
(already-exported resources are skipped).

**Expected ending:** `Export complete and verified against the source.`

## Step 2 — Apply to the target

```bash
./migrate.sh apply <customer-name>
```

It asks for the **target** URL and API key, then shows what it is about to do
and makes you **retype the target hostname** — this is the point of no
return, so check that the hostname is the target, never the live source
account. After confirmation it:

1. moves the source-bound state file aside (kept in
   `<customer-name>/source-state-backup/` — it contains secrets, never share
   it) and remembers the target, so a later re-run against a *different*
   instance is refused;
2. creates all teams first, then adopts the "Default Escalation Policy" and
   "Incident Commander" role the target auto-creates per team (so they are
   updated in place instead of duplicated), then applies everything else.
   Lines about rewriting data the current API would reject (rules whose
   targets were deleted, users who left a team, HTML in names, …) are normal;
3. if the apply fails partway with 500 errors, automatically retries what's
   left one-resource-at-a-time — that retry is normal, let it finish;
4. verifies the result with a `terraform plan` against the target and stops
   with an error unless it is clean.

**Re-running `./migrate.sh apply <customer-name>` after a failure is safe** —
everything already created is skipped.

**Expected ending:** `Migration applied and verified.`

## Step 3 — Spot-check the target UI

Compare against the source: team count, user count, a few schedules and
integrations. Done.

---

## If something goes wrong

Attach the newest log of the failing phase from
`import/logs/<customer-name>/` when escalating.

| Symptom | Meaning | What to do |
|---|---|---|
| Export stops at the plan verification (plan shows pending changes) | The export didn't faithfully capture the source | Stop and escalate with the step 3 log |
| Plan/apply error on a `data "zenduty_applications"` lookup (application not found) | The target instance's integration-app catalog doesn't have that application | Have the application added/seeded on the target, then re-run step 2 |
| `this folder was already applied to <url>` | The export folder is bound to a different target | Run a fresh export into a new customer name for the new target |
| `incident role with the name already exists` | Plain `terraform apply` was run instead of the script | Stop and escalate to engineering — the target has duplicates to clean up |
| 500 errors during the apply | Backend lock contention from parallel user invites | Handled automatically (serial retry); if it still fails, re-run step 2 |
| `WARN ... skipping` during export | Source data that can never be recreated via the API | Expected; listed so you can tell the customer what didn't carry over |
| An endpoint warns it's unavailable during export | Feature not on the source account's plan | Expected, nothing to do |

## What does NOT migrate (set expectations with the customer)

- **Incidents** — runtime data, not configuration; deliberately excluded.
- **Personal notification rules** — tied to contact methods that only exist
  after each user completes signup on the target; users get the default set
  when their invite is created.
- **Bot/integration users** (no email) — cannot be created via the API.
- **Webhook secrets** — the API never returns a webhook's signing secret or
  API key, so `zenduty_webhook_configuration` blocks arrive without them; the
  export prints a `WARN` for each affected webhook. Get the secrets from the
  customer and add them to the block before step 2 (or re-enter them in the
  target UI afterwards).
- **Workflows** — the workflow API accepts browser-session authentication
  only, so the export skips them with a `WARN` until the backend grants API
  key access. Recreate them in the target UI for now.
- **Ended maintenance windows and expired forwarding rules** — skipped on
  purpose; they could never be active again.
- **The account owner** is referenced, not created — the target account's own
  owner takes that place.
- Every migrated user is invited via a team named `GLOBAL` (created
  automatically; the invite API requires one). Users appear fully once they
  accept the invite on the target.
- Email unsubscribe preferences **do** migrate: step 1 writes them to
  `email_unsubscribes.json` and step 2 replays them per user (matched by
  email) after the apply.

## Notes

- The generated `.tf` files are scrubbed of secrets and safe to share; the
  `terraform.tfstate` files (including the `source-state-backup/` copy) are
  **not** — they contain integration keys and webhook URLs.
- Keep one export folder per customer account and per target, and don't run
  two migrations from the same folder.
- Technical background on the underlying scripts (`automate.py`,
  `findreplace.py`, `apply.py`, fast mode, resource coverage, provider
  requirements) is in the main [README](README.md) — the manual step-by-step
  commands live there too, for engineers who need to run a phase by hand.
