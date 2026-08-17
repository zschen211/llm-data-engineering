# 数据资产层系统规格说明（Asset Layer Spec）

> 版本：0.1（草案） · 状态：实现中 · 所属：asset-management 子项目

## 1. 概述与目标

资产层是 asset-management 数据工厂的**种子层**，负责把分散的多模态素材（图像/文档/图表）变成可统一管理、可追溯、可版本化、可标签化的资产池。

核心设计围绕两个关键元信息：

- **数据源（DataSource）**：定义资源的元信息（名称、类型、许可、描述）与互联网下载源（URL / HF repo / 本地目录）
- **存储位置（StorageLocation）**：定义下载后的存储后端与对象键（object key）布局

在此之上提供：统一的管理界面（CLI + Web）、版本管理（资产级 + 集合级快照）、标签管理（分组 + 组合筛选）、可插拔的下载器（按数据源类型分派）。

## 2. 总体架构

```
┌────────────────────────────────────────────────────────────┐
│                    资产管理（AssetStore）                    │
│   CLI（asset 子命令组）      Web UI（FastAPI）              │
│                           │                               │
│                           ▼                               │
│   DataSource（数据源）──► Downloader（按 kind 分派）          │
│                              │  resolve / download         │
│                              ▼                            │
│   StorageLocation：RustFS（S3 兼容对象存储，blob 权威）        │
│   Metadata：SQLite（版本/标签/来源/下载状态，索引权威）         │
└────────────────────────────────────────────────────────────┘
```

分层职责：

| 层 | 职责 | 权威性 |
| --- | --- | --- |
| SQLite 元数据 | 数据源、资产清单、版本历史、标签、下载任务、快照 | 版本/标签/来源的**唯一权威** |
| RustFS（S3 兼容对象存储） | 多模态 blob 的存储、去重、跨机访问 | blob 内容与对象键的**唯一权威** |
| StorageBackend 抽象 | 屏蔽本地磁盘与 S3 后端差异 | 可替换实现 |

## 3. 存储选型

### 3.1 元数据：SQLite（`data/assets.db`）

- 单文件、零运维、随项目走；表结构按 PostgreSQL 兼容写法，未来多人协作可迁移
- 事务保证下载状态与资产登记的一致性
- 支持关系查询（资产 ↔ 标签 ↔ 版本 ↔ 快照）

### 3.2 多模态 blob：RustFS（S3 兼容对象存储）

选型理由：获得网络访问、副本/纠删码可靠性、bitrot 校验、S3 生态兼容与内置管理 console；同时积累对象存储运维经验。

**部署**：单节点 Docker 服务（`docker-compose.yml`，见仓库根目录）：

- `9000`：S3 API 端口
- `9001`：Web console（默认凭据 `rustfsadmin / rustfsadmin`，生产必须修改）
- 数据目录持久化挂载，运行时用户 `10001:10001`

**Bucket 布局**（双层：原始层 + 资产层）：

```
asset-assets/
├── raw/<source_id>/<path_in_repo>       # 原始层：HF 仓库镜像，路径寻址（不去重）
└── blobs/<sha256[:2]>/<sha256><ext>     # 资产层：内容寻址：同内容只存一份（跨源去重）
```

分层职责：

| 层 | 寻址 | 语义 | 消费者 |
| --- | --- | --- | --- |
| `raw/` 原始层 | 路径（source_id 前缀，按源管理/删除/GC） | 下载镜像，与 repo 文件一一对应，`raw_files` 表登记 sha256/size/commit | Phase B 处理任务、reprocess |
| `blobs/` 资产层 | 内容 sha256（内容不变则 key 不变，版本天然绑定内容） | 处理后的最终资产，唯一权威 | API server（preview/download/查询） |

分层收益（管理与重试）：

- **重试分层**：处理失败 → 只重跑 Phase B（raw 已入库，零网络）；下载失败 → 只补 Phase A；换 processor → 无需重新下载
- **幂等分层**：Phase A 以 `raw_files.status` + sha256 校验判重；Phase B 以 sha256 去重（`BEGIN IMMEDIATE`）判重
- 下载临时区在本地 `data/tmp/`，校验通过后上传

**版本化策略**：SQLite `asset_versions` 为权威；RustFS bucket 层版本化默认关闭（避免存储翻倍），需要时可按 bucket 开启作为额外保护。

