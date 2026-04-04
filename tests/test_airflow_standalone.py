"""
Unit tests for cli/airflow_standalone.py with mocked subprocess calls.
"""
import json
import os
import subprocess
import sys
import threading
import unittest
from io import StringIO
from unittest.mock import MagicMock, call, mock_open, patch

# Ensure the project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cli.airflow_standalone import (
    DEFAULT_DAGS_FOLDER,
    DEFAULT_PASSWORD,
    DEFAULT_PORT,
    DEFAULT_USERNAME,
    _get_password_file,
    _reset_password_when_ready,
    _wait_for_process,
    build_env,
    check_status,
    get_parser,
    main,
    start_airflow,
    stop_airflow,
)


class TestGetParser(unittest.TestCase):
    def test_parser_returns_argparse_parser(self):
        parser = get_parser()
        self.assertIsNotNone(parser)

    def test_start_command_defaults(self):
        parser = get_parser()
        args = parser.parse_args(["start"])
        self.assertEqual(args.command, "start")
        self.assertEqual(args.port, DEFAULT_PORT)
        self.assertEqual(args.dags_folder, DEFAULT_DAGS_FOLDER)
        self.assertEqual(args.username, DEFAULT_USERNAME)
        self.assertEqual(args.password, DEFAULT_PASSWORD)

    def test_start_command_custom_port(self):
        parser = get_parser()
        args = parser.parse_args(["start", "--port", "9090"])
        self.assertEqual(args.port, 9090)

    def test_start_command_custom_dags_folder(self):
        parser = get_parser()
        args = parser.parse_args(["start", "--dags-folder", "/tmp/dags"])
        self.assertEqual(args.dags_folder, "/tmp/dags")

    def test_start_command_custom_credentials(self):
        parser = get_parser()
        args = parser.parse_args(["start", "--username", "myuser", "--password", "mypass"])
        self.assertEqual(args.username, "myuser")
        self.assertEqual(args.password, "mypass")

    def test_stop_command(self):
        parser = get_parser()
        args = parser.parse_args(["stop"])
        self.assertEqual(args.command, "stop")

    def test_status_command(self):
        parser = get_parser()
        args = parser.parse_args(["status"])
        self.assertEqual(args.command, "status")

    def test_no_command_returns_none(self):
        parser = get_parser()
        args = parser.parse_args([])
        self.assertIsNone(args.command)


class TestBuildEnv(unittest.TestCase):
    def test_build_env_sets_dags_folder(self):
        env = build_env("/tmp/test_dags")
        self.assertEqual(env["AIRFLOW__CORE__DAGS_FOLDER"], "/tmp/test_dags")

    def test_build_env_disables_examples(self):
        env = build_env("/tmp/test_dags")
        self.assertEqual(env["AIRFLOW__CORE__LOAD_EXAMPLES"], "False")

    def test_build_env_includes_os_env(self):
        with patch.dict(os.environ, {"MY_TEST_VAR": "hello"}):
            env = build_env("/tmp/dags")
        self.assertEqual(env["MY_TEST_VAR"], "hello")


class TestStartAirflow(unittest.TestCase):
    @patch("cli.airflow_standalone.threading.Thread")
    @patch("cli.airflow_standalone.subprocess.Popen")
    @patch("cli.airflow_standalone.os.makedirs")
    def test_start_airflow_success(self, mock_makedirs, mock_popen, mock_thread):
        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        result = start_airflow(8080, "/tmp/dags")

        mock_makedirs.assert_called_once_with("/tmp/dags", exist_ok=True)
        mock_popen.assert_called_once()
        mock_thread.return_value.start.assert_called_once()
        self.assertEqual(result, 0)

    @patch("cli.airflow_standalone.threading.Thread")
    @patch("cli.airflow_standalone.subprocess.Popen")
    @patch("cli.airflow_standalone.os.makedirs")
    def test_start_airflow_sets_port_env(self, mock_makedirs, mock_popen, mock_thread):
        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        start_airflow(9090, "/tmp/dags")

        call_kwargs = mock_popen.call_args.kwargs
        self.assertEqual(call_kwargs["env"]["AIRFLOW__WEBSERVER__WEB_SERVER_PORT"], "9090")

    @patch("cli.airflow_standalone.threading.Thread")
    @patch("cli.airflow_standalone.subprocess.Popen", side_effect=FileNotFoundError)
    @patch("cli.airflow_standalone.os.makedirs")
    def test_start_airflow_not_found(self, mock_makedirs, mock_popen, mock_thread):
        result = start_airflow(8080, "/tmp/dags")
        self.assertEqual(result, 1)

    @patch("cli.airflow_standalone.threading.Thread")
    @patch("cli.airflow_standalone._wait_for_process", side_effect=KeyboardInterrupt)
    @patch("cli.airflow_standalone.subprocess.Popen")
    @patch("cli.airflow_standalone.os.makedirs")
    def test_start_airflow_keyboard_interrupt(self, mock_makedirs, mock_popen, mock_wait, mock_thread):
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc

        result = start_airflow(8080, "/tmp/dags")
        mock_proc.terminate.assert_called_once()
        self.assertEqual(result, 0)

    @patch("cli.airflow_standalone.threading.Thread")
    @patch("cli.airflow_standalone.subprocess.Popen")
    @patch("cli.airflow_standalone.os.makedirs")
    def test_start_airflow_nonzero_exit(self, mock_makedirs, mock_popen, mock_thread):
        mock_proc = MagicMock()
        mock_proc.wait.return_value = 1
        mock_proc.returncode = 1
        mock_popen.return_value = mock_proc

        result = start_airflow(8080, "/tmp/dags")
        self.assertEqual(result, 1)


