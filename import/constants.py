import requests
import os

ZENDUTY_API_KEY = os.environ.get('ZENDUTY_API_KEY', '')
ZENDUTY_URL = 'https://www.zenduty.com'


zendutyrequest = requests.Session()
zendutyrequest.headers.update({"Authorization": f"Token {ZENDUTY_API_KEY}"})

ZENDUTY_TEAMS = f"{ZENDUTY_URL}/api/account/teams/"
ZENDUTY_TEAM_MEMBERS = f"{ZENDUTY_URL}/api/account/teams/{{}}/members/"
ZENDUTY_USERS = f"{ZENDUTY_URL}/api/account/users/"
ZENDUTY_INVITE_USER = f"{ZENDUTY_URL}/api/account/api_invite/"
ZENDUTY_SCHEDULES = f"{ZENDUTY_URL}/api/account/teams/{{}}/schedules/"
ZENDUTY_ESP = f"{ZENDUTY_URL}/api/account/teams/{{}}/escalation_policies/"
ZENDUTY_SERVICE = f"{ZENDUTY_URL}/api/account/teams/{{}}/services/"
ZENDUTY_USERS = f"{ZENDUTY_URL}/api/account/users/"

terraform_map = {
    "team": 'zenduty_teams.{}.id',
    "ep": "zenduty_esp.{}.id",
    "schedule": 'zenduty_schedules.{}.id',
}
