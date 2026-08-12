# llava-instruct

LLaVA 多模态**资产工厂**：统一资产管理（数据源 / HF 下载管线 / 内容寻址存储 / 标签 / 版本 / 快照 / Web 管理界面），对应《大模型数据工程》项目十四（P14）。

元数据权威存 SQLite（版本/标签/来源），多模态 blob 存可插拔 StorageBackend（本地目录 / **RustFS** S3 兼容对象存储，内容寻址去重）。

## 安装与运行

```bash
uv sync --extra dev               # 基础 + 测试（所有运行时依赖均为核心依赖，自动安装）
uv run pytest

# 启动 Web 管理界面（资产源 CRUD / 同步 / 标签 / 版本 / 快照 / 预览）
scripts/serve.sh [--port 8000] [--data-dir data]
```

## 数据资产层

资产层围绕两个关键元信息设计：**数据源**（资源的元信息 + 互联网下载源）与**存储位置**（下载后的存储后端与对象键）。详细设计见 [docs/spec/asset_layer_spec.md](docs/spec/asset_layer_spec.md)，文档索引见 [docs/](docs/README.md)。

### 启动 RustFS（可选，默认本地存储）

```bash
docker compose up -d          # S3 API:9000 / console:9001（rustfsadmin/rustfsadmin）
export RUSTFS_ENDPOINT=http://localhost:9000
export RUSTFS_ACCESS_KEY=rustfsadmin RUSTFS_SECRET_KEY=rustfsadmin
uv run python scripts/rustfs_smoke.py    # 真实后端集成冒烟测试
```

> 镜像默认走 DaoCloud 国内镜像（`docker.m.daocloud.io`）；如需官方源：
> `RUSTFS_IMAGE=rustfs/rustfs:latest docker compose up -d`。单机开发默认
> `RUSTFS_UNSAFE_BYPASS_DISK_CHECK=true`（共享磁盘绕过检查）。

不设置 `RUSTFS_ENDPOINT` 时自动使用本地磁盘后端（`data/blobs/`）。

## 可观测性

服务自带 Prometheus 埋点（`services/obs.py`：进程 / HTTP / Ray 集群与任务指标，`/metrics` 端点）+ 结构化事件流（`events.jsonl`，与日志同目录、gzip 轮转）。可视化用 docker compose 起的 Prometheus + Grafana：

```bash
docker compose up -d prometheus grafana node-exporter
# Grafana   : http://localhost:3000  （admin / $GRAFANA_ADMIN_PASSWORD，默认 admin）
# Prometheus: http://localhost:9090  （抓取 llava :8000/metrics + Ray dashboard :8265/metrics + 主机指标）
```

- 启动服务后 `scripts/obs_check.sh` 冒烟：校验 compose 配置、抓取目标全部 up、`/metrics` 可达。
- 指标名带 `llava_` 前缀，与 Ray dashboard 自身指标不冲突；dashboard 由 `grafana/provisioning/` 自动加载（进程 CPU/内存、HTTP QPS 与 P95、Ray 节点/任务/actor、任务吞吐与耗时、宿主机资源）。
- 事件流 JSON 每行一条：`{"ts", "project", "pid", "event", "level", "fields"}`，包含 `ray_cluster_started/stopped`、`sync_run_started/finished`、`ray_task_finished`，可用 `jq` 直接分析。
- Ray 自身日志固定在 session 目录（`/api/cluster/status` 返回 `logs_dir`），不再混入 uvicorn stderr。

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `LLAVA_OBS_DIR` | 同日志目录 | 事件流 `events.jsonl` 输出目录 |
| `LLAVA_OBS_INTERVAL` | `5` | 采样线程间隔（秒） |
| `LLAVA_OBS_MAX_BYTES` / `LLAVA_OBS_BACKUPS` | `50MB` / `5` | 事件流轮转大小与 gzip 备份数 |
| `LLAVA_RAY_METRICS_PORT` | `8080` | Ray metrics agent 的 Prometheus 端口（固定，供抓取） |

## 统一对外 API

其他模块（数据处理流水线、notebook、测试）**只通过 `llava_instruct.assets.api` 访问资产层**，不直接触碰 Database/StorageBackend 内部：

```python
from llava_instruct.assets.api import open_store
from pathlib import Path

with open_store() as store:                    # 后端由环境变量决定（RUSTFS_* 或本地）
    report = store.import_dir(Path("./images"), labels={"a.png": "chart_image"})
    assets = store.list_assets(tags=["task=chart"], status="ready")
    store.tag_asset(assets[0].id, "high", group="quality")
    snapshot = store.create_snapshot(name="v1")     # 集合级快照
    records = store.materialize(Path("./pool"))     # 物化到本地供下游流水线

# 指定后端（如特定 RustFS 实例）
from llava_instruct.assets.storage import S3StorageBackend
backend = S3StorageBackend("http://localhost:9000", "user", "secret", "my-bucket")
with open_store(backend=backend) as store:
    ...
```

`AssetStore` 公开方法一览：`add/list/update/delete_source`、`sync_source`、`import_dir`、`list/get/delete_asset`、`count_assets`、`tag/untag_asset`、`list_tags`、`asset_tags`、`bump_version`、`version_history`、`rollback`、`create/list_snapshot`、`snapshot_assets`、`materialize`、`export_pool`、`list_downloads`。

## 目录结构

```
llava-instruct/
├── pyproject.toml
├── docker-compose.yml       # RustFS 对象存储服务
├── docs/
│   ├── README.md         # 文档索引（spec / background / manual 三目录用途）
│   ├── spec/             # 系统设计文件
│   ├── background/       # 项目背景与需求说明
│   └── manual/           # 操作手册
├── scripts/rustfs_smoke.py  # RustFS 集成冒烟测试
├── src/llava_instruct/
│   ├── schema.py            # 数据契约（ASSET_TYPES 等常量、JSONL 读写）—— 资产层公共底座
│   ├── log.py               # 统一日志 —— 资产层公共底座
│   └── assets/              # 数据资产层
│       ├── api.py           # 对外 API：AssetStore 门面 + open_store 工厂
│       ├── classify.py      # 资产分类：classify_image（启发式）+ balance_assets（均衡）
│       ├── meta/            # 元数据管理：SQLite 存取（db.py 11 表）+ dataclass 模型（models.py）
│       ├── storage/         # 存储后端：base 抽象 + local（本地目录）/ s3（RustFS）实现
│       ├── routes/          # FastAPI 管理界面：按资源拆分（info/sources/sync/assets/downloads/snapshots）
│       ├── services/        # 业务服务层：sources/sync/assets/tags/versions/snapshots/materialize/maintenance + downloaders（下载管线）
│       └── static/webui.html  # Web 界面单文件前端（内联 css/js）
└── tests/
```

> 每个代码目录（`src/llava_instruct/`、`assets/`、`services/`、`tests/`）内都有 README，说明各文件职责与依赖关系。
