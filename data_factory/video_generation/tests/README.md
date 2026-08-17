# video-generation tests

按阶段分文件的单元测试。视频类测试用 `monkeypatch` 伪造 ffprobe / scenedetect / VLM 调用，或用 `_make_video` 生成真实小视频验证光流逻辑；GPU 阶段（aesthetic/caption 模型加载）不在测试中真实执行。

## 文件结构

```
tests/
├── test_io.py                # SafeJsonlWriter / repair_tail / scan_done_ids / 分片合并
├── test_load.py              # pexels id 解析 / 记录规整 / resume / manifest 优先 / ffprobe 缺失
├── test_scene.py             # 镜头切分（monkeypatch splitter）/ resume
├── test_motion_tag.py        # 光流运动强度（真实小视频）/ 相机运动分类 / 受控词表 / tag 合并
├── test_manifest_caption.py  # 帧采样 / caption prompt / manifest join / 校验
└── test_cli.py               # CLI 端到端：load/motion/tag/build-manifest
```

## 各文件职责

| 文件 | 职责 | 关键内容 |
| --- | --- | --- |
| `test_io.py` | I/O 基建 | 追加写不丢数据、`repair_tail` 清掉残缺行、`scan_done_ids` 幂等、`shard_for`/`merge_shards` 确定性 |
| `test_load.py` | Stage 1 | `parse_pexels_id`；`load_source_videos` 断点续跑（第二次运行跳过已写 id）；manifest 优先于文件名恢复；ffprobe 不存在时优雅跳过 |
| `test_scene.py` | Stage 2 | `_FakeSceneManager` 替身驱动 `split_one_video` 的分片与记录构建；`run_scene_detect` 按 video 粒度续跑 |
| `test_motion_tag.py` | Stage 3 + 6 | `_make_video` 生成真实短视频验证运动/静态区分；`summarize_camera_motion` 五分类；词表归一无副作用；`tag_shot_language` 有/无 VLM 两种分支 |
| `test_manifest_caption.py` | Stage 5 + manifest | 时间序均匀采样（含短镜头）；prompt 不逐帧枚举；`build_manifest` 按 shot_id join；`validate_manifest` 缺失字段标记 |
| `test_cli.py` | 命令入口 | `monkeypatch` 各阶段模块函数跑 `load-sources` / `motion-filter` / `tag-shot-language` 端到端；`build-manifest` 真实落盘 |

## 测试与源码依赖关系

```mermaid
graph TD
    test_io["test_io.py"] --> io["io.py"]
    test_load["test_load.py"] --> load["load.py"]
    test_load --> io
    test_scene["test_scene.py"] --> scene["scene.py"]
    test_scene --> io
    test_motion_tag["test_motion_tag.py"] --> motion["motion.py"]
    test_motion_tag --> tag["tag.py"]
    test_manifest["test_manifest_caption.py"] --> caption["caption.py"]
    test_manifest --> manifest["manifest.py"]
    test_cli["test_cli.py"] --> cli["cli.py"]
    cli --> io + load + scene + motion + tag + manifest
```

要点：

- **策略**：CPU 可跑逻辑（io/load 纯函数、motion/tag 光流）用真实小视频或替身验证；模型加载路径（aesthetic 的 `_require_torch`、caption 的 `load_vlm`）不进入测试，避免依赖 GPU。
- **断点续跑是重点**：load/scene 两处 resume 语义（按 id 跳过）都有专门测试。
- 运行：`cd video-generation && uv run pytest`。
