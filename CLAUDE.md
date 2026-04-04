# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install all dependencies (including dev)
uv sync --extra dev

# Run all tests with coverage
uv run pytest

# Run a single test file
uv run pytest tests/test_airflow_standalone.py

# Run a single test by name
uv run pytest tests/test_airflow_standalone.py::TestStartAirflow::test_start_airflow_success

# Start Airflow Standalone (foreground, Ctrl+C to stop)
uv run airflow-standalone start

# Custom port and DAGs folder
uv run airflow-standalone start --port 9090 --dags-folder ./dags

# Stop / check status
uv run airflow-standalone stop
uv run airflow-standalone status
```

## Architecture

The project is structured around a `cli` package and a `tests` package.

**`cli/airflow_standalone.py`** — the core module. Exposes four public functions (`get_parser`, `build_env`, `start_airflow`, `stop_airflow`, `check_status`) and a `main` entry point registered as the `airflow-standalone` CLI script. `_wait_for_process` is a thin wrapper around `proc.wait()` that exists solely to be mockable in tests — `KeyboardInterrupt` cannot be caught by `unittest.mock` side-effects, so it must be patched at the function boundary.

**`tests/test_dag.py`** — a real Airflow DAG (`test_etl_pipeline`) used to validate Airflow scheduling. It is an extract → transform → load chain using `PythonOperator` with XCom passing between tasks. Uses Airflow 3.x API (`schedule=` not `schedule_interval=`, import from `airflow.providers.standard.operators.python`).

**`tests/test_airflow_standalone.py`** — unit tests for the CLI. All subprocess calls are mocked; Airflow is never actually started. Coverage target is >80% (currently 100%).

## Airflow version notes

The project pins `apache-airflow>=2.9.0` but the resolved version is Airflow 3.x. Key 3.x breaking changes that affect this repo:
- `schedule_interval` → `schedule` on `DAG()`
- `PythonOperator` moved to `airflow.providers.standard.operators.python`

When adding new DAGs, use the 3.x API.
