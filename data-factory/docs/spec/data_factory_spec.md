# 数据工厂系统规格说明（Data Factory Spec）

> 版本：0.1（设计定稿）· 状态：待实现 · 所属：data-factory 子项目（新建）

## 1. 概述与目标

data-factory 是 llava-instruct 资产层之上的**数据生产与评测闭环**，包含两个子系统：

- **数据策略**：通过定制化数据管线，生产强化模型特定能力的训练数据（例如针对「图片中事实类信息的问答」构建足量、多样的 QA 对，作为指令微调训练集）。
- **数据评测**：纳管被测模型与评测集，对微调产物评分并产出可复查的分析报告，完成 badcase 归因，反推训练数据缺失的能力域并驱动新一轮数据生产。

核心闭环（数据飞轮）：

```
资产池(快照+标签) ──► 数据策略管线(能力域/工作流/血缘) ──► 训练数据版本
      ▲                                                    │
      │                                                    ▼
 归因反推新策略 ◄── badcase 报告 ◄── 数据评测(评测集×模型×评分) ◄── 微调产物(平台外,注册进平台)
```

设计沿用资产层的成熟模式：SQLite 元数据权威 + RustFS 对象存储 + 内容寻址 + Ray Data 执行 + 统一 API 门面。

## 2. 总体架构

```
┌──────────────────────────────────────────────────────────────────┐
│                        data-factory（独立子项目）                  │
│                                                                    │
│  数据策略侧                                      数据评测侧         │
│  ├─ 能力域注册                                    ├─ 模型注册表     │
│  ├─ 数据集定义(快照+标签/导入/派生)                 │   local / vllm / │
│  ├─ 阶段注册表(transform/qc_rule/qc_llm/sink)     │   api 三种后端   │
│  ├─ 工作流(DAG 建模, v1 线性执行)                  ├─ 评测集管理      │
│  ├─ Ray Data 执行器                               ├─ 评分器(规则/LLM)│
│  └─ 产物版本与血缘                                └─ 报告与归因      │
└──────────────┬────────────────────────────────────────┬───────────┘
               │ 只经 llava_instruct.assets.api 消费       │ 同栈复用
               ▼                                          ▼
   llava-instruct 资产层（blob/快照/标签/物化）        RustFS + SQLite
```

| 层 | 职责 | 权威性 |
| --- | --- | --- |
| SQLite（`data/datafactory.db`） | 能力域/策略/工作流/run/产物血缘/模型/评测集/报告索引 | 血缘、版本、评测结果的**唯一权威** |
| RustFS（S3 兼容，bucket `llava-datasets`） | 文本类产物（JSONL/parquet）、版本 manifest、评测报告 | 产物内容与对象键的**唯一权威** |
| Ray Data | 策略管线执行、评测推理调度 | 执行引擎 |
| 资产层（llava-instruct） | 素材 blob、快照、标签、物化 | 输入数据的唯一权威（只读消费） |

与资产层**不共享 DB、不共享进程**，仅通过 `llava_instruct.assets.api` 消费资产与快照，保持子项目独立可构建。

## 3. 关键决策记录（ADR）

| 编号 | 决策 | 理由 |
| --- | --- | --- |
| D1 | 新建独立子项目 `data-factory`，依赖 llava-instruct（path 依赖） | 仓库独立子项目约定；资产层 API 是唯一稳定入口 |
| D2 | 输入数据集 = 资产层快照引用 + 标签组合过滤，运行即固化；另支持外部导入与派生（上游 dataset 版本） | 可复现 + 支持策略产物级联迭代 |
| D3 | 工作流按 DAG 建模与校验，v1 执行器只支持链式阶段 | 分支/合并并行需自定义调度，v1 不做 |
| D4 | 执行引擎 = Ray Data（复用资产层 sync 模式） | 分片/重试/背压/进度开箱即用；单阶段可独立调试 |
| D5 | 产物与血缘：RustFS + SQLite，内容寻址 + run 路径寻址双轨 | 与资产层同栈同 API |
| D6 | QC 双形态：规则 QC（核心依赖，零 GPU）+ LLM-as-judge QC（gpu extra） | 格式类检查用规则，内容质量用 LLM 判断 |
| D7 | 微调在平台外完成，产物权重登记进模型注册表 | 训练框架不是平台职责 |
| D8 | 模型注册表三种后端统一注册（local/vllm/api）+ 统一推理适配器 + 服务发现 | 本地权重与外部 API 一视同仁可评测 |
| D9 | 打分：规则打分 + 可选 LLM-judge，打分标准随评测集/题目定义 | 零 GPU 路径可全量测试 |
| D10 | 评测集支持导入与构建；报告 = 结构化 JSON + Markdown/HTML，可复查归因 | badcase 明细含血缘链，支撑能力域反推 |
| D11 | **v1 不引入 Iceberg 等开放表格格式**（取舍分析见 §4） | 见 §4 |