### 3.3 StorageBackend 抽象

```python
class StorageBackend(ABC):
    put_file(local_path, sha256, ext) -> object_key   # 存在则跳过（去重）
    get_file(object_key, target) -> Path
    exists(object_key) -> bool
    open_stream(object_key) -> IO                    # 预览等流式读取
```

两个实现：

| 实现 | 适用场景 |
| --- | --- |
| `LocalStorageBackend` | 离线开发、无服务环境、单元测试 |
| `S3StorageBackend`（boto3，endpoint 指向 RustFS） | 默认生产路径 |

后端选择（`api.open_store`）：显式 `backend` 参数优先；否则按 `ASSET_STORAGE_BACKEND` 开关——`rustfs`（需 `RUSTFS_ENDPOINT` + 凭据，缺失直接报错）、`local`（本地内容寻址目录）、`auto`（默认：`RUSTFS_ENDPOINT` 存在 → S3；否则回退本地并打印醒目 warning，防止"声称 RustFS 实际落盘"的静默降级）。`scripts/serve.sh` 默认即导出 compose 同款 RustFS 配置并加载根目录 `.env`，`--storage local` 显式切本地。

## 4. 数据库 Schema（SQLite）

```sql
sources (
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
  url TEXT, license TEXT, description TEXT, params TEXT DEFAULT '{}',
  enabled INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT
)

assets (
  id TEXT PRIMARY KEY, source_id TEXT REFERENCES sources(id),
  name TEXT NOT NULL, asset_type TEXT, object_key TEXT,
  sha256 TEXT UNIQUE, size INTEGER, width INTEGER, height INTEGER,
  status TEXT DEFAULT 'pending',              -- pending/downloading/ready/failed
  current_version INTEGER DEFAULT 1,
  meta TEXT DEFAULT '{}', created_at TEXT, updated_at TEXT
)

asset_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, asset_id TEXT REFERENCES assets(id),
  version INTEGER, sha256 TEXT, object_key TEXT, change_note TEXT,
  created_at TEXT, UNIQUE(asset_id, version)
)

tags (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, tag_group TEXT DEFAULT 'default')

asset_tags (asset_id TEXT REFERENCES assets(id), tag_id TEXT REFERENCES tags(id),
            PRIMARY KEY (asset_id, tag_id))

downloads (
  id INTEGER PRIMARY KEY AUTOINCREMENT, asset_id TEXT,
  downloader TEXT, status TEXT, error TEXT, attempts INTEGER DEFAULT 0,
  started_at TEXT, finished_at TEXT
)

snapshots (id TEXT PRIMARY KEY, manifest_sha1 TEXT, asset_count INTEGER, created_at TEXT)

snapshot_assets (snapshot_id TEXT REFERENCES snapshots(id),
                 asset_id TEXT REFERENCES assets(id), asset_version INTEGER,
                 PRIMARY KEY (snapshot_id, asset_id))

raw_files (
  source_id TEXT NOT NULL, path_in_repo TEXT NOT NULL,
  object_key TEXT NOT NULL,              -- raw/<source_id>/<path_in_repo>
  sha256 TEXT, size INTEGER,
  status TEXT DEFAULT 'pending',         -- pending / uploaded / failed
  commit_hash TEXT, attempts INTEGER DEFAULT 0, error TEXT,
  created_at TEXT, updated_at TEXT,
  PRIMARY KEY(source_id, path_in_repo)
)

sync_stages (
  run_id TEXT, stage TEXT,               -- resolve / download_raw / process / persist
  started_at TEXT, finished_at TEXT,
  duration_s REAL, item_count INTEGER DEFAULT 0, failed_count INTEGER DEFAULT 0,
  retry_app INTEGER DEFAULT 0, retry_ray INTEGER DEFAULT 0,
  PRIMARY KEY(run_id, stage)
)
```

## 5. 版本管理设计（两层）

### 5.1 资产级版本

- `sha256` 是资产身份：内容不变 → 不新建记录，`sync` 时跳过（去重）
- 内容变更（重新导入/替换）→ 追加 `asset_versions` 历史行，`current_version` 递增
- 支持回滚：`version rollback <asset_id> <version>` 把 assets 指针指回历史版本

### 5.2 集合级快照

