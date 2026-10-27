import glob
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
        # Replace exactly-quoted occurrences → unquoted Terraform reference
        content = re.sub(rf'"{re.escape(old)}"', new, content)

        # Remaining occurrences are either bare tokens or embedded in a larger
        # string (e.g. a comma-joined tag list: "uuid1,uuid2"). Inside a string
        # the reference must be interpolated (${ref}), or Terraform treats it
        # as literal text.
        def repl(m, new=new):
            line_start = m.string.rfind("\n", 0, m.start()) + 1
            quotes = len(re.findall(r'(?<!\\)"', m.string[line_start : m.start()]))
            return f"${{{new}}}" if quotes % 2 else new

        content = re.sub(rf"\b{re.escape(old)}\b", repl, content)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)


# every generated config file: hand-listing them meant each new resource type
# silently kept its raw UUIDs until someone remembered to add it here
for path in sorted(glob.glob(os.path.join(OUTPUT_DIR, "zenduty_*.tf"))):
    direct_replace_ids(path, os.path.join(OUTPUT_DIR, "mapping.json"))
