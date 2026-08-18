// data-factory pages: the production & eval loop (capabilities/strategies/
// datasets/workflows/runs/stages/models/eval-sets/eval-runs/reports/lineage)
// — all under /api/* of :8001.

import { useState, type ReactNode } from "react";
import { toast } from "sonner";
import { del, get, post } from "../api";
import { Button } from "@/components/ui/button";
import {
  DataTable,
  ErrorNote,
  Field,
  FieldItem,
  fmtTime,
  FormModal,
  InfoCard,
  JsonBlock,
  Modal,
  Mono,
  PageContainer,
  Status,
  Table,
  useFetch,
  type Column,
} from "../widgets";

// ---- simple CRUD helper ------------------------------------------------------

interface FieldSpec {
  name: string;
  label: string;
  kind?: "text" | "select" | "textarea";
  options?: string[];
  placeholder?: string;
  mono?: boolean;
}

function SimpleCrud<T extends { id: string }>({
  title,
  desc,
  base,
  columns,
  fields,
  emptyBody,
  extraActions,
}: {
  title: string;
  desc: string;
  base: string;
  columns: Column<T>[];
  fields: FieldSpec[];
  emptyBody: Record<string, unknown>;
  extraActions?: (row: T) => ReactNode;
}) {
  const { data, error, reload } = useFetch<T[]>(base);
  const [creating, setCreating] = useState(false);
  const [body, setBody] = useState<Record<string, unknown>>({});

  const openCreate = () => {
    setBody({ ...emptyBody });
    setCreating(true);
  };

  const submit = async () => {
    try {
      await post(base, body);
      toast.success(`已创建到 ${base}`);
      setCreating(false);
      reload();
    } catch (err) {
      toast.error(String(err));
    }
  };

  const allColumns: Column<T>[] = extraActions
    ? [
        ...columns,
        { key: "ops", label: "", render: (row) => <>{extraActions(row)}</> },
      ]
    : columns;

  return (
    <PageContainer
      desc={desc}
      actions={<Button onClick={openCreate}>新建</Button>}
    >
      <ErrorNote message={error} />
      <DataTable columns={allColumns} rows={data ?? []} />
      {creating && (
        <FormModal
          title={`新建${title}`}
          onClose={() => setCreating(false)}
          onConfirm={() => void submit()}
          confirmLabel="创建"
        >
          {fields.map((f) => (
            <Field
              key={f.name}
              label={f.label}
              kind={f.kind ?? "text"}
              options={f.options}
              placeholder={f.placeholder}
              mono={f.mono}
              value={String(body[f.name] ?? "")}
              onChange={(v) => setBody((b) => ({ ...b, [f.name]: v }))}
            />
          ))}
        </FormModal>
      )}
    </PageContainer>
  );
}

// ---- overview ---------------------------------------------------------------

interface FactoryInfo {
  backend: string;
  bucket: string | null;
  data_dir: string;
  db_path: string;
  models_dir: string;
  capability_count: number;
  strategy_count: number;
  dataset_count: number;
  workflow_count: number;
  run_count: number;
  model_count: number;
  eval_set_count: number;
  eval_run_count: number;
  report_count: number;
}

function FactoryOverviewPage() {
  const { data, error, reload } = useFetch<FactoryInfo>("/api/factory-info");

  return (
    <PageContainer
      desc="数据生产与评测闭环：策略管线产训练数据，评测反推能力缺口，驱动新一轮生产。"
      actions={
        <Button variant="outline" onClick={reload}>
          刷新
        </Button>
      }
    >
      <ErrorNote message={error} />
      {data && (
        <InfoCard
          items={[
            ["capabilities", data.capability_count],
            ["strategies", data.strategy_count],
            ["workflows", data.workflow_count],
            ["runs", data.run_count],
            ["models", data.model_count],
            ["eval runs", data.eval_run_count],
            ["reports", data.report_count],
            ["backend", <Mono key="b">{data.backend}</Mono>],
            ["bucket", <Mono key="bk">{data.bucket ?? "-"}</Mono>],
            ["data_dir", <Mono key="dd">{data.data_dir}</Mono>],
            ["models_dir", <Mono key="md">{data.models_dir}</Mono>],
          ]}
        />
      )}
    </PageContainer>
  );
}

