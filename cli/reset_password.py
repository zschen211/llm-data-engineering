"""
Reset Airflow simple_auth_manager password for a user.
Usage: uv run python cli/reset_password.py [--username admin] [--password admin]
"""
import argparse
import json
import os


def get_password_file():
    airflow_home = os.environ.get("AIRFLOW_HOME", os.path.expanduser("~/airflow"))
    return os.path.join(airflow_home, "simple_auth_manager_passwords.json.generated")


def reset_password(username, password):
    path = get_password_file()
    if not os.path.exists(path):
        print(f"Password file not found: {path}")
        print("Make sure Airflow Standalone has been started at least once.")
        return 1

    with open(path, "r") as f:
        passwords = json.loads(f.read().strip())

    if username not in passwords:
        print(f"User '{username}' not found. Existing users: {list(passwords.keys())}")
        return 1

    passwords[username] = password

    with open(path, "w") as f:
        f.write(json.dumps(passwords) + "\n")

    print(f"Password for '{username}' updated. Restart Airflow for the change to take effect.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Reset Airflow standalone user password")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="admin")
    args = parser.parse_args()
    return reset_password(args.username, args.password)


if __name__ == "__main__":
    import sys
    sys.exit(main())