- `snapshot` 固定当前所有 `ready` 资产（含版本号）为一组，生成 `manifest_sha1`
- 后续 train/val 切分、QA 引用快照 → 保证可复现
- `snapshot ls` / `snapshot show <id>` 查看

## 6. 标签管理设计

- `tags.name` 全局唯一，`tags.tag_group` 分组（如 `source` / `task` / `quality`）
- 资产 ↔ 标签多对多（`asset_tags`）
- 组合筛选：`asset ls --tag task=chart --tag quality=high` → 按 group=name 匹配
- 筛选结果可导出为资产池 JSONL（供 generate 阶段消费）

## 7. 下载管线（download → process → persist）

下载被拆成三段管线，职责严格分离；当前仅支持 `huggingface` 数据源（kind），数据格式转换由 `params.process` 选择：

```
DownloadStage ──► Processor ──► PersistStage
  resolve 文件清单    下载文件→资产候选      候选→存储层+元数据索引
  单文件拉取(重试)    "file"=原样         内容寻址去重(sha256)
  attempts           "parquet"=逐行解码   登记 assets + downloads
```

**数据契约**：

```python
@dataclass
class RemoteRef:     # download 的输出/process 的输入
    id: str; name: str; path_in_repo: str; meta: dict

@dataclass
class Candidate:     # process 的输出/persist 的输入
    name: str; path: str; sha256: str; size: int; ext: str
    asset_type: str; width: int|None; height: int|None; meta: dict
```

**DownloadStage**（`services/downloaders/download.py`，网络密集型，仅 HF）：
- `resolve()`：枚举仓库文件（`subfolder`/`allow_patterns`/`ignore_patterns` 过滤）
- `download()`：单文件拉取，指数退避重试（`attempts`，默认 3），tqdm 字节进度回调
- `hub=` 参数可注入测试桩（生产默认 huggingface_hub，核心依赖）

**Processor**（`services/downloaders/process.py` + `processors/`，按 `params.process` 注册表选择）：

| name | 实现 | 转换逻辑 |
| --- | --- | --- |
| `file`（默认） | FileProcessor | 下载文件即资产（identity），`asset_type` 参数或文件名启发式分类 |
| `parquet` | ParquetProcessor | 逐行解码 parquet 中的图片（HF Image 特征，流式批量读取），坏行跳过，处理后删除 parquet 释放磁盘 |

**PersistStage**（`services/downloaders/persist.py`，唯一触碰存储层的阶段）：
- `persist_one()`：`backend.put_file`（内容寻址去重）→ sha256 查重 → 登记 `assets`（version=1）+ `downloads`，返回 `new`/`skipped`
- 读-去重-插入在 `db.transaction()`（`BEGIN IMMEDIATE`）内执行：跨进程/跨线程的写者在此串行化，sha256 去重无竞态
- `persist()`：批量执行并收集单候选错误（不拖垮整批）

**本地目录导入**（`services/sync.import_dir`）不走网络管线：直接扫描目录 → 分类 → 构造 Candidate → 交给 PersistStage，作为 store 级便捷 API 保留。

### sync 流程（Ray Data 两阶段管线）

**执行模型**：`sync_source` 由两条 Ray Data 流式管线驱动，替代手搓滑动窗口——分片、并行度、崩溃重试（`max_task_retries`）、背压全部交给 Ray Data 原生能力：

```
Phase A 原始层入库（pipeline #1，网络 IO）
  resolve（列文件清单，driver 侧 1 次）
    → ray.data.from_items(pending raw 行)
    → map(download_one, concurrency=workers, max_task_retries=2)
         hf_hub_download(本地缓存) → 上传 raw/<source_id>/<path> → raw_files 登记
    → driver iter_rows() 聚合进度（pull 停止 = 全管线背压停驻，即暂停语义）

Phase B 资产层处理（pipeline #2，CPU/存储 IO）
  ray.data.from_items(pending raw_files 行)
    → flat_map(process_one, max_task_retries=2)   # 拉取 raw → processor → Candidate 行
    → map(persist_one, max_task_retries=2)        # Candidate → blobs/ + assets 登记
    → driver iter_rows() 聚合 SyncReport
```