## 4. 开放表格格式（Iceberg）取舍分析

评估对象：是否用 Iceberg 作为产物/数据集的持久化格式。

**Iceberg 提供的能力**：ACID 更新、快照隔离与 time travel、增量读取、schema 演进、文件级提交血缘。

**本系统的数据形态**：

- 策略产物是「每次运行追加一个**不可变版本**」的 append-only 语义，读多写少，无跨 run 原地更新需求；
- 规模为十万～百万行、GB 级（单机/小集群，非 PB 级数仓场景）；
- 语义血缘（哪个策略/阶段/run 产生了哪些行、输入快照是什么）是**应用层概念**，Iceberg 快照只给出文件/提交级血缘，无法替代 SQLite 血缘表；
- 引入成本：catalog 服务（Hive/REST/Glue）与 pyiceberg 依赖、表维护（compaction/expire snapshots）、与 RustFS/本地双栈适配、Ray 集成（`read_iceberg` 需要 catalog 配置）。

**结论**：v1 采用「内容寻址不可变文件 + 版本 manifest + SQLite 血缘」，与资产层同一套成熟模式；`dataset_versions.manifest` 就是轻量表格快照，满足版本/血缘/复现三个目标。数据集抽象层（§7.4）保持格式无关，未来出现并发更新或跨 run 增量读需求时，可将产物层平移至 Iceberg（Ray Data 已支持 `read_iceberg`/`write_iceberg`，需 pyiceberg + catalog），血缘表结构无需变动。

## 5. 存储选型

### 5.1 元数据：SQLite（`data/datafactory.db`）

与资产层同思路：单文件、零运维、事务保证 run/血缘/评测记录一致性，表结构按 PostgreSQL 兼容写法。

### 5.2 产物：RustFS（bucket `llava-datasets`）

```
llava-datasets/
├── datasets/<dataset_id>/v<version>/manifest.json   # 版本清单（不可变）
├── artifacts/<run_id>/<node_id>/<file>              # 每阶段中间产物（run 路径寻址，可重跑覆盖）
└── evals/<eval_set_id>/<report_id>.json / .md       # 评测报告存档
```

双轨寻址：

| 产物 | 寻址 | 语义 |
| --- | --- | --- |
| 中间产物（intermediate） | `artifacts/<run_id>/<node_id>/` 路径式 | 阶段调试、断点续跑、重试隔离，可覆盖 |
| 结果产物（result / 训练集） | 内容寻址（sha256）+ `datasets/<id>/v<N>/` 版本式 | 不可变版本，消费方引用 manifest |

### 5.3 版本化策略

- 数据集版本 = manifest（文件清单 + 每文件 sha256/size/row_count）+ 血缘链（产生它的 run/阶段）；
- 同一 strategy 重跑产出新版本，旧版本保留可查可复现（与资产层「内容不变不新建、变更追加版本」语义对齐）；
- 版本号 = 该 dataset 上成功产出次数递增（v1、v2 …）。

## 6. 数据库 Schema（SQLite）

