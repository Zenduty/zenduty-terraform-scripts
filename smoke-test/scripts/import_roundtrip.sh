#!/usr/bin/env bash
# Import round-trip: forget every resource from state, re-import it by ID,
# then require an empty plan. Proves that (a) every importer parses its ID
# format, (b) every Read rebuilds complete state, and (c) reads are
# diff-stable against the config that created the objects.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

: "${TF_VAR_zenduty_token:?run via run.sh or export TF_VAR_zenduty_token}"

# use the per-run name prefix picked by run.sh so plans compare like for like
PREFIX_FILE="$HERE/.tfsmoke_prefix"
if [ -z "${TF_VAR_name_prefix:-}" ] && [ -f "$PREFIX_FILE" ]; then
  TF_VAR_name_prefix="$(cat "$PREFIX_FILE")"
  export TF_VAR_name_prefix
fi

printf '\n\033[1;34m== import round-trip\033[0m\n'

backup="terraform.tfstate.pre-import.$(date +%s).backup"
cp terraform.tfstate "$backup"

failures=0
while IFS=$'\t' read -r addr id; do
  echo "--- $addr  <=  $id"
  # tolerate an address already absent (left over from an earlier failed run)
  terraform state rm "$addr" > /dev/null 2>&1 || true
  if ! terraform import "$addr" "$id" > /dev/null; then
    printf '\033[1;31mIMPORT FAILED: %s (%s)\033[0m\n' "$addr" "$id"
    failures=$((failures + 1))
  fi
done < <(terraform output -json import_ids | jq -r 'to_entries[] | "\(.key)\t\(.value)"')

if [ "$failures" -gt 0 ]; then
  cp "$backup" terraform.tfstate
  printf '\033[1;31mFAIL: %d import(s) errored — state restored from %s\033[0m\n' "$failures" "$backup"
  exit 1
fi

echo "--- plan after re-import"
if terraform plan -detailed-exitcode > /dev/null; then
  printf '\033[1;32mPASS: empty plan after importing every resource\033[0m\n'
else
  terraform plan
  printf '\033[1;31mFAIL: plan is not empty after import (read/import drift above)\033[0m\n'
  exit 1
fi
