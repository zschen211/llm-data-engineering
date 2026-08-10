# llava-instruct

LLaVA 多模态指令数据工厂：把多模态资产（通用/文档/图表图像）加工成可训练、可质检、可封装的多模态监督数据资产。

对应《大模型数据工程》项目三：`资产池 -> 指令合成 -> 区域对齐 -> 多图交错 -> 质量审核 -> 训练封装 -> 报告与验证`。

## 安装与运行

```bash
uv sync --extra dev               # 基础 + 测试（所有运行时依赖均为核心依赖，自动安装）
uv run pytest

# 1. 从图片目录构建均衡资产池（按文件名 doc_*/chart_* 启发式分类，可用 --labels 指定）
uv run llava-instruct prepare-assets ./images --out assets.jsonl --labels labels.json

# 2. 基于资产 + 证据文件（captions/ocr/bbox/pairs jsonl）生成 LLaVA 格式样本
uv run llava-instruct generate assets.jsonl \
  --captions captions.jsonl --ocr ocr.jsonl --bbox bbox.jsonl --pairs pairs.jsonl \
  --out samples.jsonl

# 3. 质量检查：结构/语义/bbox 越界（结构一致性、语义规则、坐标 clamp）
uv run llava-instruct qa samples.jsonl --image-root ./images --report qa_report.md

# 4. bbox 反向渲染到原图，可视化核验 grounding 样本
uv run llava-instruct render samples_qa.jsonl --image-root ./images --out-dir render

# 5. train/val/smoke 切分 + manifest + 报告
uv run llava-instruct split samples_qa.jsonl --out-dir deliver
```

## 数据资产层（`asset` 命令族）

资产层围绕两个关键元信息设计：**数据源**（资源的元信息 + 互联网下载源）与**存储位置**（下载后的存储后端与对象键）。元数据存 SQLite（版本/标签/来源权威），多模态 blob 存 **RustFS**（S3 兼容对象存储，内容寻址去重）。详细设计见 [docs/spec/asset_layer_spec.md](docs/spec/asset_layer_spec.md)，文档索引见 [docs/](docs/README.md)。

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

### 常用命令

```bash
uv run llava-instruct asset init                                  # 查看/校验配置
uv run llava-instruct asset source add --name coco --kind huggingface \
  --params '{"repo_id": "org/ds", "process": "parquet"}'     # process=file|parquet
uv run llava-instruct asset source list
uv run llava-instruct asset sync <source_id>                      # 下载管线：Ray 并行任务+重试+转换+入库（幂等）
uv run llava-instruct asset import ./images --out assets.jsonl    # 本地导入（等效 prepare-assets）
uv run llava-instruct asset ls --tag task=chart --type chart_image
uv run llava-instruct asset tag add <asset_id> chart --group task
uv run llava-instruct asset version snapshot --name v1            # 集合级快照（可复现）
uv run llava-instruct asset materialize ./pool --tag task=chart   # 物化到本地供下游流水线
uv run llava-instruct asset serve --port 8000                     # Web 管理界面
```

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

## 样本 schema

每条样本包含：`id`、`image`（单图或多图列表）、`asset_type`（general/document/chart/interleaved_pair）、`task_type`（8 类任务）、`source_id`、`bbox`、`ocr_text`、`conversations`（LLaVA 对话格式）、`split`、`meta`（版本/生成方式/审核状态）。

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
│   ├── schema.py            # 样本契约、bbox 校验与 clamp
│   ├── assets/              # 数据资产层
│   │   ├── classify.py      # 旧资产池函数（扫描/分类/均衡，兼容保留）
│   │   ├── db.py            # SQLite 元数据（八表：sources/assets/versions/tags/downloads/snapshots）
│   │   ├── storage.py       # StorageBackend：LocalStorage / S3Storage(RustFS)
│   │   ├── registry.py      # 下载器注册表
│   │   ├── downloaders/     # download->process->persist 三段管线 + ray_sync（Ray 任务编排）
│   │   ├── store.py         # AssetStore 门面（sync 状态机/标签/版本/快照/物化）
│   │   └── web.py           # FastAPI 管理界面
│   ├── templates.py         # 受控任务模板与 LLaVA conversation 构建
│   ├── generator.py         # 监督构造：资产 + 证据 -> 样本
│   ├── qa.py                # 结构/语义/bbox 三类质检与低质量样本沉淀
│   ├── render.py            # bbox 反向渲染（Pillow）
│   ├── split.py             # train/val/smoke 切分、manifest、报告
│   └── cli.py               # 命令入口
└── tests/
```
