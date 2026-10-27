from jinja2 import Environment, FileSystemLoader
from zenduty.apiV2.authentication.zenduty_credential import ZendutyCredential
from zenduty.apiV2.client import ZendutyClient, ZendutyClientRequestMethod
from zenduty.apiV2.teams import TeamsClient
import os
import random
import shutil
import sys
import threading
import time
import uuid
import subprocess
import re
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import requests
from zenduty.apiV2.client import APIException

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _terraform_supports_import_blocks():
    try:
        run = subprocess.run(["terraform", "version", "-json"], capture_output=True, text=True)
        version = json.loads(run.stdout)["terraform_version"]
        major, minor = (int(x) for x in version.split(".")[:2])
        return (major, minor) >= (1, 5)
    except Exception:
        return False


# Fast mode enumerates everything via the API first (threaded), then imports
# all resources in one `terraform plan -generate-config-out` (parallel provider
# reads, single state write) instead of ~2 terraform subprocesses per resource.
# It is the default whenever terraform >= 1.5 (import blocks); --slow or
# ZENDUTY_FAST_IMPORT=0 forces the old per-resource path, --fast or
# ZENDUTY_FAST_IMPORT=1 demands fast mode. Best used on a fresh export folder.
if "--slow" in sys.argv[1:] or os.environ.get("ZENDUTY_FAST_IMPORT") == "0":
    FAST_MODE = False
elif "--fast" in sys.argv[1:] or os.environ.get("ZENDUTY_FAST_IMPORT") == "1":
    if not _terraform_supports_import_blocks():
        sys.exit("--fast needs terraform >= 1.5 for import blocks")
    FAST_MODE = True
else:
    FAST_MODE = _terraform_supports_import_blocks()
print(f"import mode: {'fast (import blocks)' if FAST_MODE else 'per-resource terraform import'}")
_args = [a for a in sys.argv[1:] if a not in ("--fast", "--slow")]
# Everything (generated .tf files, terraform state, mapping.json) is written here,
# making the folder a self-contained terraform workspace
OUTPUT_DIR = _args[0] if _args else os.environ.get("ZENDUTY_EXPORT_DIR", "export")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def out(path):
    return os.path.join(OUTPUT_DIR, path)


if not os.path.exists(out("main.tf")):
    shutil.copy(os.path.join(SCRIPT_DIR, "main.tf"), out("main.tf"))

# An interrupted run leaves the import scratch file behind, which duplicates
# the last imported resource and breaks terraform until removed
if os.path.exists(out("temp1.tf")):
    os.remove(out("temp1.tf"))

# a leftover imports.tf means a fast run aborted after writing config but
# before committing state + mapping.json; re-running on top would duplicate
# every resource under suffixed names
if FAST_MODE and os.path.exists(out("imports.tf")):
    sys.exit(
        f"{out('imports.tf')} is left over from an aborted fast run;"
        " start again with a fresh export folder"
    )

subprocess.run("terraform init", shell=True, cwd=OUTPUT_DIR)
# export ZENDUTY_API_KEY
cred = ZendutyCredential()
client = ZendutyClient(credential=cred, use_https=True)


class AdaptiveThrottle:
    """AIMD rate governor shared by every enumeration thread.

    Threads draw request slots from one budget, so the worker count no longer
    determines the request rate. A 429 halves the rate and pushes the next
    slot out for everyone; sustained success raises the rate additively, so
    throughput converges on whatever the API actually allows.
    """

    def __init__(self, initial_rps, min_rps=0.5, max_rps=50.0):
        self.rate = initial_rps
        self.min_rate = min_rps
        self.max_rate = max_rps
        self.lock = threading.Lock()
        self.next_slot = time.monotonic()
        self.successes = 0

    def acquire(self):
        with self.lock:
            now = time.monotonic()
            wait = self.next_slot - now
            self.next_slot = max(self.next_slot, now) + 1.0 / self.rate
        if wait > 0:
            time.sleep(wait)

    def on_success(self):
        with self.lock:
            self.successes += 1
            if self.successes >= 20:
                self.successes = 0
                self.rate = min(self.rate + 1.0, self.max_rate)

    def on_rate_limited(self, penalty):
        with self.lock:
            self.successes = 0
            self.rate = max(self.rate / 2.0, self.min_rate)
            # push the shared next slot out so every thread backs off together,
            # not just the one that saw the 429
            self.next_slot = max(self.next_slot, time.monotonic() + penalty)
            return self.rate


# every SDK call funnels through client.execute, so pacing and retries live at
# that single point; ZENDUTY_API_RPS sets the starting request rate
_throttle = AdaptiveThrottle(initial_rps=float(os.environ.get("ZENDUTY_API_RPS", "8")))
_original_execute = client.execute


