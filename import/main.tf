# Configure the zenduty provider
terraform {
  required_providers {
    zenduty = {
      source  = "zenduty/zenduty"
      version = ">= 0.1.0"
    }
  }
}


provider "zenduty" {
  # Configuration options
}
