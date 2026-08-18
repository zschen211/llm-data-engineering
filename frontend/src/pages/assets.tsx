// asset pages: the platform service (sources / asset datasets / snapshots /
// sync runs / downloads) — all under /api/* of :8000.

import { useState } from "react";
import { toast } from "sonner";
import { del, get, post, put } from "../api";
import { Button } from "@/components/ui/button";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import {
  CursorPagination,
  DataTable,
  ErrorNote,
  Field,
  fmtBytes,
  fmtTime,
  FormModal,
  InfoCard,
  JsonBlock,
  Mono,
  PageContainer,
  PageSection,
  Status,
  useFetch,
  type Column,
} from "../widgets";

// ---- overview ----------------------------------------------------------------

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

function AssetOverviewPage() {
  const { data, error, reload } = useFetch<AssetInfo>("/api/info");

  const backup = async () => {
    try {
      const result = await post<{ path: string }>("/api/backup");
      toast.success(`已备份元数据库 → ${result.path}`);
    } catch (err) {
      toast.error(String(err));
    }
  };

  return (
    <PageContainer
      desc="数字资产的唯一权威：blob 对象存储 + SQLite 元数据（数据源/标签/版本/快照）。"
      actions={
        <>
          <Button variant="outline" onClick={() => void backup()}>
            备份元数据库
          </Button>
          <Button variant="outline" onClick={reload}>
            刷新
          </Button>
        </>
      }
    >
      <ErrorNote message={error} />
      {data && (
        <InfoCard
          items={[
            ["asset_count", data.asset_count],
            ["ready", data.ready_count],
            ["failed", data.failed_count],
            ["sources", data.source_count],
            ["snapshots", data.snapshot_count],
            ["backend", <Mono key="b">{data.backend}</Mono>],
            ["bucket", <Mono key="bk">{data.bucket ?? "-"}</Mono>],
            ["data_dir", <Mono key="dd">{data.data_dir}</Mono>],
            ["db_path", <Mono key="db">{data.db_path}</Mono>],
          ]}
        />
      )}
    </PageContainer>
  );
}

// ---- sources -----------------------------------------------------------------

interface Source {
  id: string;
  name: string;
  kind: string;
  url: string;
  license: string;
  description: string;
  params: {
    repo_id?: string;
    process?: string;
    attempts?: number;
    workers?: number;
  };
  running_run_id: string | null;
  resumable_run_id: string | null;
}

