# video-generation

T2V 视频生成数据流水线：把公开视频源整理成可训练、可追溯、可恢复的 shot-level T2V 数据集。

对应《大模型数据工程》项目十四，六组件流水线：`视频源加载 -> 镜头切分 -> 运动过滤 -> 美学过滤 -> 多帧 caption -> 镜头语言标注`，每个阶段落盘 JSONL，支持断点续跑与 GPU 分片。

## 安装与运行

```bash
uv sync --extra dev                  # CPU 阶段可跑（load/scene/motion/tag/manifest）
uv sync --extra gpu                  # 美学打分与 VLM caption 需要 torch/transformers

uv run pytest

# 环境变量示例（可写进 run_pipeline.sh）
export ROOT=$(pwd) OUT=$(pwd)/out SRC=$(pwd)/videos

# Stage 1: 源视频探测（pexels_manifest.jsonl 或 pexels_*.mp4，ffprobe 补齐元数据）
uv run video-generation load-sources $SRC --out $OUT/source_videos.jsonl

# Stage 2: PySceneDetect 镜头切分 + ffmpeg 分片
uv run video-generation scene-detect $OUT/source_videos.jsonl --out-root $OUT --out $OUT/stage2_scenes.jsonl --threshold 27.0 --min-shot-len 1.0

# Stage 3: Farneback 光流运动过滤
uv run video-generation motion-filter $OUT/stage2_scenes.jsonl --out $OUT/stage3_motion.jsonl --threshold 0.5

# Stage 4: CLIP + LAION-Aesthetic 打分（多 GPU 分片，OOM 自动降级）
uv run video-generation aesthetic-filter $OUT/stage2_scenes.jsonl --out $OUT/stage4_aesthetic.jsonl --num-shards 8 --shard-id 0

# Stage 5: 多帧采样 + Qwen2.5-VL/InternVL3 生成单段英文 caption（过短自动重试）
uv run video-generation caption $OUT/stage2_scenes.jsonl --out $OUT/stage5_captions.jsonl --model Qwen/Qwen2.5-VL-7B-Instruct --frames 8

# Stage 6: 受控词表镜头语言标注 + 光流相机运动分类
uv run video-generation tag-shot-language $OUT/stage2_scenes.jsonl --out $OUT/stage6_shot_language.jsonl

# 汇总：以 shot_id 为主键 join 各阶段 -> final manifest
uv run video-generation build-manifest --sources $OUT/source_videos.jsonl --scenes $OUT/stage2_scenes.jsonl \
  --motion $OUT/stage3_motion.jsonl --aesthetic $OUT/stage4_aesthetic.jsonl \
  --captions $OUT/stage5_captions.jsonl --shot-language $OUT/stage6_shot_language.jsonl --out $OUT/final_manifest.jsonl
```

## 关键运行参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `scene-detect --threshold` | 27.0 | 切分阈值，先抽样观察再固定 |
| `scene-detect --min-shot-len` | 1.0s | 过滤过短镜头 |
| `motion-filter --threshold` | 0.5 | 动态片段召回率，慢动作素材可降低 |
| `aesthetic-filter --threshold` | 5.0 | 视觉质量门槛，建议保留分数做训练分桶 |
| `caption --min-words` | 50 | caption 详尽度下限 |
| `--num-shards / --shard-id` | 1 / 0 | GPU 确定性分片 |

## 阶段产物契约

```
source_videos.jsonl      video_id, path, license, duration, fps, width, height, file_size
stage2_scenes.jsonl      shot_id, video_id, start_ts, end_ts, segment_path
stage3_motion.jsonl      shot_id, motion_strength, n_pairs, pass_motion
stage4_aesthetic.jsonl   shot_id, aesthetic_score, per_frame_scores, pass_aesthetic
stage5_captions.jsonl    shot_id, caption_en, n_words, caption_short
stage6_shot_language     shot_id, vlm_tags(受控词表), camera_motion, status
final_manifest.jsonl     按 shot_id join 以上全部 + 来源/审计字段
```

## 目录结构

```
video_generation/
├── pyproject.toml
├── src/video_generation/
│   ├── io.py        # SafeJsonlWriter / repair_tail / 断点续跑 / 分片合并
│   ├── load.py      # Stage 1 源视频加载（manifest 或文件名恢复 + ffprobe）
│   ├── scene.py     # Stage 2 PySceneDetect 镜头切分
│   ├── motion.py    # Stage 3 Farneback 光流运动强度
│   ├── aesthetic.py # Stage 4 CLIP + LAION-Aesthetic MLP（safe_call 显存降级）
│   ├── caption.py   # Stage 5 多帧采样 + VLM caption（过短重试）
│   ├── tag.py       # Stage 6 受控词表 + 相机运动分类
│   ├── manifest.py  # final manifest join + 校验
│   └── cli.py       # 命令入口
└── tests/
```

> 每个代码目录（`src/video_generation/`、`tests/`）内都有 README，说明各文件职责与依赖关系。
