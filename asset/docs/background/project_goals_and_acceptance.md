# asset 项目目标与验收标准

对应《大模型数据工程》项目三：LLaVA 多模态指令数据工厂
（https://datascale-ai.github.io/data_engineering_book/part14/p03_asset_management/）

## 1. 项目背景与要解决的问题

通用语言模型进入视觉场景后，数据问题会立刻暴露，最常见的失真有三类：

| 失真类型 | 表现 | 后果 |
| --- | --- | --- |
| 视觉事实失真 | 图里两只狗写成三只、餐桌说成办公桌、框选左上角却描述整图 | 幻觉被当成知识进入训练集 |
| 任务失真 | 只会整图粗描述，不会对象级 grounding、文档区域阅读、图表比较、多图推理 | 任务谱系不完整 |
| 接口失真 | 图像路径、任务标签、OCR、bbox、conversation 模板、训练切分字段混乱 | 工厂退化成临时脚本 |

本项目不是"生成一些 LLaVA 格式 JSON"，而是搭建一条**可复用的多模态监督数据生产线**。

## 2. 项目目标（四个）

1. **建立多模态资产到监督样本的转化链路**
   把原始图像、标注框和派生视觉资产，转成可直接用于视觉指令微调的结构化样本。

2. **建立面向 LLaVA 风格训练的任务体系**
   不把所有样本统一成"图片 + 问答"，而是拆分为 8 类任务：
   - `image_description` 图像描述
   - `counting_vqa` 计数与视觉问答
   - `ocr_summary` OCR 摘要
   - `document_qa` 文档问答
   - `chart_reading` 图表阅读
   - `chart_comparison` 图表比较
   - `region_grounding` 区域定位（bbox）
   - `multi_image_comparison` 多图交错比较

3. **建立可审核、可回退、可版本化的 QA 机制**
   质量规则 + 人工抽检 + 可视化反查（bbox 反向渲染）+ 低质量样本标记。

4. **形成训练侧可直接消费的数据资产**
   输出训练集、验证集、smoke test、manifest、评估报告和项目检查结果，从"实验脚本"转为"正式交付物"。

## 3. 系统输入与输出

### 3.1 输入（资产层）

- 三类均衡资产池（书中基准：87 条 = 3 类 × 29 条）：
  - `general_image` 通用图像（COCO 子集）
  - `document_image` 文档图像（派生）
  - `chart_image` 图表图像（派生）
- 每个资产的证据文件：caption / OCR 文本 / bbox 标注 / 多图配对

### 3.2 输出（交付层）

**中间产物**
- `asset_manifest.jsonl` — 资产池清单
- `asset_management.jsonl` — 基础指令样本
- `asset_alignment.jsonl` — 对齐样本（含 bbox）
- `asset_interleaved.jsonl` — 多图交错样本
- `quality_audit.jsonl` / `low_quality_flags.jsonl` — 质检结果与低质量标记
- `manual_review_samples.jsonl` — 人工抽检样本
- `qa_visual_audit.jsonl` — bbox 反向渲染可视化核验

**训练产物**
- `final_asset_dataset.jsonl`
- `train.jsonl` / `val.jsonl` / `smoke_test.jsonl`
- `training_manifest.json` — 样本总数、各 split/task/asset 数量、文件路径、生成版本、overlap 检查

**报告产物**
- `p3_metrics.json` / `p3_report.md` / `p3_test_results.json` / `p3_test_report.md`

**样本 schema（最小契约）**

`id`、`image`（单图或多图列表）、`asset_type`、`task_type`、`source_id`、`bbox`、`ocr_text`、`conversations`（LLaVA 对话格式，`<image>` 占位）、`split`、`meta`（版本/生成方式/审核状态）。

## 4. 验收标准（表 P03-1）

| 验收维度 | 指标/证据 | 出版复核口径 |
| --- | --- | --- |
| 任务边界 | 8 类任务覆盖记录（LLaVA 对话模板、图像描述、OCR、图表阅读、bbox grounding、多图比较） | 说明本项目是经典 LLaVA 流程基线，不把 Qwen-VL 工厂化扩展能力写入本项目结论 |
| 图文一致性 | 可视化抽检样本、错误样本归因、图像版本与 bbox 回放记录 | 抽检时必须能回到原图、标注框、OCR 线索和生成回答 |
| 训练交付 | train/val/smoke 切分、manifest、schema 检查、项目检查报告 | 训练记录必须能被下游脚本稳定消费，且报告数字与产物数量一致 |
| 人工复核 | 抽样比例、复核人角色、失败样本处理状态和再生成记录 | grounding、OCR 和图表类样本不得只依赖自动规则通过 |

## 5. 量化指标（书中实际产出，供对照）

| 指标 | 数值 | 含义 |
| --- | --- | --- |
| 资产总数 | 87（三类各 29） | 资产层刻意均衡设计 |
| 基础指令 | 174 | 通用任务派生 |
| 对齐样本 | 79 | 含 bbox 的 grounding 样本 |
| 交错样本 | 14 | 多图比较刻意小规模 |
| 最终训练记录 | 267 | 任务派生能力（87 资产 → 267 样本），而非素材堆叠 |
| QA 可视化样本 | 29 | 支持 bbox 反向回查 |
| 质量通过率 | 100% | 受控小规模结果，**不可外推到开放世界** |
| 项目检查 | 11/11 PASS | 代码、产物、报告、训练接口一致性闭环 |

注意：训练集资产分布不是三等分，而是 general 137 / document 58 / chart 58 / interleaved 14 —— 通用图承担基础任务，多图样本成本高故限量，分布本身就是设计意图。

## 6. 项目检查（11 项，一致性验证闭环）

命令级：
1. 源码可编译（`py_compile`）
2. 工厂评估脚本可运行（`evaluate_factory`）

数据/产物级：
3. 必需文件存在
4. 资产类型覆盖完整（三类）
5. 对齐样本含 bbox
6. 多图样本确为多图
7. train/val 无 overlap（防泄漏）
8. smoke 覆盖多个任务类型
9. 样本 schema 字段齐全
10. 报告数字与产物数量一致
11. bbox 均在合法范围

> 成功判据：端到端跑通"资产 → 任务派生 → 质检（含可视化）→ 训练封装"，且**代码/数据/报告一致性闭环通过**，而不是"生成了多少 JSON"。

## 7. 成本边界（对成功衡量的约束）

- 成本大头不在模型 API：外部 caption 约 $1.3，人工复核约 267 元
- 多模态数据工厂的瓶颈在**审查与回路**，不在生成本身
- 优化优先级：哪些样本值得人工复核 > 哪些错误先用规则挡掉 > 复杂任务控数量提单价 > 中间产物复用

## 8. 边界声明（验收时不得越界）

- 数据来源：本地 COCO 子集 + 派生文档/图表图像，不覆盖开放世界真实业务图像
- 监督方式：模板化生成 + 规则补充 + 启发式审查 + 人工抽检，非大规模人工生产线
- 规模：小样本受控环境，100% 通过率不能被过度解读为生产级能力