// ---- capabilities ------------------------------------------------------------

interface Capability {
  id: string;
  name: string;
  description: string;
  parent_id: string;
}

function CapabilitiesPage() {
  return (
    <SimpleCrud<Capability>
      title="能力域"
      desc="模型能力域的登记表；评测报告按能力域聚合与归因。"
      base="/api/capabilities"
      emptyBody={{ name: "", description: "", parent_id: "" }}
      fields={[
        { name: "name", label: "name", mono: true },
        { name: "description", label: "description" },
        { name: "parent_id", label: "parent_id", mono: true },
      ]}
      columns={[
        { key: "name", label: "name", render: (c) => <Mono>{c.name}</Mono> },
        { key: "description", label: "description" },
        { key: "parent_id", label: "parent_id", render: (c) => <Mono>{c.parent_id || "-"}</Mono> },
      ]}
    />
  );
}

// ---- strategies ---------------------------------------------------------------

interface Strategy {
  id: string;
  name: string;
  capability_domain_id: string;
  description: string;
}

function StrategiesPage() {
  return (
    <SimpleCrud<Strategy>
      title="策略"
      desc="针对特定能力域的数据生产策略；工作流挂在策略下。"
      base="/api/strategies"
      emptyBody={{ name: "", capability_domain_id: "", description: "" }}
      fields={[
        { name: "name", label: "name", mono: true },
        { name: "capability_domain_id", label: "capability_domain_id", mono: true },
        { name: "description", label: "description" },
      ]}
      columns={[
        { key: "name", label: "name", render: (s) => <Mono>{s.name}</Mono> },
        { key: "cap", label: "capability_domain_id", render: (s) => <Mono>{s.capability_domain_id}</Mono> },
        { key: "description", label: "description" },
      ]}
    />
  );
}

// ---- datasets -----------------------------------------------------------------

interface Dataset {
  id: string;
  name: string;
  source_type: string;
  snapshot_id: string;
  tag_filters: unknown;
  import_manifest: string;
  derived_from: string;
}

function DatasetsPage() {
  const { data, error, reload } = useFetch<Dataset[]>("/api/datasets");
  const [creating, setCreating] = useState(false);
  const [body, setBody] = useState({
    name: "",
    source_type: "import",
    snapshot_id: "",
    tag_filters: "[]",
    import_manifest: "",
    derived_from: "",
  });

  const submit = async () => {
    try {
      const tag_filters = JSON.parse(body.tag_filters || "[]");
      await post("/api/datasets", { ...body, tag_filters });
      toast.success("数据集已创建");
      setCreating(false);
      reload();
    } catch (err) {
      toast.error(String(err));
    }
  };

  const set = (name: string) => (v: string) => setBody((b) => ({ ...b, [name]: v }));

  const columns: Column<Dataset>[] = [
    { key: "name", label: "name", render: (d) => <Mono>{d.name}</Mono> },
    { key: "type", label: "source_type", render: (d) => <Mono>{d.source_type}</Mono> },
    { key: "snapshot", label: "snapshot_id", render: (d) => <Mono>{d.snapshot_id || "-"}</Mono> },
    { key: "import", label: "import_manifest", render: (d) => <Mono>{d.import_manifest || "-"}</Mono> },
    { key: "derived", label: "derived_from", render: (d) => <Mono>{d.derived_from || "-"}</Mono> },
  ];

  return (
    <PageContainer
      desc="run 的输入定义：资产快照+标签过滤（运行即固化）/ 外部导入 / 上游数据集版本派生。"
      actions={<Button onClick={() => setCreating(true)}>新建数据集</Button>}
    >
      <ErrorNote message={error} />
      <DataTable columns={columns} rows={data ?? []} />
      {creating && (
        <FormModal
          title="新建数据集"
          onClose={() => setCreating(false)}
          onConfirm={() => void submit()}
          confirmLabel="创建"
        >
          <Field label="name" value={body.name} onChange={set("name")} mono />
          <Field
            label="source_type"
            value={body.source_type}
            onChange={set("source_type")}
            kind="select"
            options={["snapshot", "import", "derived"]}
          />
          {body.source_type === "snapshot" && (
            <Field label="snapshot_id" value={body.snapshot_id} onChange={set("snapshot_id")} mono />
          )}
          {body.source_type === "snapshot" && (
            <Field
              label="tag_filters (json)"
              value={body.tag_filters}
              onChange={set("tag_filters")}
              kind="textarea"
              mono
            />
          )}
          {body.source_type === "import" && (
            <Field label="import_manifest" value={body.import_manifest} onChange={set("import_manifest")} mono />
          )}
          {body.source_type === "derived" && (
            <Field label="derived_from (id@version)" value={body.derived_from} onChange={set("derived_from")} mono />
          )}
        </FormModal>
      )}
    </PageContainer>
  );
}

