# Configure the zenduty provider
terraform {
  required_providers {
    zenduty = {
      source = "zenduty/zenduty"
      version = ">= 0.1.0"
    }
  }
}


provider "zenduty" {
  # Configuration options
  # you can configure the token by uncommenting the line the below line 
  # or by exporting ZENDUTY_API_KEY as environment variable(ie. export ZENDUTY_API_KEY="your-api-key")
  
  # token = "your api key"
}

# user
data "zenduty_user" "user1" {
  email = "demouser@gmail.com"
}
# user2 
data "zenduty_user" "user2" {
  email = "demouser2@gmail.com"
}


# creating team 
resource "zenduty_teams" "infrateam" {
  name = "Infra Team"
}

# add user2 to team
resource "zenduty_member" "demouser2" {
   team = zenduty_teams.infrateam.id
   user = data.zenduty_user.user2.users[0].username
}

# invite user to team

resource "zenduty_user" "user3" {
  email      = "michael@scott.com"
  first_name = "Michael"
  last_name  = "Scott"
  team       =  zenduty_teams.infrateam.id
}

# schedule 
# vist for detailed docs https://registry.terraform.io/providers/Zenduty/zenduty/latest/docs/resources/zenduty_schedules
resource "zenduty_schedules" "infraschedule" {
  name = "Infra Schedule"
  team_id = zenduty_teams.infrateam.id
  time_zone = "Asia/Kolkata"  
  layers {
    name = "layer1"
    rotation_end_time = "2026-03-01 11:36"
    rotation_start_time = "2022-03-01 11:36"
    shift_length = 86400
    users = [zenduty_user.user3.id]
  }
}

# escalation policy
# vist for detailed docs https://registry.terraform.io/providers/Zenduty/zenduty/latest/docs/resources/zenduty_esp
resource "zenduty_esp" "primaryep" {
    name = "Infra escalation policy" 
    team_id = zenduty_teams.infrateam.id
    summary = "This is the summary for the new ESP"
    description = "This is the description for the new ESP"
    rules {
        delay = 0    
        targets {
            target_type = 2
            target_id = zenduty_user.user3.id  //username of user
        }
        targets {
            target_type = 1
            target_id = zenduty_schedules.infraschedule.id    // unique id of the schedule
        }
    }
    move_to_next = true
    repeat_policy=1
}

#service
resource "zenduty_services" "backend" {
    name = "Application Monitoring"
    team_id = zenduty_teams.infrateam.id 
    escalation_policy = zenduty_esp.primaryep.id 
}

# Integration 
# vist for detailed docs https://registry.terraform.io/providers/Zenduty/zenduty/latest/docs/resources/zenduty_integrations
resource "zenduty_integrations" "exampleintegration" {
    name = "API"
    summary = "This is the summary for the example integration"
    team_id = zenduty_teams.infrateam.id
    service_id = zenduty_services.backend.id
    application = "27c9800c-2856-490d-8119-790be1308dd4" # Api Integration
# To get application value, vist https://www.zenduty.com/api/account/applications/ and get unique_id of the Integration required.
}