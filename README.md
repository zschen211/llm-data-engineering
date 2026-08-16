# llm-data-engineering

LLM 数据工程多子项目仓库。根目录只作为容器，每个子项目是完全独立的 Python 包（独立的 `pyproject.toml`、依赖与测试），可单独安装、打包、运行，互不干扰。

| 子项目 | 说明 | 核心链路 |
| --- | --- | --- |
| [llava-instruct](llava-instruct/) | LLaVA 多模态指令数据工厂（资产层） | 资产池 → 指令合成 → 区域对齐 → 多图交错 → 质检 → 训练封装 |
| [data-factory](data-factory/) | 资产层之上的**数据生产与评测闭环**（数据策略 + 数据评测） | 能力域 → 策略管线 → 训练数据版本 → 模型评测 → badcase 归因 → 反推新策略 |
| [mm-rag](mm-rag/) | 多模态 RAG 企业财报助手 | PDF → 页面渲染 → 视觉索引 → Top-K 召回 → 多图推理 → 评测 |
| [video-generation](video-generation/) | 视频生成数据流水线（T2V） | 源加载 → 镜头切分 → 运动/美学过滤 → 多帧 caption → 镜头语言标注 |

## 快速开始

```bash
# 每个子项目都独立工作，先进入对应目录
cd llava-instruct
uv sync --extra dev          # 或 mm-rag / video-generation
uv run pytest                # 运行测试
uv run llava-instruct --help # 查看 CLI 命令
uv build                     # 独立打包
```

- CPU 基础功能开箱即用；GPU 相关能力（视觉索引、VLM caption、美学打分）通过 `uv sync --extra gpu` 安装。
- 各子项目 README 内含完整的端到端运行示例。