function SourcesPage() {
  const { data, error, reload } = useFetch<Source[]>("/api/sources");
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Source | null>(null);

  const runAction = async (fn: () => Promise<unknown>, okMsg: string) => {
    try {
      await fn();
      toast.success(okMsg);
      reload();
    } catch (err) {
      toast.error(String(err));
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
    { key: "url", label: "url", render: (s) => <Mono>{s.params?.repo_id || s.url || "-"}</Mono> },
    { key: "license", label: "license", render: (s) => s.license || "-" },
    { key: "description", label: "description" },
    {
      key: "run",
      label: "同步运行",
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
        <span className="flex flex-wrap items-center gap-1.5">
          <Button
            size="sm"
            variant="outline"
            onClick={() => void runAction(() => post(`/api/sources/${s.id}/sync`), "已触发同步")}
          >
            同步
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => void runAction(() => post(`/api/sources/${s.id}/reprocess`), "已触发重处理")}
          >
            重处理
          </Button>
          <Button size="sm" variant="outline" onClick={() => setEditing(s)}>
            编辑
          </Button>
          <Button size="sm" variant="destructive" onClick={() => remove(s)}>
            删除
          </Button>
        </span>
      ),
    },
  ];

  return (
    <PageContainer
      desc="互联网资源的元信息与下载源；同步跑在 Ray 上（每文件一个任务，断点续跑）。"
      actions={<Button onClick={() => setCreating(true)}>新建数据源</Button>}
    >
      <ErrorNote message={error} />
      <DataTable columns={columns} rows={data ?? []} />
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
            toast.success(msg);
            reload();
          }}
        />
      )}
    </PageContainer>
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
  const existing = source?.params ?? {};
  const [name, setName] = useState(source?.name ?? "");
  const [kind, setKind] = useState(source?.kind ?? "huggingface");
  const [url, setUrl] = useState(source?.url ?? "");
  const [license, setLicense] = useState(source?.license ?? "");
  const [description, setDescription] = useState(source?.description ?? "");
  const [repoId, setRepoId] = useState(existing.repo_id ?? "");
  const [process, setProcess] = useState(existing.process ?? "file");
  const [attempts, setAttempts] = useState(String(existing.attempts ?? 3));
  const [workers, setWorkers] = useState(String(existing.workers ?? 2));
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const save = async () => {
    if (!name.trim()) {
      setError("name 必填：数据源名称");
      return;
    }
    let params: Source["params"];
    if (kind === "huggingface") {
      if (!repoId.trim()) {
        setError("repo_id 必填：HuggingFace 仓库 id，如 lmms-lab-encoder/COCO-Caption");
        return;
      }
      params = {
        ...existing,
        repo_id: repoId.trim(),
        process,
        attempts: Math.max(1, Math.min(10, Number(attempts) || 3)),
        workers: Math.max(1, Math.min(32, Number(workers) || 2)),
      };
    } else {
      params = existing;
    }
    const body = { name: name.trim(), kind, url, license, description, params };
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
    <FormModal
      title={source ? "编辑数据源" : "新建数据源"}
      onClose={onClose}
      onConfirm={() => void save()}
      saving={saving}
      error={error}
      confirmLabel="保存"
    >
      <Field
        label="name"
        value={name}
        onChange={setName}
        mono
        required
        hint="数据源名称（唯一），如 coco-caption"
      />
      <Field
        label="kind"
        value={kind}
        onChange={setKind}
        kind="select"
        options={["huggingface", "local"]}
        required
        hint="huggingface=从 HF 仓库同步；local=本地目录导入"
      />
      {kind === "huggingface" ? (
        <>
          <Field
            label="repo_id"
            value={repoId}
            onChange={setRepoId}
            mono
            required
            placeholder="lmms-lab-encoder/COCO-Caption"
            hint="HuggingFace 仓库 id（必填）"
          />
          <Field
            label="process"
            value={process}
            onChange={setProcess}
            kind="select"
            options={["file", "parquet"]}
            hint="file=原样入库；parquet=解包数据集内嵌图像"
          />
          <Field
            label="attempts"
            value={attempts}
            onChange={setAttempts}
            type="number"
            mono
            hint="下载失败重试次数（1–10，默认 3）"
          />
          <Field
            label="workers"
            value={workers}
            onChange={setWorkers}
            type="number"
            mono
            hint="Ray 同步并发数（1–32，默认 2）"
          />
        </>
      ) : (
        <Field
          label="url"
          value={url}
          onChange={setUrl}
          mono
          hint="本地目录路径（必填），如 /data/images"
        />
      )}
      <Field label="license" value={license} onChange={setLicense} hint="如 CC-BY-4.0，可留空" />
      <Field label="description" value={description} onChange={setDescription} hint="可留空" />
    </FormModal>
  );
}

// ---- asset datasets ----------------------------------------------------------

interface AssetDataset {
  id: string;
  name: string;
  kind: string;
  url: string;
  license: string;
  description: string;
  enabled: boolean;
  asset_count: number;
  ready_count: number;
  failed_count: number;
  pending_count: number;
  ready_bytes: number;
  tags: string[];
  latest_run: {
    id: string;
    status: string;
    stage: string;
    progress: number;
    started_at: string;
    updated_at: string;
  } | null;
}

