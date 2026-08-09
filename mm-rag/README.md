# mm-rag

多模态 RAG 企业财报助手：把财报/招股书 PDF 组织成可检索、可解释、可评测的多模态 RAG 流水线。

对应《大模型数据工程》项目五：`财报 PDF -> 页面渲染 -> 视觉索引 -> 多页召回 -> 证据组织 -> 多图推理 -> 评测 -> 成本`。核心思路是 **Vision-first 检索链**：页面图像直接进入检索（ColPali + Byaldi），命中的原图再送回 VLM（Qwen2.5-VL）做多图推理，回答必须回链到证据页。

## 安装与运行

```bash
uv sync --extra dev          # 纯 CPU 可用（lexical 索引 + fallback 回答）
uv sync --extra gpu          # 额外安装 byaldi/colpali/torch 做真实视觉检索与 VLM 回答

uv run pytest

# 1. 页面资产层：PDF -> 页面 PNG + page_units.jsonl
uv run mm-rag render-pdf report.pdf --out-dir page_assets

# 2. 索引构建（默认 lexical；装 gpu extra 后自动用 byaldi）
uv run mm-rag build-index report.pdf --out rag_index.json

# 3. Top-K 多页召回 + 目录页过滤 + 回答（fallback 或 vlm）
uv run mm-rag ask rag_index.json "研发投入近三年趋势如何？" --top-k 4

# 4. 评测：hit@k、证据完备率、目录页抑制率
uv run mm-rag evaluate rag_index.json eval.jsonl --out eval_report.json
```

## 评测集格式（eval.jsonl）

```json
{"question": "研发投入占比近三年是上升还是下降？", "relevant_pages": [8, 12], "is_directory_page": false}
```

## 目录结构

```
mm-rag/
├── pyproject.toml
├── src/mm_rag/
│   ├── pages.py      # 页面资产层：渲染、页码映射、可回看
│   ├── index.py      # 视觉索引（byaldi）/ lexical 兜底，原图与索引绑定
│   ├── retrieve.py   # Top-K 召回 + 目录页抑制
│   ├── prompt.py     # 抗噪声 System Prompt（忽略目录页、要求证据回链）
│   ├── answer.py     # 多图推理（fallback / VLM）
│   ├── evaluate.py   # hit@k / 证据完备率 / 目录页抑制率
│   └── cli.py        # 命令入口
└── tests/
```
