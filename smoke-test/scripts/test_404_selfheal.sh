#!/usr/bin/env bash
# 404 self-heal: delete a Terraform-managed tag behind Terraform's back, then
# expect `terraform plan` to schedule a recreate instead of erroring. This
# exercises the new typed-error path (client.StatusCode == 404 -> clear state)
# against the real API's error bodies.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
BASE_URL="${ZENDUTY_BASE_URL:-https://www.zenduty.com}"
cd "$HERE"

: "${ZENDUTY_API_KEY:?export ZENDUTY_API_KEY}"
export TF_VAR_zenduty_token="$ZENDUTY_API_KEY"

# use the per-run name prefix picked by run.sh so plans compare like for like
PREFIX_FILE="$HERE/.tfsmoke_prefix"
if [ -z "${TF_VAR_name_prefix:-}" ] && [ -f "$PREFIX_FILE" ]; then
  TF_VAR_name_prefix="$(cat "$PREFIX_FILE")"
  export TF_VAR_name_prefix
fi

printf '\n\033[1;34m== 404 self-heal\033[0m\n'

TEAM=$(terraform output -raw team_a_id)
TAG=$(terraform output -raw tag_id)

echo "--- deleting tag $TAG via the API, behind Terraform's back"
curl -sf -X DELETE "$BASE_URL/api/account/teams/$TEAM/tags/$TAG/" \
  -H "Authorization: token $ZENDUTY_API_KEY" > /dev/null

echo "--- terraform plan (expect a scheduled recreate, not an error)"
set +e
plan_output=$(terraform plan -no-color -target=zenduty_tags.main 2>&1)
rc=$?
set -e

if [ $rc -eq 1 ]; then
  echo "$plan_output" | tail -20
  printf '\033[1;31mFAIL: plan errored — 404 was not treated as "gone"\033[0m\n'
  exit 1
fi
if ! echo "$plan_output" | grep -q "zenduty_tags.main will be created"; then
  echo "$plan_output" | tail -20
  printf '\033[1;31mFAIL: plan did not schedule a recreate\033[0m\n'
  exit 1
fi
printf '\033[1;32mPASS: deleted tag detected, recreate planned\033[0m\n'

echo "--- recreating the tag to restore a clean state"
terraform apply -auto-approve -target=zenduty_tags.main > /dev/null
printf '\033[1;32mPASS: state restored\033[0m\n'
