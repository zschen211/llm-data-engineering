"""Data production & eval closed loop over the asset asset layer.

The package disables Ray's uv-run runtime-env hook at import time
(``RAY_ENABLE_UV_RUN_RUNTIME_ENV=0``), before any submodule imports ``ray``:
Ray reads the flag once when its constants module is loaded. Without this,
every ``ray.init`` run under ``uv run`` injects ``working_dir=<cwd>`` into
the driver's runtime env, and Ray's dashboard subprocesses re-package that
directory without the driver's ``excludes``, zipping the whole project root
into a multi-GB archive that is then read into memory in one piece, OOM-killing
pipeline workers. Workers on this single-node cluster share the same venv and
never needed the uv propagation.
"""

import os

os.environ["RAY_ENABLE_UV_RUN_RUNTIME_ENV"] = "0"

__version__ = "0.1.0"