```sql
capability_domains (
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
  description TEXT, parent_id TEXT REFERENCES capability_domains(id),
  created_at TEXT
)

strategies (
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
  capability_domain_id TEXT REFERENCES capability_domains(id),
  description TEXT, enabled INTEGER DEFAULT 1,
  created_at TEXT, updated_at TEXT
)

datasets (
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
  source_type TEXT NOT NULL,           -- snapshot / import / derived
  snapshot_id TEXT,                    -- snapshot: 资产层快照 id
  tag_filters TEXT DEFAULT '[]',       -- 标签组合过滤（JSON: [{"group","name"}]）
  import_manifest TEXT,                -- import: 外部清单对象键
  derived_from TEXT,                   -- derived: "dataset_id@version"
  created_at TEXT
)

stages (                               -- 阶段类型注册表（可插拔）
  name TEXT PRIMARY KEY,
  module TEXT NOT NULL,                -- 静态导入路径（如 data_factory.strategies.stages.qc_rule）
  kind TEXT NOT NULL,                  -- transform / qc_rule / qc_llm / sink
  description TEXT, config_schema TEXT DEFAULT '{}'
)

workflows (
  id TEXT PRIMARY KEY, name TEXT NOT NULL,
  strategy_id TEXT REFERENCES strategies(id),
  description TEXT, enabled INTEGER DEFAULT 1,
  created_at TEXT, updated_at TEXT
)

workflow_nodes (                       -- 阶段实例（同一 stage 可多次实例化）
  id TEXT PRIMARY KEY,
  workflow_id TEXT REFERENCES workflows(id),
  stage_name TEXT REFERENCES stages(name),
  node_label TEXT, position INTEGER DEFAULT 0,
  config TEXT DEFAULT '{}'
)

workflow_edges (                       -- DAG 边（v1 只允许链式，仍建表并校验无环）
  workflow_id TEXT, from_node TEXT, to_node TEXT,
  PRIMARY KEY (workflow_id, from_node, to_node)
)

runs (
  id TEXT PRIMARY KEY,
  workflow_id TEXT REFERENCES workflows(id),
  input_dataset_id TEXT REFERENCES datasets(id),
  input_dataset_version INTEGER,
  status TEXT DEFAULT 'pending',       -- pending/running/succeeded/failed
  params TEXT DEFAULT '{}',            -- 运行级参数覆盖
  error TEXT, started_at TEXT, finished_at TEXT,
  stats TEXT DEFAULT '{}'
)

run_stages (
  run_id TEXT REFERENCES runs(id), node_id TEXT REFERENCES workflow_nodes(id),
  status TEXT,                         -- pending/running/succeeded/failed/skipped
  rows_in INTEGER DEFAULT 0, rows_out INTEGER DEFAULT 0,
  failed_rows INTEGER DEFAULT 0, attempts INTEGER DEFAULT 0,
  started_at TEXT, finished_at TEXT,
  PRIMARY KEY (run_id, node_id)
)

artifacts (                            -- 产物血缘
  id TEXT PRIMARY KEY,
  run_id TEXT REFERENCES runs(id), node_id TEXT REFERENCES workflow_nodes(id),
  kind TEXT NOT NULL,                  -- intermediate / result
  object_key TEXT NOT NULL, sha256 TEXT,
  size INTEGER, row_count INTEGER,
  dataset_version_id TEXT,             -- 若该产物成为某数据集版本
  created_at TEXT
)

dataset_versions (
  id TEXT PRIMARY KEY,
  dataset_id TEXT REFERENCES datasets(id), version INTEGER NOT NULL,
  artifact_id TEXT REFERENCES artifacts(id),
  manifest_key TEXT NOT NULL, row_count INTEGER,
  created_at TEXT, UNIQUE (dataset_id, version)
)

models (
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
  backend TEXT NOT NULL,               -- local / vllm / api
  model_id TEXT,                       -- local: HF id；vllm/api: 服务端模型名
  weights_dir TEXT,                    -- local: 权重目录（服务发现产物）
  base_url TEXT, api_key_env TEXT,     -- vllm/api: 端点 + 密钥环境变量名（不落明文）
  status TEXT DEFAULT 'pending',       -- pending/ready/failed
  last_check_at TEXT, last_error TEXT,
  params TEXT DEFAULT '{}',
  created_at TEXT, updated_at TEXT
)

eval_sets (
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
  capability_domain_id TEXT REFERENCES capability_domains(id),
  source TEXT NOT NULL,                -- import / built
  rubric TEXT DEFAULT '{}',            -- 集级默认打分标准（JSON）
  item_count INTEGER DEFAULT 0, created_at TEXT
)

eval_items (
  id TEXT PRIMARY KEY,
  eval_set_id TEXT REFERENCES eval_sets(id),
  seq INTEGER NOT NULL,
  question TEXT NOT NULL,              -- JSON：文本 + 图片引用（asset_id / object_key）
  expected TEXT, rubric TEXT,          -- 题级打分标准覆盖（可空）
  category TEXT,                       -- 题目级能力归类（归因维度）
  UNIQUE (eval_set_id, seq)
)

eval_runs (
  id TEXT PRIMARY KEY,
  eval_set_id TEXT REFERENCES eval_sets(id),
  model_id TEXT REFERENCES models(id),
  status TEXT DEFAULT 'running',       -- running/succeeded/failed/partial
  started_at TEXT, finished_at TEXT,
  aggregate TEXT DEFAULT '{}', error TEXT, created_at TEXT
)

eval_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  eval_run_id TEXT REFERENCES eval_runs(id),
  item_id TEXT REFERENCES eval_items(id),
  model_output TEXT, score TEXT,       -- {"score","verdict","reason"}
  latency_ms INTEGER, error TEXT
)

reports (
  id TEXT PRIMARY KEY,
  eval_run_id TEXT REFERENCES eval_runs(id),
  capability_domain_id TEXT,
  aggregate TEXT DEFAULT '{}',         -- 聚合指标（JSON）
  badcases TEXT DEFAULT '[]',          -- badcase 明细（含血缘链）
  attribution TEXT DEFAULT '{}',       -- 归因建议（JSON）
  json_key TEXT, md_key TEXT,          -- 报告对象键（存档）
  created_at TEXT
)
```

