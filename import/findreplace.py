import json
import os
import re
import sys

# Must match the folder automate.py exported into
OUTPUT_DIR = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("ZENDUTY_EXPORT_DIR", "export")

# silently no-opping on a wrong folder hides real mistakes (e.g. running
# without the folder argument after exporting somewhere else)
if not os.path.isdir(OUTPUT_DIR):
    sys.exit(f"export folder '{OUTPUT_DIR}' does not exist — pass the folder automate.py exported into")
if not os.path.exists(os.path.join(OUTPUT_DIR, "mapping.json")):
    sys.exit(f"no mapping.json in '{OUTPUT_DIR}' — run automate.py against this folder first")


def direct_replace_ids(file_path: str, mapping_path: str) -> None:
    if not (os.path.exists(file_path) and os.path.exists(mapping_path)):
        return

    with open(mapping_path, "r", encoding="utf-8") as f:
        mapping = json.load(f)
        if not isinstance(mapping, dict):
            return

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Sort keys by length to avoid partial match issues
    for old, new in sorted(mapping.items(), key=lambda x: -len(x[0])):
        # Replace quoted occurrences → unquoted Terraform reference
        content = re.sub(rf'"{re.escape(old)}"', new, content)
        # Replace unquoted occurrences (token boundary) → Terraform reference
        content = re.sub(rf"\b{re.escape(old)}\b", new, content)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)


files = [
    "zenduty_user.tf",
    "zenduty_schedules.tf",
    "zenduty_esp.tf",
    "zenduty_priorities.tf",
    "zenduty_tags.tf",
    "zenduty_alertrules.tf",
    "zenduty_integrations.tf",
    "zenduty_services.tf",
    "zenduty_sla.tf",
    "zenduty_teams.tf",
    "zenduty_roles.tf",
    "zenduty_member.tf",
    "zenduty_maintenance_window.tf",
    "zenduty_post_incident_tasks.tf",
    "zenduty_task_templates.tf",
    "zenduty_task_template_tasks.tf",
    "zenduty_team_permissions.tf",
    "zenduty_outgoing_rules.tf",
    "zenduty_notification_rules.tf",
    "zenduty_usercontact.tf",
    "zenduty_account_role.tf",
    "zenduty_globalrouter.tf",
    "zenduty_globalrouting_rule.tf",
]

for file in files:
    direct_replace_ids(os.path.join(OUTPUT_DIR, file), os.path.join(OUTPUT_DIR, "mapping.json"))
