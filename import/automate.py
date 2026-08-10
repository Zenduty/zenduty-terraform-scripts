from jinja2 import Environment, FileSystemLoader
from zenduty.apiV2.authentication.zenduty_credential import ZendutyCredential
from zenduty.apiV2.client import ZendutyClient, ZendutyClientRequestMethod
from zenduty.apiV2.teams import TeamsClient
import os
import shutil
import sys
import uuid
import subprocess
import re
import json

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Everything (generated .tf files, terraform state, mapping.json) is written here,
# making the folder a self-contained terraform workspace
OUTPUT_DIR = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("ZENDUTY_EXPORT_DIR", "export")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def out(path):
    return os.path.join(OUTPUT_DIR, path)


if not os.path.exists(out("main.tf")):
    shutil.copy(os.path.join(SCRIPT_DIR, "main.tf"), out("main.tf"))

subprocess.run("terraform init", shell=True, cwd=OUTPUT_DIR)
# export ZENDUTY_API_KEY
cred = ZendutyCredential()
client = ZendutyClient(credential=cred, use_https=True)

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


def add_mapping(unique_id, resource_name, flush=True):
    if not unique_id or not resource_name:
        return
    create_mapping[str(unique_id)] = resource_name
    if flush:
        write_mapping_file()


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
    # Append suffix if name already exists
    if check_string(out(file), name):
        name = f"{name}_{str(uuid.uuid4())[:4]}"
    return name


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


def parse_write_into_file(output, file, unique_id, name, line_assert=False):
    # remove id from output
    if not output:
        return
    clean_output = []
    for line_index, line in enumerate(output):
        # Remove ANSI escape codes for matching
        clean_line = re.sub(r"\x1b\[[0-9;]*m", "", line)
        # Skip lines containing sensitive or generated fields
        if re.search(r"\b(id|integration_key|webhook_url|unique_id|position|creation_date)\s*=", clean_line):
            if line_assert:
                # Only skip in the first 3 processed lines
                if line_index < 4:
                    continue
            else:
                # When not asserting, skip these fields everywhere
                continue
        clean_output.append(line)

    output = clean_output
    output = "\n".join(output)
    f = open(out(f"{file}.tf"), "a")
    f.write(ansi_escape.sub("", output))
    f.write("\n")
    f.close()
    add_mapping(unique_id, name, flush=True)


common_template = TEMPLATE_ENVIRONMENT.get_template("common.jinja2")


def safe_get(endpoint):
    # Some endpoints 404/403 depending on the account plan; skip them instead of aborting the run
    try:
        return client.execute(ZendutyClientRequestMethod.GET, endpoint, success_code=200) or []
    except Exception as e:
        print(f"WARN: skipping {endpoint}: {e}")
        return []


def import_and_write(display_name, resource, import_id, unique_id, line_assert=False):
    name = check_name(display_name, f"{resource}.tf")
    write_into_temp_file(name, common_template, resource)
    output = execute_terraform_import(name, resource, import_id, unique_id)
    parse_write_into_file(output, resource, unique_id, f"{resource}.{name}.id", line_assert=line_assert)
    return name


all_users = client.execute(ZendutyClientRequestMethod.GET, "/api/account/users/")

for user in all_users:
    if user["role"] == "1":
        continue

    username = user["user"]["username"]
    name = import_and_write(
        user["user"]["first_name"] + user["user"]["last_name"], "zenduty_user", username, username
    )

    for notification_rule in safe_get(f"/api/account/users/{username}/notification_rules/"):
        import_and_write(
            f"{name}_notification",
            "zenduty_notification_rules",
            f"{username}/{notification_rule['unique_id']}",
            notification_rule["unique_id"],
        )

for account_role in safe_get("/api/account/customroles/"):
    import_and_write(
        account_role["name"],
        "zenduty_account_role",
        account_role["unique_id"],
        account_role["unique_id"],
    )

