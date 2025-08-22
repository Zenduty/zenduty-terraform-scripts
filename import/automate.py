from jinja2 import Environment, FileSystemLoader
from zenduty.apiV2.authentication.zenduty_credential import ZendutyCredential
from zenduty.apiV2.client import ZendutyClient, ZendutyClientRequestMethod
from zenduty.apiV2.teams import TeamsClient
import os
import uuid
import subprocess
import re
import json

subprocess.run("terraform init", shell=True)
# export ZENDUTY_API_KEY
cred = ZendutyCredential()
client = ZendutyClient(credential=cred, use_https=True)

TEMPLATE_ENVIRONMENT = Environment(loader=FileSystemLoader("templates"))

teams_client = TeamsClient(client=client)

create_mapping = {}


def load_mapping_file(path="mapping.json"):
    global create_mapping
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


def write_mapping_file(path="mapping.json"):
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
    f = open("temp1.tf", "w")
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
    if check_string(file, name):
        name = f"{name}_{str(uuid.uuid4())[:4]}"
    return name


def execute_terraform_import(name, resource, parameters, unique_id):

    if str(unique_id) not in create_mapping:
        print(f"terraform import {resource}.{name} {parameters}")
        subprocess.run(f"terraform import {resource}.{name} {parameters}", shell=True)
        run = subprocess.run(f"terraform state show {resource}.{name}", shell=True, capture_output=True)
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
        if re.search(r"\b(id|integration_key|webhook_url|unique_id)\s*=", clean_line):
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
    f = open(f"{file}.tf", "a")
    f.write(ansi_escape.sub("", output))
    f.write("\n")
    f.close()
    add_mapping(unique_id, name, flush=True)


common_template = TEMPLATE_ENVIRONMENT.get_template("common.jinja2")

all_users = client.execute(ZendutyClientRequestMethod.GET, "/api/account/users/")

for user in all_users:
    if user["role"] == "1":
        continue

    name = check_name(user["user"]["first_name"] + user["user"]["last_name"], "zenduty_user.tf")
    write_into_temp_file(name, common_template, "zenduty_user")
    output = execute_terraform_import(name, "zenduty_user", user["user"]["username"], user["user"]["username"])
    parse_write_into_file(output, "zenduty_user", user["user"]["username"], f"zenduty_user.{name}.id")

