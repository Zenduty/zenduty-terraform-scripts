# Release smoke test: creates one of every resource the provider manages,
# chained together the way a real account would be. Object graph:
#
#   team_a ── user ── esp ── service ── integration ── alertrules / outgoing_rules
#      │        │      │        │
#      │        │      │        ├── maintenance_window
#      │        │      └── incident
#      │        ├── schedule (layer + override)
#      │        ├── sla (escalation responders)
#      │        └── assign_account_role
#      ├── tags / priorities / roles / task templates / post-incident tasks
#   team_b ── member, team_permissions        (separate team so adding the
#                                              tester as a member cannot
#                                              conflict with the invite)
#   globalrouter ── globalrouting_rule ── (routes to integration)

locals {
  # Valid react-query-builder tree taken from the backend's own test fixtures
  # (common/tests/test_setup.py) — matches check_alert_rules exactly.
  alert_rule_json = jsonencode({
    type = "group"
    id   = "9a99988a-0123-4456-b89a-b1607f326fd8"
    children1 = {
      "898aa9ba-0123-4456-b89a-b16a429f5d94" = {
        type = "rule"
        id   = "898aa9ba-0123-4456-b89a-b16a429f5d94"
        properties = {
          field           = "alert.incident_urgency"
          operator        = "select_equals"
          value           = ["high"]
          valueSrc        = ["value"]
          operatorOptions = null
          valueType       = ["select"]
        }
        path = ["9a99988a-0123-4456-b89a-b1607f326fd8", "898aa9ba-0123-4456-b89a-b16a429f5d94"]
      }
    }
    properties = { conjunction = "AND", not = false }
    path       = ["9a99988a-0123-4456-b89a-b1607f326fd8"]
  })

  # Event-router rule format, also from backend test fixtures.
  router_rule_json = jsonencode({
    type = "group"
    id   = "9a99988a-0123-4456-b89a-b1607f326fd8"
    children1 = {
      "898aa9ba-0123-4456-b89a-b16a429f5d94" = {
        type = "rule"
        id   = "898aa9ba-0123-4456-b89a-b16a429f5d94"
        properties = {
          field           = "alert.payload"
          operator        = "payloadsearch"
          value           = ["$.message", "smoke"]
          valueSrc        = ["value", "value"]
          operatorOptions = { ploperator = "equal" }
          valueType       = ["text", "text"]
        }
        path = ["9a99988a-0123-4456-b89a-b1607f326fd8", "898aa9ba-0123-4456-b89a-b16a429f5d94"]
      }
    }
    properties = { conjunction = "AND", not = false }
    path       = ["9a99988a-0123-4456-b89a-b1607f326fd8"]
  })

  # Outgoing-rule format (application/tests/test_outgoing_rule.py):
  # field1 object instead of field, value references a priority unique_id.
  outgoing_rule_json = jsonencode({
    type = "group"
    id   = "4b5f5420-9b34-4af8-8553-615164cce243"
    children1 = {
      "d248febc-c679-43ff-b7c2-6bc621a4cc5f" = {
        type = "rule"
        id   = "d248febc-c679-43ff-b7c2-6bc621a4cc5f"
        properties = {
          field1   = { field = "priority", regex = null }
          operator = "equal"
          value    = [zenduty_priorities.main.id]
          valueSrc = ["value"]
        }
        path = ["4b5f5420-9b34-4af8-8553-615164cce243", "d248febc-c679-43ff-b7c2-6bc621a4cc5f"]
      }
    }
    properties = { conjunction = "AND", not = false }
    path       = ["4b5f5420-9b34-4af8-8553-615164cce243"]
  })
}

# ---------------------------------------------------------------- teams/users

resource "zenduty_teams" "a" {
  name = "${var.name_prefix}-team-a"
}

resource "zenduty_teams" "b" {
  name = "${var.name_prefix}-team-b"
}

resource "zenduty_user" "tester" {
  team       = zenduty_teams.a.id
  email      = var.tester_email
  first_name = "Smoke"
  last_name  = "Tester"
  role       = 3
}

