import time
from jinja2 import Environment, FileSystemLoader
import json
from constants import ZENDUTY_TEAMS, zendutyrequest, ZENDUTY_SCHEDULES, ZENDUTY_ESP, ZENDUTY_SERVICE, ZENDUTY_USERS
from helpers import overide_into_file, write_into_file, check_name, create_mapping, find_replace
TEMPLATE_ENVIRONMENT = Environment(loader=FileSystemLoader('templates'))

# imprort teams


def import_team():
    res = zendutyrequest.get(ZENDUTY_TEAMS)
    team_template = TEMPLATE_ENVIRONMENT.get_template("common.jinja2")
    for team in res.json():
        time.sleep(1)
        team['name'] = check_name(team['name'], 'teams.tf')
        overide_into_file(team['name'], team_template, 'zenduty_teams')
        write_into_file(team['name'], 'teams', 'zenduty_teams', team['unique_id'])
        create_mapping(team['unique_id'], team['name'])


def import_schedules():
    schedule_instance = TEMPLATE_ENVIRONMENT.get_template("common.jinja2")
    res = zendutyrequest.get(ZENDUTY_TEAMS)
    for team in res.json():
        team_schedule = zendutyrequest.get(ZENDUTY_SCHEDULES.format(team['unique_id']))
        for schedule in team_schedule.json():
            time.sleep(1)
            schedule['name'] = check_name(schedule['name'], 'schedules.tf')
            overide_into_file(schedule['name'], schedule_instance, 'zenduty_schedules')
            write_into_file(schedule['name'], 'schedules', 'zenduty_schedules',
                            f"{team['unique_id']}/{schedule['unique_id']}")
            create_mapping(schedule['unique_id'], schedule['name'])


def import_ep():
    ep_instance = TEMPLATE_ENVIRONMENT.get_template("common.jinja2")
    res = zendutyrequest.get(ZENDUTY_TEAMS)
    for team in res.json():
        team_ep = zendutyrequest.get(ZENDUTY_ESP.format(team['unique_id']))
        for ep in team_ep.json():
            time.sleep(1)
            ep['name'] = check_name(ep['name'], 'escalations.tf')
            overide_into_file(ep['name'], ep_instance, 'zenduty_esp')
            write_into_file(ep['name'], 'escalations', 'zenduty_esp', f"{team['unique_id']}/{ep['unique_id']}")
            create_mapping(ep['unique_id'], ep['name'])


def import_service():
    service_instance = TEMPLATE_ENVIRONMENT.get_template("common.jinja2")
    res = zendutyrequest.get(ZENDUTY_TEAMS)
    for team in res.json():
        team_services = zendutyrequest.get(ZENDUTY_SERVICE.format(team['unique_id']))
        for service in team_services.json():
            time.sleep(1)
            service['name'] = check_name(service['name'], 'services.tf')
            overide_into_file(service['name'], service_instance, 'zenduty_services')
            write_into_file(service['name'], 'services', 'zenduty_services',
                            f"{team['unique_id']}/{service['unique_id']}")


def import_user():
    user_instance = TEMPLATE_ENVIRONMENT.get_template("common.jinja2")
    res = zendutyrequest.get(ZENDUTY_USERS)
    for user in res.json():
        user = user['user']
        time.sleep(1)
        name = check_name(f"{user['first_name']}{user['last_name']}", 'users.tf')
        overide_into_file(name, user_instance, 'zenduty_user')
        write_into_file(name, 'users', 'zenduty_user', f"{user['username']}")
        create_mapping(user['username'], name)


def replace():
    with open('mapping.json') as temp_f:
        datafile = json.loads(temp_f.read())
    instance = TEMPLATE_ENVIRONMENT.get_template("replace.jinja2")
    for obj in datafile:
        find_replace('escalations.tf', obj, instance, datafile)


# replace()
# import_team()
# replace()
# import_ep()
# import_service()
