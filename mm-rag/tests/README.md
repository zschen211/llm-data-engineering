# mm-rag tests

覆盖页面渲染、索引构建、召回、提示词/回答、评测与 CLI 端到端。lexical 路径纯 CPU 可跑；byaldi/vlm 路径在测试中以 ImportError 断言报错信息，不实际加载模型。

## 文件结构

```
tests/
├── test_pages.py     # 页面渲染：PDF -> PNG、文本附着、page_by_id
├── test_rag.py       # 索引/召回/目录页抑制/提示词/回答/评测
└── test_cli.py       # CLI 端到端：render+index+ask、evaluate
```

## 各文件职责

| 文件 | 职责 | 关键内容 |
| --- | --- | --- |
| `test_pages.py` | 页面资产层 | 用 `pdf_path` fixture（测试生成的小 PDF）验证渲染出图、`build_page_units` 附带 `text` 字段、`page_by_id` 查询 |
| `test_rag.py` | 核心链路 | `lex_index` fixture 构造索引；`_tokenize` 中英文分词；`is_directory_page` 目录页启发式；`retrieve` 抑制/不抑制两种行为；`build_messages` 图片 token 数；fallback 回答组织；未知 backend 报错；`evaluate` 指标计算 |
| `test_cli.py` | 命令入口 | 端到端跑 `render-pdf → build-index → ask`；`evaluate` 输出报告 |

## 测试与源码依赖关系

```mermaid
graph TD
    test_pages["test_pages.py"] --> pages["pages.py"]
    test_pages --> schema["schema.py"]

    test_rag["test_rag.py"] --> index["index.py"]
    test_rag --> retrieve["retrieve.py"]
    test_rag --> prompt["prompt.py"]
    test_rag --> answer["answer.py"]
    test_rag --> evaluate["evaluate.py"]

    test_cli["test_cli.py"] --> cli["cli.py"]
    cli --> pages + index + retrieve + answer + evaluate
```

要点：

- **fixture 驱动**：`pdf_path`（小 PDF，`test_pages.py` 内定义）、`lex_index`（lexical 索引，`test_rag.py` 内定义）；`test_cli.py` 用本地 `_make_pdf` 辅助函数生成 PDF。
- **不触网不加载模型**：视觉检索 / VLM 路径只验证「未装 gpu extra 时抛出带说明的 RuntimeError」，避免测试依赖 GPU。
- 运行：`cd mm-rag && uv run pytest`。