## 7. 数据策略设计

### 7.1 能力域（capability domain）

- 能力域标记某一数据管线针对性提升的能力（如「对齐样本」「图表事实问答」「OCR」）；
- 支持层级（`parent_id`）；策略、评测集、报告都归属能力域 → 构成归因闭环（评测报告按能力域聚合 → 缺口反推哪个域需要新策略）。

### 7.2 数据集定义（输入）

| source_type | 语义 |
| --- | --- |
| `snapshot` | 引用资产层快照 id + 标签组合过滤；**运行即固化**（run 记录输入快照与过滤条件），不受资产层后续标签漂移影响 |
| `import` | 外部 JSONL 清单（资产 id / 对象键 / 自带文本行） |
| `derived` | 引用上游 dataset 版本（`dataset_id@version`），支持策略产物级联迭代（上一轮产物 + 补充数据 → 新一轮策略） |

### 7.3 工作流与阶段注册表

**工作流** = 归属于某策略的一组阶段实例（DAG 建模）。v1 执行器只支持链式（`position` 有序），DAG 校验器负责无环/类型检查，分支合并并行留待后续。

**阶段类型**：

| kind | 职责 | 示例（内置） |
| --- | --- | --- |
| `transform` | 通用行变换：合成、过滤、增强 | `qa_synth`（LLM 合成 QA，gpu/API）、`filter` |
| `qc_rule` | 程序化质检（零 GPU） | `schema_check`、`dedup`、`field_range` |
| `qc_llm` | LLM-as-judge 质检（引用注册表中 judge 模型） | `llm_quality_gate` |
| `sink` | 产物落盘：写 dataset 版本 + 血缘 | `publish` |

**阶段接口**（每个阶段独立开发与调试）：

```python
# data_factory/strategies/stages/base.py
class Stage:
    kind: ClassVar[str]

    def __init__(self, config: dict): ...

    def transform(self, rows: "ray.data.Dataset") -> "ray.data.Dataset":
        # 行契约：dict（JSON 可序列化）；图片经 asset_id / object_key 引用，不复制 blob
        ...
```

- 阶段以 `stages` 表注册（name/module/kind/config_schema），模块静态导入（符合仓库「无动态导入」约定）；
- 同一 stage 类型可多次实例化（两个 QC 节点、不同参数）。

### 7.4 执行器（Ray Data，v1 线性链）

```
run = 物化输入数据集(清单) ─► node1(qc_rule) ─► node2(transform) ─► node3(qc_llm) ─► node4(sink)
        │                    每节点一个 Ray Data 变换，driver 侧聚合进度/失败/耗时
        └── 阶段间 materialize 边界（复用资产层经验：独立计时 + 重试隔离）
```

