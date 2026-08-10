# Negative test: an over-long team name makes the API return 400.
# Before this release the provider retried EVERY structured API error for a
# full minute; now a 400 must fail immediately. scripts/run.sh times this.

terraform {
  required_providers {
    zenduty = {
      source  = "local.zenduty.com/dev/zendutylocal"
      version = "0.0.1"
    }
  }
}

variable "zenduty_token" {
  type      = string
  sensitive = true
}

provider "zenduty" {
  token = var.zenduty_token
}

resource "zenduty_teams" "too_long" {
  name = join("", [for i in range(60) : "0123456789"]) # 600 chars
}
