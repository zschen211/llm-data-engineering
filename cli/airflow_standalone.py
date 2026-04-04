"""
CLI tool to start and manage Airflow Standalone service.
"""
import argparse
import json
import os
import subprocess
import sys
import threading


DEFAULT_PORT = 8080
DEFAULT_DAGS_FOLDER = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dags")
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin"


def get_parser():
    parser = argparse.ArgumentParser(
        prog="airflow-standalone",
        description="Start a local Airflow Standalone service",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    start_parser = subparsers.add_parser("start", help="Start Airflow Standalone")
    start_parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port for the Airflow web server (default: {DEFAULT_PORT})",
    )
    start_parser.add_argument(
        "--dags-folder",
        type=str,
        default=DEFAULT_DAGS_FOLDER,
        help=f"Path to the DAGs folder (default: {DEFAULT_DAGS_FOLDER})",
    )
    start_parser.add_argument(
        "--username",
        type=str,
        default=DEFAULT_USERNAME,
        help=f"Admin username to reset on startup (default: {DEFAULT_USERNAME})",
    )
    start_parser.add_argument(
        "--password",
        type=str,
        default=DEFAULT_PASSWORD,
        help=f"Admin password to reset on startup (default: {DEFAULT_PASSWORD})",
    )

    subparsers.add_parser("stop", help="Stop Airflow Standalone")
    subparsers.add_parser("status", help="Check Airflow Standalone status")

    return parser


def build_env(dags_folder):
    env = os.environ.copy()
    env["AIRFLOW__CORE__DAGS_FOLDER"] = dags_folder
    env["AIRFLOW__CORE__LOAD_EXAMPLES"] = "False"
    return env


def _get_password_file():
    airflow_home = os.environ.get("AIRFLOW_HOME", os.path.expanduser("~/airflow"))
    return os.path.join(airflow_home, "simple_auth_manager_passwords.json.generated")


def _reset_password_when_ready(username, password, stop_event):
    """Poll for the Airflow password file and reset the password once it appears."""
    path = _get_password_file()
    while not stop_event.is_set():
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    passwords = json.loads(f.read().strip())
                if username in passwords:
                    passwords[username] = password
                    with open(path, "w") as f:
                        f.write(json.dumps(passwords) + "\n")
                    print(f"\n[airflow-standalone] Password for '{username}' has been reset.")
                else:
                    print(f"\n[airflow-standalone] User '{username}' not found in password file; skipping reset.")
            except Exception as e:
                print(f"\n[airflow-standalone] Failed to reset password: {e}")
            return
        stop_event.wait(1)


def _wait_for_process(proc):
    """Wait for a subprocess to complete. Separated for testability."""
    proc.wait()


def start_airflow(port, dags_folder, username=DEFAULT_USERNAME, password=DEFAULT_PASSWORD):
    os.makedirs(dags_folder, exist_ok=True)
    env = build_env(dags_folder)
    env["AIRFLOW__WEBSERVER__WEB_SERVER_PORT"] = str(port)

    print(f"Starting Airflow Standalone on port {port}...")
    print(f"DAGs folder: {dags_folder}")
    print(f"Access the web UI at: http://localhost:{port}")
    print("Press Ctrl+C to stop.")

    stop_event = threading.Event()
    reset_thread = threading.Thread(
        target=_reset_password_when_ready,
        args=(username, password, stop_event),
        daemon=True,
    )
    reset_thread.start()

    try:
        proc = subprocess.Popen(
            ["airflow", "standalone"],
            env=env,
        )
        try:
            _wait_for_process(proc)
        except KeyboardInterrupt:
            print("\nShutting down Airflow Standalone...")
            stop_event.set()
            proc.terminate()
            proc.wait()
            return 0
        stop_event.set()
        return proc.returncode
    except FileNotFoundError:
        stop_event.set()
        print("Error: 'airflow' command not found. Please install Apache Airflow.", file=sys.stderr)
        return 1


def stop_airflow():
    try:
        result = subprocess.run(
            ["pkill", "-f", "airflow standalone"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print("Airflow Standalone stopped.")
        else:
            print("No running Airflow Standalone process found.")
        return result.returncode
    except FileNotFoundError:
        print("Error: 'pkill' command not found.", file=sys.stderr)
        return 1


def check_status():
    try:
        result = subprocess.run(
            ["pgrep", "-f", "airflow standalone"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            pids = result.stdout.strip().split("\n")
            print(f"Airflow Standalone is running (PID: {', '.join(pids)})")
        else:
            print("Airflow Standalone is not running.")
        return result.returncode
    except FileNotFoundError:
        print("Error: 'pgrep' command not found.", file=sys.stderr)
        return 1


def main(args=None):
    parser = get_parser()
    parsed = parser.parse_args(args)

    if parsed.command is None:
        parser.print_help()
        return 1

    if parsed.command == "start":
        return start_airflow(parsed.port, parsed.dags_folder, parsed.username, parsed.password)
    elif parsed.command == "stop":
        return stop_airflow()
    elif parsed.command == "status":
        return check_status()

    return 0  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