- **中间行契约节点无关**：Candidate 行不携带本地路径（Ray Data 跨 op 不保证同 worker）——`file` processor 走 `source_key`（引用 raw 对象，persist 时后端 server-side copy：S3 `copy_object` / 本地 `copyfile`，零字节过对象存储）；`parquet` processor 走 `payload`（解码出的图片字节，经对象存储传递）
- Phase B 中 process/persist 之间做一次 `materialize()` 边界：获得独立 stage 计时与重试隔离
- **暂停语义**：Ray Data 是 pull-based 流式执行——driver 停止 `iter_rows()` 拉取即全管线背压停驻（in-flight 批次完成，缓冲有界），不再需要 worker 轮询 `paused()`；`pause_sync`/`resume_sync` 仍切换 run 状态
- **容错**：worker 崩溃由 Ray Data `max_task_retries=2` 自动重跑（下载幂等：HF 缓存 + raw exists + sha256 校验；persist 幂等：内容寻址去重）；应用层错误（网络/解析/持久化）在任务内捕获进 outcome 行，只影响该文件/资产
- **状态共享**：任务只通过 SQLite 通信——每个任务开自己的 `Database`（`mark_stale=False`，仅 driver 可标记 stale run）、按 `BackendConfig` 重建存储后端（boto3 client 不可序列化）；WAL + busy_timeout 兜底
- `ray[data]` 是项目核心依赖（`[project.dependencies]`），同步直接可用，无需额外 extra

**阶段驱动的管理入口**（利用 raw 层持久化）：

| 入口 | 阶段 | 用途 |
| --- | --- | --- |
| `sync_source` | A → B | 完整同步（中断续跑：raw_files/sync_tasks 状态跳过已完成项） |
| `reprocess_source` | 仅 B | 换 processor 后重新解析，零网络 |
| `redownload_source` | 仅 A | 强制刷新 raw 层（sha256 校验） |

**集群生命周期**：Ray 集群由进程级单例 `services/cluster.ClusterManager` 统一持有——Web 应用在 lifespan 启动时 `ensure_started()`（一次 init，之后每次同步零初始化开销），关闭时 `stop()`；`run_ray_data_sync` 只做幂等检查。集群被外部（如测试 fixture）初始化时只读复用、不接管关闭。状态通过 `GET /api/cluster/status` 暴露（dashboard URL、CPU 总量/可用、存活节点、运行中任务/actor 数），Web UI 顶栏每 5 秒轮询显示。**连接共享集群**：经 infra 契约变量 `RAY_ADDRESS`（`infra/scripts/ray-start.sh` 启动的独立集群）；未配置时以内嵌本地集群兜底（dev/测试，醒目 warning），`ASSET_RAY_NUM_CPUS` 仅作用于兜底集群。`ray[default]` 提供 dashboard 与 State API（`ray.util.state`）支撑监控。

全程写入 `downloads` 表（status/attempts/error），Web UI 可见失败原因与重试入口。

### 可观测性设计

指标链路：Ray worker（独立进程，registry 不对外）→ outcome 行 → driver 聚合观测 → `/metrics`（uvicorn 进程内 `prometheus-client`）→ Prometheus（`asset-management` job，`:8000`；另有 `ray-metrics` `:8080` 与 `node-exporter` `:9100`）→ Grafana（host 网络，provisioning 自动加载 dashboard）。

**分阶段耗时**（指标 + SQLite 双留档）：

| 指标 | 类型 | 标签 | 记录点 |
| --- | --- | --- | --- |
| `asset_sync_stage_duration_seconds` | Histogram | `run_id, stage` | driver：每 run 每 stage 结束记 1 次（`resolve / download_raw / process / persist`） |
| `asset_sync_item_duration_seconds` | Histogram | `run_id, stage` | driver 聚合 outcome 行：单文件（download_raw/process）/单资产（persist）耗时 |

**文件 / 资产总量**：

- `asset_sync_items_total` Counter `{run_id, stage, status}`，`status ∈ {done, skipped, failed}`；`stage=persist` 时 item = 资产（new=done / 去重=skipped），"总处理资产" = `sum(...{stage="persist"})`

**错误 / 重试**：

- `asset_sync_failures_total` Counter `{run_id, stage}`：任务级失败（含重试耗尽），与 item 级失败区分（一个 parquet 文件可含多个坏行）
- `asset_sync_retries_total` Counter `{run_id, stage, kind}`：`kind="app"` 应用层指数退避重试；`kind="ray"` 任务重启（任务启动时 `attempts>1` 即 +1——Ray Data 重跑不告知任务身份，但会重走 attempts+1 代码路径，故 `sync_tasks/raw_files.attempts` 是权威重启计数）

