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
  FieldItem,
  fmtBytes,
  fmtTime,
  FormModal,
  JsonBlock,
  Mono,
  PageContainer,
  PageSection,
  StatCard,
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
        <>
          <div className="mb-5 grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-3">
            <StatCard label="asset_count" value={data.asset_count} />
            <StatCard label="ready" value={data.ready_count} />
            <StatCard label="failed" value={data.failed_count} />
            <StatCard label="sources" value={data.source_count} />
            <StatCard label="snapshots" value={data.snapshot_count} />
          </div>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-2.5">
            <FieldItem label="backend">
              <Mono>{data.backend}</Mono>
            </FieldItem>
            <FieldItem label="bucket">
              <Mono>{data.bucket ?? "-"}</Mono>
            </FieldItem>
            <FieldItem label="data_dir">
              <Mono>{data.data_dir}</Mono>
            </FieldItem>
            <FieldItem label="db_path">
              <Mono>{data.db_path}</Mono>
            </FieldItem>
          </div>
        </>
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
        <span className="flex flex-wrap items-center gap-1.5">
          <Button
            size="sm"
            variant="outline"
            onClick={() => void runAction(() => post(`/api/sources/${s.id}/sync`), "已触发同步")}
          >
            sync
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => void runAction(() => post(`/api/sources/${s.id}/reprocess`), "已触发重处理")}
          >
            reprocess
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
    <FormModal
      title={source ? "编辑数据源" : "新建数据源"}
      onClose={onClose}
      onConfirm={() => void save()}
      saving={saving}
      error={error}
      confirmLabel="保存"
    >
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
      label: "sync",
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
}

interface SyncEvent {
  seq: number;
  level: string;
  message: string;
  remote: string;
  created_at: string;
}

function SyncRunsTab() {
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
    {
      key: "ops",
      label: "",
      render: (r) => (
        <Button size="sm" variant="outline" onClick={() => void openRun(r)}>
          事件
        </Button>
      ),
    },
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
      {selected && (
        <PageSection eyebrow={`SYNC · ${selected.id}`} title="事件流">
          {events.length === 0 ? (
            <JsonBlock value="（暂无事件）" />
          ) : (
            <JsonBlock value={events} />
          )}
        </PageSection>
      )}
    </>
  );
}

// ---- downloads ---------------------------------------------------------------

interface Download {
  asset_name: string;
  status: string;
  sha256: string;
  bytes_downloaded: number;
  error: string | null;
}

function DownloadsTab() {
  const { data, error, reload } = useFetch<Download[]>("/api/downloads?limit=50");

  const columns: Column<Download>[] = [
    { key: "asset", label: "asset", render: (d) => <Mono>{d.asset_name}</Mono> },
    { key: "status", label: "status", render: (d) => <Status status={d.status} /> },
    { key: "bytes", label: "bytes", render: (d) => fmtBytes(d.bytes_downloaded) },
    { key: "sha", label: "sha256", render: (d) => <Mono>{d.sha256.slice(0, 12)}</Mono> },
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