all_teams = teams_client.list_teams()
for team in all_teams:
    for schedule in teams_client.get_schedule_client(team).get_all_schedules():
        import_and_write(
            schedule.name, "zenduty_schedules", f"{team.unique_id}/{schedule.unique_id}", schedule.unique_id
        )

    for escalation in teams_client.get_escalation_policy_client(team).get_all_policies():
        import_and_write(
            escalation.name, "zenduty_esp", f"{team.unique_id}/{escalation.unique_id}", escalation.unique_id
        )

    for sla in teams_client.get_sla_client(team).get_all_slas():
        import_and_write(sla.name, "zenduty_sla", f"{team.unique_id}/{sla.unique_id}", sla.unique_id)

    for incident_role in teams_client.get_incident_role_client(team).get_all_roles():
        import_and_write(
            incident_role.title, "zenduty_roles", f"{team.unique_id}/{incident_role.unique_id}", incident_role.unique_id
        )

    for team_tag in teams_client.get_tag_client(team).get_all_tags():
        import_and_write(team_tag.name, "zenduty_tags", f"{team.unique_id}/{team_tag.unique_id}", team_tag.unique_id)

    for priority in teams_client.get_priority_client(team).get_all_priorities():
        import_and_write(
            priority.name, "zenduty_priorities", f"{team.unique_id}/{priority.unique_id}", priority.unique_id
        )

    services_client = teams_client.get_service_client(team)
    for service in services_client.get_all_services():
        import_and_write(service.name, "zenduty_services", f"{team.unique_id}/{service.unique_id}", service.unique_id)

        integration_client = services_client.get_integration_client(service)
        for integration in integration_client.get_all_integrations():
            import_and_write(
                integration.name,
                "zenduty_integrations",
                f"{team.unique_id}/{service.unique_id}/{integration.unique_id}",
                integration.unique_id,
            )

            for alert_rule in safe_get(
                f"/api/account/teams/{team.unique_id}/services/{service.unique_id}/integrations/{integration.unique_id}/transformers/"  # noqa
            ):
                import_and_write(
                    alert_rule["description"],
                    "zenduty_alertrules",
                    f"{team.unique_id}/{service.unique_id}/{integration.unique_id}/{alert_rule['unique_id']}",
                    alert_rule["unique_id"],
                    line_assert=True,
                )

            for outgoing_rule in safe_get(
                f"/api/v2/account/teams/{team.unique_id}/services/{service.unique_id}/integrations/{integration.unique_id}/outgoingrules/"  # noqa
            ):
                import_and_write(
                    f"{integration.name}_outgoing",
                    "zenduty_outgoing_rules",
                    f"{team.unique_id}/{service.unique_id}/{integration.unique_id}/{outgoing_rule['unique_id']}",
                    outgoing_rule["unique_id"],
                )

    for member in safe_get(f"/api/account/teams/{team.unique_id}/members/"):
        member_user = member["user"]["username"] if isinstance(member.get("user"), dict) else member.get("user", "")
        import_and_write(
            f"{team.name}_{member_user}",
            "zenduty_member",
            f"{team.unique_id}/{member['unique_id']}",
            member["unique_id"],
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

    team_permissions = safe_get(f"/api/account/teams/{team.unique_id}/permissions/")
    if isinstance(team_permissions, dict) and team_permissions.get("account_permissions"):
        # dedupe key is prefixed so it doesn't collide with the team's own mapping entry
        import_and_write(
            f"{team.name}_permissions",
            "zenduty_team_permissions",
            team.unique_id,
            f"permissions-{team.unique_id}",
        )

    import_and_write(team.name, "zenduty_teams", team.unique_id, team.unique_id)

for router in safe_get("/api/v2/account/events/router/"):
    import_and_write(
        router["name"],
        "zenduty_globalrouter",
        router["unique_id"],
        router["unique_id"],
    )
    for routing_rule in safe_get(f"/api/v2/account/events/router/{router['unique_id']}/rulesets/"):
        import_and_write(
            routing_rule.get("name") or routing_rule["unique_id"],
            "zenduty_globalrouting_rule",
            f"{router['unique_id']}/{routing_rule['unique_id']}",
            routing_rule["unique_id"],
        )

# temp1.tf only exists so `terraform import` can find the resource address; leaving it
# behind duplicates the last imported resource and breaks `terraform plan`
if os.path.exists(out("temp1.tf")):
    os.remove(out("temp1.tf"))

print(f"Done. Terraform config, state and mapping.json written to {os.path.abspath(OUTPUT_DIR)}/")
