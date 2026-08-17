# data-factory 文档索引

data-factory 是 asset-management 资产层之上的**数据生产与评测闭环**（数据策略 + 数据评测），对应《大模型数据工程》项目十四的后续阶段。docs 目录按用途分为三个子目录：

| 目录 | 用途 | 包含文件 |
| --- | --- | --- |
| [spec/](spec/) | **系统设计文件**：架构、数据模型、接口契约、存储选型等技术规格 | `data_factory_spec.md` — 数据工厂系统规格（能力域/数据集/工作流/阶段注册表/执行器/产物血缘/模型注册表/评测集/报告归因、SQLite 全表 schema、Iceberg 取舍分析、CLI/Web/统一 API 契约、实施计划与测试计划） |
| [background/](background/) | **项目背景与需求说明** | （待补充：能力域定义与评测归因的业务背景） |
| [manual/](manual/) | **操作手册**：日常使用指南 | （实现后补充：`dfac` 命令、Web 界面、统一 API 接入） |

## 阅读建议

- **要改代码或做设计决策** → 读 [spec/data_factory_spec.md](spec/data_factory_spec.md)，了解数据契约与血缘模型
- **理解与资产层的关系** → 读 asset-management 的 `docs/spec/asset_layer_spec.md`（本项目的输入依赖）
- **要跑起来或使用系统** → 实现完成后查 [manual/](manual/)
