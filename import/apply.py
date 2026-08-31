"""Apply an exported workspace to a target Zenduty instance.

Creating a team also creates things on the backend: a "Default Escalation
Policy", an "Incident Commander" incident role, and a membership for the API
user. The export contains the source account's copies of those objects, so a
plain `terraform apply` collides with the ones the target just auto-created:
role creation fails with "incident role with the name already exists" (role
titles are unique per team), the default escalation policy is silently
duplicated, and re-creating the auto-added membership fails with "User
already exists" wherever the export also contains a membership for the API
user.

A faithful export can also contain states the current create APIs no longer
accept — stored data that predates today's validation (escalation policy rules
whose targets were deleted, schedule layers naming users who left the team,
alert-rule actions pointing at deleted objects, HTML in user names, collation
fields the API would reject). Those are rewritten in place first (see
COMPAT FIXUPS below), each printing what it changed.

This script wraps the apply in three steps:

1. apply only the zenduty_teams resources, letting the backend create the
   default objects as a side effect;
2. bind each auto-created object to its exported counterpart (matched per
   team by the default name; memberships by the member's email) with an
   `import` block, so the final apply
   updates it in place instead of creating a duplicate; auto-created objects
   with no exported counterpart — the source team renamed or deleted theirs —
   are deleted via the API to keep the target faithful to the source, except
   for the default escalation policy, which the backend refuses to delete
   ("You cannot delete a default escalation policy"): that one is bound to
   another exported policy of the same team instead, so the apply renames it
   in place rather than leaving it behind next to a freshly created one;
3. run the full apply (retried once serially: parallel invites can exhaust
   the backend's row locks, surfacing as 500s with MySQL 1205 lock waits).

Re-running after a partial failure is safe: teams already in state are
skipped, import blocks are only generated for objects terraform doesn't
manage yet, and deletes only target objects that still exist unmatched.

Usage: python apply.py <export-folder>

Run it after findreplace.py, with the provider block in <export-folder>/main.tf
pointing at the *target* instance (base_url + token) — the script reads the
target's address and credentials from there (ZENDUTY_TARGET_BASE_URL /
ZENDUTY_TARGET_API_KEY override it).
"""

import json
import os
import re
import subprocess
import sys

import requests

OUTPUT_DIR = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("ZENDUTY_EXPORT_DIR", "export")
ADOPT_FILE = "adopt_defaults.tf"

# What team creation auto-creates on the backend, and how to recognise the
# exported counterpart in the generated config
ADOPTIONS = [
    {
        "resource": "zenduty_roles",
        "endpoint": "roles",
        "team_attr": "team",
        "name_attr": "title",
        "default_name": "Incident Commander",
    },
    {
        "resource": "zenduty_esp",
        "endpoint": "escalation_policies",
        "team_attr": "team_id",
        "name_attr": "name",
        "default_name": "Default Escalation Policy",
        # the backend rejects DELETE on a team's default policy, so an
        # unmatched one is adopted by another exported policy instead
        "undeletable": True,
    },
]


def out(path):
    return os.path.join(OUTPUT_DIR, path)


def read_provider_config():
    base_url = os.environ.get("ZENDUTY_TARGET_BASE_URL", "http://localhost:8000")
    token = os.environ.get("ZENDUTY_TARGET_API_KEY")
    if base_url and token:
        return base_url.rstrip("/"), token
    with open(out("main.tf"), encoding="utf-8") as f:
        text = "".join(line for line in f if not line.lstrip().startswith("#"))
    if not base_url:
        match = re.search(r'base_url\s*=\s*"([^"]+)"', text)
        base_url = match.group(1) if match else "https://www.zenduty.com"
    if not token:
        match = re.search(r'token\s*=\s*"([^"]+)"', text)
        if not match:
            sys.exit(
                "no token found in the provider block of main.tf — apply.py needs the"
                " target credentials terraform itself will use (or set"
                " ZENDUTY_TARGET_API_KEY / ZENDUTY_TARGET_BASE_URL)"
            )
        token = match.group(1)
    return base_url.rstrip("/"), token


