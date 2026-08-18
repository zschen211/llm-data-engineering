# llm-data-engineering

LLM 数据工程多子项目仓库。根目录只作为容器：**四层结构**——`frontend/`（纯静态
SPA，唯一面向人的客户端）、平台/业务服务（`asset`、`data_factory` 及其中嵌套的
`mm_rag`、`video_generation`）、`infra/`（中间件与运维，声明式）。每个 Python
子项目是完全独立的包（独立的 `pyproject.toml`、依赖与测试），可单独安装、
打包、运行；`infra/` 只有 compose 配置与脚本，`frontend/` 是 vite+react 工程。

```
frontend/（SPA：只经 HTTP 调 /api/*）
   │
   ├── asset/                       平台服务：数字资产层 + 管理 API
   └── data_factory/                业务服务：数据生产与评测闭环
       ├── mm_rag/                  业务服务：多模态 RAG 助手（二期接入共享栈）
       └── video_generation/        业务服务：T2V 数据管线（二期接入共享栈）
   │
infra/（RustFS · Ray · Prometheus · Grafana · nginx 网关；契约见 infra/docs/contract.md）
```

| 模块 | 说明 |
| --- | --- |
| [frontend](frontend/) | 数据工程控制台（纯静态 SPA，调用 asset 与 data_factory 的 HTTP API） |
| [asset](asset/) | **平台服务**：数字资产管理（数据源 / 下载管线 / 内容寻址存储 / 标签 / 版本 / 快照 / 管理 API），对应《大模型数据工程》项目十四的资产层 |
| [data_factory](data_factory/) | 资产层之上的**数据生产与评测闭环**（数据策略 + 数据评测 + 管理 API） |
| [data_factory/mm_rag](data_factory/mm_rag/) | 多模态 RAG 企业财报助手 |
| [data_factory/video_generation](data_factory/video_generation/) | 视频生成数据流水线（T2V） |
| [infra](infra/) | 中间件与运维：RustFS / Ray / Prometheus / Grafana / node-exporter / nginx 网关 + 生命周期脚本 |

## 依赖方向

- `frontend` → `asset`、`data_factory`（仅 HTTP，永不 import Python 包）
- `data_factory` → `asset`（path 依赖，只经 `asset_management.assets.api` 只读消费资产；不共享 DB、不共享进程）
- 所有服务 → `infra`（仅经契约：端口 / 环境变量 / 指标名，不 import）

## 快速开始

**一条命令拉起整个开发栈**（中间件 compose → Ray 集群 → 两个后端 → 前端）：

```bash
./scripts/dev.sh up          # 幂等：重复执行只补齐缺失的部分
# 或 make dev

./scripts/dev.sh status      # 各服务状态 + 健康探针
./scripts/dev.sh logs        # 聚合日志（.run/logs/），可跟服务名看单个
./scripts/dev.sh down        # 全部停止（应用 → Ray → compose）

make help                    # 查看全部目标
```

首次使用前各子项目需各自 `uv sync --extra dev`（asset / data_factory），
前端 `cd frontend && npm install`；服务日志与 PID 文件在 `.run/`（已 gitignore）。

逐层手动启动（等价于 `dev.sh up` 的内部步骤）：

```bash
# 1. 中间件（RustFS / Prometheus / Grafana / Ray 集群）
cd infra && cp .env.example .env && ./scripts/up.sh && ./scripts/ray-start.sh
export RAY_ADDRESS=127.0.0.1:26379

# 2. 平台与业务服务（各自目录）
cd asset        && ./scripts/serve.sh --port 8000
cd data_factory && ./scripts/serve.sh --port 8001

# 3. 前端
cd frontend && npm run dev   # http://localhost:5173

# 4. 可观测性冒烟
cd infra && ./scripts/obs_check.sh
```

Python 子项目各自独立：`uv sync --extra dev`、`uv run pytest`、`uv build`
均在对应目录内执行；根目录无共享环境，不要在根目录 `uv sync`。
