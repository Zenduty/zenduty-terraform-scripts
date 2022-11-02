import os
import subprocess
import re
import subprocess
import uuid
import json
from constants import terraform_map

# 7-bit C1 ANSI sequences
ansi_escape = re.compile(r'''
    \x1B  # ESC
    (?:   # 7-bit C1 Fe (except CSI)
        [@-Z\\-_]
    |     # or [ for CSI, followed by a control sequence
        \[
        [0-?]*  # Parameter bytes
        [ -/]*  # Intermediate bytes
        [@-~]   # Final byte
    )
''', re.VERBOSE)


def overide_into_file(name, instance, resource):
    name = name.replace(" ", "")
    f = open("temp1.tf", "w")

    f.write(instance.render(
        name=name,
        resource=resource
    ))
    f.write('\n')
    f.close()


def create_mapping(id, value):
    with open("mapping.json", mode="r") as data:
        existing_data = json.loads(data.read())
    with open("mapping.json", mode="w") as file:
        existing_data.update({id: value})
        file.write(json.dumps(existing_data, indent=4))


def write_into_file(name, file, resource, parameters):
    name = name.replace(" ", "")
    try:
        subprocess.run(f"terraform import {resource}.{name} {parameters}", shell=True)
    except Exception as e:
        pass
    ff = subprocess.run(f"terraform state show {resource}.{name}", shell=True, capture_output=True)
    output = ff.stdout.decode('utf8').splitlines()
    if file == 'services':
        if "id" in output[6]:
            output.pop(6)
        else:
            j = 0
            for st in output:
                if "id" in st:
                    output.pop(j)
                    j += 1
    elif file == 'users':
        output.pop(4)
    else:
        output.pop(2)
    output = "\n".join(output)
    f = open(f"{file}.tf", "a")
    f.write(ansi_escape.sub('', output))
    f.write("\n")
    f.close()


def check_string(file, word):
    if os.path.exists(file):
        with open(file) as temp_f:
            datafile = temp_f.readlines()
        for line in datafile:
            if word in line:
                return True  # The string is found
        return False


def find_replace(file, word, instance, datafile):
    if os.path.exists(file):
        f = open(file, 'r')
        lines = f.readlines()
        f.close()
        f = open(file, 'w')
        for line in lines:
            if word in line:
                data = line.split("=")
                if "team" in data[0]:
                    line = instance.render(
                        text=terraform_map['team'].format(datafile[word]),
                        name=data[0],
                    )
                elif "escalation_policy" in data[0]:
                    line = instance.render(
                        text=terraform_map['ep'].format(datafile[word]),
                        name=data[0],
                    )
                elif "target_id" in data[0]:
                    if len(word) == 36:
                        line = instance.render(
                            text=terraform_map['schedule'].format(datafile[word]),
                            name=data[0],
                        )
                    else:
                        line = instance.render(
                            text=terraform_map['user'].format(datafile[word]),
                            name=data[0],
                        )
                elif "user" in data[0]:
                    line = instance.render(
                        text=terraform_map['user'].format(datafile[word]),
                        name=data[0],
                    )
                # else:
                #     from automate import TEMPLATE_ENVIRONMENT
                #     instance = TEMPLATE_ENVIRONMENT.get_template("userreplace.jinja2")
                #     line = instance.render(
                #         text=terraform_map['user'].format(datafile[word]),
                #     )
                f.write(line)
                f.write("\n")
            else:
                f.write(line)
        f.close()


def check_name(name, file):
    name = name.replace(" ", "").replace("-", "").replace(".", "")
    if check_string(file, name):
        return f"{name}{str(uuid.uuid4())[:4]}"
    return name
