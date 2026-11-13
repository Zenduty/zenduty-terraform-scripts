variable "zenduty_token" {
  type        = string
  sensitive   = true
  description = "API token of the TEST account. Never point this at production."
}

variable "name_prefix" {
  type        = string
  default     = "tfsmoke"
  description = "Prefix for every created object so they are easy to spot and clean up."
}

variable "application_id" {
  type        = string
  description = "unique_id of an integration application from the account catalog (GET /api/account/applications/). run.sh fetches the generic API application automatically."
}

variable "outgoing_application_id" {
  type        = string
  description = "unique_id of an OUTBOUND application (application_type == 1) from the catalog. Outgoing rules only attach to integrations built from outbound applications. run.sh fetches one automatically."
}

variable "tester_email" {
  type        = string
  description = "Email for the invited smoke-test user. Use a plus-address you control, e.g. you+tfsmoke@example.com. NOTE: the Zenduty API cannot delete users, so this member survives terraform destroy."
}

variable "notification_username" {
  type        = string
  default     = ""
  description = "Optional: username of an EXISTING user with contact methods (e.g. the account owner) to exercise zenduty_notification_rules + the usercontact data source. Freshly invited users have no contacts, so this cannot use the tester user."
}

variable "enable_workflow" {
  type        = bool
  default     = false
  description = "Create a zenduty_workflow. Off by default: the workflow API currently accepts session authentication only, so it fails with an API key until the backend grants token access."
}
