# Configure the zenduty provider
# terraform {
#   required_providers {
#     zenduty = {
#       source  = "zenduty/zenduty"
#       version = ">= 0.1.0"
#     }
#   }
# }

terraform {
  required_providers {
    # Local development build, installed by scripts/run.sh into
    # ~/.terraform.d/plugins/local.zenduty.com/dev/zendutylocal/...
    # The local name "zenduty" keeps resource types (zenduty_*) and the
    # provider block identical to a production config — only the source
    # address differs.
    zenduty = {
      source  = "local.zenduty.com/dev/zendutylocal"
      version = "0.0.1"
    }
  }
}

provider "zenduty" {
  # Configuration options

}