- 中间产物按 `artifacts/<run_id>/<node_id>/` 落 RustFS；`sink` 输出内容寻址 result 产物并创建 `dataset_versions`；
- **容错**：Ray `max_task_retries=2` 行级重试 + 应用层错误捕获进 outcome 行（只影响该行）；run 可断点续跑（按 `run_stages`/`artifacts` 状态跳过已完成节点）；
- **暂停/恢复**：沿用资产层 pull-based 语义（driver 停拉即全管线背压停驻）；
- **单阶段调试**：`dfac stage run <stage_name> --input sample.jsonl --config ...` 本地小样本单独执行，不依赖工作流上下文；每阶段可独立单测。

### 7.5 产物版本与血缘

血缘模型：

```
capability_domain ◄── strategy ──< workflow ──< run ──► run_stages ──► artifacts ──► dataset_versions
                                          │                                   │
                                          └──► 输入 dataset(快照+标签/导入/派生) ┘
```

- 每个 artifact 记录 `run_id + node_id + object_key + sha256`，语义上回答「这个文件是谁、用什么策略、在哪个阶段、哪次运行产生的」；
- 血缘查询维度：按 run / dataset 版本 / 策略 / 能力域 追溯上下游；
- 数据版本不可变，manifest 含文件级 sha256，训练集消费方（微调脚本）直接引用 manifest。

### 7.6 QC 设计（双形态）

- **规则 QC（核心依赖，零 GPU）**：内置 `schema_check`（字段类型/必填/枚举）、`dedup`（行级内容去重）、`field_range`（数值/长度范围）、格式校验等；输出 rejected 行清单。
- **LLM-as-judge QC（gpu extra）**：`llm_quality_gate` 阶段，judge 模型从模型注册表引用（本地权重离线推理或 API），prompt 模板可配置，输出 verdict + reason + score，按阈值过滤；judge 打分结果随行保留（供后续追溯）。
- **人工复核接口（预留）**：`dfac qc review` 导出待复核行 JSONL + 标注回写（Web UI 人工审核为 v2）。

## 8. 数据评测设计

### 8.1 模型注册表与服务发现

统一注册三类可评测模型：

| backend | 配置 | 服务发现机制 | 推理方式 |
| --- | --- | --- | --- |
| `local` | `model_id`（HF）或 `weights_dir` | 扫描约定权重目录（`data/models/`，可配 `DFAC_MODELS_DIR`）下含 `config.json` + 权重文件的 checkpoint 子目录 → 自动登记 `pending`，校验后可置 `ready` | transformers 离线推理（gpu extra） |
| `vllm` | `base_url` + `model_name` | 心跳探测 `GET {base_url}/v1/models` 确认可达 | HTTP（OpenAI 兼容协议） |
| `api` | `base_url` + `api_key_env` + `model_name` | 心跳探测 `/v1/models` 或 `/health` | HTTP（OpenAI 兼容协议） |

- 密钥只存**环境变量名引用**（`api_key_env`），不落库明文；
- 状态机 `pending → ready / failed`，`dfac model check` 触发周期心跳刷新 `last_check_at`；
- 统一推理适配器 `ModelClient.generate(question, images) -> str`：图片经资产层（`asset_id` → RustFS 拉取）或直接对象键读取；三后端同一接口，评测执行器与后端无关。

### 8.2 评测集管理

- **导入**：JSONL（`question` 支持文本 + 图片引用，`expected` 真值，可选 `rubric`/`category`）；
- **构建**：从策略产物 / 资产池挑选样本，人工或模板生成题目 JSONL 后导入（v1 不做自动题目生成器）；
- 打分标准（rubric）随集级定义、题级可覆盖：

```json
{
  "scorer": "exact | fuzzy | numeric | llm_judge",
  "params": {"case_sensitive": false, "tolerance": 0.01, "fuzzy": "difflib"},
  "judge_model_id": "judge-1"
}
```

- 题目可归属 `category`（细粒度能力归类），评测集可归属能力域。

### 8.3 评测执行与报告

**eval_run**：模型 × 评测集 → 逐题推理（API 后端并发请求；local 后端 Ray 并行批量）→ 按 rubric 评分 → `eval_results` 明细入库 → 聚合 → 报告。

**聚合维度**：整体得分；按能力域 / category / 题型（单选/自由问答）分维度得分。

**报告（可复查、可归因）**：