// ---- workflows ----------------------------------------------------------------

interface Workflow {
  id: string;
  name: string;
  strategy_id: string;
  description: string;
}

interface WorkflowShow {
  workflow_id: string;
  order: Array<{ node_id: string; stage: string; config: unknown }>;
}

function WorkflowsPage() {
  const { data, error, reload } = useFetch<Workflow[]>("/api/workflows");
  const [creating, setCreating] = useState(false);
  const [show, setShow] = useState<WorkflowShow | null>(null);
  const [body, setBody] = useState({ name: "", strategy_id: "", stages: "[{\"stage\":\"schema_check\"}]", description: "" });

  const submit = async () => {
    try {
      const stages = JSON.parse(body.stages);
      await post("/api/workflows", { ...body, stages });
      toast.success("工作流已定义");
      setCreating(false);
      reload();
    } catch (err) {
      toast.error(String(err));
    }
  };

  const set = (name: string) => (v: string) => setBody((b) => ({ ...b, [name]: v }));

  const viewWorkflow = async (id: string) => {
    try {
      setShow(await get<WorkflowShow>(`/api/workflows/${id}`));
    } catch (err) {
      toast.error(String(err));
    }
  };

  const validate = async (id: string) => {
    try {
      const result = await post<{ order: string[] }>(`/api/workflows/${id}/validate`);
      toast.success(`校验通过：${result.order.length} 个节点`);
    } catch (err) {
      toast.error(String(err));
    }
  };

  const columns: Column<Workflow>[] = [
    { key: "name", label: "name", render: (w) => <Mono>{w.name}</Mono> },
    { key: "strategy", label: "strategy_id", render: (w) => <Mono>{w.strategy_id}</Mono> },
    { key: "description", label: "description" },
    {
      key: "ops",
      label: "",
      render: (w) => (
        <span className="flex flex-wrap items-center gap-1.5">
          <Button size="sm" variant="outline" onClick={() => void viewWorkflow(w.id)}>
            查看
          </Button>
          <Button size="sm" variant="outline" onClick={() => void validate(w.id)}>
            校验
          </Button>
        </span>
      ),
    },
  ];

  return (
    <PageContainer
      desc="链式阶段 DAG：transform / qc_rule / qc_llm / publish（sink）。stages 为 JSON 数组。"
      actions={<Button onClick={() => setCreating(true)}>定义工作流</Button>}
    >
      <ErrorNote message={error} />
      <DataTable columns={columns} rows={data ?? []} />
      {show && (
        <Modal title={show.workflow_id} onClose={() => setShow(null)}>
          <JsonBlock value={show} />
        </Modal>
      )}
      {creating && (
        <FormModal
          title="定义工作流"
          onClose={() => setCreating(false)}
          onConfirm={() => void submit()}
          confirmLabel="定义"
        >
          <Field label="name" value={body.name} onChange={set("name")} mono />
          <Field label="strategy_id" value={body.strategy_id} onChange={set("strategy_id")} mono />
          <Field
            label="stages (json)"
            value={body.stages}
            onChange={set("stages")}
            kind="textarea"
            mono
          />
          <Field label="description" value={body.description} onChange={set("description")} />
        </FormModal>
      )}
    </PageContainer>
  );
}

