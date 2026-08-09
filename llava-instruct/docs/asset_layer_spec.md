# 数据资产层系统规格说明（Asset Layer Spec）

> 版本：0.1（草案） · 状态：实现中 · 所属：llava-instruct 子项目

## 1. 概述与目标

资产层是 llava-instruct 数据工厂的**种子层**，负责把分散的多模态素材（图像/文档/图表）变成可统一管理、可追溯、可版本化、可标签化的资产池。

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

**Bucket 布局**：

```
llava-assets/
└── blobs/<sha256[:2]>/<sha256><ext>     # 内容寻址：同内容只存一份（跨源去重）
```

- object key 由内容 sha256 决定 → 内容不变则 key 不变，版本天然绑定内容
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

CLI 默认后端选择：环境变量 `RUSTFS_ENDPOINT` 存在且 boto3 可用 → S3；否则 Local。

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

## 7. 下载抽象

```python
@dataclass
class RemoteAsset:
    id: str; name: str; url: str = ""; expected_sha256: str | None = None; meta: dict

@dataclass
class DownloadResult:
    sha256: str; size: int; ext: str; width: int | None; height: int | None; meta: dict

class BaseDownloader(ABC):
    kind: str
    @abstractmethod
    def resolve(self, source) -> list[RemoteAsset]     # 枚举该源下资源
    @abstractmethod
    def download(self, remote, target: Path) -> DownloadResult   # 拉取 + 计算 sha256
```

注册表：`@register(kind)` 装饰器 + `get_downloader(kind)` 工厂。新增数据源 = 实现一个类 + 注册，零改动上层。

| kind | 实现 | resolve 输入 | 说明 |
| --- | --- | --- | --- |
| `local` | LocalImportDownloader | 目录扫描 | 本地导入，按文件名启发式分类 |
| `http` | HttpDownloader | params.urls 列表 | HTTP Range 断点续传 + 重试 + sha256 校验 |
| `huggingface` | HfDownloader | params.repo_id 等 | huggingface_hub（`hf` extra） |
| `pexels` / `coco` | 预留 | — | 后续按需实现 |

### sync 流程（状态机）

```
resolve → 逐个下载到 data/tmp/<id>.part
        → sha256 校验（不匹配则 failed + 记录 error，可重试）
        → backend.put_file（已存在同 sha256 → 跳过上传，去重）
        → 登记 assets（status=ready，version=1；已存在则跳过）
        → 清理临时文件
```

全程写入 `downloads` 表（status/attempts/error），Web UI 可见失败原因与重试入口。

## 8. CLI 命令设计（`llava-instruct asset`）

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
RUSTFS_ENDPOINT, RUSTFS_ACCESS_KEY, RUSTFS_SECRET_KEY, RUSTFS_BUCKET, LLAVA_DATA_DIR
```

## 9. Web 管理界面（FastAPI）

- 依赖进 `web` extra（fastapi + uvicorn）
- 端点：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET/POST | `/api/sources` | 数据源列表/新增 |
| PUT/DELETE | `/api/sources/{id}` | 修改/删除数据源 |
| POST | `/api/sources/{id}/sync` | 触发下载同步 |
| GET | `/api/assets` | 资产列表（`?tag=&type=&status=&source=`） |
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

### 依赖（optional-dependencies）

| extra | 包 | 用途 |
| --- | --- | --- |
| `dev` | pytest, pytest-cov, moto, fastapi, uvicorn, httpx | 测试（moto 模拟 S3） |
| `web` | fastapi, uvicorn | Web 管理界面 |
| `rustfs` | boto3 | S3/RustFS 后端 |
| `hf` | huggingface_hub | HF 下载器 |

### 测试用例

| 层 | 用例 |
| --- | --- |
| db | 八表 CRUD、sha256 唯一约束、版本历史、标签多对多、快照 |
| storage | Local 内容寻址/去重；S3 后端用 moto（put/get/exists/流式） |
| downloaders | local 导入分类；http 本地测试服务器（断点续传/重试/校验）；hf monkeypatch |
| store | 端到端 sync：resolve→下载→上传→登记→失败记录；去重跳过 |
| cli | init/source/import/sync/ls/tag/version/snapshot/export 全流程 |
| web | TestClient：sources CRUD、资产筛选、打标、快照、预览 |

### 集成冒烟（真实 RustFS）

- 仓库根 `docker-compose.yml` 启动 RustFS
- `scripts/rustfs_smoke.py`：真实走通 上传/下载/去重/预览（需 `RUSTFS_*` 环境变量）

## 12. 后续扩展

- 下载器：pexels（API key + 分页）、coco（zip + 标注）、webdav 等
- 存储后端：云 S3/MinIO（S3StorageBackend 已天然兼容）
- Web UI：资产批量导入、下载进度实时推送（SSE/WebSocket）、标签批量操作
- 快照 diff：两个快照间资产差异对比