resource "zenduty_member" "b" {
  team = zenduty_teams.b.id
  user = zenduty_user.tester.id
  role = 2
}

resource "zenduty_roles" "main" {
  team        = zenduty_teams.a.id
  title       = "${var.name_prefix}-role"
  description = "Smoke-test incident role"
  rank        = 2
}

# The API expands the sent permissions with the read permissions they imply
# and stores the superset, so the config must list the full expanded set or
# every plan shows a diff.
resource "zenduty_account_role" "main" {
  name        = "${var.name_prefix}-account-role"
  description = "Smoke-test account role"
  permissions = [
    "escalation_policy_read",
    "incident_read",
    "incident_role_read",
    "member_read",
    "priority_read",
    "schedule_read",
    "service_read",
    "sla_read",
    "tag_read",
    "task_template_read",
    "team_read",
  ]
}

resource "zenduty_assign_account_role" "main" {
  account_role = zenduty_account_role.main.id
  username     = zenduty_user.tester.id
}

resource "zenduty_team_permissions" "b" {
  team_id = zenduty_teams.b.id
  # full expanded set, same reason as zenduty_account_role.main above
  permissions = [
    "escalation_policy_read",
    "incident_read",
    "incident_role_read",
    "member_read",
    "priority_read",
    "schedule_read",
    "service_read",
    "sla_read",
    "tag_read",
    "task_template_read",
    "team_read",
  ]
}

# --------------------------------------------------------------- on-call core

resource "zenduty_esp" "main" {
  name        = "${var.name_prefix}-esp"
  description = "Smoke-test escalation policy"
  team_id     = zenduty_teams.a.id

  # Round-robin assignment: a PUT without these used to reset the policy to
  # the defaults, so the plan-check stage must stay empty with them set.
  assignee_strategy                = 2
  notify_round_robin_assignee_only = true

  rules {
    delay = 0
    targets {
      target_type = 2 # user
      target_id   = zenduty_user.tester.id
    }
  }
}

resource "zenduty_services" "main" {
  name              = "${var.name_prefix}-service"
  team_id           = zenduty_teams.a.id
  escalation_policy = zenduty_esp.main.id
  description       = "Smoke-test service"
  summary           = "Smoke-test service"
  collation         = 0
}

resource "zenduty_integrations" "main" {
  team_id     = zenduty_teams.a.id
  service_id  = zenduty_services.main.id
  application = var.application_id
  name        = "${var.name_prefix}-integration"
  summary     = "Smoke-test integration"
}

resource "zenduty_schedules" "main" {
  name        = "${var.name_prefix}-schedule"
  description = "Smoke-test schedule"
  team_id     = zenduty_teams.a.id
  time_zone   = "UTC"

  layers {
    name                = "layer-1"
    shift_length        = 86400
    rotation_start_time = "2026-01-01 00:00"
    rotation_end_time   = "2027-01-01 00:00"
    users               = [zenduty_user.tester.id]
  }

  overrides {
    name       = "override-1"
    user       = zenduty_user.tester.id
    start_time = "2026-12-01 10:00"
    end_time   = "2026-12-01 18:00"
  }
}

resource "zenduty_sla" "main" {
  name        = "${var.name_prefix}-sla"
  description = "Smoke-test SLA"
  team_id     = zenduty_teams.a.id

  acknowledge_time = 10
  resolve_time     = 60

  escalations {
    time = 5
    type = 1 # acknowledge breach
    responders {
      user = zenduty_user.tester.id
    }
  }

  escalations {
    time = -10 # negative = notify before breach
    type = 2   # resolve breach
    responders {
      user = zenduty_user.tester.id
    }
  }
}

# ------------------------------------------------------- team-scoped catalog

resource "zenduty_priorities" "main" {
  team_id     = zenduty_teams.a.id
  name        = "${var.name_prefix}-priority"
  description = "Smoke-test priority"
  color       = "red"
}

resource "zenduty_tags" "main" {
  team_id = zenduty_teams.a.id
  name    = "${var.name_prefix}-tag"
  color   = "blue"
}

