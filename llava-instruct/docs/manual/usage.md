# 操作手册（Manual）

llava-instruct 数据工厂的日常操作指南：环境准备、CLI 命令、Web 管理界面、统一 API 与存储后端运维。

## 1. 环境准备

```bash
cd llava-instruct
uv sync --extra dev                          # 基础 + 测试
uv run pytest                                # 跑测试
```

> 所有运行时依赖（ray/huggingface_hub/pyarrow/boto3/fastapi 等）都在 `[project.dependencies]` 中，随 `uv sync` 自动安装；`dev` extra 仅包含测试工具（pytest/moto/httpx）。

### 存储后端选择

| 场景 | 设置 | 效果 |
| --- | --- | --- |
| RustFS 对象存储（默认） | `LLAVA_STORAGE_BACKEND=rustfs` + `RUSTFS_ENDPOINT` + `RUSTFS_ACCESS_KEY` + `RUSTFS_SECRET_KEY`（可选 `RUSTFS_BUCKET`，默认 `llava-assets`） | 图片 blob 存 RustFS，元数据仍存本地 SQLite；配置缺失直接报错 |
| 本地开发 | `LLAVA_STORAGE_BACKEND=local`（或 `scripts/serve.sh --storage local`） | 本地内容寻址目录 `data/blobs/` + `data/assets.db` |
| 自动（兼容旧行为） | 不设置 `LLAVA_STORAGE_BACKEND` | `RUSTFS_ENDPOINT` 存在 → RustFS；否则回退本地并打印 warning |
| 数据目录 | `LLAVA_DATA_DIR`（默认 `data/`） | 指定 SQLite 与临时区位置 |

```bash
# 启动 RustFS（首次）
docker compose up -d
# 用 DaoCloud 国内镜像，本地单盘需绕过磁盘检查（compose 已默认开启）
# S3 API: http://localhost:9000，console: http://localhost:9001（rustfsadmin/rustfsadmin）

# scripts/serve.sh 默认即连 RustFS（自动加载根目录 .env，模板 .env.example）
scripts/serve.sh                       # rustfs（compose 默认凭据）
scripts/serve.sh --storage local       # 显式本地后端

# 此前用本地后端积累过数据？把 data/blobs/ 按原 key 搬进 bucket（可重入）：
uv run python scripts/migrate_to_rustfs.py
```

## 2. 数据工厂流水线（5 个核心命令）

```bash
# 1) 资产池：导入图片目录 + 均衡（按文件名 doc_*/chart_* 启发式分类）
uv run llava-instruct prepare-assets ./images --out assets.jsonl --labels labels.json

# 2) 指令合成：资产 + 证据文件 -> LLaVA 对话样本
uv run llava-instruct generate assets.jsonl \
  --captions captions.jsonl --ocr ocr.jsonl --bbox bbox.jsonl --pairs pairs.jsonl \
  --out samples.jsonl

# 3) 质检：结构 / 语义 / bbox 越界检查
uv run llava-instruct qa samples.jsonl --image-root ./images --report qa_report.md

# 4) bbox 反向渲染可视化核验
uv run llava-instruct render samples_qa.jsonl --image-root ./images --out-dir render

# 5) train/val/smoke 切分 + manifest
uv run llava-instruct split samples_qa.jsonl --out-dir deliver
```

## 3. 资产层管理（`asset` 命令族）

```bash
uv run llava-instruct asset init                         # 查看当前配置（后端/数据目录/bucket）
uv run llava-instruct asset source add --name coco --kind huggingface \
  --params '{"repo_id": "org/ds", "subfolder": "images", "allow_patterns": ["*.png"]}'
uv run llava-instruct asset source list
uv run llava-instruct asset source rm <source_id>

# HF parquet 数据集（如 COCO-Caption）：process=parquet 逐行解码为图片资产
export HF_ENDPOINT=https://hf-mirror.com        # 国内访问 HF 需走镜像
uv run llava-instruct asset source add --name coco-caption --kind huggingface \
  --params '{"repo_id": "lmms-lab-encoder/COCO-Caption", "subfolder": "data", "allow_patterns": ["*.parquet"], "process": "parquet"}'

uv run llava-instruct asset sync <source_id>             # 下载管线：Ray 逐文件任务（并行+崩溃自动重试+转换+入库，幂等可重试）
uv run llava-instruct asset import ./images --out assets.jsonl   # 本地导入（等效 prepare-assets）

uv run llava-instruct asset ls --tag task=chart --type chart_image --status ready
uv run llava-instruct asset tag add <asset_id> chart --group task
uv run llava-instruct asset tag rm <asset_id> chart
uv run llava-instruct asset version ls <asset_id>        # 版本历史
uv run llava-instruct asset version rollback <asset_id> 1
uv run llava-instruct asset version snapshot --name v1   # 集合级快照（固定资产清单，可复现）
uv run llava-instruct asset version snapshot-list
uv run llava-instruct asset materialize ./pool --tag task=chart   # 物化到本地供下游
```

## 4. Web 管理界面

```bash
uv run llava-instruct asset serve --port 8000            # 浏览器访问 http://localhost:8000
```

页面功能：

- **数据源**：添加（名称/类型/url/license）、一键同步、删除（级联删除资产元数据）
- **资产表**：**游标分页**（每页 50 条，上一页/下一页），按类型/状态/标签/数据源筛选、服务端名称/ID 搜索；行内打标/删除；图片预览（从存储后端流式加载）
- **资产详情**：点击资产名打开——元信息、标签、版本历史与回滚
- **集合快照**：创建/查看固定版本清单
- **下载记录**：最近 20 条同步任务的成败与错误原因
- 顶部统计条：数据源数/资产总数/已就绪/失败/快照数，右上角显示当前后端

## 5. 统一 API（其他模块接入）

```python
from llava_instruct.assets.api import open_store

with open_store() as store:                     # 后端由环境变量决定
    report = store.import_dir(Path("./images"))
    assets = store.list_assets(tags=["task=chart"], status="ready")
    snapshot = store.create_snapshot(name="v1")
    records = store.materialize(Path("./pool"))

with open_store(backend=S3StorageBackend("http://localhost:9000", "u", "p", "bucket")) as store:
    ...
```

## 6. 常见问题

| 现象 | 处理 |
| --- | --- |
| `RUSTFS_ENDPOINT` 已设置但报缺凭据 | 补 `RUSTFS_ACCESS_KEY` / `RUSTFS_SECRET_KEY` |
| 镜像拉取超时 | compose 默认走 DaoCloud 国内镜像；可 `RUSTFS_IMAGE=rustfs/rustfs:latest docker compose up -d` 换官方源 |
| RustFS 启动失败 "distinct physical disks" | 单机多目录共盘需 `RUSTFS_UNSAFE_BYPASS_DISK_CHECK=true`（本地开发已默认） |
| 同步后资产为 failed | `asset serve` 页面"下载记录"或 `asset ls --status failed` 查看错误原因 |
