# llava-instruct

LLaVA 多模态指令数据工厂：把多模态资产（通用/文档/图表图像）加工成可训练、可质检、可封装的多模态监督数据资产。

对应《大模型数据工程》项目三：`资产池 -> 指令合成 -> 区域对齐 -> 多图交错 -> 质量审核 -> 训练封装 -> 报告与验证`。

## 安装与运行

```bash
uv sync --extra dev
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

## 样本 schema

每条样本包含：`id`、`image`（单图或多图列表）、`asset_type`（general/document/chart/interleaved_pair）、`task_type`（8 类任务）、`source_id`、`bbox`、`ocr_text`、`conversations`（LLaVA 对话格式）、`split`、`meta`（版本/生成方式/审核状态）。

## 目录结构

```
llava-instruct/
├── pyproject.toml
├── src/llava_instruct/
│   ├── schema.py      # 样本契约、bbox 校验与 clamp
│   ├── assets.py      # 资产池：扫描、分类、均衡采样
│   ├── templates.py   # 受控任务模板与 LLaVA conversation 构建
│   ├── generator.py   # 监督构造：资产 + 证据 -> 样本
│   ├── qa.py          # 结构/语义/bbox 三类质检与低质量样本沉淀
│   ├── render.py      # bbox 反向渲染（Pillow）
│   ├── split.py       # train/val/smoke 切分、manifest、报告
│   └── cli.py         # 命令入口
└── tests/
```