def _execute_with_retry(*args, **kwargs):
    delay = 2.0
    last_error = None
    for attempt in range(8):
        _throttle.acquire()
        try:
            result = _original_execute(*args, **kwargs)
        except (APIException, requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_error = e
            code = getattr(e, "_code", None)
            if code == 429:
                new_rate = _throttle.on_rate_limited(penalty=2.0 * (attempt + 1) + random.uniform(0, 1))
                print(f"WARN: rate limited; slowing shared request rate to {new_rate:.1f}/s")
                continue
            if code in (502, 503, 504) or not isinstance(e, APIException):
                wait = delay + random.uniform(0, delay / 2)
                print(f"WARN: API request failed ({code or type(e).__name__}); retrying in {wait:.1f}s")
                time.sleep(wait)
                delay = min(delay * 2, 60)
                continue
            raise
        _throttle.on_success()
        return result
    raise last_error


client.execute = _execute_with_retry

TEMPLATE_ENVIRONMENT = Environment(loader=FileSystemLoader(os.path.join(SCRIPT_DIR, "templates")))

teams_client = TeamsClient(client=client)

create_mapping = {}


def load_mapping_file(path=None):
    path = path or out("mapping.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    # Ensure keys are strings for JSON compatibility
                    create_mapping.update({str(k): v for k, v in data.items()})
        except Exception:
            # If the file is malformed, ignore and start fresh in-memory
            pass


# Initialize in-memory mapping from existing file (if any)
load_mapping_file()


# fast mode queues every resource here during enumeration and imports them all
# at the end; config for data sources is buffered so nothing lands on disk
# unless the terraform phase commits. Enumeration runs threaded in fast mode
# (the SDK issues a stateless request per call), so all shared bookkeeping is
# guarded by one reentrant lock.
fast_worklist = []
config_buffers = {}
planned_names = {}
state_lock = threading.RLock()


def run_parallel(items, fn, what):
    # normal mode must stay sequential: concurrent `terraform import` runs
    # fight over the state lock
    if not FAST_MODE:
        for item in items:
            fn(item)
        return
    # worker count no longer determines the request rate — the shared
    # AdaptiveThrottle paces actual requests, so threads just provide
    # concurrency for whatever rate the API sustains
    env_workers = os.environ.get("ZENDUTY_ENUM_THREADS")
    workers = int(env_workers) if env_workers else min(32, (os.cpu_count() or 4) + 4)
    workers = max(1, min(workers, len(items)))
    print(f"enumerating {len(items)} {what} with {workers} threads")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fn, item) for item in items]
        for future in as_completed(futures):
            future.result()


def add_mapping(unique_id, resource_name, flush=True):
    if not unique_id or not resource_name:
        return
    with state_lock:
        create_mapping[str(unique_id)] = resource_name
        # in fast mode the mapping is only persisted after the import succeeds, so
        # an aborted run doesn't leave entries for resources that never landed
        if flush and not FAST_MODE:
            write_mapping_file()


def append_config(file, text):
    with state_lock:
        if FAST_MODE:
            config_buffers.setdefault(file, []).append(text)
            return
        with open(out(file), "a", encoding="utf-8") as f:
            f.write(text)


def write_mapping_file(path=None):
    path = path or out("mapping.json")
    # Persist the current in-memory mapping as-is
    with open(path, "w", encoding="utf-8") as f:
        json.dump(create_mapping, f, indent=4, sort_keys=True)


ansi_escape = re.compile(
    r"""
    \x1B  # ESC
    (?:   # 7-bit C1 Fe (except CSI)
        [@-Z\\-_]
    |     # or [ for CSI, followed by a control sequence
        \[
        [0-?]*  # Parameter bytes
        [ -/]*  # Intermediate bytes
        [@-~]   # Final byte
    )
""",
    re.VERBOSE,
)


def check_string(file: str, word: str) -> bool:
    if not os.path.exists(file):
        return False
    with open(file, encoding="utf-8") as temp_f:
        for line in temp_f:
            if word in line:
                return True
    return False


def write_into_temp_file(name, instance, resource):
    name = name.replace(" ", "")
    f = open(out("temp1.tf"), "w")
    f.write(instance.render(name=name, resource=resource))
    f.write("\n")
    f.close()


def check_name(name: str, file: str) -> str:
    # Lowercase and replace invalid characters with underscores
    name = re.sub(r"[^a-z0-9_]", "_", name.lower())
    # Ensure it doesn't start with a digit
    if re.match(r"^\d", name):
        name = f"_{name}"
    with state_lock:
        # Suffix until the name is actually free (in the file, or planned for it
        # in fast mode where nothing is written until the end). The suffix must
        # be re-checked too: with hundreds of identically-named resources a
        # random 4-hex suffix collides far sooner than 65536 names (birthday
        # paradox), which surfaced as duplicate import blocks.
        planned = planned_names.setdefault(file, set())
        candidate = name
        while check_string(out(file), candidate) or candidate in planned:
            candidate = f"{name}_{str(uuid.uuid4())[:4]}"
        planned.add(candidate)
    return candidate


def execute_terraform_import(name, resource, parameters, unique_id):

    if str(unique_id) not in create_mapping:
        print(f"terraform import {resource}.{name} {parameters}")
        subprocess.run(f"terraform import {resource}.{name} {parameters}", shell=True, cwd=OUTPUT_DIR)
        run = subprocess.run(
            f"terraform state show {resource}.{name}", shell=True, capture_output=True, cwd=OUTPUT_DIR
        )
        output = run.stdout.decode("utf8").splitlines()
        return output
    print(f"terraform import {resource}.{name} {parameters} already exists")


# Computed/sensitive fields that `terraform state show` prints but that are not
# valid config arguments
SKIP_FIELDS = ("id", "integration_key", "webhook_url", "unique_id", "position", "creation_date")