// ---- runs ----------------------------------------------------------------------

interface Run {
  id: string;
  workflow_id: string;
  input_dataset_id: string;
  status: string;
  started_at: string;
  finished_at: string;
}

interface RunShow {
  run: Run;
  stages: Array<{
    node_id: string;
    stage: string;
    status: string;
    rows_in: number;
    rows_out: number;
    failed_rows: number;
  }>;
}

function RunsPage() {
  const { data, error, reload } = useFetch<Run[]>("/api/runs");
  const [creating, setCreating] = useState(false);
  const [show, setShow] = useState<RunShow | null>(null);
  const [body, setBody] = useState({ workflow_id: "", input_dataset_id: "" });

  const set = (name: string) => (v: string) => setBody((b) => ({ ...b, [name]: v }));

  const runAction = async (fn: () => Promise<unknown>, okMsg: string) => {
    try {
      await fn();
      toast.success(okMsg);
      reload();
    } catch (err) {
      toast.error(String(err));
    }
  };

  const createRun = async () => {
    try {
      const run = await post<Run>("/api/runs", body);
      toast.success(`run 已创建：${run.id}`);
      setCreating(false);
      reload();
    } catch (err) {
      toast.error(String(err));
    }
  };

  const columns: Column<Run>[] = [
    { key: "id", label: "run_id", render: (r) => <Mono>{r.id}</Mono> },
    { key: "wf", label: "workflow_id", render: (r) => <Mono>{r.workflow_id}</Mono> },
    { key: "ds", label: "dataset_id", render: (r) => <Mono>{r.input_dataset_id}</Mono> },
    { key: "status", label: "status", render: (r) => <Status status={r.status} /> },
    { key: "started", label: "started_at", render: (r) => <Mono>{fmtTime(r.started_at)}</Mono> },
    {
      key: "ops",
      label: "",
      render: (r) => (
        <span className="flex flex-wrap items-center gap-1.5">
          <Button
            size="sm"
            variant="outline"
            disabled={r.status === "running"}
            onClick={() => void runAction(() => post(`/api/runs/${r.id}/run`), "已启动执行")}
          >
            执行
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              void get<RunShow>(`/api/runs/${r.id}`)
                .then(setShow)
                .catch((err: unknown) => toast.error(String(err)))
            }
          >
            详情
          </Button>
          <Button
            size="sm"
            variant="destructive"
            disabled={r.status !== "running"}
            onClick={() => void runAction(() => post(`/api/runs/${r.id}/cancel`), "已取消")}
          >
            取消
          </Button>
        </span>
      ),
    },
  ];

  return (
    <PageContainer
      desc="工作流 × 输入数据集的一次执行；Ray Data 链式跑，断点可续。"
      actions={<Button onClick={() => setCreating(true)}>新建运行</Button>}
    >
      <ErrorNote message={error} />
      <DataTable columns={columns} rows={data ?? []} />
      {creating && (
        <FormModal
          title="新建 run"
          onClose={() => setCreating(false)}
          onConfirm={() => void createRun()}
          confirmLabel="创建"
        >
          <Field label="workflow_id" value={body.workflow_id} onChange={set("workflow_id")} mono />
          <Field label="input_dataset_id" value={body.input_dataset_id} onChange={set("input_dataset_id")} mono />
        </FormModal>
      )}
      {show && (
        <Modal title={`run ${show.run.id}`} onClose={() => setShow(null)}>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-2.5">
            <FieldItem label="status">
              <Status status={show.run.status} />
            </FieldItem>
            <FieldItem label="workflow">
              <Mono>{show.run.workflow_id}</Mono>
            </FieldItem>
            <FieldItem label="dataset">
              <Mono>{show.run.input_dataset_id}</Mono>
            </FieldItem>
          </div>
          <Table
            columns={[
              { key: "stage", label: "stage", render: (s) => <Mono>{s.stage}</Mono> },
              { key: "status", label: "status", render: (s) => <Status status={s.status} /> },
              { key: "in", label: "rows_in", render: (s) => <Mono>{s.rows_in}</Mono> },
              { key: "out", label: "rows_out", render: (s) => <Mono>{s.rows_out}</Mono> },
              { key: "failed", label: "failed", render: (s) => <Mono>{s.failed_rows}</Mono> },
            ]}
            rows={show.stages}
          />
        </Modal>
      )}
    </PageContainer>
  );
}

