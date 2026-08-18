# 基础设施契约（Infra Contract）

本仓库四层结构的中间件契约：**infra（声明式运维层）** 与各业务/平台服务之间的
对接点全部集中在这里 —— 端口、环境变量、指标名、API 路径、数据目录。服务端
代码不 import infra，infra 也不 import 服务代码；两端都按本契约实现。

## 分层总览

```
frontend/          纯静态 SPA，只经 HTTP 调后端，不 import 任何 Python 包
asset   平台服务：数字资产层 + 管理 API（原 llava-instruct）
data-factory       业务服务：数据生产与评测闭环（FastAPI + CLI）
mm-rag / video-generation   业务服务（二期接入）
infra/             中间件 + 运维（docker compose / 脚本 / 配置），声明式
```

## 端口契约

| 端口 | 服务 | 说明 |
| --- | --- | --- |
| 9000 | RustFS S3 API | 对象存储（所有服务的 blob/产物） |
| 9001 | RustFS Console | 管理台 |
| 9090 | Prometheus | 抓取全部 `/metrics` 目标 |
| 3000 | Grafana | dashboard 由 provisioning 自动加载 |
| 9100 | node-exporter | 宿主机指标 |
| 9256 | process-exporter | 主机进程资源（按进程名分组的 CPU/内存/线程/FD） |
| 26379 | Ray GCS | 集群连接地址（`ray://` 或 `ip:26379`，默认被系统 redis 占用时用 26379） |
| 8265 | Ray Dashboard | 集群状态/任务/日志 |
| 8080 | Ray metrics agent | Prometheus 抓取（`ASSET_RAY_METRICS_PORT` 固定） |
| 8000 | asset HTTP | FastAPI 管理 API |
| 8001 | data-factory HTTP | FastAPI 管理 API |

## 环境变量契约

### infra 持有（中间件本身，服务只读）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `RUSTFS_ENDPOINT` | `http://localhost:9000` | 对象存储地址 |
| `RUSTFS_ACCESS_KEY` | `rustfsadmin` | |
| `RUSTFS_SECRET_KEY` | `rustfsadmin` | |
| `RUSTFS_BUCKET` | 服务各自持有 | 见下 |
| `RAY_ADDRESS` | 必填（无兜底） | 独立 Ray 集群地址，`ray start` 产出；服务未配置即报错 |
| `RAY_GCS_PORT` | `26379` | Ray head GCS 端口（避开系统 redis 的 6379 与 worker 端口段） |
| `RAY_NUM_CPUS` | `4` | ray-start.sh 集群 CPU 数（Ray 按 CPU 预启动同等数量空闲 worker） |
| `RAY_OBJECT_STORE_MEMORY` | `2147483648` (2GB) | ray-start.sh 对象存储共享内存（每个 worker 会 mmap 它） |
| `GRAFANA_ADMIN_PASSWORD` | `admin` | compose 注入 |

### asset（前缀 `ASSET_`）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `ASSET_DATA_DIR` | `data` | SQLite/缓存/临时区根目录 |
| `ASSET_STORAGE_BACKEND` | `auto` | `auto\|local\|rustfs`（rustfs 缺配置直接报错） |
| `ASSET_LOG_DIR` / `ASSET_LOG_LEVEL` | 同数据目录 / INFO | 日志持久化 |
| `ASSET_LOG_MAX_BYTES` / `ASSET_LOG_BACKUPS` | 50MB / 5 | 日志轮转 |
| `ASSET_OBS_DIR` / `ASSET_OBS_INTERVAL` | 同日志目录 / 5s | 事件流 |
| `ASSET_OBS_MAX_BYTES` / `ASSET_OBS_BACKUPS` | 50MB / 5 | 事件流轮转 |
| `ASSET_RAY_NUM_CPUS` | 全部核心 | 仅显式 `address="local"` 本地集群使用（测试） |
| `ASSET_RAY_METRICS_PORT` | 8080 | Ray metrics agent 固定端口 |

RustFS bucket 默认：**`asset-assets`**。

