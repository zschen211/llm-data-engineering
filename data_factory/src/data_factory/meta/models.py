"""Data models for data-factory (mirror the SQLite schema)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC

RUN_PENDING = "pending"
RUN_RUNNING = "running"
RUN_SUCCEEDED = "succeeded"
RUN_FAILED = "failed"
RUN_CANCELLED = "cancelled"

MODEL_PENDING = "pending"
MODEL_READY = "ready"
MODEL_FAILED = "failed"

EVAL_RUNNING = "running"
EVAL_SUCCEEDED = "succeeded"
EVAL_FAILED = "failed"
EVAL_PARTIAL = "partial"


def _now() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class CapabilityDomain:
    id: str
    name: str
    description: str = ""
    parent_id: str = ""
    created_at: str = field(default_factory=_now)


@dataclass
class Strategy:
    id: str
    name: str
    capability_domain_id: str
    description: str = ""
    enabled: bool = True
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)


@dataclass
class DatasetDefinition:
    id: str
    name: str
    source_type: str
    snapshot_id: str = ""
    tag_filters: list[dict] = field(default_factory=list)
    import_manifest: str = ""
    derived_from: str = ""
    created_at: str = field(default_factory=_now)


@dataclass
class StageType:
    name: str
    module: str
    kind: str
    description: str = ""
    config_schema: dict = field(default_factory=dict)


@dataclass
class Workflow:
    id: str
    name: str
    strategy_id: str
    description: str = ""
    enabled: bool = True
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)


@dataclass
class WorkflowNode:
    id: str
    workflow_id: str
    stage_name: str
    node_label: str = ""
    position: int = 0
    config: dict = field(default_factory=dict)


@dataclass
class WorkflowEdge:
    workflow_id: str
    from_node: str
    to_node: str


@dataclass
class Run:
    id: str
    workflow_id: str
    input_dataset_id: str
    input_dataset_version: int = 1
    status: str = RUN_PENDING
    params: dict = field(default_factory=dict)
    error: str = ""
    started_at: str = ""
    finished_at: str = ""
    stats: dict = field(default_factory=dict)


@dataclass
class RunStage:
    run_id: str
    node_id: str
    status: str = RUN_PENDING
    rows_in: int = 0
    rows_out: int = 0
    failed_rows: int = 0
    attempts: int = 0
    started_at: str = ""
    finished_at: str = ""


@dataclass
class Artifact:
    id: str
    run_id: str
    node_id: str = ""
    kind: str = "intermediate"
    object_key: str = ""
    sha256: str = ""
    size: int = 0
    row_count: int = 0
    dataset_version_id: str = ""
    created_at: str = field(default_factory=_now)


@dataclass
class DatasetVersion:
    id: str
    dataset_id: str
    version: int
    artifact_id: str = ""
    manifest_key: str = ""
    row_count: int = 0
    created_at: str = field(default_factory=_now)


@dataclass
class Model:
    id: str
    name: str
    backend: str
    model_id: str = ""
    weights_dir: str = ""
    base_url: str = ""
    api_key_env: str = ""
    status: str = MODEL_PENDING
    last_check_at: str = ""
    last_error: str = ""
    params: dict = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)


@dataclass
class EvalSet:
    id: str
    name: str
    capability_domain_id: str = ""
    source: str = "import"
    rubric: dict = field(default_factory=dict)
    item_count: int = 0
    created_at: str = field(default_factory=_now)


@dataclass
class EvalItem:
    id: str
    eval_set_id: str
    seq: int
    question: dict
    expected: str = ""
    rubric: dict | None = None
    category: str = ""


@dataclass
class EvalRun:
    id: str
    eval_set_id: str
    model_id: str
    status: str = EVAL_RUNNING
    started_at: str = ""
    finished_at: str = ""
    aggregate: dict = field(default_factory=dict)
    error: str = ""
    created_at: str = field(default_factory=_now)


@dataclass
class EvalResult:
    id: int
    eval_run_id: str
    item_id: str
    model_output: str = ""
    score: dict = field(default_factory=dict)
    latency_ms: int = 0
    error: str = ""


@dataclass
class Report:
    id: str
    eval_run_id: str
    capability_domain_id: str = ""
    aggregate: dict = field(default_factory=dict)
    badcases: list = field(default_factory=list)
    attribution: dict = field(default_factory=dict)
    json_key: str = ""
    md_key: str = ""
    created_at: str = field(default_factory=_now)