// ---- stages ---------------------------------------------------------------------

interface StageInfo {
  name: string;
  kind: string;
  description: string;
  config_schema: unknown;
}

function StagesPage() {
  const { data, error, reload } = useFetch<StageInfo[]>("/api/stages");

  return (
    <PageContainer
      desc="内置管线阶段；配置结构见 config_schema。"
      actions={
        <Button variant="outline" onClick={reload}>
          刷新
        </Button>
      }
    >
      <ErrorNote message={error} />
      <DataTable
        columns={[
          { key: "name", label: "name", render: (s) => <Mono>{s.name}</Mono> },
          { key: "kind", label: "kind", render: (s) => <Mono>{s.kind}</Mono> },
          { key: "description", label: "description" },
        ]}
        rows={data ?? []}
      />
    </PageContainer>
  );
}

// ---- models ---------------------------------------------------------------------

interface Model {
  id: string;
  name: string;
  backend: string;
  model_id: string;
  base_url: string;
  status: string;
  last_error: string;
}

function ModelsPage() {
  const { data, error, reload } = useFetch<Model[]>("/api/models");
  const [creating, setCreating] = useState(false);
  const [body, setBody] = useState({
    name: "",
    backend: "api",
    model_id: "",
    weights_dir: "",
    base_url: "",
    api_key_env: "",
  });

  const set = (name: string) => (v: string) => setBody((b) => ({ ...b, [name]: v }));

  const runAction = async (fn: () => Promise<unknown>, okMsg: string) => {
    try {
      await fn();
      toast.success(okMsg);
      reload();
    } catch (err) {
      toast.error(String(err));
    }
  };

  const register = async () => {
    try {
      await post("/api/models", body);
      toast.success("模型已注册");
      setCreating(false);
      reload();
    } catch (err) {
      toast.error(String(err));
    }
  };

  const columns: Column<Model>[] = [
    { key: "name", label: "name", render: (m) => <Mono>{m.name}</Mono> },
    { key: "backend", label: "backend", render: (m) => <Mono>{m.backend}</Mono> },
    { key: "model_id", label: "model_id", render: (m) => <Mono>{m.model_id || "-"}</Mono> },
    { key: "url", label: "base_url", render: (m) => <Mono>{m.base_url || "-"}</Mono> },
    { key: "status", label: "status", render: (m) => <Status status={m.status} /> },
    {
      key: "ops",
      label: "",
      render: (m) => (
        <span className="flex flex-wrap items-center gap-1.5">
          <Button
            size="sm"
            variant="outline"
            onClick={() => void runAction(() => post(`/api/models/${m.id}/check`), "心跳已刷新")}
          >
            检查
          </Button>
          <Button
            size="sm"
            variant="destructive"
            onClick={() =>
              window.confirm(`删除模型「${m.name}」？`) &&
              void runAction(() => del(`/api/models/${m.id}`), "已删除")
            }
          >
            删除
          </Button>
        </span>
      ),
    },
  ];

  return (
    <PageContainer
      desc="local（checkpoint 目录）/ vllm / api（OpenAI 兼容）；api_key 只存环境变量名。"
      actions={
        <>
          <Button onClick={() => setCreating(true)}>注册模型</Button>
          <Button
            variant="outline"
            onClick={() => void runAction(() => post("/api/models/scan"), "目录扫描完成")}
          >
            扫描本地模型
          </Button>
        </>
      }
    >
      <ErrorNote message={error} />
      <DataTable columns={columns} rows={data ?? []} />
      {creating && (
        <FormModal
          title="注册模型"
          onClose={() => setCreating(false)}
          onConfirm={() => void register()}
          confirmLabel="注册"
        >
          <Field label="name" value={body.name} onChange={set("name")} mono />
          <Field
            label="backend"
            value={body.backend}
            onChange={set("backend")}
            kind="select"
            options={["api", "vllm", "local"]}
          />
          {body.backend === "local" && <Field label="weights_dir" value={body.weights_dir} onChange={set("weights_dir")} mono />}
          {(body.backend === "api" || body.backend === "vllm") && (
            <Field label="base_url" value={body.base_url} onChange={set("base_url")} mono />
          )}
          {(body.backend === "api" || body.backend === "vllm") && (
            <Field label="api_key_env" value={body.api_key_env} onChange={set("api_key_env")} mono />
          )}
          <Field label="model_id" value={body.model_id} onChange={set("model_id")} mono />
        </FormModal>
      )}
    </PageContainer>
  );
}

