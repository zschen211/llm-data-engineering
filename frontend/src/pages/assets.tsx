// asset pages: the platform service (sources/assets/snapshots/
// sync runs/downloads/cluster/info) — all under /api/* of :8000.

import { useState, type ReactNode } from "react";
import { del, get, post, put } from "../api";
import {
  Btn,
  ErrorNote,
  Field,
  fmtBytes,
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

// ---- info ------------------------------------------------------------------

interface AssetInfo {
  backend: string;
  bucket: string | null;
  data_dir: string;
  db_path: string;
  source_count: number;
  asset_count: number;
  ready_count: number;
  failed_count: number;
  snapshot_count: number;
}

function InfoPage() {
  const { data, error, reload } = useFetch<AssetInfo>("/api/info");
  const [toast, setToast] = useState<{ kind: "ok" | "error"; message: string } | null>(null);

  const backup = async () => {
    try {
      const result = await post<{ path: string }>("/api/backup");
      setToast({ kind: "ok", message: `已备份元数据库 → ${result.path}` });
    } catch (err) {
      setToast({ kind: "error", message: String(err) });
    }
  };

  return (
    <Page
      eyebrow="ASSET · 资产层"
      title="总览"
      desc="数字资产的唯一权威：blob 对象存储 + SQLite 元数据（数据源/标签/版本/快照）。"
      actions={
        <>
          <Btn onClick={backup}>备份元数据库</Btn>
          <Btn onClick={reload}>刷新</Btn>
        </>
      }
    >
      {toast && <Toast kind={toast.kind} message={toast.message} />}
      <ErrorNote message={error} />
      {data && (
        <>
          <div className="cards">
            <div className="card">
              <div className="card-label">asset_count</div>
              <div className="card-value">{data.asset_count}</div>
            </div>
            <div className="card">
              <div className="card-label">ready</div>
              <div className="card-value">{data.ready_count}</div>
            </div>
            <div className="card">
              <div className="card-label">failed</div>
              <div className="card-value">{data.failed_count}</div>
            </div>
            <div className="card">
              <div className="card-label">sources</div>
              <div className="card-value">{data.source_count}</div>
            </div>
            <div className="card">
              <div className="card-label">snapshots</div>
              <div className="card-value">{data.snapshot_count}</div>
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
              <span className="field-label">db_path</span>
              <Mono>{data.db_path}</Mono>
            </div>
          </div>
        </>
      )}
    </Page>
  );
}

// ---- sources ---------------------------------------------------------------

interface Source {
  id: string;
  name: string;
  kind: string;
  url: string;
  license: string;
  description: string;
  running_run_id: string | null;
  resumable_run_id: string | null;
}

function SourcesPage() {
  const { data, error, reload } = useFetch<Source[]>("/api/sources");
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Source | null>(null);
  const [toast, setToast] = useState<{ kind: "ok" | "error"; message: string } | null>(null);

  const runAction = async (fn: () => Promise<unknown>, okMsg: string) => {
    try {
      await fn();
      setToast({ kind: "ok", message: okMsg });
      reload();
    } catch (err) {
      setToast({ kind: "error", message: String(err) });
    }
  };

  const remove = (source: Source) => {
    if (window.confirm(`删除数据源「${source.name}」？元数据与已入库资产一并移除。`)) {
      void runAction(() => del(`/api/sources/${source.id}`), "已删除");
    }
  };

  const columns: Column<Source>[] = [
    { key: "name", label: "name" },
    { key: "kind", label: "kind", render: (s) => <Mono>{s.kind}</Mono> },
    { key: "url", label: "url", render: (s) => <Mono>{s.url || "-"}</Mono> },
    { key: "license", label: "license", render: (s) => s.license || "-" },
    { key: "description", label: "description" },
    {
      key: "run",
      label: "sync run",
      render: (s) => (
        <>
          {s.running_run_id && <Status status="running" />}
          {!s.running_run_id && s.resumable_run_id && <Status status="pending" />}
          {!s.running_run_id && !s.resumable_run_id && <Status status="done" />}
          <Mono>{s.running_run_id ?? s.resumable_run_id ?? ""}</Mono>
        </>
      ),
    },
    {
      key: "ops",
      label: "",
      render: (s) => (
        <span className="page-actions" style={{ margin: 0 }}>
          <Btn
            className="btn-sm"
            onClick={() => void runAction(() => post(`/api/sources/${s.id}/sync`), "已触发同步")}
          >
            sync
          </Btn>
          <Btn
            className="btn-sm"
            onClick={() => void runAction(() => post(`/api/sources/${s.id}/reprocess`), "已触发重处理")}
          >
            reprocess
          </Btn>
          <Btn className="btn-sm" onClick={() => setEditing(s)}>
            编辑
          </Btn>
          <Btn className="btn-sm btn-danger" onClick={() => remove(s)}>
            删除
          </Btn>
        </span>
      ),
    },
  ];

  return (
    <Page
      eyebrow="ASSET · 资产层"
      title="数据源"
      desc="互联网资源的元信息与下载源；同步跑在 Ray 上（每文件一个任务，断点续跑）。"
      actions={
        <Btn tone="primary" onClick={() => setCreating(true)}>
          新建数据源
        </Btn>
      }
    >
      {toast && <Toast kind={toast.kind} message={toast.message} />}
      <ErrorNote message={error} />
      <Table columns={columns} rows={data ?? []} />
      {(creating || editing) && (
        <SourceModal
          source={editing}
          onClose={() => {
            setCreating(false);
            setEditing(null);
          }}
          onSaved={(msg) => {
            setCreating(false);
            setEditing(null);
            setToast({ kind: "ok", message: msg });
            reload();
          }}
        />
      )}
    </Page>
  );
}

function SourceModal({
  source,
  onClose,
  onSaved,
}: {
  source: Source | null;
  onClose: () => void;
  onSaved: (msg: string) => void;
}) {
  const [name, setName] = useState(source?.name ?? "");
  const [kind, setKind] = useState(source?.kind ?? "huggingface");
  const [url, setUrl] = useState(source?.url ?? "");
  const [license, setLicense] = useState(source?.license ?? "");
  const [description, setDescription] = useState(source?.description ?? "");
  const [paramsText, setParamsText] = useState(
    source ? JSON.stringify((source as unknown as { params?: unknown }).params ?? {}, null, 2) : "{}",
  );
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const save = async () => {
    let params: unknown;
    try {
      params = JSON.parse(paramsText || "{}");
    } catch {
      setError("params 不是合法 JSON");
      return;
    }
    const body = { name, kind, url, license, description, params };
    setSaving(true);
    setError("");
    try {
      if (source) {
        await put(`/api/sources/${source.id}`, body);
        onSaved("已保存");
      } else {
        await post("/api/sources", body);
        onSaved("已创建");
      }
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={source ? "编辑数据源" : "新建数据源"} onClose={onClose}>
      <Field label="name" value={name} onChange={setName} mono />
      <Field label="kind" value={kind} onChange={setKind} mono placeholder="huggingface" />
      <Field label="url" value={url} onChange={setUrl} mono />
      <Field label="license" value={license} onChange={setLicense} />
      <Field label="description" value={description} onChange={setDescription} />
      <Field
        label="params (json)"
        value={paramsText}
        onChange={setParamsText}
        kind="textarea"
        mono
      />
      <ErrorNote message={error} />
      <div className="page-actions">
        <Btn tone="primary" onClick={() => void save()} disabled={saving}>
          {saving ? "保存中…" : "保存"}
        </Btn>
        <Btn onClick={onClose}>取消</Btn>
      </div>
    </Modal>
  );
}

// ---- assets ----------------------------------------------------------------

interface Asset {
  id: string;
  name: string;
  asset_type: string;
  status: string;
  size: number | null;
  width: number | null;
  height: number | null;
  sha256: string;
  tags: Array<[string, string]>;
  version: number;
}

interface AssetPage {
  items: Asset[];
  next_cursor: string | null;
  page_size: number;
}

function AssetsPage() {
  const [status, setStatus] = useState("");
  const [tag, setTag] = useState("");
  const [q, setQ] = useState("");
  const [cursor, setCursor] = useState<string | undefined>(undefined);
  const [toast, setToast] = useState<{ kind: "ok" | "error"; message: string } | null>(null);

  const params = new URLSearchParams({ page_size: "50" });
  if (status) {
    params.set("status", status);
  }
  if (tag) {
    params.set("tag", tag);
  }
  if (q) {
    params.set("q", q);
  }
  if (cursor) {
    params.set("cursor", cursor);
  }
  const path = `/api/assets?${params.toString()}`;
  const { data, error, reload } = useFetch<AssetPage>(path);

  const remove = (asset: Asset) => {
    if (window.confirm(`删除资产「${asset.name}」？`)) {
      void del(`/api/assets/${asset.id}`)
        .then(() => setToast({ kind: "ok", message: "已删除" }))
        .then(reload)
        .catch((err) => setToast({ kind: "error", message: String(err) }));
    }
  };

  const tagAsset = (asset: Asset) => {
    const group = window.prompt("标签组（如 quality / task）", "default");
    if (group == null) {
      return;
    }
    const name = window.prompt("标签名");
    if (name == null || !name) {
      return;
    }
    void post(`/api/assets/${asset.id}/tags`, { name, group })
      .then(() => setToast({ kind: "ok", message: "已打标" }))
      .then(reload)
      .catch((err) => setToast({ kind: "error", message: String(err) }));
  };

  const rollback = (asset: Asset) => {
    const version = window.prompt(`回滚「${asset.name}」到版本号`, String(asset.version));
    if (version == null || !version) {
      return;
    }
    void post(`/api/assets/${asset.id}/rollback`, { version: Number(version) })
      .then(() => setToast({ kind: "ok", message: "已回滚" }))
      .then(reload)
      .catch((err) => setToast({ kind: "error", message: String(err) }));
  };

  const columns: Column<Asset>[] = [
    { key: "name", label: "name", render: (a) => <Mono>{a.name}</Mono> },
    { key: "type", label: "type", render: (a) => <Mono>{a.asset_type}</Mono> },
    { key: "status", label: "status", render: (a) => <Status status={a.status} /> },
    { key: "size", label: "size", render: (a) => fmtBytes(a.size) },
    {
      key: "wh",
      label: "wxh",
      render: (a) => (a.width && a.height ? <Mono>{`${a.width}×${a.height}`}</Mono> : "-"),
    },
    { key: "sha", label: "sha256", render: (a) => <Mono>{a.sha256.slice(0, 12)}</Mono> },
    {
      key: "tags",
      label: "tags",
      render: (a) => a.tags.map(([g, n]) => `${g}=${n}`).join(", ") || "-",
    },
    {
      key: "ops",
      label: "",
      render: (a) => (
        <span className="page-actions" style={{ margin: 0 }}>
          <Btn
            className="btn-sm"
            title="新窗口预览"
            onClick={() => window.open(`/api/assets/${a.id}/preview`, "_blank")}
          >
            预览
          </Btn>
          <Btn className="btn-sm" onClick={() => tagAsset(a)}>
            打标
          </Btn>
          <Btn className="btn-sm" onClick={() => rollback(a)}>
            回滚
          </Btn>
          <Btn className="btn-sm btn-danger" onClick={() => remove(a)}>
            删除
          </Btn>
        </span>
      ),
    },
  ];

  return (
    <Page
      eyebrow="ASSET · 资产层"
      title="资产"
      desc="内容寻址 blob + 标签/版本元数据；预览走后端流式接口。"
      actions={
        <>
          <Field label="status" value={status} onChange={setStatus} kind="select" options={["ready", "failed", "processing", "deleted"]} />
          <Field label="tag" value={tag} onChange={setTag} placeholder="group=name" mono />
          <Field label="q" value={q} onChange={setQ} placeholder="名称/摘要搜索" />
          <Btn onClick={reload}>刷新</Btn>
        </>
      }
    >
      {toast && <Toast kind={toast.kind} message={toast.message} />}
      <ErrorNote message={error} />
      <Table columns={columns} rows={data?.items ?? []} />
      <div className="pagination">
        <Btn
          disabled={!cursor}
          onClick={() => {
            setCursor(undefined);
            reload();
          }}
        >
          首页
        </Btn>
        <Btn disabled={!data?.next_cursor} onClick={() => setCursor(data?.next_cursor ?? undefined)}>
          下一页
        </Btn>
      </div>
    </Page>
  );
}

// ---- snapshots -------------------------------------------------------------

interface Snapshot {
  id: string;
  name: string;
  created_at: string;
  asset_count: number;
  manifest_sha1: string;
}

function SnapshotsPage() {
  const { data, error, reload } = useFetch<Snapshot[]>("/api/snapshots");
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [toast, setToast] = useState<{ kind: "ok" | "error"; message: string } | null>(null);

  const create = async () => {
    try {
      await post("/api/snapshots", { name });
      setToast({ kind: "ok", message: "快照已创建" });
      setName("");
      setCreating(false);
      reload();
    } catch (err) {
      setToast({ kind: "error", message: String(err) });
    }
  };

  const columns: Column<Snapshot>[] = [
    { key: "id", label: "id", render: (s) => <Mono>{s.id}</Mono> },
    { key: "name", label: "name", render: (s) => <Mono>{s.name || "-"}</Mono> },
    { key: "created", label: "created_at", render: (s) => <Mono>{fmtTime(s.created_at)}</Mono> },
    { key: "count", label: "assets", render: (s) => <Mono>{s.asset_count}</Mono> },
    { key: "sha", label: "manifest_sha1", render: (s) => <Mono>{s.manifest_sha1.slice(0, 12)}</Mono> },
  ];

  return (
    <Page
      eyebrow="ASSET · 资产层"
      title="快照"
      desc="集合级快照：把当前资产集固化为不可变版本，供 data-factory 数据集引用。"
      actions={
        creating ? (
          <>
            <Field label="快照名" value={name} onChange={setName} mono placeholder="v1" />
            <Btn tone="primary" onClick={() => void create()}>
              创建
            </Btn>
            <Btn onClick={() => setCreating(false)}>取消</Btn>
          </>
        ) : (
          <Btn tone="primary" onClick={() => setCreating(true)}>
            新建快照
          </Btn>
        )
      }
    >
      {toast && <Toast kind={toast.kind} message={toast.message} />}
      <ErrorNote message={error} />
      <Table columns={columns} rows={data ?? []} />
    </Page>
  );
}

// ---- sync runs -------------------------------------------------------------

interface SyncRun {
  id: string;
  source_id: string;
  status: string;
  started_at: string;
  finished_at: string | null;
}

interface SyncEvent {
  seq: number;
  level: string;
  message: string;
  remote: string;
  created_at: string;
}

function SyncPage() {
  const { data, error, reload } = useFetch<SyncRun[]>("/api/sync/runs?limit=50");
  const [selected, setSelected] = useState<SyncRun | null>(null);
  const [events, setEvents] = useState<SyncEvent[]>([]);

  const openRun = async (run: SyncRun) => {
    setSelected(run);
    const evs = await get<SyncEvent[]>(`/api/sync/${run.id}/events?limit=500`);
    setEvents(evs);
  };

  const columns: Column<SyncRun>[] = [
    { key: "id", label: "run_id", render: (r) => <Mono>{r.id}</Mono> },
    { key: "source", label: "source_id", render: (r) => <Mono>{r.source_id}</Mono> },
    { key: "status", label: "status", render: (r) => <Status status={r.status} /> },
    { key: "started", label: "started_at", render: (r) => <Mono>{fmtTime(r.started_at)}</Mono> },
    { key: "finished", label: "finished_at", render: (r) => <Mono>{fmtTime(r.finished_at)}</Mono> },
    { key: "ops", label: "", render: (r) => <Btn className="btn-sm" onClick={() => void openRun(r)}>事件</Btn> },
  ];

  return (
    <Page
      eyebrow="ASSET · 资产层"
      title="同步运行"
      desc="每次 sync/reprocess 一次 run；文件级事件流可复查每步处理。"
      actions={<Btn onClick={reload}>刷新</Btn>}
    >
      <ErrorNote message={error} />
      <Table columns={columns} rows={data ?? []} />
      {selected && (
        <div style={{ marginTop: 20 }}>
          <div className="page-head">
            <span className="page-eyebrow">SYNC · {selected.id}</span>
            <h2 className="page-title" style={{ fontSize: 15 }}>
              事件流
            </h2>
          </div>
          {events.length === 0 ? (
            <JsonBlock value="（暂无事件）" />
          ) : (
            <JsonBlock value={events} />
          )}
        </div>
      )}
    </Page>
  );
}

// ---- downloads -------------------------------------------------------------

interface Download {
  asset_name: string;
  status: string;
  sha256: string;
  bytes_downloaded: number;
  error: string | null;
}

function DownloadsPage() {
  const { data, error, reload } = useFetch<Download[]>("/api/downloads?limit=50");

  const columns: Column<Download>[] = [
    { key: "asset", label: "asset", render: (d) => <Mono>{d.asset_name}</Mono> },
    { key: "status", label: "status", render: (d) => <Status status={d.status} /> },
    { key: "bytes", label: "bytes", render: (d) => fmtBytes(d.bytes_downloaded) },
    { key: "sha", label: "sha256", render: (d) => <Mono>{d.sha256.slice(0, 12)}</Mono> },
    { key: "error", label: "error", render: (d) => d.error || "-" },
  ];

  return (
    <Page
      eyebrow="ASSET · 资产层"
      title="下载记录"
      desc="每文件的下载流水账（含失败原因），用于排查网络源问题。"
      actions={<Btn onClick={reload}>刷新</Btn>}
    >
      <ErrorNote message={error} />
      <Table columns={columns} rows={data ?? []} />
    </Page>
  );
}

// ---- cluster ---------------------------------------------------------------

interface ClusterStatus {
  initialized: boolean;
  address: string;
  dashboard_url: string;
  logs_dir: string;
  metrics_port: number;
  total_cpus: number;
  available_cpus: number;
  alive_nodes: number;
  running_tasks: number;
  alive_actors: number;
}

function ClusterPage() {
  const { data, error, reload } = useFetch<ClusterStatus>("/api/cluster/status");

  return (
    <Page
      eyebrow="ASSET · 资产层"
      title="Ray 集群"
      desc="infra 契约变量 RAY_ADDRESS 连接的共享集群；未配置时为内嵌兜底集群。"
      actions={<Btn onClick={reload}>刷新</Btn>}
    >
      <ErrorNote message={error} />
      {data && (
        <div className="detail-grid">
          {Object.entries(data).map(([k, v]) => (
            <div className="detail-item" key={k}>
              <span className="field-label">{k}</span>
              <Mono>{typeof v === "boolean" ? (v ? "true" : "false") : String(v ?? "")}</Mono>
            </div>
          ))}
        </div>
      )}
      {data?.dashboard_url && (
        <p className="page-desc" style={{ marginTop: 14 }}>
          <a href={data.dashboard_url} target="_blank" rel="noreferrer">
            {data.dashboard_url}
          </a>
        </p>
      )}
    </Page>
  );
}

export const assetPages = [
  { key: "info", label: "总览", component: InfoPage },
  { key: "sources", label: "数据源", component: SourcesPage },
  { key: "assets", label: "资产", component: AssetsPage },
  { key: "snapshots", label: "快照", component: SnapshotsPage },
  { key: "sync", label: "同步", component: SyncPage },
  { key: "downloads", label: "下载", component: DownloadsPage },
  { key: "cluster", label: "集群", component: ClusterPage },
];