**SQLite 留档**：`sync_stages` 表（run × stage 一行：`duration_s/item_count/failed_count/retry_app/retry_ray`），端点 `GET /api/sync/runs/{id}/stages` 供 Web UI 展示，不受 Prometheus 保留期影响。

**Grafana**：新 dashboard `asset-management Sync Pipeline`（`../infra/grafana/provisioning/dashboards/asset-sync-pipeline.json`，模板变量 `run_id`）：

| Row | 面板 |
| --- | --- |
| 分阶段耗时 | 各阶段平均耗时（bar gauge，`rate(_sum)/rate(_count)` by stage）；各阶段平均单文件/单资产耗时（timeseries + P95）；单 run 阶段耗时明细（table，`$run` 过滤） |
| 文件/资产 | 吞吐（stacked，`rate(asset_sync_items_total[1m]) by (stage, status)`）；累计处理资产（stat）；单 run 处理量 |
| 错误/重试 | 重试速率（by kind）；失败速率（by stage）；失败率（失败/总量）；失败 Top runs（table）；Ray Data `ray_data_*` 补充（spilled bytes 等，版本相关） |

指标基数说明：`run_id` 为低频标签（每天个位数 run），序列数 = run × stage × bucket，Prometheus 保留窗口内可控。

## 8. CLI 命令设计（`asset-management asset`）

```
asset init [--backend rustfs|local] [--data-dir DIR] [--endpoint URL] [--bucket NAME]
asset source add|list|rm <...>
asset import <dir> [--labels LABELS.json] [--source-name NAME] [--type-hint]
asset sync <source_id>
asset ls [--tag g=n] [--type T] [--status S] [--source SRC] [--json]
asset tag add <asset_id> <name> [--group G] | rm <asset_id> <name> | list [--group G]
asset version ls <asset_id> | rollback <asset_id> <version> | snapshot [--name N] | snapshot ls
asset serve [--host 0.0.0.0] [--port 8000]
asset export <out.jsonl> [--tag g=n ...] [--source S]
```

环境变量（与 CLI 参数互备）：

```
RUSTFS_ENDPOINT, RUSTFS_ACCESS_KEY, RUSTFS_SECRET_KEY, RUSTFS_BUCKET, ASSET_DATA_DIR
```

## 9. Web 管理界面（FastAPI）

- fastapi + uvicorn 为核心依赖，`asset serve` 直接可用
- 端点：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET/POST | `/api/sources` | 数据源列表/新增 |
| PUT/DELETE | `/api/sources/{id}` | 修改/删除数据源 |
| POST | `/api/sources/{id}/sync` | 触发下载同步 |
| POST | `/api/sources/{id}/reprocess` | 仅重跑 Phase B（raw 已入库，零网络） |
| GET | `/api/sources/{id}/raw` | raw 层文件清单（`raw_files` 表） |
| GET | `/api/sync/runs/{id}/stages` | 各阶段耗时/重试留档（`sync_stages` 表） |
| GET | `/api/assets` | 资产游标分页列表（`?tag=&type=&status=&source=&q=&cursor=&page_size=`，返回 `{items, next_cursor, page_size}`；`cursor` 为不透明 base64url token，翻页用返回的 `next_cursor`，无 `next_cursor` 即末页；标签与搜索在 SQL 侧求值） |
| GET/DELETE | `/api/assets/{id}` | 资产详情/删除 |
| POST/DELETE | `/api/assets/{id}/tags` | 打标/去标 |
| POST/GET | `/api/snapshots` | 创建/列出快照 |
| GET | `/api/assets/{id}/preview` | 图片预览（从后端流式读取） |
| GET | `/` | 管理页面（原生 HTML+JS） |

## 10. 与现有流水线兼容

- `prepare-assets` 保留原语义（扫描 + 分类 + 均衡 + 导出 JSONL），实现改为走 AssetStore 的本地导入，导出产物格式不变 → `generate / qa / render / split` 零改动
- `classify_image` 等分类逻辑迁移到 `assets/classify.py`，从包根重新导出，保持导入兼容
- 资产池快照可导出为 JSONL（`asset export`），作为 `generate` 的输入