class TestStopAirflow(unittest.TestCase):
    @patch("cli.airflow_standalone.subprocess.run")
    def test_stop_airflow_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        result = stop_airflow()
        self.assertEqual(result, 0)
        mock_run.assert_called_once_with(
            ["pkill", "-f", "airflow standalone"],
            capture_output=True,
            text=True,
        )

    @patch("cli.airflow_standalone.subprocess.run")
    def test_stop_airflow_not_running(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        result = stop_airflow()
        self.assertEqual(result, 1)

    @patch("cli.airflow_standalone.subprocess.run", side_effect=FileNotFoundError)
    def test_stop_airflow_pkill_not_found(self, mock_run):
        result = stop_airflow()
        self.assertEqual(result, 1)


class TestCheckStatus(unittest.TestCase):
    @patch("cli.airflow_standalone.subprocess.run")
    def test_status_running(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="1234\n5678\n")
        result = check_status()
        self.assertEqual(result, 0)

    @patch("cli.airflow_standalone.subprocess.run")
    def test_status_not_running(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = check_status()
        self.assertEqual(result, 1)

    @patch("cli.airflow_standalone.subprocess.run", side_effect=FileNotFoundError)
    def test_status_pgrep_not_found(self, mock_run):
        result = check_status()
        self.assertEqual(result, 1)


class TestMain(unittest.TestCase):
    def test_main_no_command_prints_help(self):
        result = main([])
        self.assertEqual(result, 1)

    @patch("cli.airflow_standalone.start_airflow", return_value=0)
    def test_main_start_command(self, mock_start):
        result = main(["start"])
        mock_start.assert_called_once_with(DEFAULT_PORT, DEFAULT_DAGS_FOLDER, DEFAULT_USERNAME, DEFAULT_PASSWORD)
        self.assertEqual(result, 0)

    @patch("cli.airflow_standalone.start_airflow", return_value=0)
    def test_main_start_command_custom_args(self, mock_start):
        result = main(["start", "--port", "9090", "--dags-folder", "/tmp/dags"])
        mock_start.assert_called_once_with(9090, "/tmp/dags", DEFAULT_USERNAME, DEFAULT_PASSWORD)
        self.assertEqual(result, 0)

    @patch("cli.airflow_standalone.stop_airflow", return_value=0)
    def test_main_stop_command(self, mock_stop):
        result = main(["stop"])
        mock_stop.assert_called_once()
        self.assertEqual(result, 0)

    @patch("cli.airflow_standalone.check_status", return_value=0)
    def test_main_status_command(self, mock_status):
        result = main(["status"])
        mock_status.assert_called_once()
        self.assertEqual(result, 0)


class TestGetPasswordFile(unittest.TestCase):
    def test_default_path(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AIRFLOW_HOME", None)
            path = _get_password_file()
        self.assertTrue(path.endswith("simple_auth_manager_passwords.json.generated"))
        self.assertIn("airflow", path)

    def test_custom_airflow_home(self):
        with patch.dict(os.environ, {"AIRFLOW_HOME": "/custom/airflow"}):
            path = _get_password_file()
        self.assertEqual(path, "/custom/airflow/simple_auth_manager_passwords.json.generated")


class TestResetPasswordWhenReady(unittest.TestCase):
    def _run_in_thread(self, *args):
        """Helper: run _reset_password_when_ready in a thread and join it."""
        stop_event = threading.Event()
        t = threading.Thread(target=_reset_password_when_ready, args=(*args, stop_event))
        t.start()
        t.join(timeout=2)
        return stop_event

    @patch("cli.airflow_standalone._get_password_file", return_value="/fake/passwords.json")
    @patch("cli.airflow_standalone.os.path.exists", return_value=True)
    def test_resets_password_when_file_exists(self, mock_exists, mock_path):
        data = json.dumps({"admin": "old"})
        stop_event = threading.Event()
        with patch("builtins.open", mock_open(read_data=data)) as m:
            _reset_password_when_ready("admin", "newpass", stop_event)
        handle = m()
        written = "".join(c.args[0] for c in handle.write.call_args_list)
        self.assertIn('"admin": "newpass"', written)

    @patch("cli.airflow_standalone._get_password_file", return_value="/fake/passwords.json")
    @patch("cli.airflow_standalone.os.path.exists", return_value=True)
    def test_skips_when_user_not_found(self, mock_exists, mock_path):
        data = json.dumps({"other_user": "pass"})
        stop_event = threading.Event()
        with patch("builtins.open", mock_open(read_data=data)):
            with patch("builtins.print") as mock_print:
                _reset_password_when_ready("admin", "newpass", stop_event)
        mock_print.assert_called_once()
        self.assertIn("not found", mock_print.call_args.args[0])

    @patch("cli.airflow_standalone._get_password_file", return_value="/fake/passwords.json")
    @patch("cli.airflow_standalone.os.path.exists", return_value=True)
    def test_handles_read_exception(self, mock_exists, mock_path):
        stop_event = threading.Event()
        with patch("builtins.open", side_effect=OSError("disk error")):
            with patch("builtins.print") as mock_print:
                _reset_password_when_ready("admin", "newpass", stop_event)
        mock_print.assert_called_once()
        self.assertIn("Failed to reset password", mock_print.call_args.args[0])

    @patch("cli.airflow_standalone._get_password_file", return_value="/fake/passwords.json")
    @patch("cli.airflow_standalone.os.path.exists", return_value=False)
    def test_exits_when_stop_event_set(self, mock_exists, mock_path):
        stop_event = threading.Event()
        stop_event.set()
        # Should return immediately without blocking
        _reset_password_when_ready("admin", "newpass", stop_event)


if __name__ == "__main__":
    unittest.main()
