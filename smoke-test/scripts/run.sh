#!/usr/bin/env bash
# Release smoke test for terraform-provider-zenduty, run against the LOCALLY
# BUILT provider (dev_overrides), not a released one.
#
#   ZENDUTY_API_KEY=<test-account-token> \
#   TF_VAR_tester_email=you+tfsmoke@example.com \
#   ./scripts/run.sh all
#
# Stages (run individually or 'all'):
#   build      compile the provider and generate dev.tfrc
#   validate   terraform validate against the local build
#   apply      create every resource
#   plan-check empty plan right after apply  -> catches perpetual diffs
#   import     re-import everything, then expect an empty plan
#   selfheal   delete a tag via curl, expect plan to recreate (typed 404 path)
#   fast400    a 400 from the API must fail in seconds, not retry for a minute
#   destroy    tear everything down
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
PROVIDER_REPO="${PROVIDER_REPO:-$HERE/../../terraform-provider-zenduty}"
 BASE_URL="${ZENDUTY_BASE_URL:-https://www.zenduty.com}"
cd "$HERE"

: "${ZENDUTY_API_KEY:?set ZENDUTY_API_KEY to the TEST account token}"
export TF_VAR_zenduty_token="$ZENDUTY_API_KEY"

# Deleted teams are only soft-deleted server-side but their (account, name)
# row keeps the DB uniqueness slot, so a prefix can never be reused after a
# destroy. Generate a fresh prefix per run and keep it in a file so every
# stage of the same run agrees; stage_destroy rotates it.
PREFIX_FILE="$HERE/.tfsmoke_prefix"
if [ -z "${TF_VAR_name_prefix:-}" ]; then
  [ -f "$PREFIX_FILE" ] || date +tfsmoke%s > "$PREFIX_FILE"
  TF_VAR_name_prefix="$(cat "$PREFIX_FILE")"
  export TF_VAR_name_prefix
fi

log()  { printf '\n\033[1;34m== %s\033[0m\n' "$*"; }
pass() { printf '\033[1;32mPASS: %s\033[0m\n' "$*"; }
fail() { printf '\033[1;31mFAIL: %s\033[0m\n' "$*"; exit 1; }

# Build the local working tree into the filesystem mirror Terraform searches
# automatically, as local.zenduty.com/dev/zendutylocal v0.0.1 — the source
# address versions.tf pins. Plain `terraform init` then picks it up; no CLI
# config tricks needed.
build() {
  log "building provider from $PROVIDER_REPO"
  local os_arch plugin_dir
  os_arch="$(go env GOOS)_$(go env GOARCH)"
  plugin_dir="$HOME/.terraform.d/plugins/local.zenduty.com/dev/zendutylocal/0.0.1/$os_arch"
  mkdir -p "$plugin_dir"
  (cd "$PROVIDER_REPO" && go build -o "$plugin_dir/terraform-provider-zendutylocal_v0.0.1" .)
  # drop stale lock/plugin caches so a rebuilt binary is never rejected on
  # checksum mismatch
  rm -f "$HERE/.terraform.lock.hcl" "$HERE/badteam/.terraform.lock.hcl"
  rm -rf "$HERE/.terraform" "$HERE/badteam/.terraform"
  terraform init -input=false > /dev/null
  (cd "$HERE/badteam" && terraform init -input=false > /dev/null)
  pass "provider installed at $plugin_dir"
}