## 11. 依赖与测试计划

### 依赖

所有运行时依赖（ray[data]、huggingface_hub、pyarrow、boto3、fastapi、uvicorn、pillow、tqdm、prometheus-client、psutil）都在 `[project.dependencies]`（核心依赖，随 `uv sync` 自动安装），不使用动态 import 或运行时安装：

| extra | 包 | 用途 |
| --- | --- | --- |
| `dev` | pytest, pytest-cov, moto, httpx | 测试（moto 模拟 S3） |

### 测试用例

| 层 | 用例 |
| --- | --- |
| db | 八表 CRUD、sha256 唯一约束、版本历史、标签多对多、快照；raw_files / sync_stages CRUD |
| storage | Local 内容寻址/去重；S3 后端用 moto（put/get/exists/流式 + put_object/copy_object） |
| services/downloaders | local 导入分类；DownloadStage 重试/退避；PersistStage 事务去重；Ray Data 管线：分阶段跑（仅 A / 仅 B reprocess）、worker 崩溃重试（首跑抛异常验证 max_task_retries）、source_key 零拷贝与 payload 两条 persist 路径 |
| store | 端到端 sync（Ray Data）：resolve→raw→blobs→登记→失败记录；并发去重；暂停（停拉即停驻）/恢复；中断续跑 |
| obs | stage/item/retry 指标计数与 sync_stages 落库 |
| cli | init/source/import/sync/reprocess/ls/tag/version/snapshot/export 全流程 |
| web | TestClient：sources CRUD、资产筛选、打标、快照、预览、raw 清单、stages |

### 集成冒烟（真实 RustFS）

- 仓库根 `docker-compose.yml` 启动 RustFS
- `scripts/rustfs_smoke.py`：真实走通 上传/下载/去重/预览（需 `RUSTFS_*` 环境变量）

## 12. 统一对外 API（其他模块访问方式）

资产层对外只有两个稳定入口，其他模块一律经此访问，不触碰 `Database` / `StorageBackend` / 下载器内部：

```python
from asset_management.assets.api import open_store

with open_store() as store:                    # 环境变量决定后端（RUSTFS_* 或本地）
    report = store.import_dir(Path("./images"))  # SyncReport
    assets = store.list_assets(tags=["task=chart"], status="ready")
    snapshot = store.create_snapshot(name="v1")
    records = store.materialize(Path("./pool"))

# 显式指定后端
with open_store(backend=S3StorageBackend(...)) as store:
    ...
```

**`open_store(data_dir=None, backend=None)` 工厂**：
- `backend` 显式传入优先
- 否则读 `RUSTFS_ENDPOINT`（+ `RUSTFS_ACCESS_KEY`/`RUSTFS_SECRET_KEY`/`RUSTFS_BUCKET`）→ S3 后端；缺失凭据抛 `ValueError`
- 否则本地内容寻址后端（`data/blobs/`）
- `data_dir` 缺省读 `ASSET_DATA_DIR`，默认 `data/`

**`AssetStore` 公开方法（稳定契约）**：

| 分组 | 方法 |
| --- | --- |
| 数据源 | `add_source / get_source / get_source_by_name / list_sources / update_source / delete_source` |
| 同步 | `sync_source -> SyncReport`、`import_dir -> SyncReport` |
| 资产 | `list_assets / get_asset / delete_asset / count_assets / asset_tags / list_downloads` |
| 标签 | `tag_asset / untag_asset / list_tags` |
| 版本 | `bump_version / version_history / rollback` |
| 快照 | `create_snapshot / list_snapshots / snapshot_assets` |
| 物化 | `materialize / export_pool` |

**内部模块（非公开）**：`meta/`（`db.py` 的 `Database`、`models.py`）、`storage/`（`StorageBackend` 实现）、`routes/`（API router）、`services/downloaders/` —— 仅资产层自身与测试使用，不在稳定 API 范围内。

## 13. 后续扩展

- 下载器：pexels（API key + 分页）、coco（zip + 标注）、webdav 等
- 存储后端：云 S3/MinIO（S3StorageBackend 已天然兼容）
- Web UI：资产批量导入、下载进度实时推送（SSE/WebSocket）、标签批量操作
- 快照 diff：两个快照间资产差异对比
