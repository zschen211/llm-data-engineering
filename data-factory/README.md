# data-factory

数据生产与评测闭环（数据飞轮）：在 asset-management 资产层之上，用**数据策略**
（能力域 → 数据集 → 工作流 → Ray Data 执行 → 产物版本与血缘）生产强化特定能力的
训练数据，再用**数据评测**（模型注册表 × 评测集 → 逐题评分 → 报告与 badcase 归因）
反推能力缺口、驱动新一轮数据生产。

详细设计见 [docs/spec/data_factory_spec.md](docs/spec/data_factory_spec.md)。

## 安装与运行

```bash
uv sync --extra dev      # 核心 + 测试（ray 等重依赖；gpu extra 可选，见下）
uv run pytest
```

与资产层的关系：**不共享 DB、不共享进程**，只通过 `asset_management.assets.api`
消费资产与快照（path 依赖，同仓 `../asset-management`）。

## 管理 API（Web）

FastAPI 管理接口（路由全部挂在 `/api/*`，契约见 [../infra/docs/contract.md](../infra/docs/contract.md)）：

```bash
scripts/serve.sh [--port 8001] [--data-dir data] [--storage rustfs|local]
# 或：uv run uvicorn data_factory.routes:default_app --factory --port 8001
```

| 资源 | 端点 |
| --- | --- |
| 概览 | `GET /api/factory-info` |
| 能力域 | `GET/POST /api/capabilities` |
| 策略 | `GET/POST /api/strategies` |
| 数据集 | `GET/POST /api/datasets` |
| 工作流 | `GET/POST /api/workflows`、`GET /api/workflows/{id}`、`POST /api/workflows/{id}/validate` |
| 运行 | `GET/POST /api/runs`、`GET /api/runs/{id}`、`POST /api/runs/{id}/run`、`POST /api/runs/{id}/cancel` |
| 阶段注册表 | `GET /api/stages` |
| 模型 | `GET/POST /api/models`、`POST /api/models/{id}/check`、`POST /api/models/scan`、`DELETE /api/models/{id}` |
| 评测集 | `GET/POST /api/eval-sets`（items 直接提交）、`GET /api/eval-sets/{id}` |
| 评测 run | `GET/POST /api/eval-runs`、`GET /api/eval-runs/{id}`、`POST /api/eval-runs/{id}/run` |
| 报告 | `GET /api/reports`、`GET /api/reports/{id}`、`GET /api/reports/{id}/payload` |
| 血缘 | `GET /api/lineage?run_id=\|dataset_id=\|strategy_id=` |
| 指标 | `GET /metrics`（`asset_` 前缀，供 infra Prometheus 抓取） |

Python API 与 CLI 等价（见下）。

## 最小端到端示例（全部 CPU、离线、确定性）

```bash
uv run python examples/minimal.py
```

跑通完整数据飞轮：

1. **数据策略**：能力域 `chart_fact_qa` → 策略 `fact-qa` → import 数据集（44 行
   含 3 条重复 + 1 条超长）→ 工作流 `schema_check → dedup → field_range → filter
   → publish`（Ray Data 链式执行）→ 产出不可变数据集版本 `v1`（40 行）+ 血缘 manifest；
2. **数据评测**：起一个进程内 OpenAI 兼容 mock 模型 → 注册为 `api` 后端 → 心跳置
   `ready` → 导入 10 题评测集 → 评测 run（8 过 2 错）→ 报告含聚合指标 + badcase
   血缘链 + 归因建议（`chart_fact` 缺口由 `fact-qa` 策略覆盖）。

## 使用方式

```python
from data_factory.api import open_factory

with open_factory() as factory:          # DFAC_DATA_DIR / DFAC_STORAGE_BACKEND 配置
    domain = factory.create_capability_domain("chart_fact_qa")
    strategy = factory.create_strategy("fact-qa", domain.id)
    ds = factory.create_dataset("qa", source_type="import",
                                import_manifest="rows.jsonl")
    wf = factory.define_workflow(strategy.id, "qc-chain",
                                 [("schema_check", None),
                                  ("dedup", None),
                                  ("publish", {"dataset_id": ds.id})])
    run = factory.run_workflow(factory.create_run(wf.id, ds.id).id)   # Ray Data 执行

    model = factory.register_model("qwen", backend="api", base_url=...)
    factory.check_model(model.id)                                     # 心跳 → ready
    es = factory.import_eval_set("chart-10", Path("eval.jsonl"))
    er = factory.run_eval(factory.create_eval_run(es.id, model.id).id)
    factory.export_report(factory.list_reports(er.id)[0].id, "report.md")
```

CLI 等价命令：`dfac capability|strategy|dataset|workflow|run|stage|lineage|
model|evalset|eval|report ...`（见 spec §10）。

## 模块一览

```
src/data_factory/
├── api.py               # DataFactory 门面 + open_factory（唯一稳定入口）
├── pipeline.py          # 策略/数据集/工作流/run 编排（PipelineService）
├── lineage.py           # 血缘查询：by_run / by_dataset / by_strategy
├── input.py             # 输入物化：snapshot（走资产层 API）/ import / derived
├── jsonl.py             # 行产物与 manifest 的存储读写
├── routes/              # FastAPI 管理 API：按资源拆分 + 指标中间件
├── meta/                # SQLite 元数据权威（db.py 17 张表 + models.py）
├── storage/             # 产物存储：local / S3（RustFS），双轨寻址
├── strategies/
│   ├── dag.py           # 工作流 DAG 校验（v1 链式）
│   ├── executor.py      # Ray Data 线性执行器（断点续跑/行级错误隔离）
│   └── stages/          # 阶段注册表：schema_check/dedup/field_range/filter/
│                        #   qc_llm（LLM-as-judge）/ publish（sink）
└── eval/
    ├── registry.py      # 模型注册表：local/vllm/api 三后端 + 目录扫描发现 + 心跳
    ├── models.py        # ModelClient 统一推理适配器（api/vllm 走 OpenAI 协议）
    ├── scorers.py       # 规则打分（exact/fuzzy/numeric）+ LLM-judge
    ├── runner.py        # 评测执行：模型 × 评测集 → 逐题评分
    ├── report.py        # 聚合 / badcase 血缘链 / 归因建议 / JSON+Markdown 导出
    └── service.py       # 评测集导入、评测 run、报告导出（EvalService）
```

## 存储布局（bucket `dfac-datasets`）

```
blobs/<sha256[:2]>/<sha256>.jsonl     # 内容寻址 result 产物（不可变）
artifacts/<run_id>/<node_id>/out.jsonl  # run 路径寻址中间产物（可重跑覆盖）
datasets/<dataset_id>/v<N>/manifest.json  # 版本 manifest（不可变）
evals/<eval_set_id>/<report_id>.json/.md   # 评测报告存档
```

- 元数据权威：`data/datafactory.db`（SQLite）；`DFAC_DATA_DIR` 可换目录。
- 存储后端：`DFAC_STORAGE_BACKEND=auto|local|s3`（同资产层 env 约定，默认 auto）。

## 依赖与 GPU 策略

- 核心依赖零 GPU：规则 QC、规则打分、api/vllm 后端评测、全部管线执行均可 CPU 全量跑。
- `gpu` extra（torch + transformers）只用于 **local 后端离线推理**（`eval/models.py`
  guard-import，AGENTS.md 已批准的第 3 个例外，模式同 mm-rag）——不扩展至其他代码。

## 代码质量

```bash
scripts/run_lint.sh      # ruff + radon（复杂度 ≤ B）+ pylint + bandit 四门全过
```