- 结构化 JSON：aggregate + 每题明细（输入/输出/得分/理由/耗时）+ badcase 列表；
- Markdown/HTML 详情：逐题展示，badcase 附**血缘链**（题目 category → 能力域 → 关联策略的 run/产物版本），支撑「哪个能力缺口、缺在哪个策略产物上」的归因；
- 归因建议：badcase 按 category/能力域聚合为「缺口清单」，自动关联到尚未覆盖该域的策略，输出 `attribution`（供数据策略员发起新一轮生产）；
- 报告存档 RustFS（`evals/<eval_set_id>/<report_id>.json/.md`）+ SQLite 索引，可导出、可复查历史。

## 9. 依赖与 GPU 策略

```toml
# data-factory/pyproject.toml（设计草案）
[project]
name = "data-factory"
requires-python = ">=3.11"
dependencies = [
    "llava-instruct = {path = \"../llava-instruct\"}",  # 资产层 API（同仓 path 依赖）
    "ray[data,default]>=2.9",
    "fastapi>=0.115.0", "uvicorn>=0.30.0",
    "boto3>=1.34.0", "pyarrow>=16.0.0",
    "tqdm>=4.60.0", "prometheus-client>=0.21.0",
]

[project.optional-dependencies]
dev = ["pytest", "pytest-cov", "httpx", "bandit", "pylint", "radon", "ruff"]
gpu = ["torch", "transformers"]   # local 后端离线推理 + LLM-judge（第 3 个 gpu extra 例外，需批准）
```

- 规则打分、规则 QC、API/vllm 后端评测全部走核心依赖，**CPU 路径零 GPU、零 torch 可全量测试**；
- LLM 能力（local 推理、`qc_llm`、`llm_judge`）走 `gpu` extra——沿用 mm-rag/video-generation 的已批准例外模式，不扩展至其他代码；
- 静态导入、无动态 import、无运行时安装（仓库硬约束）。

## 10. CLI 命令设计（`dfac`）

```
dfac init [--data-dir DIR]
dfac capability add|list <name> [--parent ID]
dfac strategy add|list|show <...>
dfac dataset add|list <name> [--snapshot S --tag group=name ...] | --import manifest | --derived id@ver
dfac workflow define <strategy> --stages name[=cfg],... | validate <id> | show <id>
dfac run <workflow_id> [--params ...] | ls | show <id> | cancel <id>
dfac stage run <stage_name> --input sample.jsonl --config ...     # 单阶段独立调试
dfac lineage --run R | --dataset D@V | --strategy S               # 血缘追溯
dfac model register|list|scan|check|rm
dfac evalset import <file.jsonl> [--name N --domain D --rubric ...] | list | show
dfac eval run --eval-set E --model M [--concurrency C] | ls | show
dfac report show <report_id> | export <report_id> <out.md>
dfac serve [--port 8000]
```

环境变量：`DFAC_DATA_DIR`（默认 `data/`）、`DFAC_MODELS_DIR`（默认 `data/models/`）、`RUSTFS_ENDPOINT` 等（复用资产层后端解析）。

## 11. Web 管理界面（FastAPI）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET/POST | `/api/capabilities` | 能力域列表/新增 |
| GET/POST | `/api/strategies` | 策略列表/新增 |
| GET/POST | `/api/datasets` | 数据集定义/新增 |
| GET/POST | `/api/workflows` | 工作流（含 DAG 校验）/新增 |
| GET/POST | `/api/runs` | 触发/查看策略运行；`/api/runs/{id}/stages` 阶段明细 |
| GET | `/api/lineage?run=&dataset=&strategy=` | 血缘追溯 |
| GET/POST | `/api/models` | 模型注册表；`POST /api/models/scan` 触发权重目录扫描 |
| GET/POST | `/api/eval-sets` | 评测集；`/api/eval-sets/{id}/items` 题目明细 |
| POST/GET | `/api/eval-runs` | 发起/查询评测；`/api/eval-runs/{id}/results` 逐题结果 |
| GET | `/api/reports/{id}` | 报告详情（JSON/Markdown/HTML） |
| GET | `/` | 管理页面（原生 HTML+JS，风格同资产层） |

## 12. 统一对外 API（programmatic）

```python
from data_factory.api import open_factory

with open_factory() as factory:            # 环境变量决定后端（同 open_store 语义）
    factory.create_strategy("fact-qa", domain="chart_fact_qa", dataset=ds_id)
    run = factory.run_workflow(wf_id)      # -> RunReport（含各阶段统计与血缘）
    factory.register_model(name="qwen-vl-sft", backend="local", weights_dir=...)
    factory.scan_models()                  # 服务发现：扫描权重目录
    report = factory.run_eval(eval_set_id, model_id)   # -> EvalReport（含归因）
    factory.export_report(report.id, "report.md")
```