// ---- eval sets ------------------------------------------------------------------

interface EvalSet {
  id: string;
  name: string;
  item_count: number;
  capability_domain_id: string;
}

interface EvalSetShow {
  eval_set: EvalSet;
  items: Array<{ seq: number; question: unknown; expected: string; category: string }>;
}

function EvalSetsPage() {
  const { data, error, reload } = useFetch<EvalSet[]>("/api/eval-sets");
  const [creating, setCreating] = useState(false);
  const [show, setShow] = useState<EvalSetShow | null>(null);
  const [body, setBody] = useState({ name: "", capability_domain_id: "", items: "[]" });

  const set = (name: string) => (v: string) => setBody((b) => ({ ...b, [name]: v }));

  const create = async () => {
    try {
      const items = JSON.parse(body.items);
      await post("/api/eval-sets", { ...body, items });
      toast.success("评测集已导入");
      setCreating(false);
      reload();
    } catch (err) {
      toast.error(String(err));
    }
  };

  const columns: Column<EvalSet>[] = [
    { key: "name", label: "name", render: (e) => <Mono>{e.name}</Mono> },
    { key: "items", label: "items", render: (e) => <Mono>{e.item_count}</Mono> },
    { key: "cap", label: "capability_domain_id", render: (e) => <Mono>{e.capability_domain_id || "-"}</Mono> },
    {
      key: "ops",
      label: "",
      render: (e) => (
        <Button
          size="sm"
          variant="outline"
          onClick={() =>
            void get<EvalSetShow>(`/api/eval-sets/${e.id}`)
              .then(setShow)
              .catch((err: unknown) => toast.error(String(err)))
          }
        >
          查看
        </Button>
      ),
    },
  ];

  return (
    <PageContainer
      desc="JSONL 行：{question: 文本或{text,images}, expected, category?}。items 直接以 JSON 数组提交。"
      actions={<Button onClick={() => setCreating(true)}>导入评测集</Button>}
    >
      <ErrorNote message={error} />
      <DataTable columns={columns} rows={data ?? []} />
      {creating && (
        <FormModal
          title="导入评测集"
          onClose={() => setCreating(false)}
          onConfirm={() => void create()}
          confirmLabel="导入"
        >
          <Field label="name" value={body.name} onChange={set("name")} mono />
          <Field label="capability_domain_id" value={body.capability_domain_id} onChange={set("capability_domain_id")} mono />
          <Field label="items (json)" value={body.items} onChange={set("items")} kind="textarea" mono />
        </FormModal>
      )}
      {show && (
        <Modal title={`评测集 ${show.eval_set.name}`} onClose={() => setShow(null)}>
          <Table
            columns={[
              { key: "seq", label: "seq", render: (i) => <Mono>{i.seq}</Mono> },
              { key: "q", label: "question", render: (i) => <Mono>{JSON.stringify(i.question)}</Mono> },
              { key: "expected", label: "expected", render: (i) => i.expected },
              { key: "category", label: "category", render: (i) => i.category || "-" },
            ]}
            rows={show.items}
          />
        </Modal>
      )}
    </PageContainer>
  );
}

