# llava-instruct 文档索引

docs 目录按用途分为三个子目录：

| 目录 | 用途 | 包含文件 |
| --- | --- | --- |
| [spec/](spec/) | **系统设计文件**：架构、数据模型、接口契约、存储选型等技术规格 | `asset_layer_spec.md` — 数据资产层系统规格（SQLite 八表 schema、RustFS 存储布局、下载器抽象、CLI/Web/统一 API 契约、版本与标签策略、测试计划） |
| [background/](background/) | **项目背景与需求说明**：项目目标、验收标准、领域背景知识 | `project_goals_and_acceptance.md` — 项目目标与验收标准（对应书中 P03 验收口径）<br>`multimodal_vlm_background.md` — VLM / 多模态指令数据 / 相关数据集与模型背景 |
| [manual/](manual/) | **操作手册**：日常使用指南，面向使用者 | `usage.md` — 环境准备、CLI 命令、Web 管理界面、统一 API 接入、常见问题 |

## 阅读建议

- **刚接触本项目** → 先读 [background/](background/) 理解"做什么、为什么"
- **要改代码或做设计决策** → 读 [spec/](spec/) 了解既有契约，避免破坏接口
- **要跑起来或使用系统** → 直接查 [manual/](manual/)