def parse_write_into_file(output, file, unique_id, name, line_assert=False, extra_lines=None, extra_skip=None):
    # remove id from output
    if not output:
        return
    skip_pattern = rf"\b({'|'.join(SKIP_FIELDS + tuple(extra_skip or ()))})\s*="
    clean_output = []
    for line_index, line in enumerate(output):
        # Remove ANSI escape codes for matching
        clean_line = re.sub(r"\x1b\[[0-9;]*m", "", line)
        # Skip lines containing sensitive or generated fields
        if re.search(skip_pattern, clean_line):
            if line_assert:
                # Only skip in the first 3 processed lines
                if line_index < 4:
                    continue
            else:
                # When not asserting, skip these fields everywhere
                continue
        clean_output.append(line)

    if extra_lines:
        for line_index, line in enumerate(clean_output):
            if re.match(r"^resource ", re.sub(r"\x1b\[[0-9;]*m", "", line)):
                clean_output[line_index + 1:line_index + 1] = extra_lines
                break

    output = clean_output
    output = "\n".join(output)
    f = open(out(f"{file}.tf"), "a")
    f.write(ansi_escape.sub("", output))
    f.write("\n")
    f.close()
    add_mapping(unique_id, name, flush=True)


common_template = TEMPLATE_ENVIRONMENT.get_template("common.jinja2")

# mirrors the provider's action validation (resource_alertrules.go): these
# action types carry their payload in a dedicated field, 3/18 take none, 11
# needs `key`, everything else needs `value`. An action whose target object
# was deleted survives in the source DB with a null payload and can never be
# re-created.
ALERT_ACTION_TYPE_ATTR = {
    4: "escalation_policy",
    5: "schedule",
    6: "assign_to",
    14: "sla",
    15: "team_priority",
    16: "task_template",
}


def creatable_alert_actions(actions):
    creatable = 0
    for action in actions:
        action_type = action.get("action_type")
        if action_type in (3, 18):
            creatable += 1
        elif action_type == 11:
            creatable += bool(action.get("key"))
        elif action.get("value") or action.get(ALERT_ACTION_TYPE_ATTR.get(action_type, "value")):
            creatable += 1
    return creatable


def safe_get(endpoint, raw=False):
    # Some endpoints 404/403 depending on the account plan; skip them instead of aborting the run
    try:
        data = client.execute(ZendutyClientRequestMethod.GET, endpoint, success_code=200)
    except Exception as e:
        print(f"WARN: skipping {endpoint}: {e}")
        return {} if raw else []
    if raw:
        return data or {}
    # Newer endpoints (e.g. post_incident_tasks) are paginated and wrap the
    # list in {"count": ..., "next": ..., "results": [...]}; iterating that
    # dict directly would yield its string keys
    if isinstance(data, dict):
        items = data.get("results")
        if not isinstance(items, list):
            return []
        items = list(items)
        next_url = data.get("next")
        while next_url:
            parsed = urlparse(next_url)
            page_endpoint = parsed.path + (f"?{parsed.query}" if parsed.query else "")
            try:
                page = client.execute(ZendutyClientRequestMethod.GET, page_endpoint, success_code=200)
            except Exception as e:
                print(f"WARN: stopping pagination at {page_endpoint}: {e}")
                break
            if not isinstance(page, dict):
                break
            items.extend(page.get("results") or [])
            next_url = page.get("next")
        return items
    return data or []


def import_and_write(
    display_name, resource, import_id, unique_id, line_assert=False, extra_lines=None, extra_skip=None
):
    name = check_name(display_name, f"{resource}.tf")

    if FAST_MODE:
        with state_lock:
            if str(unique_id) in create_mapping:
                print(f"{resource}.{name} ({unique_id}) already imported")
                return name
            fast_worklist.append(
                {
                    "resource": resource,
                    "name": name,
                    "import_id": import_id,
                    "unique_id": str(unique_id),
                    "line_assert": line_assert,
                    "extra_lines": extra_lines,
                    "extra_skip": extra_skip,
                }
            )
            # in-memory only (see add_mapping): downstream references like the
            # member depends_on derivation need the entry during enumeration
            add_mapping(unique_id, f"{resource}.{name}.id")
        print(f"queued {resource}.{name}")
        return name

    write_into_temp_file(name, common_template, resource)
    output = execute_terraform_import(name, resource, import_id, unique_id)
    parse_write_into_file(
        output,
        resource,
        unique_id,
        f"{resource}.{name}.id",
        line_assert=line_assert,
        extra_lines=extra_lines,
        extra_skip=extra_skip,
    )
    return name


all_teams = teams_client.list_teams()

# zenduty_user requires `team`: the invite API contract always takes a destination
# team (the provider suppresses the diff on imported users). Use a dedicated team
# so the choice is deterministic, creating it on the first run if missing.
GLOBAL_TEAM_NAME = os.environ.get("ZENDUTY_INVITE_TEAM", "GLOBAL")
default_team = next((t.unique_id for t in all_teams if t.name == GLOBAL_TEAM_NAME), None)
if default_team is None:
    print(f"creating invite team '{GLOBAL_TEAM_NAME}'")
    default_team = teams_client.create_team(GLOBAL_TEAM_NAME).unique_id
    all_teams = teams_client.list_teams()

all_users = client.execute(ZendutyClientRequestMethod.GET, "/api/account/users/")