// ---- eval runs ------------------------------------------------------------------

interface EvalRun {
  id: string;
  eval_set_id: string;
  model_id: string;
  status: string;
  started_at: string;
  finished_at: string;
  overall_score: number | null;
}

interface EvalRunShow {
  eval_run: EvalRun;
  model: string;
  results: Array<{ seq: number; score: unknown; verdict: string; output: string }>;
}

function EvalRunsPage() {
  const { data, error, reload } = useFetch<EvalRun[]>("/api/eval-runs");
  const [creating, setCreating] = useState(false);
  const [show, setShow] = useState<EvalRunShow | null>(null);
  const [body, setBody] = useState({ eval_set_id: "", model_id: "" });

  const set = (name: string) => (v: string) => setBody((b) => ({ ...b, [name]: v }));

  const runAction = async (fn: () => Promise<unknown>, okMsg: string) => {
    try {
      await fn();
      toast.success(okMsg);
      reload();
    } catch (err) {
      toast.error(String(err));
    }
  };

  const create = async () => {
    try {
      const run = await post<EvalRun>("/api/eval-runs", body);
      toast.success(`评测 run 已创建：${run.id}`);
      setCreating(false);
      reload();
    } catch (err) {
      toast.error(String(err));
    }
  };

  const columns: Column<EvalRun>[] = [
    { key: "id", label: "eval_run_id", render: (r) => <Mono>{r.id}</Mono> },
    { key: "set", label: "eval_set_id", render: (r) => <Mono>{r.eval_set_id}</Mono> },
    { key: "model", label: "model_id", render: (r) => <Mono>{r.model_id}</Mono> },
    { key: "status", label: "status", render: (r) => <Status status={r.status} /> },
    { key: "score", label: "score", render: (r) => <Mono>{r.overall_score ?? "-"}</Mono> },
    {
      key: "ops",
      label: "",
      render: (r) => (
        <span className="flex flex-wrap items-center gap-1.5">
          <Button
            size="sm"
            variant="outline"
            disabled={r.status === "running"}
            onClick={() => void runAction(() => post(`/api/eval-runs/${r.id}/run`), "已启动评测")}
          >
            运行
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              void get<EvalRunShow>(`/api/eval-runs/${r.id}`)
                .then(setShow)
                .catch((err: unknown) => toast.error(String(err)))
            }
          >
            结果
          </Button>
        </span>
      ),
    },
  ];

  return (
    <PageContainer
      desc="就绪模型 × 评测集 → 逐题评分 → 报告。模型需先 check 通过。"
      actions={<Button onClick={() => setCreating(true)}>新建评测</Button>}
    >
      <ErrorNote message={error} />
      <DataTable columns={columns} rows={data ?? []} />
      {creating && (
        <FormModal
          title="新建评测"
          onClose={() => setCreating(false)}
          onConfirm={() => void create()}
          confirmLabel="创建"
        >
          <Field label="eval_set_id" value={body.eval_set_id} onChange={set("eval_set_id")} mono />
          <Field label="model_id" value={body.model_id} onChange={set("model_id")} mono />
        </FormModal>
      )}
      {show && (
        <Modal title={`评测 ${show.eval_run.id} · ${show.model}`} onClose={() => setShow(null)}>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-2.5">
            <FieldItem label="status">
              <Status status={show.eval_run.status} />
            </FieldItem>
            <FieldItem label="score">
              <Mono>{show.eval_run.overall_score ?? "-"}</Mono>
            </FieldItem>
          </div>
          <Table
            columns={[
              { key: "seq", label: "seq", render: (r) => <Mono>{r.seq}</Mono> },
              { key: "score", label: "score", render: (r) => <Mono>{JSON.stringify(r.score)}</Mono> },
              { key: "verdict", label: "verdict", render: (r) => <Status status={r.verdict} /> },
              { key: "output", label: "output", render: (r) => String(r.output).slice(0, 120) },
            ]}
            rows={show.results}
          />
        </Modal>
      )}
    </PageContainer>
  );
}

