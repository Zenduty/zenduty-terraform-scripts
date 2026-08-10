# One read of every data source, pointed at the objects main.tf created.
# Validates that list/read endpoints return what was just written.

data "zenduty_teams" "a" {
  team_id    = zenduty_teams.a.id
  depends_on = [zenduty_teams.a]
}

data "zenduty_services" "main" {
  team_id    = zenduty_teams.a.id
  service_id = zenduty_services.main.id
}

data "zenduty_esp" "main" {
  team_id = zenduty_teams.a.id
  esp_id  = zenduty_esp.main.id
}

data "zenduty_schedules" "main" {
  team_id     = zenduty_teams.a.id
  schedule_id = zenduty_schedules.main.id
}

data "zenduty_sla" "main" {
  team_id    = zenduty_teams.a.id
  depends_on = [zenduty_sla.main]
}

data "zenduty_integrations" "main" {
  team_id        = zenduty_teams.a.id
  service_id     = zenduty_services.main.id
  integration_id = zenduty_integrations.main.id
}

data "zenduty_alertrules" "main" {
  team_id        = zenduty_teams.a.id
  service_id     = zenduty_services.main.id
  integration_id = zenduty_integrations.main.id
  depends_on     = [zenduty_alertrules.main]
}

data "zenduty_outgoing_rules" "main" {
  team_id        = zenduty_teams.a.id
  service_id     = zenduty_services.main.id
  integration_id = zenduty_integrations.outgoing.id
  depends_on     = [zenduty_outgoing_rules.main]
}

data "zenduty_globalrouter" "main" {
  router_id  = zenduty_globalrouter.main.id
  depends_on = [zenduty_globalrouter.main]
}

data "zenduty_global_routing_rules" "main" {
  router_id  = zenduty_globalrouter.main.id
  depends_on = [zenduty_globalrouting_rule.route, zenduty_globalrouting_rule.suppress]
}

data "zenduty_tags" "main" {
  team_id    = zenduty_teams.a.id
  depends_on = [zenduty_tags.main]
}

data "zenduty_priorities" "main" {
  team_id    = zenduty_teams.a.id
  depends_on = [zenduty_priorities.main]
}

data "zenduty_roles" "main" {
  team_id    = zenduty_teams.a.id
  depends_on = [zenduty_roles.main]
}

data "zenduty_members" "b" {
  team_id    = zenduty_teams.b.id
  depends_on = [zenduty_member.b]
}

data "zenduty_maintenance_window" "main" {
  team_id    = zenduty_teams.a.id
  depends_on = [zenduty_maintenance_window.main]
}

data "zenduty_task_templates" "main" {
  team_id    = zenduty_teams.a.id
  depends_on = [zenduty_task_templates.main]
}

data "zenduty_task_template_tasks" "main" {
  team_id          = zenduty_teams.a.id
  task_template_id = zenduty_task_templates.main.id
  depends_on       = [zenduty_task_template_tasks.main]
}

# Exercises the new pagination + server-side status filter.
data "zenduty_post_incident_tasks" "main" {
  team_id    = zenduty_teams.a.id
  depends_on = [zenduty_post_incident_tasks.main]
}

data "zenduty_incidents" "open" {
  status     = "-1" # open (triggered + acknowledged), new in this release
  limit      = 100
  depends_on = [zenduty_incidents.main]
}

data "zenduty_account_role" "main" {
  depends_on = [zenduty_account_role.main]
}

data "zenduty_team_permissions" "b" {
  team_id    = zenduty_teams.b.id
  depends_on = [zenduty_team_permissions.b]
}

data "zenduty_user" "tester" {
  email      = var.tester_email
  depends_on = [zenduty_user.tester]
}

data "zenduty_notification_rules" "owner" {
  count      = var.notification_username != "" ? 1 : 0
  username   = var.notification_username
  depends_on = [zenduty_notification_rules.owner]
}
