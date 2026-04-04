# airflow_standalone CLI

`cli/airflow_standalone.py` 提供三个子命令，用于管理本地 Airflow Standalone 进程。

## 用法

```bash
uv run airflow-standalone <command> [options]
```

### start

启动 Airflow Standalone，前台运行，`Ctrl+C` 停止。

```bash
uv run airflow-standalone start [--port PORT] [--dags-folder PATH]
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--port` | `8080` | Web UI 端口 |
| `--dags-folder` | `<project_root>/dags` | DAG 文件目录，不存在时自动创建 |

启动后访问 `http://localhost:<port>`。

### stop

终止所有 `airflow standalone` 进程。

```bash
uv run airflow-standalone stop
```

### status

检查是否有正在运行的 `airflow standalone` 进程，并打印 PID。

```bash
uv run airflow-standalone status
```

## 环境变量

启动时自动注入以下 Airflow 配置：

| 变量 | 值 |
|------|----|
| `AIRFLOW__CORE__DAGS_FOLDER` | `--dags-folder` 的值 |
| `AIRFLOW__CORE__LOAD_EXAMPLES` | `False` |
| `AIRFLOW__WEBSERVER__WEB_SERVER_PORT` | `--port` 的值 |

## 测试

单元测试位于 `tests/test_airflow_standalone.py`，全部使用 mock，不启动真实 Airflow。

```bash
uv run pytest tests/test_airflow_standalone.py
```