// ---- reports --------------------------------------------------------------------

interface Report {
  id: string;
  eval_run_id: string;
  created_at: string;
  json_key: string;
}

function ReportsPage() {
  const { data, error, reload } = useFetch<Report[]>("/api/reports");
  const [payload, setPayload] = useState<unknown | null>(null);

  return (
    <PageContainer
      desc="聚合指标 + badcase 血缘链 + 归因建议；payload 为完整 JSON。"
      actions={
        <Button variant="outline" onClick={reload}>
          刷新
        </Button>
      }
    >
      <ErrorNote message={error} />
      <DataTable
        columns={[
          { key: "id", label: "report_id", render: (r) => <Mono>{r.id}</Mono> },
          { key: "run", label: "eval_run_id", render: (r) => <Mono>{r.eval_run_id}</Mono> },
          { key: "created", label: "created_at", render: (r) => <Mono>{fmtTime(r.created_at)}</Mono> },
          {
            key: "ops",
            label: "",
            render: (r) => (
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  void get(`/api/reports/${r.id}/payload`)
                    .then(setPayload)
                    .catch((err: unknown) => setPayload({ error: String(err) }))
                }
              >
                查看详情
              </Button>
            ),
          },
        ]}
        rows={data ?? []}
      />
      {payload !== null && (
        <Modal title="报告 payload" onClose={() => setPayload(null)}>
          <JsonBlock value={payload} />
        </Modal>
      )}
    </PageContainer>
  );
}

// ---- lineage --------------------------------------------------------------------

function LineagePage() {
  const [mode, setMode] = useState<"run_id" | "dataset_id" | "strategy_id">("run_id");
  const [value, setValue] = useState("");
  const [result, setResult] = useState<unknown | null>(null);
  const [error, setError] = useState("");

  const query = async () => {
    setError("");
    try {
      setResult(await get(`/api/lineage?${mode}=${encodeURIComponent(value)}`));
    } catch (err) {
      setResult(null);
      setError(String(err));
    }
  };

  return (
    <PageContainer
      desc="按 run / 数据集版本 / 策略追溯：输入来源 → 中间产物 → 发布版本。"
    >
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Field
          label="维度"
          value={mode}
          onChange={(v) => setMode(v as typeof mode)}
          kind="select"
          options={["run_id", "dataset_id", "strategy_id"]}
        />
        <Field label="id" value={value} onChange={setValue} mono placeholder="run_xxx / ds_xxx@1 / st_xxx" />
        <Button onClick={() => void query()}>查询</Button>
      </div>
      <ErrorNote message={error} />
      {result !== null && <JsonBlock value={result} />}
    </PageContainer>
  );
}

export const factoryOverviewPage = {
  key: "overview",
  label: "总览",
  component: FactoryOverviewPage,
};

export const factoryStrategyPages = [
  { key: "capabilities", label: "能力域", component: CapabilitiesPage },
  { key: "strategies", label: "策略", component: StrategiesPage },
  { key: "datasets", label: "数据集", component: DatasetsPage },
  { key: "workflows", label: "工作流", component: WorkflowsPage },
  { key: "runs", label: "运行", component: RunsPage },
  { key: "stages", label: "阶段", component: StagesPage },
  { key: "lineage", label: "血缘", component: LineagePage },
];

export const factoryEvalPages = [
  { key: "models", label: "模型注册", component: ModelsPage },
  { key: "eval-sets", label: "评测集", component: EvalSetsPage },
  { key: "eval-runs", label: "评测运行", component: EvalRunsPage },
  { key: "reports", label: "评测报告", component: ReportsPage },
];
