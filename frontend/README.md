# frontend —— 数据工程控制台（纯静态 SPA）

仓库四层结构的第一层（唯一面向人的客户端）：**只做页面展示，只经 HTTP 调用
后端 API，永不 import 任何 Python 包**。当前消费两个服务：

- `asset`（:8000，`/api/*` 资产域）
- `data-factory`（:8001，`/api/*` 工厂域）

## 技术栈

vite + react 18 + typescript（严格模式），无 UI 框架依赖；设计为「工程蓝图」
主题（IBM Plex Mono 承载全部数据/ID/状态，蓝色发丝线，顶部四层状态带为
唯一 signature 元素）。

## 本地开发

```bash
npm install
npm run dev          # http://localhost:5173
```

vite 按路径前缀代理到两个后端（见 `vite.config.ts`）：

| 前缀 | 目标 |
| --- | --- |
| `/api/{assets,sources,snapshots,sync,downloads,cluster,info,backup}` | `http://localhost:8000`（asset） |
| 其余 `/api/*` | `http://localhost:8001`（data-factory） |

两个后端需先启动：`asset/scripts/serve.sh --port 8000` 与
`data_factory/scripts/serve.sh --port 8001`（存储可 `--storage local` 离线跑；
Ray 共享集群经 `RAY_ADDRESS`，未配置时服务内嵌兜底）。

## 生产构建

```bash
npm run build        # tsc + vite build → dist/（纯静态）
```

静态产物与 `/api` 分流由 infra nginx 网关承载（`infra/nginx/nginx.conf`），
单源访问、无 CORS。

## 质量

```bash
npm run lint         # eslint（typescript-eslint + react-hooks）
npm run typecheck    # tsc -b（严格模式）
```

## 结构

```
src/
├── api.ts               # 单源 HTTP 客户端（/api + 错误映射）
├── widgets.tsx          # useFetch/usePoll、Table、Modal、Field、Status 等
├── LayeredStrip.tsx     # signature：四层状态带（INFRA/ASSET/FACTORY/CONSOLE）
├── App.tsx              # hash 路由 + 侧边导航 + 布局
└── pages/
    ├── assets.tsx       # 资产层页面：总览/数据源/资产/快照/同步/下载/集群
    └── factory.tsx      # 工厂页面：总览/能力域/策略/数据集/工作流/运行/阶段/
                         #   模型/评测集/评测/报告/血缘
```