def process_user(user):
    username = user["user"]["username"]

    # role 1 is the account owner, which the provider cannot manage (the API
    # returns the role as an int); expose it as a data source instead so other
    # resources can still reference the owner
    if str(user["role"]) == "1":
        owner_email = user["user"].get("email")
        with state_lock:
            if owner_email and username not in create_mapping:
                name = check_name(user["user"]["first_name"] + user["user"]["last_name"], "zenduty_user.tf")
                append_config(
                    "zenduty_user.tf", f'data "zenduty_user" "{name}" {{\n    email = "{owner_email}"\n}}\n'
                )
                add_mapping(username, f"data.zenduty_user.{name}.users[0].username")
        return

    # bot/integration users have no email; the provider cannot create or manage them
    if not user["user"].get("email"):
        print(f"skipping bot user {user['user'].get('first_name', '')} {user['user'].get('last_name', '')}")
        return

    import_and_write(
        user["user"]["first_name"] + user["user"]["last_name"],
        "zenduty_user",
        username,
        username,
        extra_lines=[f'    team       = "{default_team}"'] if default_team else None,
    )

    # notification rules are deliberately not migrated: they are personal
    # per-user preferences tied to contact methods (phone/email UUIDs) that
    # only exist once the user completes signup on the target, and the invite
    # already creates a sensible default set there. The usercontact data-source
    # lookups existed only to serve them, so they are gone too.


run_parallel(all_users, process_user, "users")

for account_role in safe_get("/api/account/customroles/"):
    import_and_write(
        account_role["name"],
        "zenduty_account_role",
        account_role["unique_id"],
        account_role["unique_id"],
    )


def process_team(team):
    # the team itself goes first so an interrupted run still has the resources
    # everything else references
    import_and_write(team.name, "zenduty_teams", team.unique_id, team.unique_id, extra_skip=("owner",))

    # members go before schedules/ESPs/SLAs: the create APIs reject rule and
    # layer targets who aren't members of the team yet, so those resources
    # need an explicit depends_on to order the applies on a fresh account
    member_refs = []
    for member in safe_get(f"/api/account/teams/{team.unique_id}/members/"):
        member_user = member.get("user") if isinstance(member.get("user"), dict) else {}
        member_label = (
            member_user.get("first_name", "") + member_user.get("last_name", "")
            or member_user.get("username", "")
            or str(member.get("user", ""))
        )
        import_and_write(
            f"{team.name}_{member_label}",
            "zenduty_member",
            f"{team.unique_id}/{member['unique_id']}",
            member["unique_id"],
        )
        # derive the resource address from the mapping so incremental re-runs
        # (where check_name would mint a fresh suffixed name) stay correct
        member_ref = create_mapping.get(str(member["unique_id"]), "")
        if member_ref.startswith("zenduty_member.") and member_ref.endswith(".id"):
            member_refs.append(member_ref[: -len(".id")])

    member_depends = None
    if member_refs:
        member_depends = (
            ["    depends_on = ["] + [f"        {ref}," for ref in member_refs] + ["    ]"]
        )

    for schedule in teams_client.get_schedule_client(team).get_all_schedules():
        import_and_write(
            schedule.name,
            "zenduty_schedules",
            f"{team.unique_id}/{schedule.unique_id}",
            schedule.unique_id,
            extra_lines=member_depends,
        )

    for escalation in teams_client.get_escalation_policy_client(team).get_all_policies():
        # the create API refuses EPs with no rules — and treats rules without
        # targets the same way — so an exported one could never be applied to
        # a fresh account; skip it (and anything that references it will
        # surface the dangling id at plan time). EPs that keep at least one
        # targeted rule are exported as-is; apply.py strips their remaining
        # target-less rules before applying.
        rules = getattr(escalation, "rules", None) or []
        if not any(
            (rule.get("targets") if isinstance(rule, dict) else getattr(rule, "targets", None))
            for rule in rules
        ):
            print(
                f"WARN: skipping escalation policy '{escalation.name}' ({escalation.unique_id}):"
                " it has no rules with targets, which the create API rejects; add a"
                " targeted rule in the source account to include it"
            )
            continue
        import_and_write(
            escalation.name,
            "zenduty_esp",
            f"{team.unique_id}/{escalation.unique_id}",
            escalation.unique_id,
            extra_lines=member_depends,
        )

    for sla in teams_client.get_sla_client(team).get_all_slas():
        import_and_write(
            sla.name,
            "zenduty_sla",
            f"{team.unique_id}/{sla.unique_id}",
            sla.unique_id,
            extra_lines=member_depends,
        )

    for incident_role in teams_client.get_incident_role_client(team).get_all_roles():
        import_and_write(
            incident_role.title,
            "zenduty_roles",
            f"{team.unique_id}/{incident_role.unique_id}",
            incident_role.unique_id,
        )

    for team_tag in teams_client.get_tag_client(team).get_all_tags():
        import_and_write(team_tag.name, "zenduty_tags", f"{team.unique_id}/{team_tag.unique_id}", team_tag.unique_id)

    for priority in teams_client.get_priority_client(team).get_all_priorities():
        import_and_write(
            priority.name, "zenduty_priorities", f"{team.unique_id}/{priority.unique_id}", priority.unique_id
        )

    services_client = teams_client.get_service_client(team)
    for service in services_client.get_all_services():
        import_and_write(
            service.name,
            "zenduty_services",
            f"{team.unique_id}/{service.unique_id}",
            service.unique_id,
            extra_skip=("status", "under_maintenance"),
        )

        integration_client = services_client.get_integration_client(service)
        for integration in integration_client.get_all_integrations():
            # application catalog UUIDs differ between instances (prod vs a
            # local backend), so hardcoding them makes the config unportable;
            # emit a name-based data source lookup and map the UUID to it
            app_ref = integration.application_reference
            app_name = app_ref["name"] if isinstance(app_ref, dict) else getattr(app_ref, "name", None)
            app_uuid = str(integration.application or "")
            # under the lock: the same application shows up in many teams, and
            # two threads must not both pass the dedupe check
            with state_lock:
                if app_uuid and app_name and app_uuid not in create_mapping:
                    data_name = check_name(app_name, "zenduty_applications.tf")
                    append_config(
                        "zenduty_applications.tf",
                        f'data "zenduty_applications" "{data_name}" {{\n'
                        f"    name = {json.dumps(app_name)}\n"
                        f"}}\n",
                    )
                    add_mapping(app_uuid, f"data.zenduty_applications.{data_name}.id")

            import_and_write(
                integration.name,
                "zenduty_integrations",
                f"{team.unique_id}/{service.unique_id}/{integration.unique_id}",
                integration.unique_id,
            )

            for alert_rule in safe_get(
                f"/api/account/teams/{team.unique_id}/services/{service.unique_id}/integrations/{integration.unique_id}/transformers/"  # noqa
            ):
                # the create API rejects alert rules without actions, so a rule
                # with none left (all targets deleted on the source) could never
                # be applied to a fresh account; skip it. Rules that keep at
                # least one creatable action are exported as-is; apply.py strips
                # their broken actions before applying.
                if not creatable_alert_actions(alert_rule.get("actions") or []):
                    print(
                        f"WARN: skipping alert rule '{alert_rule['description']}'"
                        f" ({alert_rule['unique_id']}): none of its actions are creatable"
                        " (payload objects deleted on the source), which the create API rejects"
                    )
                    continue
                import_and_write(
                    alert_rule["description"],
                    "zenduty_alertrules",
                    f"{team.unique_id}/{service.unique_id}/{integration.unique_id}/{alert_rule['unique_id']}",
                    alert_rule["unique_id"],
                    line_assert=True,
                )

            # outgoing rules only exist for outbound integrations (integration_type 1);
            # the endpoint 400s with "Integration does not exists" for inbound ones
            if getattr(integration, "integration_type", 0) == 1:
                for outgoing_rule in safe_get(
                    f"/api/v2/account/teams/{team.unique_id}/services/{service.unique_id}/integrations/{integration.unique_id}/outgoingrules/"  # noqa
                ):
                    import_and_write(
                        f"{integration.name}_outgoing",
                        "zenduty_outgoing_rules",
                        f"{team.unique_id}/{service.unique_id}/{integration.unique_id}/{outgoing_rule['unique_id']}",
                        outgoing_rule["unique_id"],
                    )

    for window in safe_get(f"/api/account/teams/{team.unique_id}/maintenance/"):
        import_and_write(
            window["name"],
            "zenduty_maintenance_window",
            f"{team.unique_id}/{window['unique_id']}",
            window["unique_id"],
        )

    for task in safe_get(f"/api/account/teams/{team.unique_id}/post_incident_tasks/"):
        import_and_write(
            task["title"],
            "zenduty_post_incident_tasks",
            f"{team.unique_id}/{task['unique_id']}",
            task["unique_id"],
        )

    for template in safe_get(f"/api/account/teams/{team.unique_id}/task_templates/"):
        import_and_write(
            template["name"],
            "zenduty_task_templates",
            f"{team.unique_id}/{template['unique_id']}",
            template["unique_id"],
        )
        for template_task in safe_get(
            f"/api/account/teams/{team.unique_id}/task_templates/{template['unique_id']}/tasks/"
        ):
            import_and_write(
                template_task["title"],
                "zenduty_task_template_tasks",
                f"{team.unique_id}/{template['unique_id']}/{template_task['unique_id']}",
                template_task["unique_id"],
            )

    team_permissions = safe_get(f"/api/account/teams/{team.unique_id}/permissions/", raw=True)
    if isinstance(team_permissions, dict) and team_permissions.get("account_permissions"):
        # dedupe key is prefixed so it doesn't collide with the team's own mapping entry
        import_and_write(
            f"{team.name}_permissions",
            "zenduty_team_permissions",
            team.unique_id,
            f"permissions-{team.unique_id}",
        )