fetch_application_id() {
  if [ -n "${TF_VAR_application_id:-}" ] && [ -n "${TF_VAR_outgoing_application_id:-}" ]; then return; fi
  log "fetching application catalog"
  local apps
  apps=$(curl -sf "$BASE_URL/api/account/applications/" \
    -H "Authorization: token $ZENDUTY_API_KEY")
  # handle both a bare list and a paginated {results: []}; prefer the generic
  # API application, fall back to the first entry
  if [ -z "${TF_VAR_application_id:-}" ]; then
    # skip inactive catalog entries: the API rejects them with "This
    # application is not available" (a local catalog ships the API app inactive)
    TF_VAR_application_id=$(printf '%s' "$apps" | jq -r '
      (if type == "object" then .results else . end)
      | map(select((.is_active // 1) != 0 and (.application_type // 0) == 0))
      | (map(select((.name // "") | ascii_downcase == "api")) + .)[0].unique_id')
    [ -n "$TF_VAR_application_id" ] && [ "$TF_VAR_application_id" != "null" ] \
      || fail "could not determine an application id; set TF_VAR_application_id"
    export TF_VAR_application_id
    pass "application_id=$TF_VAR_application_id"
  fi
  # outgoing rules need an integration built from an OUTBOUND application
  # (application_type == 1); prefer a generic webhook, fall back to the first
  if [ -z "${TF_VAR_outgoing_application_id:-}" ]; then
    TF_VAR_outgoing_application_id=$(printf '%s' "$apps" | jq -r '
      (if type == "object" then .results else . end)
      | map(select(.application_type == 1 and (.is_active // 1) != 0))
      | (map(select((.name // "") | ascii_downcase | contains("webhook"))) + .)[0].unique_id')
    [ -n "$TF_VAR_outgoing_application_id" ] && [ "$TF_VAR_outgoing_application_id" != "null" ] \
      || fail "no outbound application (application_type == 1) in the catalog; set TF_VAR_outgoing_application_id"
    export TF_VAR_outgoing_application_id
    pass "outgoing_application_id=$TF_VAR_outgoing_application_id"
  fi
}

stage_validate() {
  log "terraform validate"
  terraform validate
  pass "configuration is valid against the local provider schema"
}

stage_apply() {
  fetch_application_id
  log "terraform apply"
  terraform apply -auto-approve
  pass "all resources created"
}

stage_plan_check() {
  fetch_application_id
  log "plan immediately after apply (perpetual-diff check)"
  if terraform plan -detailed-exitcode > /dev/null; then
    pass "empty plan — no perpetual diffs"
  else
    terraform plan
    fail "plan is not empty right after apply (perpetual diff)"
  fi
}

stage_import() {
  fetch_application_id
  "$HERE/scripts/import_roundtrip.sh"
}

stage_selfheal() {
  fetch_application_id
  "$HERE/scripts/test_404_selfheal.sh"
}

stage_fast400() {
  log "400s must fail fast (no 60s retry loop)"
  local start elapsed
  start=$(date +%s)
  if (cd "$HERE/badteam" && terraform apply -auto-approve > /dev/null 2>&1); then
    (cd "$HERE/badteam" && terraform destroy -auto-approve > /dev/null 2>&1) || true
    fail "over-long team name unexpectedly succeeded — adjust the negative test"
  fi
  elapsed=$(( $(date +%s) - start ))
  echo "apply failed after ${elapsed}s"
  if [ "$elapsed" -lt 30 ]; then
    pass "400 failed fast (${elapsed}s)"
  else
    fail "400 took ${elapsed}s — looks like it is still being retried"
  fi
}

stage_destroy() {
  fetch_application_id
  log "terraform destroy"
  terraform destroy -auto-approve
  # names are burned after a destroy (soft delete keeps the uniqueness row);
  # rotate the prefix so the next run creates fresh names
  rm -f "$PREFIX_FILE"
  echo "NOTE: the invited test user cannot be deleted via API and survives destroy."
  pass "destroyed"
}

case "${1:-all}" in
  build)      build ;;
  validate)   build; stage_validate ;;
  apply)      build; stage_apply ;;
  plan-check) stage_plan_check ;;
  import)     stage_import ;;
  selfheal)   stage_selfheal ;;
  fast400)    stage_fast400 ;;
  destroy)    stage_destroy ;;
  all)
    build
    stage_validate
    stage_apply
    stage_plan_check
    stage_import
    stage_selfheal
    stage_fast400
    stage_destroy
    log "ALL STAGES PASSED"
    ;;
  *) echo "usage: $0 {build|validate|apply|plan-check|import|selfheal|fast400|destroy|all}"; exit 64 ;;
esac