resource "zenduty_maintenance_window" "main" {
  team_id    = zenduty_teams.a.id
  name       = "${var.name_prefix}-maintenance"
  start_time = "2027-01-01 10:00"
  end_time   = "2027-01-01 12:00"
  timezone   = "UTC"
  services   = [zenduty_services.main.id]
}

resource "zenduty_task_templates" "main" {
  team_id = zenduty_teams.a.id
  name    = "${var.name_prefix}-task-template"
  summary = "Smoke-test task template"
}

resource "zenduty_task_template_tasks" "main" {
  team_id          = zenduty_teams.a.id
  task_template_id = zenduty_task_templates.main.id
  title            = "${var.name_prefix}-template-task"
  description      = "Smoke-test template task"
}

resource "zenduty_post_incident_tasks" "main" {
  team_id     = zenduty_teams.a.id
  title       = "${var.name_prefix}-pit"
  description = "Smoke-test post-incident task"
  status      = 0
  assigned_to = zenduty_user.tester.id
}

# ------------------------------------------------------------------- routing

resource "zenduty_alertrules" "main" {
  team_id        = zenduty_teams.a.id
  service_id     = zenduty_services.main.id
  integration_id = zenduty_integrations.main.id
  description    = "${var.name_prefix}-alertrule"
  rule_json      = local.alert_rule_json
  stop           = false
  rule_type      = 0

  # New in this release: structured conditions (position = list order).
  conditions {
    alert_condition_type = 2 # payload field
    alert_field          = "summary"
    pattern              = "CRITICAL"
  }

  conditions {
    alert_condition_type = 1 # alert type
    alert_field          = "alert_type"
    pattern              = "error"
  }

  # NOTE: value-style action on purpose — the typed attributes
  # (escalation_policy = ...) are diff-stable only when the state remembers
  # which spelling the config used, which an import round-trip cannot.
  actions {
    action_type = 2 # add note
    value       = "smoke-test note"
  }
}

# Outgoing rules only attach to integrations built from an OUTBOUND
# application (application_type == 1); the backend 400s with "Integration
# does not exists" for inbound ones like the generic API app.
resource "zenduty_integrations" "outgoing" {
  team_id     = zenduty_teams.a.id
  service_id  = zenduty_services.main.id
  application = var.outgoing_application_id
  name        = "${var.name_prefix}-outgoing-integration"
  summary     = "Smoke-test outgoing integration"
}

resource "zenduty_outgoing_rules" "main" {
  team_id        = zenduty_teams.a.id
  service_id     = zenduty_services.main.id
  integration_id = zenduty_integrations.outgoing.id
  rule_json      = local.outgoing_rule_json
  enabled        = true
}

resource "zenduty_globalrouter" "main" {
  name        = "${var.name_prefix}-router"
  description = "Smoke-test global event router"
}

resource "zenduty_globalrouting_rule" "route" {
  router_id = zenduty_globalrouter.main.id
  name      = "${var.name_prefix}-route"
  rule_json = local.router_rule_json

  actions {
    action_type = 0 # route to integration
    integration = zenduty_integrations.main.id
  }
}

resource "zenduty_globalrouting_rule" "suppress" {
  router_id = zenduty_globalrouter.main.id
  name      = "${var.name_prefix}-suppress"
  rule_json = local.router_rule_json

  actions {
    action_type = 1 # suppress — legitimately carries no payload
  }
}

# ----------------------------------------------------------------- incidents

resource "zenduty_incidents" "main" {
  title             = "${var.name_prefix}-incident"
  summary           = "Smoke-test incident"
  service           = zenduty_services.main.id
  escalation_policy = zenduty_esp.main.id
  user              = zenduty_user.tester.id
  urgency           = 0 # low on purpose: exercises the pointer-typed zero value
}

# ------------------------------------------------ optional: notification rule

data "zenduty_usercontact" "owner_email" {
  count        = var.notification_username != "" ? 1 : 0
  user_id      = var.notification_username
  contact_type = 1 # email
}

