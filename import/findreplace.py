import json
import os
import re


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
    "zenduty_alertrules.tf",
]

for file in files:
    direct_replace_ids(file, "mapping.json")
