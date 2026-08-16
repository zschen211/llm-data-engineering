"""SQLite metadata store for data-factory.

Schema is written in a PostgreSQL-compatible style (TEXT PKs, explicit
timestamps) so it can be migrated to a shared server later. The store owns
lineage/version/eval metadata exclusively; product blobs live in the
storage backend (see ``data_factory/storage``).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import models as m

SCHEMA = """
CREATE TABLE IF NOT EXISTS capability_domains (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  description TEXT DEFAULT '',
  parent_id TEXT REFERENCES capability_domains(id),
  created_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS strategies (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  capability_domain_id TEXT REFERENCES capability_domains(id),
  description TEXT DEFAULT '',
  enabled INTEGER DEFAULT 1,
  created_at TEXT DEFAULT '',
  updated_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS datasets (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  source_type TEXT NOT NULL,           -- snapshot / import / derived
  snapshot_id TEXT DEFAULT '',
  tag_filters TEXT DEFAULT '[]',
  import_manifest TEXT DEFAULT '',
  derived_from TEXT DEFAULT '',
  created_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS stages (
  name TEXT PRIMARY KEY,
  module TEXT NOT NULL,
  kind TEXT NOT NULL,                  -- transform / qc_rule / qc_llm / sink
  description TEXT DEFAULT '',
  config_schema TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS workflows (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  strategy_id TEXT REFERENCES strategies(id),
  description TEXT DEFAULT '',
  enabled INTEGER DEFAULT 1,
  created_at TEXT DEFAULT '',
  updated_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS workflow_nodes (
  id TEXT PRIMARY KEY,
  workflow_id TEXT REFERENCES workflows(id),
  stage_name TEXT REFERENCES stages(name),
  node_label TEXT DEFAULT '',
  position INTEGER DEFAULT 0,
  config TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS workflow_edges (
  workflow_id TEXT,
  from_node TEXT,
  to_node TEXT,
  PRIMARY KEY (workflow_id, from_node, to_node)
);

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  workflow_id TEXT REFERENCES workflows(id),
  input_dataset_id TEXT REFERENCES datasets(id),
  input_dataset_version INTEGER DEFAULT 1,
  status TEXT DEFAULT 'pending',       -- pending/running/succeeded/failed/cancelled
  params TEXT DEFAULT '{}',
  error TEXT DEFAULT '',
  started_at TEXT DEFAULT '',
  finished_at TEXT DEFAULT '',
  stats TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS run_stages (
  run_id TEXT REFERENCES runs(id),
  node_id TEXT REFERENCES workflow_nodes(id),
  status TEXT DEFAULT 'pending',       -- pending/running/succeeded/failed/skipped
  rows_in INTEGER DEFAULT 0,
  rows_out INTEGER DEFAULT 0,
  failed_rows INTEGER DEFAULT 0,
  attempts INTEGER DEFAULT 0,
  started_at TEXT DEFAULT '',
  finished_at TEXT DEFAULT '',
  PRIMARY KEY (run_id, node_id)
);

CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY,
  run_id TEXT REFERENCES runs(id),
  node_id TEXT DEFAULT '',
  kind TEXT NOT NULL,                  -- intermediate / result
  object_key TEXT NOT NULL,
  sha256 TEXT DEFAULT '',
  size INTEGER DEFAULT 0,
  row_count INTEGER DEFAULT 0,
  dataset_version_id TEXT DEFAULT '',
  created_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_artifacts_run ON artifacts(run_id, node_id);

CREATE TABLE IF NOT EXISTS dataset_versions (
  id TEXT PRIMARY KEY,
  dataset_id TEXT REFERENCES datasets(id),
  version INTEGER NOT NULL,
  artifact_id TEXT DEFAULT '',
  manifest_key TEXT NOT NULL,
  row_count INTEGER DEFAULT 0,
  created_at TEXT DEFAULT '',
  UNIQUE (dataset_id, version)
);

CREATE TABLE IF NOT EXISTS models (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  backend TEXT NOT NULL,               -- local / vllm / api
  model_id TEXT DEFAULT '',
  weights_dir TEXT DEFAULT '',
  base_url TEXT DEFAULT '',
  api_key_env TEXT DEFAULT '',
  status TEXT DEFAULT 'pending',       -- pending/ready/failed
  last_check_at TEXT DEFAULT '',
  last_error TEXT DEFAULT '',
  params TEXT DEFAULT '{}',
  created_at TEXT DEFAULT '',
  updated_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS eval_sets (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  capability_domain_id TEXT DEFAULT '',
  source TEXT NOT NULL,                -- import / built
  rubric TEXT DEFAULT '{}',
  item_count INTEGER DEFAULT 0,
  created_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS eval_items (
  id TEXT PRIMARY KEY,
  eval_set_id TEXT REFERENCES eval_sets(id),
  seq INTEGER NOT NULL,
  question TEXT NOT NULL,
  expected TEXT DEFAULT '',
  rubric TEXT,
  category TEXT DEFAULT '',
  UNIQUE (eval_set_id, seq)
);

CREATE TABLE IF NOT EXISTS eval_runs (
  id TEXT PRIMARY KEY,
  eval_set_id TEXT REFERENCES eval_sets(id),
  model_id TEXT REFERENCES models(id),
  status TEXT DEFAULT 'running',       -- running/succeeded/failed/partial
  started_at TEXT DEFAULT '',
  finished_at TEXT DEFAULT '',
  aggregate TEXT DEFAULT '{}',
  error TEXT DEFAULT '',
  created_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS eval_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  eval_run_id TEXT REFERENCES eval_runs(id),
  item_id TEXT REFERENCES eval_items(id),
  model_output TEXT DEFAULT '',
  score TEXT DEFAULT '{}',
  latency_ms INTEGER DEFAULT 0,
  error TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS reports (
  id TEXT PRIMARY KEY,
  eval_run_id TEXT REFERENCES eval_runs(id),
  capability_domain_id TEXT DEFAULT '',
  aggregate TEXT DEFAULT '{}',
  badcases TEXT DEFAULT '[]',
  attribution TEXT DEFAULT '{}',
  json_key TEXT DEFAULT '',
  md_key TEXT DEFAULT '',
  created_at TEXT DEFAULT ''
);
"""

# JSON-encoded columns per table; decoded on read, encoded on write.
JSON_COLUMNS = {
    "datasets": ("tag_filters",),
    "stages": ("config_schema",),
    "workflow_nodes": ("config",),
    "runs": ("params", "stats"),
    "models": ("params",),
    "eval_sets": ("rubric",),
    "eval_items": ("question", "rubric"),
    "eval_runs": ("aggregate",),
    "eval_results": ("score",),
    "reports": ("aggregate", "badcases", "attribution"),
}

# dataclass → table name (class name + "s" is not a reliable plural).
MODEL_TABLES = {
    m.CapabilityDomain: "capability_domains",
    m.Strategy: "strategies",
    m.DatasetDefinition: "datasets",
    m.StageType: "stages",
    m.Workflow: "workflows",
    m.WorkflowNode: "workflow_nodes",
    m.WorkflowEdge: "workflow_edges",
    m.Run: "runs",
    m.RunStage: "run_stages",
    m.Artifact: "artifacts",
    m.DatasetVersion: "dataset_versions",
    m.Model: "models",
    m.EvalSet: "eval_sets",
    m.EvalItem: "eval_items",
    m.EvalRun: "eval_runs",
    m.EvalResult: "eval_results",
    m.Report: "reports",
}

PENDING_RUN_STATUSES = (m.RUN_PENDING, m.RUN_RUNNING)


def new_id(prefix: str = "") -> str:
    import uuid

    return f"{prefix}{uuid.uuid4().hex[:12]}"


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _encode_row(model: Any) -> dict:
    """Dataclass → column dict (JSON fields encoded; empty FKs → NULL)."""
    row = asdict(model)
    for col in JSON_COLUMNS.get(_table_of(model), ()):
        row[col] = _dumps(row[col])
    if _table_of(model) == "capability_domains" and not row["parent_id"]:
        row["parent_id"] = None
    return row


def _table_of(model: Any) -> str:
    return MODEL_TABLES[model.__class__]


def _decode(model_cls: type, row: dict) -> Any:
    """Column dict → dataclass (JSON fields decoded; NULL rubric → None)."""
    data = dict(row)
    for col in JSON_COLUMNS.get(_table_name(model_cls), ()):
        raw = data.get(col)
        data[col] = json.loads(raw) if raw else None if col == "rubric" else {}
    return model_cls(**data)


def _table_name(model_cls: type) -> str:
    return MODEL_TABLES[model_cls]


class Database:
    """Thin sqlite3 wrapper: schema init + typed CRUD for data-factory."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._thread_local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._connections_lock = threading.Lock()
        self._executescript(SCHEMA)
        self._seed_stages()
        self._mark_stale_runs_interrupted()

    def _new_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.isolation_level = None
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        with self._connections_lock:
            self._connections.append(conn)
        return conn

    @property
    def _conn(self) -> sqlite3.Connection:
        """Per-thread connection (WAL mode): concurrent workers each get their
        own connection instead of sharing one across threads."""
        conn = getattr(self._thread_local, "conn", None)
        if conn is None:
            conn = self._new_connection()
            self._thread_local.conn = conn
        return conn

    def _executescript(self, script: str) -> None:
        self._conn.executescript(script)

    def _seed_stages(self) -> None:
        """Register the built-in stage types (static registry mirror).

        The ``stages`` table documents which built-in stages exist; actual
        dispatch uses the static registry in ``strategies.stages`` (no
        dynamic imports, repo convention).
        """
        from ..strategies.stages import BUILTIN_STAGES

        for stage in BUILTIN_STAGES:
            row = {
                "name": stage.name,
                "module": stage.module,
                "kind": stage.kind,
                "description": stage.description,
                "config_schema": _dumps(stage.config_schema),
            }
            self._conn.execute(
                "INSERT INTO stages (name, module, kind, description, config_schema)"
                " VALUES (:name, :module, :kind, :description, :config_schema)"
                " ON CONFLICT(name) DO UPDATE SET"
                " module=excluded.module, kind=excluded.kind,"
                " description=excluded.description, config_schema=excluded.config_schema",
                row,
            )

    def _mark_stale_runs_interrupted(self) -> None:
        """Crash recovery: interrupted pipeline/eval runs are failed, and
        their in-flight stage rows reset so a re-run can resume from them."""
        now = m._now()
        self._conn.execute(
            "UPDATE runs SET status='failed', error='interrupted', finished_at=? "
            "WHERE status IN (?, ?)",
            (now, m.RUN_PENDING, m.RUN_RUNNING),
        )
        self._conn.execute(
            "UPDATE run_stages SET status='pending' WHERE status='running'"
        )
        self._conn.execute(
            "UPDATE eval_runs SET status='failed', error='interrupted',"
            " finished_at=? WHERE status='running'",
            (now,),
        )

    @contextmanager
    def transaction(self):
        conn = self._conn
        try:
            conn.execute("BEGIN")
            yield
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    def close(self) -> None:
        """Close all per-thread connections (idempotent)."""
        with self._connections_lock:
            for conn in self._connections:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            self._connections.clear()

    def _execute(self, sql: str, params: tuple = ()) -> int:
        cur = self._conn.execute(sql, params)
        return cur.rowcount

    def _fetch_one(self, sql: str, params: tuple = ()) -> dict | None:
        row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def insert(self, model: Any) -> None:
        """Insert a dataclass row (all fields must map to columns)."""
        row = _encode_row(model)
        cols = ", ".join(row)
        marks = ", ".join(f":{c}" for c in row)
        # B608 (all dynamic SQL here): identifiers come from MODEL_TABLES /
        # column keys (fixed maps), values are always bound parameters —
        # verified-safe f-string SQL.
        self._conn.execute(
            f"INSERT INTO {_table_of(model)} ({cols}) VALUES ({marks})",  # nosec B608
            row,
        )

    def update(self, model_cls: type, ident: dict, fields: dict) -> int:
        """Partial update by identity columns; JSON columns auto-encoded."""
        table = _table_name(model_cls)
        json_cols = JSON_COLUMNS.get(table, ())
        data = {c: _dumps(v) if c in json_cols else v for c, v in fields.items()}
        set_sql = ", ".join(f"{c} = :{c}" for c in data)
        where_sql = " AND ".join(f"{c} = :w_{c}" for c in ident)
        params = {**data, **{f"w_{c}": v for c, v in ident.items()}}
        return self._execute(
            f"UPDATE {table} SET {set_sql} WHERE {where_sql}",  # nosec B608
            params,
        )

    def delete(self, model_cls: type, ident: dict) -> int:
        table = _table_name(model_cls)
        where_sql = " AND ".join(f"{c} = ?" for c in ident)
        return self._execute(
            f"DELETE FROM {table} WHERE {where_sql}",  # nosec B608
            tuple(ident.values()),
        )

    def get(self, model_cls: type, ident: dict) -> Any | None:
        table = _table_name(model_cls)
        where_sql = " AND ".join(f"{c} = ?" for c in ident)
        row = self._fetch_one(
            f"SELECT * FROM {table} WHERE {where_sql}",  # nosec B608
            tuple(ident.values()),
        )
        return _decode(model_cls, row) if row else None

    def list(
        self, model_cls: type, where: str = "", params: tuple = (), order_by: str = ""
    ) -> list:
        table = _table_name(model_cls)
        sql = f"SELECT * FROM {table}"  # nosec B608
        if where:
            sql += f" WHERE {where}"  # nosec B608
        if order_by:
            sql += f" ORDER BY {order_by}"  # nosec B608
        return [_decode(model_cls, r) for r in self._fetch_all(sql, params)]

    def count(self, table: str, where: str = "", params: tuple = ()) -> int:
        sql = f"SELECT COUNT(*) AS n FROM {table}"  # nosec B608
        if where:
            sql += f" WHERE {where}"  # nosec B608
        row = self._fetch_one(sql, params)
        return int(row["n"]) if row else 0

    # ---- capability domains -------------------------------------------------

    def create_capability_domain(self, cd: m.CapabilityDomain) -> str:
        self.insert(cd)
        return cd.id

    def get_capability_domain(self, cid: str) -> m.CapabilityDomain | None:
        return self.get(m.CapabilityDomain, {"id": cid})

    def get_capability_domain_by_name(self, name: str) -> m.CapabilityDomain | None:
        return self.get(m.CapabilityDomain, {"name": name})

    def list_capability_domains(self) -> list[m.CapabilityDomain]:
        return self.list(m.CapabilityDomain, order_by="name")

    def delete_capability_domain(self, cid: str) -> int:
        return self.delete(m.CapabilityDomain, {"id": cid})

    # ---- strategies ---------------------------------------------------------

    def create_strategy(self, strategy: m.Strategy) -> str:
        self.insert(strategy)
        return strategy.id

    def get_strategy(self, sid: str) -> m.Strategy | None:
        return self.get(m.Strategy, {"id": sid})

    def list_strategies(self) -> list[m.Strategy]:
        return self.list(m.Strategy, order_by="name")

    def list_strategies_by_domain(self, cid: str) -> list[m.Strategy]:
        return self.list(
            m.Strategy,
            where="capability_domain_id = ?",
            params=(cid,),
            order_by="name",
        )

    def update_strategy(self, sid: str, fields: dict) -> int:
        return self.update(m.Strategy, {"id": sid}, fields)

    def delete_strategy(self, sid: str) -> int:
        return self.delete(m.Strategy, {"id": sid})

    # ---- datasets -----------------------------------------------------------

    def create_dataset(self, ds: m.DatasetDefinition) -> str:
        self.insert(ds)
        return ds.id

    def get_dataset(self, did: str) -> m.DatasetDefinition | None:
        return self.get(m.DatasetDefinition, {"id": did})

    def list_datasets(self) -> list[m.DatasetDefinition]:
        return self.list(m.DatasetDefinition, order_by="name")

    def delete_dataset(self, did: str) -> int:
        return self.delete(m.DatasetDefinition, {"id": did})

    # ---- stages -------------------------------------------------------------

    def get_stage_type(self, name: str) -> m.StageType | None:
        return self.get(m.StageType, {"name": name})

    def list_stage_types(self) -> list[m.StageType]:
        return self.list(m.StageType, order_by="name")

    # ---- workflows ----------------------------------------------------------

    def create_workflow(self, wf: m.Workflow) -> str:
        self.insert(wf)
        return wf.id

    def get_workflow(self, wid: str) -> m.Workflow | None:
        return self.get(m.Workflow, {"id": wid})

    def list_workflows(self) -> list[m.Workflow]:
        return self.list(m.Workflow, order_by="name")

    def update_workflow(self, wid: str, fields: dict) -> int:
        return self.update(m.Workflow, {"id": wid}, fields)

    def delete_workflow(self, wid: str) -> None:
        self._execute("DELETE FROM workflow_nodes WHERE workflow_id = ?", (wid,))
        self._execute("DELETE FROM workflow_edges WHERE workflow_id = ?", (wid,))
        self.delete(m.Workflow, {"id": wid})

    def create_workflow_nodes(self, nodes: list[m.WorkflowNode]) -> None:
        with self.transaction():
            for node in nodes:
                self.insert(node)

    def list_workflow_nodes(self, wid: str) -> list[m.WorkflowNode]:
        return self.list(
            m.WorkflowNode,
            where="workflow_id = ?",
            params=(wid,),
            order_by="position",
        )

    def get_workflow_node(self, nid: str) -> m.WorkflowNode | None:
        return self.get(m.WorkflowNode, {"id": nid})

    def delete_workflow_nodes(self, wid: str) -> None:
        self._execute("DELETE FROM workflow_nodes WHERE workflow_id = ?", (wid,))

    def replace_workflow_edges(self, wid: str, edges: list[tuple[str, str]]) -> None:
        with self.transaction():
            self._execute("DELETE FROM workflow_edges WHERE workflow_id = ?", (wid,))
            for frm, to in edges:
                self._conn.execute(
                    "INSERT OR IGNORE INTO workflow_edges (workflow_id, from_node,"
                    " to_node) VALUES (?, ?, ?)",
                    (wid, frm, to),
                )

    def list_workflow_edges(self, wid: str) -> list[m.WorkflowEdge]:
        rows = self._fetch_all(
            "SELECT * FROM workflow_edges WHERE workflow_id = ? ORDER BY"
            " from_node, to_node",
            (wid,),
        )
        return [m.WorkflowEdge(**r) for r in rows]

    # ---- runs / run_stages --------------------------------------------------

    def create_run(self, run: m.Run) -> str:
        self.insert(run)
        return run.id

    def get_run(self, rid: str) -> m.Run | None:
        return self.get(m.Run, {"id": rid})

    def list_runs(self, workflow_id: str = "") -> list[m.Run]:
        if workflow_id:
            return self.list(
                m.Run,
                where="workflow_id = ?",
                params=(workflow_id,),
                order_by="started_at DESC",
            )
        return self.list(m.Run, order_by="started_at DESC")

    def list_runs_by_dataset(self, did: str, version: int | None = None) -> list[m.Run]:
        if version is None:
            return self.list(
                m.Run,
                where="input_dataset_id = ?",
                params=(did,),
                order_by="started_at DESC",
            )
        return self.list(
            m.Run,
            where="input_dataset_id = ? AND input_dataset_version = ?",
            params=(did, version),
            order_by="started_at DESC",
        )

    def update_run(self, rid: str, fields: dict) -> int:
        return self.update(m.Run, {"id": rid}, fields)

    def upsert_run_stage(self, rs: m.RunStage) -> None:
        row = _encode_row(rs)
        cols = ", ".join(row)
        marks = ", ".join(f":{c}" for c in row)
        self._conn.execute(
            f"INSERT INTO run_stages ({cols}) VALUES ({marks})"  # nosec B608
            " ON CONFLICT(run_id, node_id) DO UPDATE SET"
            " status=excluded.status, rows_in=excluded.rows_in,"
            " rows_out=excluded.rows_out, failed_rows=excluded.failed_rows,"
            " attempts=excluded.attempts, started_at=excluded.started_at,"
            " finished_at=excluded.finished_at",
            row,
        )

    def get_run_stage(self, rid: str, nid: str) -> m.RunStage | None:
        return self.get(m.RunStage, {"run_id": rid, "node_id": nid})

    def list_run_stages(self, rid: str) -> list[m.RunStage]:
        return self.list(
            m.RunStage, where="run_id = ?", params=(rid,), order_by="node_id"
        )

    # ---- artifacts / dataset_versions ---------------------------------------

    def create_artifact(self, art: m.Artifact) -> str:
        self.insert(art)
        return art.id

    def get_artifact(self, aid: str) -> m.Artifact | None:
        return self.get(m.Artifact, {"id": aid})

    def list_artifacts(self, run_id: str) -> list[m.Artifact]:
        return self.list(
            m.Artifact, where="run_id = ?", params=(run_id,), order_by="node_id"
        )

    def create_dataset_version(self, dv: m.DatasetVersion) -> str:
        self.insert(dv)
        return dv.id

    def get_dataset_version(self, did: str, version: int) -> m.DatasetVersion | None:
        return self.get(m.DatasetVersion, {"dataset_id": did, "version": version})

    def list_dataset_versions(self, did: str) -> list[m.DatasetVersion]:
        return self.list(
            m.DatasetVersion,
            where="dataset_id = ?",
            params=(did,),
            order_by="version DESC",
        )

    def next_dataset_version(self, did: str) -> int:
        row = self._fetch_one(
            "SELECT COALESCE(MAX(version), 0) + 1 AS n FROM dataset_versions"
            " WHERE dataset_id = ?",
            (did,),
        )
        return int(row["n"])

    # ---- models -------------------------------------------------------------

    def create_model(self, model: m.Model) -> str:
        self.insert(model)
        return model.id

    def get_model(self, mid: str) -> m.Model | None:
        return self.get(m.Model, {"id": mid})

    def get_model_by_name(self, name: str) -> m.Model | None:
        return self.get(m.Model, {"name": name})

    def list_models(self) -> list[m.Model]:
        return self.list(m.Model, order_by="name")

    def update_model(self, mid: str, fields: dict) -> int:
        return self.update(m.Model, {"id": mid}, fields)

    def delete_model(self, mid: str) -> int:
        return self.delete(m.Model, {"id": mid})

    def models_in_dir(self, weights_dir: str) -> list[m.Model]:
        return self.list(m.Model, where="weights_dir = ?", params=(weights_dir,))

    # ---- eval sets / items --------------------------------------------------

    def create_eval_set(self, evs: m.EvalSet) -> str:
        self.insert(evs)
        return evs.id

    def get_eval_set(self, eid: str) -> m.EvalSet | None:
        return self.get(m.EvalSet, {"id": eid})

    def list_eval_sets(self) -> list[m.EvalSet]:
        return self.list(m.EvalSet, order_by="name")

    def delete_eval_set(self, eid: str) -> None:
        self._execute("DELETE FROM eval_items WHERE eval_set_id = ?", (eid,))
        self.delete(m.EvalSet, {"id": eid})

    def create_eval_item(self, item: m.EvalItem) -> None:
        self.insert(item)

    def list_eval_items(self, eid: str) -> list[m.EvalItem]:
        return self.list(
            m.EvalItem, where="eval_set_id = ?", params=(eid,), order_by="seq"
        )

    def count_eval_items(self, eid: str) -> int:
        return self.count("eval_items", where="eval_set_id = ?", params=(eid,))

    def set_eval_item_count(self, eid: str) -> None:
        self.update(m.EvalSet, {"id": eid}, {"item_count": self.count_eval_items(eid)})

    # ---- eval runs / results ------------------------------------------------

    def create_eval_run(self, er: m.EvalRun) -> str:
        self.insert(er)
        return er.id

    def get_eval_run(self, eid: str) -> m.EvalRun | None:
        return self.get(m.EvalRun, {"id": eid})

    def list_eval_runs(self, eval_set_id: str = "") -> list[m.EvalRun]:
        if eval_set_id:
            return self.list(
                m.EvalRun,
                where="eval_set_id = ?",
                params=(eval_set_id,),
                order_by="created_at DESC",
            )
        return self.list(m.EvalRun, order_by="created_at DESC")

    def update_eval_run(self, eid: str, fields: dict) -> int:
        return self.update(m.EvalRun, {"id": eid}, fields)

    def create_eval_result(self, result: m.EvalResult) -> None:
        row = _encode_row(result)
        if row["id"] == 0:
            row.pop("id")  # AUTOINCREMENT: let SQLite assign it
        cols = ", ".join(row)
        marks = ", ".join(f":{c}" for c in row)
        self._conn.execute(
            f"INSERT INTO eval_results ({cols}) VALUES ({marks})",  # nosec B608
            row,
        )

    def list_eval_results(self, rid: str) -> list[m.EvalResult]:
        rows = self._fetch_all(
            "SELECT er.* FROM eval_results er"
            " JOIN eval_items ei ON ei.id = er.item_id"
            " WHERE er.eval_run_id = ? ORDER BY ei.seq",
            (rid,),
        )
        return [_decode(m.EvalResult, r) for r in rows]

    # ---- reports ------------------------------------------------------------

    def create_report(self, rep: m.Report) -> str:
        self.insert(rep)
        return rep.id

    def get_report(self, rid: str) -> m.Report | None:
        return self.get(m.Report, {"id": rid})

    def list_reports(self, eval_run_id: str = "") -> list[m.Report]:
        if eval_run_id:
            return self.list(
                m.Report,
                where="eval_run_id = ?",
                params=(eval_run_id,),
                order_by="created_at DESC",
            )
        return self.list(m.Report, order_by="created_at DESC")
