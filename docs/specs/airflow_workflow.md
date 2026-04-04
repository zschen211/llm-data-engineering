# Airflow Workflow

## 背景和目标
本项目未来可能包含多个代码模块，分别对应不同的数据处理管线。如果每个管线都使用原生的 python 文件实现，并且需要手动执行各个 python 文件的话，整体流程的编排不够自动化，并且很难对不同的数据管线逻辑进行统一管理。因此本项目需要实现一个统一的 Airflow Workflow 模块，帮助统一拉起 Airflow standalone 服务，并接收不同的数据管线编排逻辑。

## 实现路径

### Airflow Standlone
#### 模块说明
实现一个 cli 命令行工具，帮助拉起本地运行的 Airflow Standalone
#### 评估标准
1. 使用 cli 命令行工具拉起 Airflow standalone 之后，可以通过 WebUI 访问 Airflow 的管理后台网页 
2. 实现一个测试用的 DAG 工作流，将该工作流提交给 Airflow 应该能够正常调度和执行

## 规范和约束
1. Airflow Standalone cli 需要实现在项目目录的 cli 文件夹下
2. 测试 DAG 工作流应该实现在项目目录下的 tests 文件夹下
3. cli 文件需要有超过 80% 的单元测试覆盖率，其中单元测试中不需要真正拉起 Airflow，而是用 mock 的方式模拟
