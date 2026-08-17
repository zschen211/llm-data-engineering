// data-factory pages: the production & eval loop (capabilities/strategies/
// datasets/workflows/runs/stages/models/eval-sets/eval-runs/reports/lineage)
// — all under /api/* of :8001.

import { useState, type ReactNode } from "react";
import { del, get, post } from "../api";
import {
  Btn,
  ErrorNote,
  Field,
  fmtTime,
  JsonBlock,
  Modal,
  Mono,
  Status,
  Table,
  Toast,
  type Column,
  useFetch,
} from "../widgets";

function Page({
  eyebrow,
  title,
  desc,
  actions,
  children,
}: {
  eyebrow: string;
  title: string;
  desc: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section>
      <div className="page-head">
        <span className="page-eyebrow">{eyebrow}</span>
        <h1 className="page-title">{title}</h1>
      </div>
      <p className="page-desc">{desc}</p>
      {actions && <div className="page-actions">{actions}</div>}
      {children}
    </section>
  );
}

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
  const [toast, setToast] = useState<{ kind: "ok" | "error"; message: string } | null>(null);

  const openCreate = () => {
    setBody({ ...emptyBody });
    setCreating(true);
  };

  const submit = async () => {
    try {
      await post(base, body);
      setToast({ kind: "ok", message: `已创建到 ${base}` });
      setCreating(false);
      reload();
    } catch (err) {
      setToast({ kind: "error", message: String(err) });
    }
  };

  const allColumns: Column<T>[] = extraActions
    ? [
        ...columns,
        { key: "ops", label: "", render: (row) => <>{extraActions(row)}</> },
      ]
    : columns;

  return (
    <Page
      eyebrow="FACTORY · 数据工厂"
      title={title}
      desc={desc}
      actions={
        <Btn tone="primary" onClick={openCreate}>
          新建
        </Btn>
      }
    >
      {toast && <Toast kind={toast.kind} message={toast.message} />}
      <ErrorNote message={error} />
      <Table columns={allColumns} rows={data ?? []} />
      {creating && (
        <Modal title={`新建${title}`} onClose={() => setCreating(false)}>
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
          <div className="page-actions">
            <Btn tone="primary" onClick={() => void submit()}>
              创建
            </Btn>
            <Btn onClick={() => setCreating(false)}>取消</Btn>
          </div>
        </Modal>
      )}
    </Page>
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

function OverviewPage() {
  const { data, error, reload } = useFetch<FactoryInfo>("/api/factory-info");

  return (
    <Page
      eyebrow="FACTORY · 数据工厂"
      title="总览"
      desc="数据生产与评测闭环：策略管线产训练数据，评测反推能力缺口，驱动新一轮生产。"
      actions={<Btn onClick={reload}>刷新</Btn>}
    >
      <ErrorNote message={error} />
      {data && (
        <>
          <div className="cards">
            <div className="card">
              <div className="card-label">capabilities</div>
              <div className="card-value">{data.capability_count}</div>
            </div>
            <div className="card">
              <div className="card-label">strategies</div>
              <div className="card-value">{data.strategy_count}</div>
            </div>
            <div className="card">
              <div className="card-label">workflows</div>
              <div className="card-value">{data.workflow_count}</div>
            </div>
            <div className="card">
              <div className="card-label">runs</div>
              <div className="card-value">{data.run_count}</div>
            </div>
            <div className="card">
              <div className="card-label">models</div>
              <div className="card-value">{data.model_count}</div>
            </div>
            <div className="card">
              <div className="card-label">eval runs</div>
              <div className="card-value">{data.eval_run_count}</div>
            </div>
            <div className="card">
              <div className="card-label">reports</div>
              <div className="card-value">{data.report_count}</div>
            </div>
          </div>
          <div className="detail-grid">
            <div className="detail-item">
              <span className="field-label">backend</span>
              <Mono>{data.backend}</Mono>
            </div>
            <div className="detail-item">
              <span className="field-label">bucket</span>
              <Mono>{data.bucket ?? "-"}</Mono>
            </div>
            <div className="detail-item">
              <span className="field-label">data_dir</span>
              <Mono>{data.data_dir}</Mono>
            </div>
            <div className="detail-item">
              <span className="field-label">models_dir</span>
              <Mono>{data.models_dir}</Mono>
            </div>
          </div>
        </>
      )}
    </Page>
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
  const [toast, setToast] = useState<{ kind: "ok" | "error"; message: string } | null>(null);

  const submit = async () => {
    try {
      const tag_filters = JSON.parse(body.tag_filters || "[]");
      await post("/api/datasets", { ...body, tag_filters });
      setToast({ kind: "ok", message: "数据集已创建" });
      setCreating(false);
      reload();
    } catch (err) {
      setToast({ kind: "error", message: String(err) });
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
    <Page
      eyebrow="FACTORY · 数据工厂"
      title="数据集"
      desc="run 的输入定义：资产快照+标签过滤（运行即固化）/ 外部导入 / 上游数据集版本派生。"
      actions={
        <Btn tone="primary" onClick={() => setCreating(true)}>
          新建数据集
        </Btn>
      }
    >
      {toast && <Toast kind={toast.kind} message={toast.message} />}
      <ErrorNote message={error} />
      <Table columns={columns} rows={data ?? []} />
      {creating && (
        <Modal title="新建数据集" onClose={() => setCreating(false)}>
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
          <div className="page-actions">
            <Btn tone="primary" onClick={() => void submit()}>
              创建
            </Btn>
            <Btn onClick={() => setCreating(false)}>取消</Btn>
          </div>
        </Modal>
      )}
    </Page>
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
  const [toast, setToast] = useState<{ kind: "ok" | "error"; message: string } | null>(null);

  const submit = async () => {
    try {
      const stages = JSON.parse(body.stages);
      await post("/api/workflows", { ...body, stages });
      setToast({ kind: "ok", message: "工作流已定义" });
      setCreating(false);
      reload();
    } catch (err) {
      setToast({ kind: "error", message: String(err) });
    }
  };

  const set = (name: string) => (v: string) => setBody((b) => ({ ...b, [name]: v }));

  const viewWorkflow = async (id: string) => {
    try {
      setShow(await get<WorkflowShow>(`/api/workflows/${id}`));
    } catch (err) {
      setToast({ kind: "error", message: String(err) });
    }
  };

  const validate = async (id: string) => {
    try {
      const result = await post<{ order: string[] }>(`/api/workflows/${id}/validate`);
      setToast({ kind: "ok", message: `校验通过：${result.order.length} 个节点` });
    } catch (err) {
      setToast({ kind: "error", message: String(err) });
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
        <span className="page-actions" style={{ margin: 0 }}>
          <Btn className="btn-sm" onClick={() => void viewWorkflow(w.id)}>
            查看
          </Btn>
          <Btn className="btn-sm" onClick={() => void validate(w.id)}>
            校验
          </Btn>
        </span>
      ),
    },
  ];

  return (
    <Page
      eyebrow="FACTORY · 数据工厂"
      title="工作流"
      desc="链式阶段 DAG：transform / qc_rule / qc_llm / publish（sink）。stages 为 JSON 数组。"
      actions={<Btn tone="primary" onClick={() => setCreating(true)}>定义工作流</Btn>}
    >
      {toast && <Toast kind={toast.kind} message={toast.message} />}
      <ErrorNote message={error} />
      <Table columns={columns} rows={data ?? []} />
      {show && (
        <Modal title={show.workflow_id} onClose={() => setShow(null)}>
          <JsonBlock value={show} />
        </Modal>
      )}
      {creating && (
        <Modal title="定义工作流" onClose={() => setCreating(false)}>
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
          <div className="page-actions">
            <Btn tone="primary" onClick={() => void submit()}>
              定义
            </Btn>
            <Btn onClick={() => setCreating(false)}>取消</Btn>
          </div>
        </Modal>
      )}
    </Page>
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
  const [toast, setToast] = useState<{ kind: "ok" | "error"; message: string } | null>(null);

  const set = (name: string) => (v: string) => setBody((b) => ({ ...b, [name]: v }));

  const runAction = async (fn: () => Promise<unknown>, okMsg: string) => {
    try {
      await fn();
      setToast({ kind: "ok", message: okMsg });
      reload();
    } catch (err) {
      setToast({ kind: "error", message: String(err) });
    }
  };

  const createRun = async () => {
    try {
      const run = await post<Run>("/api/runs", body);
      setToast({ kind: "ok", message: `run 已创建：${run.id}` });
      setCreating(false);
      reload();
    } catch (err) {
      setToast({ kind: "error", message: String(err) });
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
        <span className="page-actions" style={{ margin: 0 }}>
          <Btn
            className="btn-sm"
            disabled={r.status === "running"}
            onClick={() => void runAction(() => post(`/api/runs/${r.id}/run`), "已启动执行")}
          >
            执行
          </Btn>
          <Btn className="btn-sm" onClick={() => void get<RunShow>(`/api/runs/${r.id}`).then(setShow).catch((err) => setToast({ kind: "error", message: String(err) }))}>
            详情
          </Btn>
          <Btn
            className="btn-sm btn-danger"
            disabled={r.status !== "running"}
            onClick={() => void runAction(() => post(`/api/runs/${r.id}/cancel`), "已取消")}
          >
            取消
          </Btn>
        </span>
      ),
    },
  ];

  return (
    <Page
      eyebrow="FACTORY · 数据工厂"
      title="运行"
      desc="工作流 × 输入数据集的一次执行；Ray Data 链式跑，断点可续。"
      actions={<Btn tone="primary" onClick={() => setCreating(true)}>新建 run</Btn>}
    >
      {toast && <Toast kind={toast.kind} message={toast.message} />}
      <ErrorNote message={error} />
      <Table columns={columns} rows={data ?? []} />
      {creating && (
        <Modal title="新建 run" onClose={() => setCreating(false)}>
          <Field label="workflow_id" value={body.workflow_id} onChange={set("workflow_id")} mono />
          <Field label="input_dataset_id" value={body.input_dataset_id} onChange={set("input_dataset_id")} mono />
          <div className="page-actions">
            <Btn tone="primary" onClick={() => void createRun()}>
              创建
            </Btn>
            <Btn onClick={() => setCreating(false)}>取消</Btn>
          </div>
        </Modal>
      )}
      {show && (
        <Modal title={`run ${show.run.id}`} onClose={() => setShow(null)}>
          <div className="detail-grid">
            <div className="detail-item">
              <span className="field-label">status</span>
              <Status status={show.run.status} />
            </div>
            <div className="detail-item">
              <span className="field-label">workflow</span>
              <Mono>{show.run.workflow_id}</Mono>
            </div>
            <div className="detail-item">
              <span className="field-label">dataset</span>
              <Mono>{show.run.input_dataset_id}</Mono>
            </div>
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
    </Page>
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
    <Page
      eyebrow="FACTORY · 数据工厂"
      title="阶段注册表"
      desc="内置管线阶段；配置结构见 config_schema。"
      actions={<Btn onClick={reload}>刷新</Btn>}
    >
      <ErrorNote message={error} />
      <Table
        columns={[
          { key: "name", label: "name", render: (s) => <Mono>{s.name}</Mono> },
          { key: "kind", label: "kind", render: (s) => <Mono>{s.kind}</Mono> },
          { key: "description", label: "description" },
        ]}
        rows={data ?? []}
      />
    </Page>
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
  const [toast, setToast] = useState<{ kind: "ok" | "error"; message: string } | null>(null);

  const set = (name: string) => (v: string) => setBody((b) => ({ ...b, [name]: v }));

  const runAction = async (fn: () => Promise<unknown>, okMsg: string) => {
    try {
      await fn();
      setToast({ kind: "ok", message: okMsg });
      reload();
    } catch (err) {
      setToast({ kind: "error", message: String(err) });
    }
  };

  const register = async () => {
    try {
      await post("/api/models", body);
      setToast({ kind: "ok", message: "模型已注册" });
      setCreating(false);
      reload();
    } catch (err) {
      setToast({ kind: "error", message: String(err) });
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
        <span className="page-actions" style={{ margin: 0 }}>
          <Btn className="btn-sm" onClick={() => void runAction(() => post(`/api/models/${m.id}/check`), "心跳已刷新")}>
            check
          </Btn>
          <Btn className="btn-sm btn-danger" onClick={() => window.confirm(`删除模型「${m.name}」？`) && void runAction(() => del(`/api/models/${m.id}`), "已删除")}>
            删除
          </Btn>
        </span>
      ),
    },
  ];

  return (
    <Page
      eyebrow="FACTORY · 数据工厂"
      title="模型注册表"
      desc="local（checkpoint 目录）/ vllm / api（OpenAI 兼容）；api_key 只存环境变量名。"
      actions={
        <>
          <Btn tone="primary" onClick={() => setCreating(true)}>
            注册模型
          </Btn>
          <Btn onClick={() => void runAction(() => post("/api/models/scan"), "目录扫描完成")}>
            扫描本地模型
          </Btn>
        </>
      }
    >
      {toast && <Toast kind={toast.kind} message={toast.message} />}
      <ErrorNote message={error} />
      <Table columns={columns} rows={data ?? []} />
      {creating && (
        <Modal title="注册模型" onClose={() => setCreating(false)}>
          <Field label="name" value={body.name} onChange={set("name")} mono />
          <Field label="backend" value={body.backend} onChange={set("backend")} kind="select" options={["api", "vllm", "local"]} />
          {body.backend === "local" && <Field label="weights_dir" value={body.weights_dir} onChange={set("weights_dir")} mono />}
          {(body.backend === "api" || body.backend === "vllm") && (
            <Field label="base_url" value={body.base_url} onChange={set("base_url")} mono />
          )}
          {(body.backend === "api" || body.backend === "vllm") && (
            <Field label="api_key_env" value={body.api_key_env} onChange={set("api_key_env")} mono />
          )}
          <Field label="model_id" value={body.model_id} onChange={set("model_id")} mono />
          <div className="page-actions">
            <Btn tone="primary" onClick={() => void register()}>
              注册
            </Btn>
            <Btn onClick={() => setCreating(false)}>取消</Btn>
          </div>
        </Modal>
      )}
    </Page>
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
  const [toast, setToast] = useState<{ kind: "ok" | "error"; message: string } | null>(null);

  const set = (name: string) => (v: string) => setBody((b) => ({ ...b, [name]: v }));

  const create = async () => {
    try {
      const items = JSON.parse(body.items);
      await post("/api/eval-sets", { ...body, items });
      setToast({ kind: "ok", message: "评测集已导入" });
      setCreating(false);
      reload();
    } catch (err) {
      setToast({ kind: "error", message: String(err) });
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
        <Btn className="btn-sm" onClick={() => void get<EvalSetShow>(`/api/eval-sets/${e.id}`).then(setShow).catch((err) => setToast({ kind: "error", message: String(err) }))}>
          查看
        </Btn>
      ),
    },
  ];

  return (
    <Page
      eyebrow="FACTORY · 数据工厂"
      title="评测集"
      desc="JSONL 行：{question: 文本或{text,images}, expected, category?}。items 直接以 JSON 数组提交。"
      actions={<Btn tone="primary" onClick={() => setCreating(true)}>导入评测集</Btn>}
    >
      {toast && <Toast kind={toast.kind} message={toast.message} />}
      <ErrorNote message={error} />
      <Table columns={columns} rows={data ?? []} />
      {creating && (
        <Modal title="导入评测集" onClose={() => setCreating(false)}>
          <Field label="name" value={body.name} onChange={set("name")} mono />
          <Field label="capability_domain_id" value={body.capability_domain_id} onChange={set("capability_domain_id")} mono />
          <Field label="items (json)" value={body.items} onChange={set("items")} kind="textarea" mono />
          <div className="page-actions">
            <Btn tone="primary" onClick={() => void create()}>
              导入
            </Btn>
            <Btn onClick={() => setCreating(false)}>取消</Btn>
          </div>
        </Modal>
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
    </Page>
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
  const [toast, setToast] = useState<{ kind: "ok" | "error"; message: string } | null>(null);

  const set = (name: string) => (v: string) => setBody((b) => ({ ...b, [name]: v }));

  const runAction = async (fn: () => Promise<unknown>, okMsg: string) => {
    try {
      await fn();
      setToast({ kind: "ok", message: okMsg });
      reload();
    } catch (err) {
      setToast({ kind: "error", message: String(err) });
    }
  };

  const create = async () => {
    try {
      const run = await post<EvalRun>("/api/eval-runs", body);
      setToast({ kind: "ok", message: `评测 run 已创建：${run.id}` });
      setCreating(false);
      reload();
    } catch (err) {
      setToast({ kind: "error", message: String(err) });
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
        <span className="page-actions" style={{ margin: 0 }}>
          <Btn
            className="btn-sm"
            disabled={r.status === "running"}
            onClick={() => void runAction(() => post(`/api/eval-runs/${r.id}/run`), "已启动评测")}
          >
            运行
          </Btn>
          <Btn className="btn-sm" onClick={() => void get<EvalRunShow>(`/api/eval-runs/${r.id}`).then(setShow).catch((err) => setToast({ kind: "error", message: String(err) }))}>
            结果
          </Btn>
        </span>
      ),
    },
  ];

  return (
    <Page
      eyebrow="FACTORY · 数据工厂"
      title="评测运行"
      desc="就绪模型 × 评测集 → 逐题评分 → 报告。模型需先 check 通过。"
      actions={<Btn tone="primary" onClick={() => setCreating(true)}>新建评测</Btn>}
    >
      {toast && <Toast kind={toast.kind} message={toast.message} />}
      <ErrorNote message={error} />
      <Table columns={columns} rows={data ?? []} />
      {creating && (
        <Modal title="新建评测" onClose={() => setCreating(false)}>
          <Field label="eval_set_id" value={body.eval_set_id} onChange={set("eval_set_id")} mono />
          <Field label="model_id" value={body.model_id} onChange={set("model_id")} mono />
          <div className="page-actions">
            <Btn tone="primary" onClick={() => void create()}>
              创建
            </Btn>
            <Btn onClick={() => setCreating(false)}>取消</Btn>
          </div>
        </Modal>
      )}
      {show && (
        <Modal title={`评测 ${show.eval_run.id} · ${show.model}`} onClose={() => setShow(null)}>
          <div className="detail-grid">
            <div className="detail-item">
              <span className="field-label">status</span>
              <Status status={show.eval_run.status} />
            </div>
            <div className="detail-item">
              <span className="field-label">score</span>
              <Mono>{show.eval_run.overall_score ?? "-"}</Mono>
            </div>
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
    </Page>
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
    <Page
      eyebrow="FACTORY · 数据工厂"
      title="评测报告"
      desc="聚合指标 + badcase 血缘链 + 归因建议；payload 为完整 JSON。"
      actions={<Btn onClick={reload}>刷新</Btn>}
    >
      <ErrorNote message={error} />
      <Table
        columns={[
          { key: "id", label: "report_id", render: (r) => <Mono>{r.id}</Mono> },
          { key: "run", label: "eval_run_id", render: (r) => <Mono>{r.eval_run_id}</Mono> },
          { key: "created", label: "created_at", render: (r) => <Mono>{fmtTime(r.created_at)}</Mono> },
          {
            key: "ops",
            label: "",
            render: (r) => (
              <Btn className="btn-sm" onClick={() => void get(`/api/reports/${r.id}/payload`).then(setPayload).catch((err) => setPayload({ error: String(err) }))}>
                payload
              </Btn>
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
    </Page>
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
    <Page
      eyebrow="FACTORY · 数据工厂"
      title="血缘"
      desc="按 run / 数据集版本 / 策略追溯：输入来源 → 中间产物 → 发布版本。"
      actions={
        <>
          <Field label="维度" value={mode} onChange={(v) => setMode(v as typeof mode)} kind="select" options={["run_id", "dataset_id", "strategy_id"]} />
          <Field label="id" value={value} onChange={setValue} mono placeholder="run_xxx / ds_xxx@1 / st_xxx" />
          <Btn tone="primary" onClick={() => void query()}>
            查询
          </Btn>
        </>
      }
    >
      <ErrorNote message={error} />
      {result !== null && <JsonBlock value={result} />}
    </Page>
  );
}

export const factoryPages = [
  { key: "overview", label: "总览", component: OverviewPage },
  { key: "capabilities", label: "能力域", component: CapabilitiesPage },
  { key: "strategies", label: "策略", component: StrategiesPage },
  { key: "datasets", label: "数据集", component: DatasetsPage },
  { key: "workflows", label: "工作流", component: WorkflowsPage },
  { key: "runs", label: "运行", component: RunsPage },
  { key: "stages", label: "阶段", component: StagesPage },
  { key: "models", label: "模型", component: ModelsPage },
  { key: "eval-sets", label: "评测集", component: EvalSetsPage },
  { key: "eval-runs", label: "评测", component: EvalRunsPage },
  { key: "reports", label: "报告", component: ReportsPage },
  { key: "lineage", label: "血缘", component: LineagePage },
];
