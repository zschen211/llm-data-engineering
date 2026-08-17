# llm-data-engineering

LLM 数据工程多子项目仓库。根目录只作为容器：**四层结构**——`frontend/`（纯静态
SPA，唯一面向人的客户端）、业务/平台服务（`asset-management`、`data-factory`、
`mm-rag`、`video-generation`）、`infra/`（中间件与运维，声明式）。每个 Python
子项目是完全独立的包（独立的 `pyproject.toml`、依赖与测试），可单独安装、
打包、运行；`infra/` 只有 compose 配置与脚本，`frontend/` 是 vite+react 工程。

```
frontend/（SPA：只经 HTTP 调 /api/*）
   │
   ├── asset-management/（平台服务：数字资产层 + 管理 API）
   ├── data-factory/    （业务服务：数据生产与评测闭环）
   ├── mm-rag/          （业务服务：多模态 RAG 助手，二期接入共享栈）
   └── video-generation/（业务服务：T2V 数据管线，二期接入共享栈）
   │
infra/（RustFS · Ray · Prometheus · Grafana · nginx 网关；契约见 infra/docs/contract.md）
```

| 模块 | 说明 |
| --- | --- |
| [frontend](frontend/) | 数据工程控制台（纯静态 SPA，调用 asset-management 与 data-factory 的 HTTP API） |
| [asset-management](asset-management/) | **平台服务**：数字资产管理（数据源 / 下载管线 / 内容寻址存储 / 标签 / 版本 / 快照 / 管理 API），对应《大模型数据工程》项目十四的资产层 |
| [data-factory](data-factory/) | 资产层之上的**数据生产与评测闭环**（数据策略 + 数据评测 + 管理 API） |
| [mm-rag](mm-rag/) | 多模态 RAG 企业财报助手 |
| [video-generation](video-generation/) | 视频生成数据流水线（T2V） |
| [infra](infra/) | 中间件与运维：RustFS / Ray / Prometheus / Grafana / node-exporter / nginx 网关 + 生命周期脚本 |

## 依赖方向

- `frontend` → `asset-management`、`data-factory`（仅 HTTP，永不 import Python 包）
- `data-factory` → `asset-management`（path 依赖，只经 `asset_management.assets.api` 只读消费资产；不共享 DB、不共享进程）
- 所有服务 → `infra`（仅经契约：端口 / 环境变量 / 指标名，不 import）

## 快速开始

```bash
# 1. 中间件（RustFS / Prometheus / Grafana / Ray 集群）
cd infra && cp .env.example .env && ./scripts/up.sh && ./scripts/ray-start.sh
export RAY_ADDRESS=127.0.0.1:6379

# 2. 平台与业务服务（各自目录）
cd asset-management && uv sync --extra dev && ./scripts/serve.sh --port 8000
cd data-factory     && uv sync --extra dev && ./scripts/serve.sh --port 8001

# 3. 前端
cd frontend && npm install && npm run dev   # http://localhost:5173

# 4. 可观测性冒烟
cd infra && ./scripts/obs_check.sh
```

Python 子项目各自独立：`uv sync --extra dev`、`uv run pytest`、`uv build`
均在对应目录内执行；根目录无共享环境，不要在根目录 `uv sync`。
