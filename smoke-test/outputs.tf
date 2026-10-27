# Map of resource address -> import ID, in each resource's documented import
# format. scripts/import_roundtrip.sh drives `terraform import` from this.
# zenduty_workflow is absent: it is opt-in (var.enable_workflow).
output "import_ids" {
  value = {
    "zenduty_teams.a"                           = zenduty_teams.a.id
    "zenduty_teams.b"                           = zenduty_teams.b.id
    "zenduty_user.tester"                       = zenduty_user.tester.id
    "zenduty_member.b"                          = "${zenduty_teams.b.id}/${zenduty_member.b.id}"
    "zenduty_roles.main"                        = "${zenduty_teams.a.id}/${zenduty_roles.main.id}"
    "zenduty_account_role.main"                 = zenduty_account_role.main.id
    "zenduty_team_permissions.b"                = zenduty_teams.b.id
    "zenduty_esp.main"                          = "${zenduty_teams.a.id}/${zenduty_esp.main.id}"
    "zenduty_services.main"                     = "${zenduty_teams.a.id}/${zenduty_services.main.id}"
    "zenduty_integrations.main"                 = "${zenduty_teams.a.id}/${zenduty_services.main.id}/${zenduty_integrations.main.id}"
    "zenduty_schedules.main"                    = "${zenduty_teams.a.id}/${zenduty_schedules.main.id}"
    "zenduty_sla.main"                          = "${zenduty_teams.a.id}/${zenduty_sla.main.id}"
    "zenduty_priorities.main"                   = "${zenduty_teams.a.id}/${zenduty_priorities.main.id}"
    "zenduty_tags.main"                         = "${zenduty_teams.a.id}/${zenduty_tags.main.id}"
    "zenduty_maintenance_window.main"           = "${zenduty_teams.a.id}/${zenduty_maintenance_window.main.id}"
    "zenduty_task_templates.main"               = "${zenduty_teams.a.id}/${zenduty_task_templates.main.id}"
    "zenduty_task_template_tasks.main"          = "${zenduty_teams.a.id}/${zenduty_task_templates.main.id}/${zenduty_task_template_tasks.main.id}"
    "zenduty_post_incident_tasks.main"          = "${zenduty_teams.a.id}/${zenduty_post_incident_tasks.main.id}"
    "zenduty_alertrules.main"                   = "${zenduty_teams.a.id}/${zenduty_services.main.id}/${zenduty_integrations.main.id}/${zenduty_alertrules.main.id}"
    "zenduty_integrations.outgoing"             = "${zenduty_teams.a.id}/${zenduty_services.main.id}/${zenduty_integrations.outgoing.id}"
    "zenduty_outgoing_rules.main"               = "${zenduty_teams.a.id}/${zenduty_services.main.id}/${zenduty_integrations.outgoing.id}/${zenduty_outgoing_rules.main.id}"
    "zenduty_globalrouter.main"                 = zenduty_globalrouter.main.id
    "zenduty_globalrouting_rule.route"          = "${zenduty_globalrouter.main.id}/${zenduty_globalrouting_rule.route.id}"
    "zenduty_globalrouting_rule.suppress"       = "${zenduty_globalrouter.main.id}/${zenduty_globalrouting_rule.suppress.id}"
    "zenduty_incidents.main"                    = zenduty_incidents.main.id
    "zenduty_assign_account_role.main"          = zenduty_user.tester.id
    "zenduty_heartbeat.main"                    = zenduty_heartbeat.main.id
    "zenduty_account_setting.main"              = "account"
    "zenduty_webhook_configuration.main"        = zenduty_webhook_configuration.main.id
    "zenduty_account_stakeholder_template.main" = zenduty_account_stakeholder_template.main.id
    "zenduty_service_maintenance_window.main"   = "${zenduty_teams.a.id}/${zenduty_services.main.id}/${zenduty_service_maintenance_window.main.id}"
    "zenduty_maintenance_template.main"         = "${zenduty_teams.a.id}/${zenduty_maintenance_template.main.id}"
    "zenduty_service_context.main"              = "${zenduty_teams.a.id}/${zenduty_services.main.id}/${zenduty_service_context.main.id}"
    "zenduty_incident_custom_field.main"        = zenduty_incident_custom_field.main.id
    "zenduty_incident_type.main"                = zenduty_incident_type.main.id
    "zenduty_user_forwarding_rule.main"         = "${zenduty_user.tester.id}/${zenduty_user_forwarding_rule.main.id}"
  }
}

# IDs the negative tests need (curl DELETE, then watch the provider notice).
output "team_a_id" {
  value = zenduty_teams.a.id
}

output "tag_id" {
  value = zenduty_tags.main.id
}

# Sanity probes for the new data-source behavior.
output "open_incident_count" {
  value = length(data.zenduty_incidents.open.results)
}

output "alertrule_conditions_readback" {
  value = data.zenduty_alertrules.main.alertrules[0].conditions
}