### data-factory（前缀 `DFAC_`）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `DFAC_DATA_DIR` | `data` | 工厂根目录（SQLite/tmp） |
| `DFAC_MODELS_DIR` | `data/models` | 本地 checkpoint 发现根 |
| `DFAC_STORAGE_BACKEND` | `auto` | `auto\|local\|s3\|rustfs` |

RustFS bucket 默认：**`dfac-datasets`**。Ray：共享 `RAY_ADDRESS`（无独立
覆盖变量）。

## 指标契约

- 所有服务自带 Prometheus 埋点，指标一律带 **`asset_`** 前缀（进程/HTTP/Ray/
  sync 系列），避免与 Ray metrics agent 的 `ray_*` 冲突。
- Prometheus scrape 目标（`infra/prometheus/prometheus.yml`）：
  `asset:8000`、`data-factory:8001`、`ray-metrics:8080`、
  `node-exporter:9100`、`process-exporter:9256`（进程名分组配置见
  `infra/process-exporter/process-exporter.yml`，指标 `namedprocess_namegroup_*`）。
- 本地 dev：Ray 集群跑在宿主机 netns，Prometheus 容器必须 host network。

## API 路径契约

- asset 服务自身挂载 `/api/*`：`/api/info` `/api/sources`
  `/api/assets` `/api/asset-datasets` `/api/snapshots` `/api/sync`
  `/api/downloads` `/api/cluster` `/api/backup`，另有 `/metrics`。
  `/api/asset-datasets` 是按数据源聚合的数据集列表（资产层「数据集」）；
  路径避开 `/api/datasets`（data-factory 的 run 输入数据集）。
- data-factory 服务自身挂载 `/api/*`：`/api/capabilities` `/api/strategies`
  `/api/datasets` `/api/workflows` `/api/runs` `/api/stages` `/api/models`
  `/api/eval-sets` `/api/eval-runs` `/api/reports` `/api/factory-info`，
  另有 `/metrics`。
- 前端单源访问：dev 用 vite proxy 按路径前缀分流；生产由 infra nginx 网关
  分流（`/api/{sources,assets,asset-datasets,snapshots,sync,downloads,cluster,info,backup}`
  → asset:8000，其余 `/api/*` → data-factory:8001）。

## Ray 集群

- 独立集群：`infra/scripts/ray-start.sh`（head：GCS `$RAY_GCS_PORT` 默认 26379 /
  dashboard 8265 / metrics `$ASSET_RAY_METRICS_PORT` 默认 8080 / CPU
  `$RAY_NUM_CPUS` 默认 4 / 对象存储 `$RAY_OBJECT_STORE_MEMORY` 默认 2GB）；
  GPU worker 二期接入时 `ray start --address=127.0.0.1:26379 --num-gpus=N`。
  GCS 端口默认避开 6379（本机常被系统 redis 占用）与 Ray worker 端口段
  10002–19999。Ray 按 CPU 数预启动空闲 worker（每个都会 mmap 对象存储
  共享内存），因此 CPU 数即空闲 worker 数，应保持与业务并发匹配。
- 服务连接：读 `RAY_ADDRESS`（必填）；**未配置时直接报错，无内嵌兜底**
  （一进程一集群；`scripts/dev.sh` 对全部子命令统一 export）。
- 测试约定：pytest 用 session 级私有本地集群
  （`ray.init(address="local", num_cpus=...)`），绝不连接共享集群，互不干扰。
- `RAY_ENABLE_UV_RUN_RUNTIME_ENV=0` 由两个包的 `__init__.py` 强制设置：
  uv-run 的 runtime-env 会把 path 依赖整个打包进 worker，OOM 且无法重建
  （两个包都有此 hack，属已知必要项）。

## 数据目录与备份

- SQLite 是各服务进程内嵌文件（非中间件服务）：`ASSET_DATA_DIR/assets.db`、
  `DFAC_DATA_DIR/datafactory.db`。infra 只负责**目录约定 + 备份**。
- `infra/scripts/backup.sh`：用 `sqlite3 .backup`（WAL 一致快照）备份两个
  db 到 `infra/backups/<ts>/`，并附各服务 tmp 清理前的最小保留。
- `infra/scripts/clean.sh`：清理服务的 `tmp/` 与 Ray 旧 session（需显式确认）。