function DatasetList() {
  const { data, error, reload } = useFetch<AssetDataset[]>("/api/asset-datasets");
  const [tagFilter, setTagFilter] = useState("");
  const [open, setOpen] = useState<AssetDataset | null>(null);

  const rows = (data ?? []).filter(
    (d) => !tagFilter || d.tags.some((t) => t === tagFilter || t.endsWith(`=${tagFilter}`)),
  );

  const columns: Column<AssetDataset>[] = [
    { key: "name", label: "dataset", render: (d) => <Mono>{d.name}</Mono> },
    { key: "kind", label: "kind", render: (d) => <Mono>{d.kind}</Mono> },
    { key: "tags", label: "tags", render: (d) => d.tags.join(", ") || "-" },
    { key: "count", label: "assets", render: (d) => <Mono>{d.asset_count}</Mono> },
    { key: "ready", label: "ready", render: (d) => <Mono>{d.ready_count}</Mono> },
    { key: "failed", label: "failed", render: (d) => <Mono>{d.failed_count}</Mono> },
    { key: "bytes", label: "bytes", render: (d) => fmtBytes(d.ready_bytes) },
    {
      key: "sync",
      label: "同步",
      render: (d) =>
        d.latest_run ? (
          <>
            <Status status={d.latest_run.status} />
            <Mono>{d.latest_run.id.slice(0, 12)}</Mono>
          </>
        ) : (
          "-"
        ),
    },
    {
      key: "ops",
      label: "",
      render: (d) => (
        <Button size="sm" variant="outline" onClick={() => setOpen(d)}>
          查看资产
        </Button>
      ),
    },
  ];

  return (
    <>
      <ErrorNote message={error} />
      <DataTable
        columns={columns}
        rows={rows}
        toolbar={
          <>
            <Field label="tag" value={tagFilter} onChange={setTagFilter} placeholder="group=name" mono />
            <Button variant="outline" onClick={reload}>
              刷新
            </Button>
          </>
        }
      />
      {open && <DatasetAssets dataset={open} onBack={() => setOpen(null)} />}
    </>
  );
}

// ---- assets within a dataset -------------------------------------------------

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

function DatasetAssets({
  dataset,
  onBack,
}: {
  dataset: AssetDataset;
  onBack: () => void;
}) {
  const [status, setStatus] = useState("");
  const [tag, setTag] = useState("");
  const [q, setQ] = useState("");
  const [cursor, setCursor] = useState<string | undefined>(undefined);

  const params = new URLSearchParams({ page_size: "50", source: dataset.id });
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

  const notify = (fn: () => Promise<unknown>, okMsg: string) =>
    fn()
      .then(() => toast.success(okMsg))
      .then(reload)
      .catch((err: unknown) => toast.error(String(err)));

  const remove = (asset: Asset) => {
    if (window.confirm(`删除资产「${asset.name}」？`)) {
      void notify(() => del(`/api/assets/${asset.id}`), "已删除");
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
    void notify(() => post(`/api/assets/${asset.id}/tags`, { name, group }), "已打标");
  };

  const rollback = (asset: Asset) => {
    const version = window.prompt(`回滚「${asset.name}」到版本号`, String(asset.version));
    if (version == null || !version) {
      return;
    }
    void notify(
      () => post(`/api/assets/${asset.id}/rollback`, { version: Number(version) }),
      "已回滚",
    );
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
        <span className="flex flex-wrap items-center gap-1.5">
          <Button
            size="sm"
            variant="outline"
            onClick={() => window.open(`/api/assets/${a.id}/preview`, "_blank")}
          >
            预览
          </Button>
          <Button size="sm" variant="outline" onClick={() => tagAsset(a)}>
            打标
          </Button>
          <Button size="sm" variant="outline" onClick={() => rollback(a)}>
            回滚
          </Button>
          <Button size="sm" variant="destructive" onClick={() => remove(a)}>
            删除
          </Button>
        </span>
      ),
    },
  ];

  return (
    <PageSection eyebrow={`DATASET · ${dataset.name}`} title={`${dataset.asset_count} 个资产`}>
      <p className="mb-4 text-sm text-muted-foreground">
        标签：{dataset.tags.join(", ") || "-"} · 来源：<Mono>{dataset.kind}</Mono>
      </p>
      <ErrorNote message={error} />
      <DataTable
        columns={columns}
        rows={data?.items ?? []}
        toolbar={
          <>
            <Field
              label="status"
              value={status}
              onChange={setStatus}
              kind="select"
              options={["ready", "failed", "processing", "deleted"]}
            />
            <Field label="tag" value={tag} onChange={setTag} placeholder="group=name" mono />
            <Field label="q" value={q} onChange={setQ} placeholder="名称/摘要搜索" />
            <Button variant="outline" onClick={reload}>
              刷新
            </Button>
            <Button variant="outline" onClick={onBack}>
              返回数据集列表
            </Button>
          </>
        }
        footer={
          <CursorPagination
            cursor={cursor}
            nextCursor={data?.next_cursor}
            total={dataset.asset_count}
            onFirst={() => {
              setCursor(undefined);
              reload();
            }}
            onNext={() => setCursor(data?.next_cursor ?? undefined)}
          />
        }
      />
    </PageSection>
  );
}

