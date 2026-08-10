# Zenduty provider release smoke test

Pre-release gate for `terraform-provider-zenduty`. Runs the **locally built**
provider — never a released binary — against a real Zenduty account: creates
one of every resource, checks for perpetual diffs, re-imports everything, and
exercises the failure paths that changed in the release (typed 404 handling,
fast 400s, pagination, alert-rule conditions).

The local build is installed as its own provider address,
`local.zenduty.com/dev/zendutylocal` v0.0.1, in Terraform's filesystem mirror
(`~/.terraform.d/plugins/`). `versions.tf` pins that address, so plain
`terraform init` resolves the local binary and can never silently fall back
to the released `zenduty/zenduty` from the registry. The local name stays
`zenduty`, so resource types and the `provider "zenduty"` block are identical
to a production config. `scripts/run.sh build` (re)installs the binary from
the provider repo and re-inits.

## Requirements

- A **test account** API token. Do not point this at production: it creates
  teams/services/incidents, invites a user (undeletable via API), and deletes
  a tag out-of-band.
- `terraform` (>= 1.0), `go`, `jq`, `curl`.
- The provider repo checked out next to this repository, i.e. as a sibling of
  `zenduty-terraform-scripts` (or set
  `PROVIDER_REPO=/path/to/terraform-provider-zenduty`).

## Run

```sh
export ZENDUTY_API_KEY=<test account token>
export TF_VAR_tester_email=you+tfsmoke@yourdomain.com   # plus-address you control

# optional: exercise notification rules + the usercontact data source
# (must be an EXISTING user with contact methods, e.g. the account owner)
# export TF_VAR_notification_username=<owner username>

./scripts/run.sh all
```

Or stage by stage: `build`, `validate`, `apply`, `plan-check`, `import`,
`selfheal`, `fast400`, `destroy`.

## What each stage proves

| Stage | Proves |
|---|---|
| `validate` | every config block type-checks against the new provider schemas |
| `apply` | all 24 resources create against the real API, incl. alert-rule `conditions`, SLA responders, `urgency = 0` incidents |
| `plan-check` | empty plan right after apply — no perpetual diffs anywhere |
| `import` | every importer parses its documented ID, every Read rebuilds full state, plan stays empty afterwards |
| `selfheal` | a resource deleted behind Terraform's back is re-planned, not an error (typed-404 path against real API error bodies) |
| `fast400` | a structured 400 fails in seconds — the old provider retried every such error for a full minute |
| data sources | `zenduty_incidents` pagination + server-side `status = "-1"`, `zenduty_post_incident_tasks` full pagination, alert-rule `conditions` read-back (see `outputs`) |

## Known limitations / by design

- `zenduty_assign_account_role` has no importer, so it is absent from the
  import round-trip.
- The alert rule uses a `value`-style action on purpose: typed action
  attributes (`escalation_policy = ...`) are diff-stable only while state
  remembers which spelling the config used, which a fresh import cannot.
  After importing such a rule, move the value into the typed attribute once.
- `terraform destroy` cannot remove the invited user (the API has no delete
  endpoint — the provider warns) or the created incident (incidents resolve,
  they don't delete). Clean both up in the UI if you care.
- The import stage backs up `terraform.tfstate` before touching state
  (`terraform.tfstate.pre-import.<ts>.backup`).

## Pagination depth

To really exercise incident pagination (> 1 page), the account needs > 10
incidents. `data.zenduty_incidents.open` uses `limit = 100`; the
`open_incident_count` output tells you how many came back. On a fresh account
this is 1 (the smoke incident) — still a valid single-page check. If you want
a multi-page check, fire ~15 test alerts at the smoke integration first.