resource "zenduty_notification_rules" "owner" {
  count    = var.notification_username != "" ? 1 : 0
  username = var.notification_username
  contact  = data.zenduty_usercontact.owner_email[0].id
  urgency  = 1
  # rules are unique on (contact, delay, urgency) and every contact gets
  # default rules with template delays (0, ...) at signup — use an oddball
  # delay so this never collides with them
  delay = 47
}

# ----------------------------------------------- account-level configuration
# Resources added in provider 2.2.0.

resource "zenduty_heartbeat" "main" {
  service_id       = zenduty_services.main.id
  name             = "${var.name_prefix}-heartbeat"
  description      = "Smoke-test heartbeat"
  interval_seconds = 3600
  alert_message    = "Smoke-test heartbeat expired"
  alert_urgency    = 0
}

# Singleton: pins one setting to its backend default so the smoke test never
# flips account behaviour; destroy only forgets it.
resource "zenduty_account_setting" "main" {
  enable_regen_key = true
}

# No secrets on purpose: the API masks them on read, so an import round-trip
# with secrets would (correctly) show them as missing from state.
resource "zenduty_webhook_configuration" "main" {
  url    = "https://example.com/${var.name_prefix}"
  event  = ["incident_create", "incident_resolve"]
  filter = jsonencode({ smoke = true })
}

resource "zenduty_account_stakeholder_template" "main" {
  title    = "${var.name_prefix}-stakeholder"
  subject  = "Smoke-test stakeholder update"
  template = "Smoke-test body"
}

resource "zenduty_service_maintenance_window" "main" {
  team_id    = zenduty_teams.a.id
  service_id = zenduty_services.main.id
  name       = "${var.name_prefix}-service-maintenance"
  start_time = "2027-02-01 10:00"
  end_time   = "2027-02-01 12:00"
  timezone   = "Europe/Berlin"
}

resource "zenduty_maintenance_template" "main" {
  team_id   = zenduty_teams.a.id
  name      = "${var.name_prefix}-mt"
  rule_json = local.alert_rule_json
}

resource "zenduty_service_context" "main" {
  team_id     = zenduty_teams.a.id
  service_id  = zenduty_services.main.id
  integration = zenduty_integrations.main.id
}

resource "zenduty_incident_custom_field" "main" {
  field_type = 3 # select
  properties = jsonencode({
    type  = "string"
    title = "${var.name_prefix}-region"
    enum  = ["eu", "us"]
  })
}

# Deleting a type only deactivates it and the name stays reserved; the
# per-run prefix keeps names fresh.
resource "zenduty_incident_type" "main" {
  name = "${var.name_prefix}-type"

  form_toggles = {
    impacted_services = true
  }

  custom_fields {
    custom_field  = zenduty_incident_custom_field.main.id
    required      = true
    default_value = jsonencode("eu")
  }
}

# Forwarding to oneself is accepted by the API and keeps the test
# self-contained (the tester is the only user this config creates).
resource "zenduty_user_forwarding_rule" "main" {
  username  = zenduty_user.tester.id
  to_user   = zenduty_user.tester.id
  from_date = "2027-03-01 00:00"
  to_date   = "2027-03-02 00:00"
  time_zone = "UTC"
}

# Opt-in: the workflow endpoints accept session authentication only, so this
# fails with an API key until the backend grants token access.
resource "zenduty_workflow" "main" {
  count       = var.enable_workflow ? 1 : 0
  name        = "${var.name_prefix}-workflow"
  description = "Smoke-test workflow"
  is_draft    = false

  json_schema = jsonencode({
    nodes = [
      {
        id   = "trigger-1"
        type = "trigger"
        data = { name = "Incident Created", action_name = "incident_create", group = "Incident" }
      },
      {
        id   = "action-1"
        type = "action"
        data = { name = "Add Note", action_name = "add_note", group = "Incident", input = { note = "smoke" } }
      },
    ]
    edges = [
      { id = "trigger-1->action-1", source = "trigger-1", target = "action-1" },
    ]
  })

  teams {
    team_id     = zenduty_teams.a.id
    service_ids = [zenduty_services.main.id]
  }
}
