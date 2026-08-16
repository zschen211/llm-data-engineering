"""Pipeline services: capability domains, strategies, datasets, workflows,
runs, lineage — the data-production side of the factory.

Mixed into the ``DataFactory`` facade; the executor + DAG validator do the
actual execution. Workflow definition is validated eagerly (unknown stages,
chain-only DAG) so a broken workflow never reaches a run.
"""

from __future__ import annotations

from pathlib import Path

from . import jsonl
from .meta import models as m
from .meta.db import new_id
from .strategies import dag
from .strategies.executor import PipelineExecutor, ensure_ray
from .strategies.stages import REGISTRY, build_stage


class PipelineService:
    """Strategy/workflow/run operations (mixin of DataFactory)."""

    # ---- capability domains -------------------------------------------------

    def create_capability_domain(
        self, name: str, description: str = "", parent_id: str = ""
    ) -> m.CapabilityDomain:
        if self._db.get_capability_domain_by_name(name) is not None:
            raise ValueError(f"capability domain already exists: {name}")
        domain = m.CapabilityDomain(
            id=new_id("cd_"),
            name=name,
            description=description,
            parent_id=parent_id,
        )
        self._db.create_capability_domain(domain)
        return domain

    def list_capability_domains(self) -> list[m.CapabilityDomain]:
        return self._db.list_capability_domains()

    # ---- strategies ---------------------------------------------------------

    def create_strategy(
        self, name: str, capability_domain_id: str, description: str = ""
    ) -> m.Strategy:
        strategy = m.Strategy(
            id=new_id("st_"),
            name=name,
            capability_domain_id=capability_domain_id,
            description=description,
        )
        self._db.create_strategy(strategy)
        return strategy

    def list_strategies(self) -> list[m.Strategy]:
        return self._db.list_strategies()

    # ---- datasets -----------------------------------------------------------

    def create_dataset(
        self,
        name: str,
        source_type: str,
        snapshot_id: str = "",
        tag_filters: list | None = None,
        import_manifest: str = "",
        derived_from: str = "",
    ) -> m.DatasetDefinition:
        if source_type not in ("snapshot", "import", "derived"):
            raise ValueError(
                f"unknown source_type: {source_type} (snapshot|import|derived)"
            )
        if source_type == "snapshot" and not snapshot_id:
            raise ValueError("snapshot dataset needs snapshot_id")
        if source_type == "import" and not import_manifest:
            raise ValueError("import dataset needs import_manifest")
        if source_type == "derived" and not derived_from:
            raise ValueError("derived dataset needs derived_from (id@version)")
        dataset = m.DatasetDefinition(
            id=new_id("ds_"),
            name=name,
            source_type=source_type,
            snapshot_id=snapshot_id,
            tag_filters=tag_filters or [],
            import_manifest=import_manifest,
            derived_from=derived_from,
        )
        self._db.create_dataset(dataset)
        return dataset

    def list_datasets(self) -> list[m.DatasetDefinition]:
        return self._db.list_datasets()

    # ---- workflows ----------------------------------------------------------

    def define_workflow(
        self,
        strategy_id: str,
        name: str,
        stages: list[tuple[str, dict | None]] | None = None,
        description: str = "",
    ) -> m.Workflow:
        """Create a workflow; ``stages`` = [(stage_name, config), ...] in
        execution order (chain)."""
        workflow = m.Workflow(
            id=new_id("wf_"),
            name=name,
            strategy_id=strategy_id,
            description=description,
        )
        self._db.create_workflow(workflow)
        for stage_name, config in stages or []:
            self._add_workflow_node(workflow.id, stage_name, config)
        self.validate_workflow(workflow.id)
        return workflow

    def _add_workflow_node(
        self, workflow_id: str, stage_name: str, config: dict | None = None
    ) -> None:
        if stage_name not in REGISTRY:
            raise ValueError(
                f"unknown stage: {stage_name} (registered: {sorted(REGISTRY)})"
            )
        position = len(self._db.list_workflow_nodes(workflow_id))
        node = m.WorkflowNode(
            id=new_id("nd_"),
            workflow_id=workflow_id,
            stage_name=stage_name,
            node_label=f"{stage_name}#{position}",
            position=position,
            config=config or {},
        )
        self._db.create_workflow_nodes([node])
        nodes = self._db.list_workflow_nodes(workflow_id)
        self._db.replace_workflow_edges(
            workflow_id,
            [(nodes[i].id, nodes[i + 1].id) for i in range(len(nodes) - 1)],
        )

    def validate_workflow(self, workflow_id: str) -> list[str]:
        """Validate the workflow DAG; return the execution order."""
        workflow = self._db.get_workflow(workflow_id)
        if workflow is None:
            raise ValueError(f"unknown workflow: {workflow_id}")
        nodes = self._db.list_workflow_nodes(workflow_id)
        return dag.validate(nodes, self._db.list_workflow_edges(workflow_id))

    def list_workflows(self) -> list[m.Workflow]:
        return self._db.list_workflows()

    def show_workflow(self, workflow_id: str) -> dict:
        order = self.validate_workflow(workflow_id)
        nodes = {n.id: n for n in self._db.list_workflow_nodes(workflow_id)}
        return {
            "workflow_id": workflow_id,
            "order": [
                {
                    "node_id": nid,
                    "stage": nodes[nid].stage_name,
                    "config": nodes[nid].config,
                }
                for nid in order
            ],
        }

    # ---- runs ---------------------------------------------------------------

    def create_run(
        self, workflow_id: str, input_dataset_id: str, params: dict | None = None
    ) -> m.Run:
        workflow = self._db.get_workflow(workflow_id)
        if workflow is None:
            raise ValueError(f"unknown workflow: {workflow_id}")
        if self._db.get_dataset(input_dataset_id) is None:
            raise ValueError(f"unknown dataset: {input_dataset_id}")
        run = m.Run(
            id=new_id("run_"),
            workflow_id=workflow_id,
            input_dataset_id=input_dataset_id,
            params=params or {},
        )
        self._db.create_run(run)
        return run

    def run_workflow(self, run_id: str) -> m.Run:
        """Execute a run (Ray Data linear chain; resumable)."""
        executor = PipelineExecutor(self._db, self.backend, self.tmp_dir)
        return executor.run(run_id)

    def cancel_run(self, run_id: str) -> m.Run:
        self._db.update_run(
            run_id, {"status": m.RUN_CANCELLED, "finished_at": m._now()}
        )
        return self._db.get_run(run_id)

    def list_runs(self, workflow_id: str = "") -> list[m.Run]:
        return self._db.list_runs(workflow_id)

    def show_run(self, run_id: str) -> dict:
        run = self._db.get_run(run_id)
        if run is None:
            raise ValueError(f"unknown run: {run_id}")
        return {
            "run": run,
            "stages": [self._stage_view(s) for s in self._db.list_run_stages(run_id)],
            "artifacts": self._db.list_artifacts(run_id),
        }

    def _stage_view(self, rs: m.RunStage) -> dict:
        node = self._db.get_workflow_node(rs.node_id)
        return {
            "node_id": rs.node_id,
            "stage": node.stage_name if node else "",
            "status": rs.status,
            "rows_in": rs.rows_in,
            "rows_out": rs.rows_out,
            "failed_rows": rs.failed_rows,
            "attempts": rs.attempts,
        }

    # ---- single-stage debugging --------------------------------------------

    def stage_run(
        self, stage_name: str, input_path: Path, config: dict | None = None
    ) -> list[dict]:
        """Run one stage over a sample JSONL file, without a workflow."""
        rows = jsonl.read_rows_from_path(Path(input_path))
        ensure_ray()
        if REGISTRY[stage_name].kind == "sink":
            raise ValueError("sink stages need a workflow run (config dataset_id)")
        stage = build_stage(stage_name, config)
        import ray

        return stage.transform(ray.data.from_items(rows)).take_all()