// ---- snapshots ---------------------------------------------------------------

interface Snapshot {
  id: string;
  name: string;
  created_at: string;
  asset_count: number;
  manifest_sha1: string;
}

function SnapshotsTab() {
  const { data, error, reload } = useFetch<Snapshot[]>("/api/snapshots");
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");

  const create = async () => {
    try {
      await post("/api/snapshots", { name });
      toast.success("快照已创建");
      setName("");
      setCreating(false);
      reload();
    } catch (err) {
      toast.error(String(err));
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
    <>
      <ErrorNote message={error} />
      <DataTable
        columns={columns}
        rows={data ?? []}
        toolbar={
          <>
            {creating ? (
              <>
                <Field label="快照名" value={name} onChange={setName} mono placeholder="v1" />
                <Button onClick={() => void create()}>创建</Button>
                <Button variant="outline" onClick={() => setCreating(false)}>
                  取消
                </Button>
              </>
            ) : (
              <Button onClick={() => setCreating(true)}>新建快照</Button>
            )}
            <Button variant="outline" onClick={reload}>
              刷新
            </Button>
          </>
        }
      />
    </>
  );
}

function DatasetsPage() {
  return (
    <PageContainer
      desc="同一数据源同步完成的资产归纳为一个数据集；标签区分数据集构成。快照为集合级不可变版本，供 data-factory 引用。"
    >
      <Tabs defaultValue="datasets">
        <TabsList className="mb-4">
          <TabsTrigger value="datasets">数据集</TabsTrigger>
          <TabsTrigger value="snapshots">快照</TabsTrigger>
        </TabsList>
        <TabsContent value="datasets">
          <DatasetList />
        </TabsContent>
        <TabsContent value="snapshots">
          <SnapshotsTab />
        </TabsContent>
      </Tabs>
    </PageContainer>
  );
}

// ---- sync runs ---------------------------------------------------------------

interface SyncRun {
  id: string;
  source_id: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  progress: number;
  total_files: number;
  done_files: number;
  failed_files: number;
  current_stage: string;
}

interface SyncEvent {
  seq: number;
  level: string;
  message: string;
  remote: string;
  created_at: string;
}

interface SyncTask {
  id: string;
  name: string;
  path_in_repo: string;
  status: string;
  bytes_downloaded: number;
  total_bytes: number;
  fraction: number | null;
  attempts: number;
  process_attempts: number;
  error: string;
}

interface SyncTaskPage {
  items: SyncTask[];
  total: number;
  offset: number;
  limit: number;
}

const TASK_PAGE_SIZE = 20;

function SyncRunsTab() {
  const { data, error, reload } = useFetch<SyncRun[]>("/api/sync/runs?limit=50");
  const [selected, setSelected] = useState<SyncRun | null>(null);
  const [events, setEvents] = useState<SyncEvent[]>([]);
  const [tasks, setTasks] = useState<SyncTaskPage | null>(null);
  const [taskError, setTaskError] = useState("");

  const openRun = async (run: SyncRun) => {
    setSelected(run);
    try {
      const evs = await get<SyncEvent[]>(`/api/sync/${run.id}/events?limit=500`);
      setEvents(evs);
    } catch (err) {
      setEvents([]);
      toast.error(String(err));
    }
  };

  const loadTasks = async (run: SyncRun, offset: number) => {
    setTaskError("");
    try {
      const page = await get<SyncTaskPage>(
        `/api/sync/${run.id}/tasks?offset=${offset}&limit=${TASK_PAGE_SIZE}`,
      );
      setTasks(page);
    } catch (err) {
      setTaskError(String(err));
    }
  };

  const openTasks = (run: SyncRun) => {
    setSelected(run);
    void loadTasks(run, 0);
  };

  const controlRun = async (run: SyncRun, action: "pause" | "resume", okMsg: string) => {
    try {
      await post(`/api/sync/${run.id}/${action}`);
      toast.success(okMsg);
      reload();
      if (selected?.id === run.id) {
        void loadTasks(run, tasks?.offset ?? 0);
        void openRun(run);
      }
    } catch (err) {
      toast.error(String(err));
    }
  };

  const runColumns: Column<SyncRun>[] = [
    { key: "id", label: "run_id", render: (r) => <Mono>{r.id}</Mono> },
    { key: "source", label: "source_id", render: (r) => <Mono>{r.source_id}</Mono> },
    { key: "status", label: "status", render: (r) => <Status status={r.status} /> },
    {
      key: "progress",
      label: "进度",
      render: (r) => (
        <>
          <Mono>{r.progress.toFixed(1)}%</Mono>
          <span className="ml-1 text-xs text-muted-foreground">{r.current_stage}</span>
        </>
      ),
    },
    {
      key: "files",
      label: "文件",
      render: (r) => (
        <Mono>
          {r.done_files}/{r.failed_files}/{r.total_files}
        </Mono>
      ),
    },
    { key: "started", label: "started_at", render: (r) => <Mono>{fmtTime(r.started_at)}</Mono> },
    {
      key: "finished",
      label: "finished_at",
      render: (r) => <Mono>{fmtTime(r.finished_at)}</Mono>,
    },
    {
      key: "ops",
      label: "",
      render: (r) => (
        <span className="flex flex-wrap items-center gap-1.5">
          <Button size="sm" variant="outline" onClick={() => openTasks(r)}>
            任务
          </Button>
          <Button size="sm" variant="outline" onClick={() => void openRun(r)}>
            事件
          </Button>
          {r.status === "running" && (
            <Button size="sm" variant="outline" onClick={() => void controlRun(r, "pause", "已暂停")}>
              暂停
            </Button>
          )}
          {r.status === "paused" && (
            <Button size="sm" variant="outline" onClick={() => void controlRun(r, "resume", "已继续")}>
              继续
            </Button>
          )}
        </span>
      ),
    },
  ];

  const taskColumns: Column<SyncTask>[] = [
    { key: "name", label: "文件", render: (t) => <Mono>{t.name}</Mono> },
    { key: "status", label: "状态", render: (t) => <Status status={t.status} /> },
    {
      key: "bytes",
      label: "下载",
      render: (t) =>
        t.total_bytes > 0 ? (
          <Mono>
            {fmtBytes(t.bytes_downloaded)}/{fmtBytes(t.total_bytes)}
            {t.fraction != null ? ` (${Math.round(t.fraction * 100)}%)` : ""}
          </Mono>
        ) : (
          "-"
        ),
    },
    {
      key: "attempts",
      label: "尝试(下载/处理)",
      render: (t) => <Mono>{t.attempts}/{t.process_attempts}</Mono>,
    },
    {
      key: "error",
      label: "错误",
      render: (t) =>
        t.error ? (
          <span className="text-xs text-destructive" title={t.error}>
            {t.error.length > 60 ? `${t.error.slice(0, 60)}…` : t.error}
          </span>
        ) : (
          "-"
        ),
    },
  ];

  return (
    <>
      <ErrorNote message={error} />
      <DataTable
        columns={runColumns}
        rows={data ?? []}
        toolbar={
          <Button variant="outline" onClick={reload}>
            刷新
          </Button>
        }
      />
      {selected && (
        <PageSection eyebrow={`SYNC · ${selected.id}`} title="批次任务管理">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <Status status={selected.status} />
            <Mono>{selected.progress.toFixed(1)}%</Mono>
            <span className="text-xs text-muted-foreground">
              阶段 {selected.current_stage || "-"} · 完成 {selected.done_files}/{selected.total_files}
              失败 {selected.failed_files}
            </span>
            <Button
              size="sm"
              variant="outline"
              onClick={() => void loadTasks(selected, tasks?.offset ?? 0)}
            >
              刷新
            </Button>
            {selected.status === "running" && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => void controlRun(selected, "pause", "已暂停")}
              >
                暂停
              </Button>
            )}
            {selected.status === "paused" && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => void controlRun(selected, "resume", "已继续")}
              >
                继续
              </Button>
            )}
          </div>
          <ErrorNote message={taskError} />
          <DataTable
            columns={taskColumns}
            rows={tasks?.items ?? []}
            footer={
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!tasks || tasks.offset <= 0}
                  onClick={() => void loadTasks(selected, 0)}
                >
                  首页
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!tasks || tasks.offset <= 0}
                  onClick={() => void loadTasks(selected, Math.max(0, (tasks?.offset ?? 0) - TASK_PAGE_SIZE))}
                >
                  上一页
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!tasks || tasks.offset + tasks.items.length >= tasks.total}
                  onClick={() => void loadTasks(selected, (tasks?.offset ?? 0) + TASK_PAGE_SIZE)}
                >
                  下一页
                </Button>
                {tasks && (
                  <span className="ml-2 font-mono text-xs text-muted-foreground">
                    共 {tasks.total} 个文件任务 · 第 {tasks.offset / TASK_PAGE_SIZE + 1} 页
                  </span>
                )}
              </div>
            }
          />
          <PageSection eyebrow={`SYNC · ${selected.id}`} title="事件流">
            {events.length === 0 ? (
              <JsonBlock value="（暂无事件）" />
            ) : (
              <JsonBlock value={events} />
            )}
          </PageSection>
        </PageSection>
      )}
    </>
  );
}

