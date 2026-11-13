# Configure the zenduty provider
terraform {
  required_providers {
    zenduty = {
      source  = "zenduty/zenduty"
      version = ">= 2.2.0"
    }
  }
}


provider "zenduty" {
  # Configuration options

}