all_teams = teams_client.list_teams()
for team in all_teams:
    schedule_client = teams_client.get_schedule_client(team)
    all_schedules = schedule_client.get_all_schedules()

    for schedule in all_schedules:
        name = check_name(schedule.name, "zenduty_schedules.tf")
        write_into_temp_file(name, common_template, "zenduty_schedules")
        output = execute_terraform_import(
            name, "zenduty_schedules", f"{team.unique_id}/{schedule.unique_id}", schedule.unique_id
        )
        parse_write_into_file(output, "zenduty_schedules", schedule.unique_id, f"zenduty_schedules.{name}.id")

    escalation_client = teams_client.get_escalation_policy_client(team)
    all_escalations = escalation_client.get_all_policies()
    for escalation in all_escalations:
        name = check_name(escalation.name, "zenduty_esp.tf")
        write_into_temp_file(name, common_template, "zenduty_esp")
        output = execute_terraform_import(
            name, "zenduty_esp", f"{team.unique_id}/{escalation.unique_id}", escalation.unique_id
        )
        parse_write_into_file(output, "zenduty_esp", escalation.unique_id, f"zenduty_esp.{name}.id")

    sla_client = teams_client.get_sla_client(team)
    all_slas = sla_client.get_all_slas()
    for sla in all_slas:
        name = check_name(sla.name, "zenduty_sla.tf")
        write_into_temp_file(name, common_template, "zenduty_sla")
        output = execute_terraform_import(name, "zenduty_sla", f"{team.unique_id}/{sla.unique_id}", sla.unique_id)
        parse_write_into_file(output, "zenduty_sla", sla.unique_id, f"zenduty_sla.{name}.id")

    incident_roles_client = teams_client.get_incident_role_client(team)
    all_incident_roles = incident_roles_client.get_all_roles()
    for incident_role in all_incident_roles:
        name = check_name(incident_role.title, "zenduty_roles.tf")
        write_into_temp_file(name, common_template, "zenduty_roles")
        output = execute_terraform_import(
            name, "zenduty_roles", f"{team.unique_id}/{incident_role.unique_id}", incident_role.unique_id
        )
        parse_write_into_file(output, "zenduty_roles", incident_role.unique_id, f"zenduty_roles.{name}.id")

    team_tag_client = teams_client.get_tag_client(team)
    all_team_tags = team_tag_client.get_all_tags()
    for team_tag in all_team_tags:
        name = check_name(team_tag.name, "zenduty_tags.tf")
        write_into_temp_file(name, common_template, "zenduty_tags")
        output = execute_terraform_import(
            name, "zenduty_tags", f"{team.unique_id}/{team_tag.unique_id}", team_tag.unique_id
        )
        parse_write_into_file(output, "zenduty_tags", team_tag.unique_id, f"zenduty_tags.{name}.id")

    priority_client = teams_client.get_priority_client(team)
    all_priorities = priority_client.get_all_priorities()
    for priority in all_priorities:
        name = check_name(priority.name, "zenduty_priorities.tf")
        write_into_temp_file(name, common_template, "zenduty_priorities")
        output = execute_terraform_import(
            name, "zenduty_priorities", f"{team.unique_id}/{priority.unique_id}", priority.unique_id
        )
        parse_write_into_file(output, "zenduty_priorities", priority.unique_id, f"zenduty_priorities.{name}.id")

    services_client = teams_client.get_service_client(team)
    all_services = services_client.get_all_services()
    for service in all_services:

        name = check_name(service.name, "zenduty_services.tf")
        write_into_temp_file(name, common_template, "zenduty_services")
        output = execute_terraform_import(
            name, "zenduty_services", f"{team.unique_id}/{service.unique_id}", service.unique_id
        )
        parse_write_into_file(output, "zenduty_services", service.unique_id, f"zenduty_services.{name}.id")

        integration_client = services_client.get_integration_client(service)
        all_integrations = integration_client.get_all_integrations()
        for integration in all_integrations:
            name = check_name(integration.name, "zenduty_integrations.tf")
            write_into_temp_file(name, common_template, "zenduty_integrations")
            output = execute_terraform_import(
                name,
                "zenduty_integrations",
                f"{team.unique_id}/{service.unique_id}/{integration.unique_id}",
                integration.unique_id,
            )
            parse_write_into_file(
                output, "zenduty_integrations", integration.unique_id, f"zenduty_integrations.{name}.id"
            )

            all_alert_rules = integration_client._client.execute(
                method=ZendutyClientRequestMethod.GET,
                endpoint=f"/api/account/teams/{team.unique_id}/services/{service.unique_id}/integrations/{integration.unique_id}/transformers/",  # noqa
                success_code=200,
            )
            if not all_alert_rules:
                continue
            for alert_rule in all_alert_rules:
                name = check_name(alert_rule["description"], "zenduty_alertrules.tf")
                write_into_temp_file(name, common_template, "zenduty_alertrules")
                output = execute_terraform_import(
                    name,
                    "zenduty_alertrules",
                    f"{team.unique_id}/{service.unique_id}/{integration.unique_id}/{alert_rule['unique_id']}",  # noqa
                    alert_rule["unique_id"],
                )
                parse_write_into_file(
                    output,
                    "zenduty_alertrules",
                    alert_rule["unique_id"],
                    f"zenduty_alertrules.{name}.id",
                    line_assert=True,
                )

    name = check_name(team.name, "zenduty_teams.tf")
    write_into_temp_file(name, common_template, "zenduty_teams")
    output = execute_terraform_import(name, "zenduty_teams", team.unique_id, team.unique_id)
    parse_write_into_file(output, "zenduty_teams", team.unique_id, f"zenduty_teams.{name}.id")