// ---- downloads ---------------------------------------------------------------

interface Download {
  asset_name: string | null;
  asset_id: string;
  status: string;
  sha256: string | null;
  bytes_downloaded: number | null;
  error: string | null;
  downloader: string;
  attempts: number;
  started_at: string;
}

function DownloadsTab() {
  const { data, error, reload } = useFetch<Download[]>("/api/downloads?limit=50");

  const columns: Column<Download>[] = [
    {
      key: "asset",
      label: "asset",
      render: (d) => <Mono>{d.asset_name || d.asset_id}</Mono>,
    },
    { key: "status", label: "status", render: (d) => <Status status={d.status} /> },
    { key: "bytes", label: "bytes", render: (d) => fmtBytes(d.bytes_downloaded) },
    { key: "sha", label: "sha256", render: (d) => <Mono>{d.sha256?.slice(0, 12) ?? "-"}</Mono> },
    { key: "downloader", label: "downloader", render: (d) => <Mono>{d.downloader}</Mono> },
    { key: "attempts", label: "attempts", render: (d) => <Mono>{d.attempts}</Mono> },
    { key: "error", label: "error", render: (d) => d.error || "-" },
  ];

  return (
    <>
      <ErrorNote message={error} />
      <DataTable
        columns={columns}
        rows={data ?? []}
        toolbar={
          <Button variant="outline" onClick={reload}>
            刷新
          </Button>
        }
      />
    </>
  );
}

function RecordsPage() {
  return (
    <PageContainer
      desc="每次 sync/reprocess 一次 run（文件级事件流可复查）；下载流水账含失败原因。"
    >
      <Tabs defaultValue="sync">
        <TabsList className="mb-4">
          <TabsTrigger value="sync">同步运行</TabsTrigger>
          <TabsTrigger value="downloads">下载记录</TabsTrigger>
        </TabsList>
        <TabsContent value="sync">
          <SyncRunsTab />
        </TabsContent>
        <TabsContent value="downloads">
          <DownloadsTab />
        </TabsContent>
      </Tabs>
    </PageContainer>
  );
}

export const assetOverviewPage = {
  key: "overview",
  label: "总览",
  component: AssetOverviewPage,
};

export const assetPages = [
  { key: "sources", label: "数据源", component: SourcesPage },
  { key: "datasets", label: "数据集", component: DatasetsPage },
  { key: "records", label: "同步/下载记录", component: RecordsPage },
];