run_parallel(all_teams, process_team, "teams")

for router in safe_get("/api/v2/account/events/router/"):
    import_and_write(
        router["name"],
        "zenduty_globalrouter",
        router["unique_id"],
        router["unique_id"],
    )
    for routing_rule in safe_get(f"/api/v2/account/events/router/{router['unique_id']}/rulesets/"):
        # the create API rejects routing rules with no actions, so an exported
        # one could never be applied to a fresh account; skip it
        if not routing_rule.get("actions"):
            print(
                f"WARN: skipping global routing rule"
                f" '{routing_rule.get('name') or routing_rule['unique_id']}'"
                f" ({routing_rule['unique_id']}): it has no actions, which the"
                " create API rejects; add an action in the source account to include it"
            )
            continue
        import_and_write(
            routing_rule.get("name") or routing_rule["unique_id"],
            "zenduty_globalrouting_rule",
            f"{router['unique_id']}/{routing_rule['unique_id']}",
            routing_rule["unique_id"],
        )


def _tf(args):
    return subprocess.run(["terraform", *args], cwd=OUTPUT_DIR, capture_output=True, text=True)


def _tf_stream(args):
    # stream terraform's output live so long plans don't look stuck, while
    # still capturing it for error parsing
    process = subprocess.Popen(
        ["terraform", *args],
        cwd=OUTPUT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    lines = []
    for line in process.stdout:
        print(line, end="", flush=True)
        lines.append(line)
    process.wait()
    return process.returncode, "".join(lines)


def _check_terraform_version():
    run = _tf(["version", "-json"])
    try:
        version = json.loads(run.stdout)["terraform_version"]
        major, minor = (int(x) for x in version.split(".")[:2])
    except Exception:
        return
    if (major, minor) < (1, 5):
        sys.exit(f"--fast needs terraform >= 1.5 for import blocks (found {version})")


def _advance_depth(line, depth):
    # brace depth after this line, ignoring braces inside quoted strings
    in_string = False
    escaped = False
    for ch in line:
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == '"':
            in_string = not in_string
        elif not in_string and ch == "{":
            depth += 1
        elif not in_string and ch == "}":
            depth -= 1
    return depth


def _split_generated_blocks(text):
    blocks = []
    current = None
    depth = 0
    for line in text.splitlines():
        start_depth = depth
        depth = _advance_depth(line, depth)
        if current is None:
            if start_depth == 0:
                match = re.match(r'^resource\s+"([a-z0-9_]+)"\s+"([^"]+)"', line)
                if match:
                    current = {"type": match.group(1), "name": match.group(2), "lines": [line]}
        else:
            current["lines"].append(line)
            if depth == 0:
                blocks.append(current)
                current = None
    return blocks


def _postprocess_block(lines, entry):
    # generated config includes every settable attribute; drop the ones the
    # per-resource `terraform import` flow used to strip. Only top-level
    # attributes are touched so values inside nested blocks or jsonencode()
    # expressions survive; nested `attr = null` is valid HCL and stays.
    # `provider` too: generate-config writes the provider's type name
    # (zendutylocal) instead of the config's local name (zenduty), which makes
    # terraform look for hashicorp/zendutylocal; the default provider from
    # main.tf is correct for every resource, so the meta-argument just goes
    skip_fields = SKIP_FIELDS + ("provider",) + tuple(entry.get("extra_skip") or ())
    skip_pattern = re.compile(rf"^\s*({'|'.join(skip_fields)})\s*=")
    null_pattern = re.compile(r"^\s*[A-Za-z0-9_]+\s*=\s*null\s*(#.*)?$")
    result = [lines[0]]
    if entry.get("extra_lines"):
        result.extend(entry["extra_lines"])
    depth = _advance_depth(lines[0], 0)
    for line in lines[1:]:
        start_depth = depth
        depth = _advance_depth(line, depth)
        if start_depth == 1 and (skip_pattern.match(line) or null_pattern.match(line)):
            continue
        result.append(line)
    return result


def _write_import_blocks(entries):
    # `provider = zenduty` is spelled out because terraform 1.16.0-1.16.2 stop
    # consulting required_providers for import targets that have no config yet
    # and fall back to hashicorp/zenduty, which does not exist
    # (hashicorp/terraform#39144). The argument is accepted since 1.5.0.
    with open(out("imports.tf"), "w", encoding="utf-8") as f:
        f.write("# generated by automate.py --fast; removed automatically after a successful import\n")
        for entry in entries:
            f.write(
                f'import {{\n  provider = zenduty\n  to = {entry["resource"]}.{entry["name"]}\n'
                f'  id = "{entry["import_id"]}"\n}}\n'
            )


def _plan_counts(output):
    match = re.search(r"Plan:(?:\s*(\d+) to import,)?\s*(\d+) to add,\s*(\d+) to change,\s*(\d+) to destroy", output)
    if not match:
        return None
    return tuple(int(g or 0) for g in match.groups())


def _flush_buffers_and_mapping(dropped_entries):
    for file, texts in config_buffers.items():
        with open(out(file), "a", encoding="utf-8") as f:
            f.write("".join(texts))
    config_buffers.clear()
    dropped_addresses = {f'{e["resource"]}.{e["name"]}' for e in dropped_entries}
    for key, value in list(create_mapping.items()):
        if value.endswith(".id") and value[: -len(".id")] in dropped_addresses:
            del create_mapping[key]
    write_mapping_file()


def _hcl_quoted(value):
    # HCL's quoted-string escapes are JSON's; the two template sigils are the
    # only extras, and doubling them keeps the literal inert
    return json.dumps(value).replace("${", "$${").replace("%{", "%%{")


def _bracket_depth(line, depth):
    # bracket depth after this line, ignoring brackets inside quoted strings.
    # generate-config-out never emits heredocs, so quote tracking is enough
    in_string = False
    escaped = False
    for ch in line:
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == '"':
            in_string = not in_string
        elif not in_string:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
    return depth


def _same_json(before, after):
    # one JSON document rendered two ways: jsonencode() sorts object keys and
    # drops whitespace, the API hands back whatever order it was sent in
    if not isinstance(before, str) or not isinstance(after, str) or before == after:
        return False
    try:
        return json.loads(before) == json.loads(after)
    except ValueError:
        return False


def _diff_leaves(before, after, path=()):
    # every differing leaf of the plan's before/after, with the path to it
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            yield from _diff_leaves(before.get(key), after.get(key), path + (key,))
    elif isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        for index, (b, a) in enumerate(zip(before, after)):
            yield from _diff_leaves(b, a, path + (index,))
    elif before != after:
        yield path, before, after


def _json_reorder_fixes(plan_file="fast.tfplan"):
    """Split the plan's updates into cosmetic JSON reorderings and real drift.

    Returns (fixes, blockers): fixes are (address, path, remote_value) for
    attributes where config and remote hold the same JSON document in a
    different key order; blockers describe anything else the plan wants to
    change. A resource contributes to both if it has some of each.
    """
    run = _tf(["show", "-json", plan_file])
    if run.returncode != 0:
        return [], [f"terraform show -json {plan_file} failed: {(run.stderr or '').strip()}"]
    try:
        plan = json.loads(run.stdout)
    except ValueError as e:
        return [], [f"could not parse the plan JSON: {e}"]
    fixes, blockers = [], []
    for change in plan.get("resource_changes") or []:
        delta = change.get("change") or {}
        if "update" not in (delta.get("actions") or []):
            continue
        address = change.get("address", "?")
        leaves = list(_diff_leaves(delta.get("before"), delta.get("after")))
        if not leaves:
            blockers.append(f"{address}: plan wants an update but no attribute differs")
            continue
        for path, before, after in leaves:
            where = ".".join(str(p) for p in path) or "(root)"
            if _same_json(before, after):
                fixes.append((address, path, before))
            else:
                blockers.append(f"{address}: {where} differs for real")
    return fixes, blockers


def _resource_range(lines, resource_type, name):
    # body line range (start, end) of one resource block, end exclusive
    header = re.compile(rf'^resource\s+"{re.escape(resource_type)}"\s+"{re.escape(name)}"')
    for number, line in enumerate(lines):
        if header.match(line):
            last = number
            depth = _bracket_depth(line, 0)
            while depth > 0 and last + 1 < len(lines):
                last += 1
                depth = _bracket_depth(lines[last], depth)
            return number + 1, last
    return None


def _nth_block(lines, start, end, name, occurrence):
    # body of the occurrence-th `name {` / `name = {` at the top level of a region
    pattern = re.compile(rf"^\s*{re.escape(name)}\s*(=\s*)?\{{\s*$")
    depth = 0
    seen = -1
    for number in range(start, end):
        if depth == 0 and pattern.match(lines[number]):
            seen += 1
            if seen == occurrence:
                last = number
                inner = _bracket_depth(lines[number], 0)
                while inner > 0 and last + 1 < end:
                    last += 1
                    inner = _bracket_depth(lines[last], inner)
                return number + 1, last
        depth = _bracket_depth(lines[number], depth)
    return None


def _assignment_range(lines, start, end, name):
    # line range of `name = <expr>`, following the expression across lines
    pattern = re.compile(rf"^\s*{re.escape(name)}\s*=")
    depth = 0
    for number in range(start, end):
        if depth == 0 and pattern.match(lines[number]):
            last = number
            open_depth = _bracket_depth(lines[number], 0)
            while open_depth > 0 and last + 1 < end:
                last += 1
                open_depth = _bracket_depth(lines[last], open_depth)
            return number, last
        depth = _bracket_depth(lines[number], depth)
    return None


def _resolve_attribute(lines, start, end, path):
    """Line range of the attribute a plan path names, or None if unmappable.

    Plan paths mix attribute names with list indices. A name followed by an
    index is a repeated nested block (`actions { ... }` twice over), a name
    followed by a name is a single nested block, and a trailing name is the
    assignment itself. None means "this shape isn't understood" — callers must
    then leave the config untouched rather than guess.
    """
    position = 0
    while position < len(path):
        name = path[position]
        if not isinstance(name, str):
            return None
        position += 1
        occurrence = 0
        if position < len(path) and isinstance(path[position], int):
            occurrence = path[position]
            position += 1
        if position < len(path):
            found = _nth_block(lines, start, end, name, occurrence)
            if found is None:
                return None
            start, end = found
            continue
        if occurrence:
            return None
        return _assignment_range(lines, start, end, name)
    return None


def _rewrite_json_attributes(fixes):
    """Pin jsonencode()-rendered attributes to the literal the API returned.

    generate-config-out renders a JSON string attribute as jsonencode({...}),
    which re-serialises with sorted keys. When the source account stored the
    same document in another key order, terraform reports a permanent update:
    it fails the import-only gate, and applying it would rewrite the value in
    the source account to say exactly what it already says. Writing the remote
    literal into the config makes the plan clean instead.
    """
    by_file = {}
    for address, path, remote in fixes:
        resource_type, _, name = address.partition(".")
        by_file.setdefault(f"{resource_type}.tf", []).append((resource_type, name, path, remote))
    rewritten = 0
    for file, items in sorted(by_file.items()):
        file_path = out(file)
        if not os.path.exists(file_path):
            print(f"WARN: {file} is missing; cannot pin its JSON attributes")
            continue
        with open(file_path, encoding="utf-8") as f:
            lines = f.read().split("\n")
        changed = False
        for resource_type, name, path, remote in items:
            where = ".".join(str(p) for p in path)
            block = _resource_range(lines, resource_type, name)
            if block is None:
                print(f"WARN: no config block for {resource_type}.{name}; leaving {where} alone")
                continue
            located = _resolve_attribute(lines, block[0], block[1], path)
            if located is None:
                print(f"WARN: could not locate {where} in {resource_type}.{name}; leaving it alone")
                continue
            first, last = located
            indent = lines[first][: len(lines[first]) - len(lines[first].lstrip())]
            lines[first:last + 1] = [f"{indent}{path[-1]} = {_hcl_quoted(remote)}"]
            print(f"pinned {resource_type}.{name} {where} to the value the API returned")
            rewritten += 1
            changed = True
        if changed:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
    return rewritten


def run_fast_import():
    _check_terraform_version()
    generated_name = "generated.tf"
    if os.path.exists(out(generated_name)):
        os.remove(out(generated_name))

    entries = list(fast_worklist)
    dropped = []
    if not entries:
        print("nothing new to import")
        _flush_buffers_and_mapping(dropped)
        return

    parallelism = os.environ.get("ZENDUTY_TF_PARALLELISM", "8")

    for _ in range(4):
        _write_import_blocks(entries)
        print(f"importing {len(entries)} resources via a single terraform plan (parallelism {parallelism})")
        returncode, output = _tf_stream(["plan", f"-generate-config-out={generated_name}", "-input=false",
                                         "-no-color", f"-parallelism={parallelism}"])
        if returncode == 0:
            break
        failed = set(re.findall(r"with (\w+\.\w+)", output))
        failing_entries = [e for e in entries if f'{e["resource"]}.{e["name"]}' in failed]
        if not failing_entries:
            sys.exit(f"import plan failed and no failing resource could be identified:\n{output[-4000:]}")
        if len(failing_entries) > max(20, len(entries) // 5):
            sys.exit(
                f"{len(failing_entries)} of {len(entries)} imports failed — aborting instead of dropping"
                f" that many (rate limit or auth problem?):\n{output[-4000:]}"
            )
        for entry in failing_entries:
            print(f'WARN: dropping {entry["resource"]}.{entry["name"]} ({entry["import_id"]}): import failed')
            dropped.append(entry)
        entries = [e for e in entries if e not in failing_entries]
        if os.path.exists(out(generated_name)):
            os.remove(out(generated_name))
    else:
        sys.exit("import plan still failing after 4 attempts")

    with open(out(generated_name), encoding="utf-8") as f:
        generated_text = f.read()
    by_address = {f'{e["resource"]}.{e["name"]}': e for e in entries}
    blocks = _split_generated_blocks(generated_text)
    generated_addresses = {f'{b["type"]}.{b["name"]}' for b in blocks}
    for address in sorted(set(by_address) - generated_addresses):
        print(f"WARN: no config was generated for {address}; dropping it")
        dropped.append(by_address[address])
    written = 0
    for block in blocks:
        entry = by_address.get(f'{block["type"]}.{block["name"]}')
        if entry is None:
            continue
        lines = _postprocess_block(block["lines"], entry)
        with open(out(f'{block["type"]}.tf'), "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        written += 1
    os.remove(out(generated_name))

    # data-source blocks (applications, contacts, owner) join the config now so
    # the verification plan exercises them too
    for file, texts in config_buffers.items():
        with open(out(file), "a", encoding="utf-8") as f:
            f.write("".join(texts))
    config_buffers.clear()

    def gate_failure(counts):
        return (
            f"verification plan is not import-only (import/add/change/destroy = {counts});"
            " aborting before apply so the source account is never modified."
            " imports.tf and the generated config are kept for inspection."
        )

    # a cosmetic jsonencode() key reorder can be pinned out of the plan and the
    # gate re-checked; anything else still aborts, so the plan that reaches the
    # apply below is import-only on its own terms and never edits the source
    counts = None
    for attempt in range(3):
        print("running verification plan (must be import-only before anything is applied)")
        returncode, output = _tf_stream(["plan", "-input=false", "-no-color",
                                         f"-parallelism={parallelism}", "-out=fast.tfplan"])
        if returncode != 0:
            sys.exit("verification plan failed; imports.tf is kept for inspection (output above)")
        counts = _plan_counts(output)
        if counts is None:
            sys.exit(gate_failure(counts))
        if counts[1:] == (0, 0, 0):
            break
        # only in-place updates can be cosmetic: an add or a destroy never is
        if counts[1] or counts[3] or attempt == 2:
            sys.exit(gate_failure(counts))
        fixes, blockers = _json_reorder_fixes()
        for blocker in blockers:
            print(f"BLOCKED: {blocker}")
        if blockers or not fixes:
            sys.exit(gate_failure(counts))
        if not _rewrite_json_attributes(fixes):
            sys.exit(gate_failure(counts))
    else:
        sys.exit(gate_failure(counts))
    print("committing imports to state")
    returncode, output = _tf_stream(["apply", "-input=false", "-no-color", "fast.tfplan"])
    if returncode != 0:
        sys.exit("apply of the import plan failed (output above)")

    # leftover import blocks would error on the next plan once the resources
    # are already in state
    os.remove(out("imports.tf"))
    os.remove(out("fast.tfplan"))
    _flush_buffers_and_mapping(dropped)
    print(f"imported {written} resources, dropped {len(dropped)}")
    if dropped:
        by_type = {}
        for entry in dropped:
            by_type[entry["resource"]] = by_type.get(entry["resource"], 0) + 1
        for resource, count in sorted(by_type.items(), key=lambda x: -x[1]):
            print(f"WARN: dropped {count} {resource} resources — they are missing from config,"
                  " state and mapping.json")


if FAST_MODE:
    run_fast_import()

# temp1.tf only exists so `terraform import` can find the resource address; leaving it
# behind duplicates the last imported resource and breaks `terraform plan`
if os.path.exists(out("temp1.tf")):
    os.remove(out("temp1.tf"))

print(f"Done. Terraform config, state and mapping.json written to {os.path.abspath(OUTPUT_DIR)}/")