**内部模块（非公开）**：`meta/`（db/models）、`strategies/`（stages 注册表 + 执行器）、`eval/`（registry/scorers/runner）、`routes/` —— 仅 data-factory 自身与测试使用。

## 13. 实施计划（里程碑）

| 里程碑 | 内容 | 验收标准 |
| --- | --- | --- |
| **M1 骨架与元数据层** | 子项目初始化（pyproject/lint 配置/文档骨架）；SQLite schema（§6 全表）+ dataclass；能力域/策略/数据集/工作流 CRUD | 单测覆盖全部表 CRUD；`scripts/run_lint.sh` 四门全过 |
| **M2 阶段注册表与执行器** | Stage 接口与注册表（静态导入）；内置规则 QC 阶段；DAG 校验器；Ray Data 线性执行器；run/run_stages 落库；单阶段调试 CLI | 端到端跑通「快照数据集 → 规则 QC → sink」；失败行不拖垮整批；续跑跳过已完成节点 |
| **M3 产物版本与血缘** | artifact 内容寻址落 RustFS；dataset 版本 manifest；血缘查询 API；断点续跑 | 血缘追溯 run/dataset/策略 三视角正确；manifest 可复现物化 |
| **M4 模型注册表** | 三后端注册 + 权重目录扫描发现 + 心跳检查；统一推理适配器（先 API/vllm，local 推理留 M5） | 扫描/心跳/状态机单测；CPU 全链路可跑 |
| **M5 LLM 能力（gpu extra）** | transformers 本地离线推理；`qc_llm` 阶段；`llm_judge` 评分器；CPU 路径保持零 GPU 可测 | 三后端 generate 一致契约；LLM 功能在 gpu extra 下测试，核心依赖测试不依赖 torch |
| **M6 评测闭环** | 评测集导入/构建；eval run 执行（API 并发/local 并行）；规则+LLM 打分；报告（JSON+Markdown/HTML）；badcase 血缘归因与导出 | 端到端：评测 → 报告 → 归因建议 → 新建策略全链路示例 |
| **M7 Web UI 与可观测性** | FastAPI 管理界面；/metrics + 事件流（复用资产层 obs 模式） | TestClient 全端点；指标留档 |
| **M8 端到端验收** | 全链路示例脚本（资产池 → 策略产出 QA → 注册模型 → 评测 → 报告归因 → 反推新策略）+ README | 示例可一键复现；验收文档定稿 |

## 14. 测试计划

| 层 | 用例 |
| --- | --- |
| db | §6 全表 CRUD、级联/唯一约束、dataset 版本递增、workflow DAG 校验 |
| storage | artifact 内容寻址去重；manifest 物化；s3 用 moto |
| stages | 内置 QC 各阶段行为（schema/dedup/range）；stage 独立调试入口 |
| executor | Ray Data 线性链、行级失败隔离、节点重试、续跑跳过、暂停/恢复 |
| lineage | run/节点/artifact/dataset 版本血缘一致性；快照输入固化 |
| models | 三后端注册/发现（目录扫描、心跳 mock）/状态机；适配器契约 |
| eval | 评测集导入与题级覆盖；规则评分器全类型（exact/fuzzy/numeric）；LLM-judge（mock 模型）；并发执行 |
| report | 聚合维度正确；badcase 血缘链；JSON/Markdown 导出 |
| cli/web | `dfac` 全流程；TestClient 端点 |

## 15. 风险与后续扩展

**风险**：

- LLM-judge 稳定性（打分漂移）：prompt 模板配置化 + 抽样人工复核 + 同一评测集固定 judge 模型；
- gpu extra 体积：CPU 路径与 GPU 路径测试隔离（AGENTS.md 已批准模式）；
- 子项目间依赖漂移：llava-instruct 以固定版本/path 锁定，资产层 API 变化走版本协商。

**后续扩展**：完整 DAG 分支/合并并行；数据集产物层平移 Iceberg；评测集自动题目生成器；人工审核 UI（v2）；评测集差异对比（两模型/两版本模型结果 diff）。
