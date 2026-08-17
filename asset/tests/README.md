# asset tests

全部为资产层测试：schema 契约、资产分类、元数据（meta/db）、存储后端（storage/）、三段管线（services/downloaders/）、门面（assets/api）、Web REST（assets/routes/）、分页、备份、可观测性。Ray 相关测试注入 `fakehub`，不依赖真实网络。

## 文件结构

```
tests/
├── conftest.py                # 共享 fixture：ray_runtime（会话级本地 Ray 集群）
├── fakehub.py                 # 假 HuggingFace Hub（FakeHub / FailingHub / CrashingHub）
├── test_assets.py             # 资产分类：启发式 / 显式 labels / 按类型均衡
├── test_schema.py             # schema：ASSET_TYPES 常量 / JSONL 写入
├── test_asset_api.py          # 对外 API 契约（只走 assets.api）
├── test_asset_db.py           # SQLite：source/asset CRUD、唯一约束、标签、下载、快照
├── test_asset_storage.py      # 存储后端：键布局 / 本地去重 / S3（moto 模拟）
├── test_asset_pipeline.py     # 三段管线：download → process → persist
├── test_asset_parquet_processor.py  # parquet 解码为图片资产
├── test_asset_store.py        # AssetStore：导入 / sync / 标签 / 快照 / 版本回滚 / 级联删除
├── test_asset_web.py          # Web 端点：sources/assets/tag/rollback/preview/同步轮询与暂停恢复
├── test_asset_pagination.py   # 游标分页：db keyset / SQL 下推过滤 / Web API
├── test_asset_observability.py# 可观测性：sync run 生命周期 / 进度事件 / 统一日志 / 失败恢复
└── test_asset_backup.py       # 在线备份：一致且可恢复
```

## 各文件职责

| 文件 | 职责 | 关键内容 |
| --- | --- | --- |
| `conftest.py` | 测试基建 | `ray_runtime`：启动本地 Ray（runtime_env excludes 源树 + PYTHONPATH 指向 tests，保证 fakehub 可被 worker 导入） |
| `fakehub.py` | 测试替身 | `FakeHub`（正常下载）、`FailingHub`（网络失败）、`CrashingHub`（worker 崩溃模拟），实现 `list_repo_files` / `hf_hub_download` |
| `test_asset_*.py` | 资产层测试 | 覆盖 db 8 类操作、storage 两种后端（moto 起 S3 mock）、管线三段、store 门面、Web REST、分页、备份、可观测性 |

## 测试与源码依赖关系

```mermaid
graph TD
    conftest["conftest.py（ray_runtime）"] --> ray["ray（外部）"]
    fakehub["fakehub.py"] --> sync["assets/services/downloaders/ray_data_sync.py"]

    test_assets["test_assets.py"] --> classify["assets/classify.py"]
    test_schema["test_schema.py"] --> schema["schema.py"]

    test_asset_db["test_asset_db.py"] --> db["assets/meta/db.py"]
    test_asset_storage["test_asset_storage.py"] --> storage["assets/storage/"]
    test_asset_pipeline["test_asset_pipeline.py"] --> dl["assets/services/downloaders/*"]
    test_asset_parquet["test_asset_parquet_processor.py"] --> parquet["assets/services/downloaders/processors/parquet.py"]
    test_asset_store["test_asset_store.py"] --> api["assets/api.py"]
    test_asset_api["test_asset_api.py"] --> api["assets/api.py"]
    test_asset_web["test_asset_web.py"] --> web["assets/routes/"]
    test_asset_observability["test_asset_observability.py"] --> api + dl
    test_asset_backup["test_asset_backup.py"] --> api
    test_asset_pagination["test_asset_pagination.py"] --> db + web
```

要点：

- **sync 类测试注入 fakehub**（`store.sync_source(..., hub=)` → `SyncConfig.hub` → 云 pickle 传给 Ray worker），绝不访问真实 HF。
- **Ray 测试依赖 conftest 的会话级集群**，且要求 `fakehub` 在 worker 的 PYTHONPATH 上（runtime_env env_vars 已处理）。
- **moto 起 S3 mock** 测试 `S3StorageBackend`（`endpoint_url=None` 走默认 AWS 端点即可命中 moto）。
- 运行：`cd asset && uv run pytest`。