def tf_stream(args):
    # stream terraform's output live so long applies don't look stuck
    process = subprocess.Popen(
        ["terraform", *args],
        cwd=OUTPUT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    for line in process.stdout:
        print(line, end="", flush=True)
    process.wait()
    return process.returncode


def state_resources():
    run = subprocess.run(["terraform", "show", "-json"], cwd=OUTPUT_DIR, capture_output=True, text=True)
    if run.returncode != 0:
        sys.exit(f"terraform show failed:\n{run.stderr}")
    data = json.loads(run.stdout or "{}")
    resources = ((data.get("values") or {}).get("root_module") or {}).get("resources") or []
    return [r for r in resources if r.get("mode") == "managed"]


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


def parse_resource_blocks(path, resource_type):
    """Every `resource "<type>" "<name>"` block with its top-level attributes.

    Only depth-1 attribute lines are collected, so a `name` inside a nested
    rules/targets block can't shadow the resource's own.
    """
    if not os.path.exists(path):
        return []
    blocks = []
    current = None
    depth = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            start_depth = depth
            depth = _advance_depth(line, depth)
            if current is None:
                if start_depth == 0:
                    match = re.match(rf'^resource\s+"{resource_type}"\s+"([^"]+)"', line)
                    if match:
                        current = {"name": match.group(1), "attrs": {}}
                continue
            if start_depth == 1:
                attr = re.match(r"^\s*([A-Za-z0-9_]+)\s*=\s*(.+?)\s*$", line)
                if attr and attr.group(1) not in current["attrs"]:
                    current["attrs"][attr.group(1)] = attr.group(2)
            if depth == 0:
                blocks.append(current)
                current = None
    return blocks


def api_get(base_url, token, path):
    response = requests.get(base_url + path, headers={"Authorization": f"Token {token}"}, timeout=30)
    if not response.ok:
        sys.exit(f"GET {path} failed {response.status_code}: {response.text[:500]}")
    data = response.json()
    if isinstance(data, dict):
        return data.get("results") or []
    return data or []


def api_delete(base_url, token, path):
    response = requests.delete(base_url + path, headers={"Authorization": f"Token {token}"}, timeout=30)
    # 404 means it's already gone (e.g. a re-run after a partial failure)
    if not response.ok and response.status_code != 404:
        sys.exit(f"DELETE {path} failed {response.status_code}: {response.text[:500]}")


def exported_team_blocks(spec, state_addresses):
    """Adoption candidates per team address, as `(address, is_default)` pairs.

    Only blocks not yet in state are candidates — anything already managed was
    adopted or created on a previous run. `is_default` marks the exported
    counterpart of the auto-created object (same name); the others are only
    used as a fallback for objects the backend refuses to delete. File order
    is preserved, so the fallback pick is deterministic across runs.
    """
    candidates = {}
    for block in parse_resource_blocks(out(f'{spec["resource"]}.tf'), spec["resource"]):
        address = f'{spec["resource"]}.{block["name"]}'
        if address in state_addresses:
            continue
        team_ref = re.fullmatch(
            r"zenduty_teams\.([A-Za-z0-9_]+)\.id", block["attrs"].get(spec["team_attr"], "")
        )
        if not team_ref:
            continue
        is_default = block["attrs"].get(spec["name_attr"]) == f'"{spec["default_name"]}"'
        candidates.setdefault(f"zenduty_teams.{team_ref.group(1)}", []).append((address, is_default))
    return candidates


def take_candidate(candidates, team_address, spec):
    """Claim the exported block the auto-created object should bind to.

    Returns `(address, matched_by_name)`, or `(None, False)` when nothing is
    left to bind. The exported object of the same name is the natural
    counterpart; when the source renamed or deleted it, an undeletable object
    borrows any other exported block of the same kind on that team so the
    apply renames it in place instead of adding a duplicate.
    """
    waiting = candidates.get(team_address) or []
    for index, (address, is_default) in enumerate(waiting):
        if is_default:
            waiting.pop(index)
            return address, True
    if spec.get("undeletable") and waiting:
        return waiting.pop(0)[0], False
    return None, False


def _exported_user_emails():
    """Map each user reference expression the export uses to an email address.

    Members reference created users as `zenduty_user.<n>.id` and pre-existing
    (data-source) users as `data.zenduty_user.<n>.users[0].username`; both
    blocks carry the email, the only identifier shared with the target.
    """
    emails = {}
    for block in parse_resource_blocks(out("zenduty_user.tf"), "zenduty_user"):
        email = block["attrs"].get("email", "").strip('"')
        if email:
            emails[f'zenduty_user.{block["name"]}.id'] = email
    if os.path.exists(out("zenduty_user.tf")):
        with open(out("zenduty_user.tf"), encoding="utf-8") as f:
            text = f.read()
        for match in re.finditer(
            r'data\s+"zenduty_user"\s+"([A-Za-z0-9_]+)"\s*\{[^}]*?email\s*=\s*"([^"]+)"', text
        ):
            emails[f"data.zenduty_user.{match.group(1)}.users[0].username"] = match.group(2)
    return emails


def adopt_existing_memberships(base_url, token, team_ids, state_addresses):
    """Import ids for exported memberships that already exist on the target.

    Team creation auto-adds the API user as a member, and the backend rejects
    re-adding an existing member ("User already exists"), so an exported
    membership for that user can never be created. Bind it to the existing
    membership instead — matched by email — so the final apply updates it in
    place (e.g. its role) rather than failing to create a duplicate.
    """
    emails = _exported_user_emails()
    pending = {}
    for block in parse_resource_blocks(out("zenduty_member.tf"), "zenduty_member"):
        address = f'zenduty_member.{block["name"]}'
        if address in state_addresses:
            continue
        team_ref = re.fullmatch(r"zenduty_teams\.([A-Za-z0-9_]+)\.id", block["attrs"].get("team", ""))
        email = emails.get(block["attrs"].get("user", ""))
        if team_ref and email:
            pending.setdefault(f"zenduty_teams.{team_ref.group(1)}", []).append((address, email))

    imports = []
    for team_address, members in pending.items():
        team_id = team_ids.get(team_address)
        if not team_id:
            continue
        existing = {}
        for obj in api_get(base_url, token, f"/api/account/teams/{team_id}/members/"):
            user_email = (obj.get("user") or {}).get("email")
            if user_email:
                existing[user_email] = obj["unique_id"]
        for address, email in members:
            membership_id = existing.get(email)
            if membership_id:
                print(f"adopting existing membership on {team_address} as {address}")
                imports.append((address, f"{team_id}/{membership_id}"))
    return imports


# ---------------------------------------------------------------------------
# COMPAT FIXUPS
#
# The source database can hold states the current create APIs reject, so a
# faithful export would fail to apply. Each fixup below rewrites the exported
# config into the nearest state the API accepts, mirroring the exact backend
# validation it works around, and prints every change. All are idempotent, so
# re-running apply.py on a fixed folder changes nothing.
# ---------------------------------------------------------------------------


def _bracket_balance(line):
    # net [ ] balance, ignoring brackets inside quoted strings
    balance = 0
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
            if ch == "[":
                balance += 1
            elif ch == "]":
                balance -= 1
    return balance


def split_segments(block_lines):
    """Split a `... { ... }` block's body into its direct children.

    Returns (kind, name, lines) tuples: "block" for nested `name { ... }`
    blocks, "attr" for `name = ...` (multi-line lists included), "raw" for
    everything else (header, footer, comments). Concatenating the lines in
    order reproduces the input, so callers can drop or rewrite segments.
    """
    segments = []
    depth = 0
    index = 0
    total = len(block_lines)
    while index < total:
        line = block_lines[index]
        start_depth = depth
        depth = _advance_depth(line, depth)
        index += 1
        if start_depth != 1:
            segments.append(("raw", None, [line]))
            continue
        block_match = re.match(r"^\s*([A-Za-z0-9_]+)\s*\{", line)
        attr_match = re.match(r"^\s*([A-Za-z0-9_]+)\s*=", line)
        if block_match and depth > start_depth:
            sub = [line]
            while index < total and depth > 1:
                sub.append(block_lines[index])
                depth = _advance_depth(block_lines[index], depth)
                index += 1
            segments.append(("block", block_match.group(1), sub))
        elif attr_match:
            sub = [line]
            balance = _bracket_balance(line)
            while index < total and balance > 0:
                sub.append(block_lines[index])
                balance += _bracket_balance(block_lines[index])
                depth = _advance_depth(block_lines[index], depth)
                index += 1
            segments.append(("attr", attr_match.group(1), sub))
        else:
            segments.append(("raw", None, [line]))
    return segments


def join_segments(segments):
    return [line for _, _, seg_lines in segments for line in seg_lines]


def transform_resources(path, resource_type, transform):
    """Rewrite every `resource "<type>" "<name>"` block via transform(name, lines)."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        source_lines = f.read().splitlines()
    result = []
    changed = False
    index = 0
    total = len(source_lines)
    while index < total:
        line = source_lines[index]
        match = re.match(rf'^resource\s+"{resource_type}"\s+"([^"]+)"', line)
        if not match:
            result.append(line)
            index += 1
            continue
        depth = 0
        block = []
        while index < total:
            depth = _advance_depth(source_lines[index], depth)
            block.append(source_lines[index])
            index += 1
            if depth == 0:
                break
        new_block = transform(match.group(1), block)
        if new_block != block:
            changed = True
        result.extend(new_block)
    if changed:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(result) + "\n")


def _segment_attrs(segments):
    attrs = {}
    for kind, name, seg_lines in segments:
        if kind == "attr" and name not in attrs:
            match = re.match(r"^\s*[A-Za-z0-9_]+\s*=\s*(.+?)\s*$", seg_lines[0])
            attrs[name] = match.group(1) if match else ""
    return attrs


def _attr_present(attrs, name):
    return attrs.get(name) not in (None, "null", '""')


def fixup_esp_untargeted_rules():
    # the EP create API rejects any rule whose targets are gone (deleted
    # users/schedules): "Cannot create EP without any rules". automate.py
    # already skips EPs with no targeted rules at all; this strips the
    # leftover empty rules from the EPs that kept at least one real rule.
    def transform(name, lines):
        segments = split_segments(lines)
        kept = []
        removed = 0
        for segment in segments:
            kind, seg_name, seg_lines = segment
            if kind == "block" and seg_name == "rules" and not any(
                re.match(r"^\s*targets\s*\{", seg_line) for seg_line in seg_lines
            ):
                removed += 1
                continue
            kept.append(segment)
        if not removed:
            return lines
        print(f"fixup: zenduty_esp.{name}: dropped {removed} rules block(s) with no targets")
        return join_segments(kept)

    transform_resources(out("zenduty_esp.tf"), "zenduty_esp", transform)


# mirrors the provider's AlertRuleAction validation: these action types take
# their payload from a dedicated attribute (legacy "value" also accepted) …
ALERT_ACTION_TYPE_ATTR = {
    4: "escalation_policy",
    5: "schedule",
    6: "assign_to",
    14: "sla",
    15: "team_priority",
    16: "task_template",
}
# … and these take no payload at all (3 suppress, 18 change entity id hash)
ALERT_ACTION_TYPES_WITHOUT_VALUE = {3, 18}


def fixup_alertrule_broken_actions():
    # an action whose target object was deleted survives in the source DB with
    # a null payload, which both the provider and the create API refuse
    def transform(name, lines):
        segments = split_segments(lines)
        kept = []
        dropped = []
        for segment in segments:
            kind, seg_name, seg_lines = segment
            if kind == "block" and seg_name == "actions":
                attrs = _segment_attrs(split_segments(seg_lines))
                try:
                    action_type = int(attrs.get("action_type", ""))
                except ValueError:
                    action_type = None
                if action_type is not None and action_type not in ALERT_ACTION_TYPES_WITHOUT_VALUE:
                    if action_type == 11:  # assign incident role: key is the role id
                        payload_attrs = ("key",)
                    else:
                        payload_attrs = ("value", ALERT_ACTION_TYPE_ATTR.get(action_type, "value"))
                    if not any(_attr_present(attrs, attr) for attr in payload_attrs):
                        dropped.append(action_type)
                        continue
            kept.append(segment)
        if not dropped:
            return lines
        print(
            f"fixup: zenduty_alertrules.{name}: dropped {len(dropped)} action(s) with no"
            f" payload (action_type {', '.join(map(str, dropped))}: the referenced object"
            " no longer exists on the source)"
        )
        return join_segments(kept)

    transform_resources(out("zenduty_alertrules.tf"), "zenduty_alertrules", transform)


def _team_memberships():
    members = {}
    for block in parse_resource_blocks(out("zenduty_member.tf"), "zenduty_member"):
        team = block["attrs"].get("team")
        user = block["attrs"].get("user")
        if team and user:
            members.setdefault(team, set()).add(user)
    return members


def fixup_schedule_nonmember_users():
    # the schedule create API rejects layer/override users who are not team
    # members ("is not part of team …"); the source keeps users in schedules
    # after they leave the team, so follow the API's own advice and drop them
    members = _team_memberships()

    def transform(name, lines):
        segments = split_segments(lines)
        membership = members.get(_segment_attrs(segments).get("team_id"))
        if membership is None:
            return lines  # team has no exported members; nothing to check against
        removed = []
        kept = []
        for segment in segments:
            kind, seg_name, seg_lines = segment
            if kind == "block" and seg_name == "layers":
                new_layer = []
                for layer_segment in split_segments(seg_lines):
                    layer_kind, layer_name, layer_lines = layer_segment
                    if layer_kind == "attr" and layer_name == "users":
                        text = " ".join(layer_lines)
                        match = re.search(r"users\s*=\s*\[(.*)\]", text)
                        users = [u.strip() for u in match.group(1).split(",") if u.strip()] if match else []
                        outsiders = [u for u in users if u not in membership]
                        if outsiders:
                            removed.extend(outsiders)
                            remaining = ", ".join(u for u in users if u in membership)
                            indent = re.match(r"^\s*", layer_lines[0]).group(0)
                            layer_lines = [f"{indent}users = [{remaining}]"]
                    new_layer.append((layer_kind, layer_name, layer_lines))
                kept.append((kind, seg_name, join_segments(new_layer)))
            elif kind == "block" and seg_name == "overrides":
                override_user = _segment_attrs(split_segments(seg_lines)).get("user")
                if override_user and override_user != "null" and override_user not in membership:
                    removed.append(override_user)
                    continue  # an override without its user is meaningless: drop the block
                kept.append(segment)
            else:
                kept.append(segment)
        if not removed:
            return lines
        print(
            f"fixup: zenduty_schedules.{name}: removed non-member user(s) from layers/overrides:"
            f" {', '.join(sorted(set(removed)))}"
        )
        return join_segments(kept)

    transform_resources(out("zenduty_schedules.tf"), "zenduty_schedules", transform)


def _default_eps_by_team():
    defaults = {}
    for block in parse_resource_blocks(out("zenduty_esp.tf"), "zenduty_esp"):
        if block["attrs"].get("name") == '"Default Escalation Policy"':
            team = block["attrs"].get("team_id")
            if team:
                defaults.setdefault(team, f'zenduty_esp.{block["name"]}.id')
    return defaults


_RAW_UUID = re.compile(r'^"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"$')


def fixup_services():
    # collation semantics (mirrors the backend + provider validation):
    # 0 off, 1 time-based, 3 content-based; collation_time must be 1-1440 when
    # collation is on and absent when off. The runtime only reads the time for
    # type 1 (a time of 0 means the collation never fires) and ignores it for
    # type 3, so 1/0 maps to off and 3/0 gets the minimum valid window.
    #
    # a still-quoted UUID in escalation_policy/sla/task_template is a source
    # reference findreplace.py could not map — the object was deleted on the
    # source or skipped at export. Services need an EP, so those fall back to
    # the team's default; sla/task_template are optional and just drop.
    default_eps = _default_eps_by_team()

    def transform(name, lines):
        segments = split_segments(lines)
        attrs = _segment_attrs(segments)
        try:
            collation = int(attrs.get("collation", "0"))
        except ValueError:
            collation = 0
        try:
            collation_time = int(attrs.get("collation_time", "0"))
        except ValueError:
            collation_time = 0
        kept = []
        changed = []
        for segment in segments:
            kind, seg_name, seg_lines = segment
            if kind != "attr":
                kept.append(segment)
                continue
            value = attrs.get(seg_name, "")
            if seg_name == "collation":
                if collation == 1 and collation_time == 0:
                    changed.append("dropped collation = 1 (time-based with no collation_time"
                                   " never fires, so this maps to off)")
                elif collation == 2:
                    changed.append("dropped collation = 2 (unused type the API rejects)")
                elif collation == 3 and collation_time == 0:
                    indent = re.match(r"^\s*", seg_lines[0]).group(0)
                    kept.append(segment)
                    kept.append(("attr", "collation_time", [f"{indent}collation_time = 1"]))
                    changed.append("added collation_time = 1 (content-based collation ignores"
                                   " the time, but the API requires 1-1440)")
                else:
                    kept.append(segment)
                continue
            if seg_name == "collation_time":
                if collation in (1, 3) and collation_time != 0:
                    kept.append(segment)  # valid: collation on with a real window
                elif collation_time == 0 and collation == 0:
                    kept.append(segment)  # harmless zero on an off service
                else:
                    changed.append(f"dropped collation_time = {collation_time}"
                                   " (meaningless for this collation setting)")
                continue
            if seg_name == "escalation_policy" and _RAW_UUID.match(value):
                fallback = default_eps.get(attrs.get("team_id"))
                if fallback:
                    line = re.sub(r"(escalation_policy\s*=\s*).*$", rf"\g<1>{fallback}", seg_lines[0])
                    kept.append(("attr", seg_name, [line]))
                    changed.append(f"escalation_policy {value} no longer exists on the source;"
                                   f" fell back to {fallback}")
                    continue
                changed.append(f"WARNING: escalation_policy {value} is unmapped and the team has"
                               " no Default Escalation Policy — the apply will fail on this service")
            if seg_name in ("sla", "task_template") and _RAW_UUID.match(value):
                changed.append(f"dropped {seg_name} = {value} (no longer exists on the source)")
                continue
            kept.append(segment)
        if not changed:
            return lines
        for message in changed:
            print(f"fixup: zenduty_services.{name}: {message}")
        return join_segments(kept)

    transform_resources(out("zenduty_services.tf"), "zenduty_services", transform)


# mirrors the backend's name_sanitizer: names with HTML tags or URLs are
# rejected on invite, and 64 chars is the hard cap
_NAME_HTML = re.compile(r"<[^>]+>")
_NAME_URL = re.compile(r"https?://|www\.", re.IGNORECASE)


def fixup_user_names():
    def transform(name, lines):
        segments = split_segments(lines)
        kept = []
        changed = False
        for segment in segments:
            kind, seg_name, seg_lines = segment
            if kind == "attr" and seg_name in ("first_name", "last_name"):
                match = re.match(r'^(\s*[A-Za-z0-9_]+\s*=\s*)"(.*)"(\s*)$', seg_lines[0])
                if match:
                    original = match.group(2)
                    sanitized = _NAME_HTML.sub("", original)
                    sanitized = _NAME_URL.sub("", sanitized)[:64]
                    if sanitized != original:
                        changed = True
                        print(
                            f"fixup: zenduty_user.{name}: sanitized {seg_name}"
                            f' "{original}" -> "{sanitized}" (the invite API rejects HTML/URLs)'
                        )
                        segment = (kind, seg_name, [f'{match.group(1)}"{sanitized}"{match.group(3)}'])
            kept.append(segment)
        return join_segments(kept) if changed else lines

    transform_resources(out("zenduty_user.tf"), "zenduty_user", transform)


def run_compat_fixups():
    fixup_esp_untargeted_rules()
    fixup_alertrule_broken_actions()
    fixup_schedule_nonmember_users()
    fixup_services()
    fixup_user_names()


def main():
    # regenerated from scratch below; a stale one would re-import adopted objects
    if os.path.exists(out(ADOPT_FILE)):
        os.remove(out(ADOPT_FILE))

    base_url, token = read_provider_config()
    print(f"target instance: {base_url}")

    run_compat_fixups()

    if subprocess.run(["terraform", "init"], cwd=OUTPUT_DIR).returncode != 0:
        sys.exit("terraform init failed")

    team_blocks = parse_resource_blocks(out("zenduty_teams.tf"), "zenduty_teams")
    if not team_blocks:
        sys.exit(f"no zenduty_teams resources found in {out('zenduty_teams.tf')} — is this an export folder?")

    parallelism = f"-parallelism={os.environ.get('ZENDUTY_TF_PARALLELISM', '10')}"

    state = state_resources()
    state_addresses = {r["address"] for r in state}
    team_addresses = [f'zenduty_teams.{b["name"]}' for b in team_blocks]
    missing = [a for a in team_addresses if a not in state_addresses]
    if missing:
        print(f"phase 1: creating {len(missing)} of {len(team_addresses)} teams")
        code = tf_stream(
            ["apply", "-input=false", "-auto-approve", "-no-color", parallelism]
            + [f"-target={a}" for a in team_addresses]
        )
        if code != 0:
            sys.exit("team apply failed (output above)")
        state = state_resources()
        state_addresses = {r["address"] for r in state}
    else:
        print("phase 1: all teams already in state")

    team_ids = {
        r["address"]: (r.get("values") or {}).get("id")
        for r in state
        if r["type"] == "zenduty_teams"
    }
    # ids of objects terraform already manages, so adopted/created ones are
    # never re-imported or deleted on a re-run
    managed_ids = {}
    for r in state:
        values = r.get("values") or {}
        for key in ("id", "unique_id"):
            if values.get(key):
                managed_ids.setdefault(r["type"], set()).add(values[key])

    imports = []
    deletes = []
    strays = []
    for spec in ADOPTIONS:
        candidates = exported_team_blocks(spec, state_addresses)
        for team_address, team_id in team_ids.items():
            if not team_id:
                continue
            for obj in api_get(base_url, token, f"/api/account/teams/{team_id}/{spec['endpoint']}/"):
                if obj.get(spec["name_attr"]) != spec["default_name"]:
                    continue
                if obj["unique_id"] in managed_ids.get(spec["resource"], set()):
                    continue
                address, matched_by_name = take_candidate(candidates, team_address, spec)
                if address and not matched_by_name:
                    print(
                        f'adopting auto-created "{spec["default_name"]}" on {team_address} as'
                        f" {address}: no exported counterpart, and the backend refuses to"
                        " delete it"
                    )
                if address:
                    imports.append((address, f'{team_id}/{obj["unique_id"]}'))
                elif spec.get("undeletable"):
                    strays.append((spec, team_address))
                else:
                    deletes.append((spec, team_address, team_id, obj["unique_id"]))

    imports.extend(adopt_existing_memberships(base_url, token, team_ids, state_addresses))

    for spec, team_address, team_id, obj_id in deletes:
        print(f'deleting auto-created "{spec["default_name"]}" on {team_address}: no exported counterpart')
        api_delete(base_url, token, f"/api/account/teams/{team_id}/{spec['endpoint']}/{obj_id}/")

    for spec, team_address in strays:
        print(
            f'keeping auto-created "{spec["default_name"]}" on {team_address}: the export has no'
            f" {spec['resource']} for this team and the backend refuses to delete it"
        )

    if imports:
        with open(out(ADOPT_FILE), "w", encoding="utf-8") as f:
            f.write(
                "# generated by apply.py — binds the objects team creation auto-created on the\n"
                "# target to their exported counterparts; removed automatically after the apply\n"
            )
            for address, import_id in imports:
                f.write(f'import {{\n  to = {address}\n  id = "{import_id}"\n}}\n')
        print(f"phase 2: adopting {len(imports)} auto-created defaults, then applying everything else")
    else:
        print("phase 2: nothing to adopt, applying everything else")

    try:
        code = tf_stream(["apply", "-input=false", "-auto-approve", "-no-color", parallelism])
        if code != 0:
            # concurrent invites contend on backend row locks (MySQL 1205 lock
            # wait timeouts surfacing as 500s); everything already created is
            # in state, so a serial pass just picks up the casualties
            print("apply failed — retrying what's left serially (-parallelism=1)")
            code = tf_stream(["apply", "-input=false", "-auto-approve", "-no-color", "-parallelism=1"])
    finally:
        # successful imports are already committed to state; a leftover import
        # block for a managed resource would error every later plan
        if os.path.exists(out(ADOPT_FILE)):
            os.remove(out(ADOPT_FILE))
    if code != 0:
        sys.exit("apply failed (output above); fix the reported errors and re-run apply.py")
    print(f"done — target now managed by {os.path.abspath(OUTPUT_DIR)}/")


if __name__ == "__main__":
    main()
