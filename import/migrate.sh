#!/usr/bin/env bash
# Guided cross-instance migration of a Zenduty account.
#
#   ./migrate.sh export <customer-name>   # export the SOURCE account + verify
#   ./migrate.sh apply  <customer-name>   # apply the export to the TARGET + verify
#
# Support runbook: ../MIGRATION.md. Prompts for API keys (never pass them as
# arguments), writes a log per phase into logs/<customer-name>/, and stops on
# the first failure.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON=${PYTHON:-python3}

usage() {
    sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
}

die() {
    echo "ERROR: $*" >&2
    exit 1
}

MODE=${1:-}
CUSTOMER=${2:-}
[[ -n "$MODE" && -n "$CUSTOMER" && $# -eq 2 ]] || usage
[[ "$CUSTOMER" != */* ]] || die "customer name must be a plain folder name, not a path"

LOGDIR="logs/$CUSTOMER"
STAMP=$(date +%Y%m%d-%H%M%S)   # one stamp per run: a run's logs group together
mkdir -p "$LOGDIR"

# run <logname> <cmd...> — stream to the terminal and to a log file
run() {
    local name=$1
    shift
    echo
    echo "==> $* (log: $LOGDIR/$name-$STAMP.log)"
    "$@" 2>&1 | tee "$LOGDIR/$name-$STAMP.log"
}

# verify <logname> — plan must show no changes; -detailed-exitcode: 0 clean, 2 changes
verify_plan() {
    local name=$1 rc=0
    echo
    echo "==> terraform plan (log: $LOGDIR/$name-$STAMP.log)"
    set +e
    terraform -chdir="$CUSTOMER" plan -detailed-exitcode 2>&1 | tee "$LOGDIR/$name-$STAMP.log"
    rc=${PIPESTATUS[0]}
    set -e
    case $rc in
        0) echo "OK: no changes — workspace matches the instance" ;;
        2) die "plan shows pending changes — do not continue; escalate with $LOGDIR/$name-$STAMP.log" ;;
        *) die "terraform plan failed — escalate with $LOGDIR/$name-$STAMP.log" ;;
    esac
}

command -v terraform >/dev/null || die "terraform not found on PATH"
command -v "$PYTHON" >/dev/null || die "$PYTHON not found on PATH"
"$PYTHON" -c 'import requests' 2>/dev/null || die "python dependencies missing — run: pip install -r ../requirements.txt"

prompt_key() {   # prompt_key <label> -> $REPLY
    read -rs -p "$1 API key: " REPLY
    echo
    [[ -n "$REPLY" ]] || die "empty API key"
}

case "$MODE" in
export)
    if [[ -z "${ZENDUTY_API_KEY:-}" ]]; then
        prompt_key "SOURCE"
        export ZENDUTY_API_KEY=$REPLY
    fi
    read -r -p "Source URL [https://www.zenduty.com]: " SOURCE_URL
    [[ -n "$SOURCE_URL" ]] && export ZENDUTY_BASE_URL=$SOURCE_URL

    run step1-export "$PYTHON" -u automate.py "$CUSTOMER"
    run step2-rewrite "$PYTHON" -u findreplace.py "$CUSTOMER"
    verify_plan step3-verify-source

    echo
    echo "Export complete and verified against the source."
    echo "Next: ./migrate.sh apply $CUSTOMER"
    ;;

apply)
    [[ -f "$CUSTOMER/zenduty_teams.tf" ]] || die "$CUSTOMER/ is not an export folder — run: ./migrate.sh export $CUSTOMER"

    read -r -p "TARGET instance URL: " TARGET_URL
    TARGET_URL=${TARGET_URL%/}   # normalize: a trailing slash must not read as a different target
    [[ -n "$TARGET_URL" ]] || die "target URL is required"
    prompt_key "TARGET"
    TARGET_KEY=$REPLY

    # a folder is bound to one target forever; refuse silent cross-target reuse
    MARKER="$CUSTOMER/.migration-target"
    if [[ -f "$MARKER" && "$(cat "$MARKER")" != "$TARGET_URL" ]]; then
        die "this folder was already applied to $(cat "$MARKER") — for a different target, make a fresh export"
    fi

    HOST=${TARGET_URL#*://}
    HOST=${HOST%%/*}
    echo
    echo "About to apply export '$CUSTOMER' onto: $TARGET_URL"
    echo "This creates objects there, and deletes auto-created defaults that"
    echo "don't match the export. Make sure this is NOT the live source account."
    read -r -p "Type the target hostname ($HOST) to confirm: " CONFIRM
    [[ "$CONFIRM" == "$HOST" ]] || die "confirmation did not match — nothing was done"

    export ZENDUTY_API_KEY=$TARGET_KEY ZENDUTY_BASE_URL=$TARGET_URL
    export ZENDUTY_TARGET_API_KEY=$TARGET_KEY ZENDUTY_TARGET_BASE_URL=$TARGET_URL

    # first apply only: detach the state bound to the source (kept as backup —
    # it contains secrets, never share it). On re-runs the state is the
    # target's and must stay.
    if [[ ! -f "$MARKER" ]]; then
        if compgen -G "$CUSTOMER/terraform.tfstate*" >/dev/null; then
            mkdir -p "$CUSTOMER/source-state-backup"
            mv "$CUSTOMER"/terraform.tfstate* "$CUSTOMER/source-state-backup/"
            echo "moved source state to $CUSTOMER/source-state-backup/"
        fi
        echo "$TARGET_URL" >"$MARKER"
    fi

    run step6-apply "$PYTHON" -u apply.py "$CUSTOMER"
    verify_plan step7-verify-target

    echo
    echo "Migration applied and verified. Spot-check the target UI (team count,"
    echo "user count, a few schedules/integrations) against the source."
    ;;

*)
    usage
    ;;
esac
