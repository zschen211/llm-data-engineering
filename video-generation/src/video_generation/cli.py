"""video-generation CLI: six pipeline stages + manifest builder."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import io, load as load_mod, manifest as manifest_mod, motion as motion_mod
from . import scene as scene_mod, tag as tag_mod


def get_parser():
    parser = argparse.ArgumentParser(
        prog="video-generation",
        description="T2V video generation data pipeline (six stages)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p1 = subparsers.add_parser("load-sources", help="Stage 1: probe source videos -> source_videos.jsonl")
    p1.add_argument("src", type=Path, help="dir with pexels_*.mp4 / pexels_manifest.jsonl")
    p1.add_argument("--out", type=Path, default=Path("source_videos.jsonl"))
    p1.add_argument("--max-samples", type=int, default=None)

    p2 = subparsers.add_parser("scene-detect", help="Stage 2: shot segmentation -> stage2_scenes.jsonl + shots/")
    p2.add_argument("sources", type=Path)
    p2.add_argument("--out-root", type=Path, default=Path("out"))
    p2.add_argument("--out", type=Path, default=Path("stage2_scenes.jsonl"))
    p2.add_argument("--threshold", type=float, default=27.0)
    p2.add_argument("--min-shot-len", type=float, default=1.0)
    p2.add_argument("--max-samples", type=int, default=None)

    p3 = subparsers.add_parser("motion-filter", help="Stage 3: optical-flow motion filtering")
    p3.add_argument("scenes", type=Path)
    p3.add_argument("--out", type=Path, default=Path("stage3_motion.jsonl"))
    p3.add_argument("--threshold", type=float, default=0.5)
    p3.add_argument("--workers", type=int, default=1)

    p4 = subparsers.add_parser("aesthetic-filter", help="Stage 4: CLIP + LAION-Aesthetic scoring (requires gpu extra)")
    p4.add_argument("scenes", type=Path)
    p4.add_argument("--out", type=Path, default=Path("stage4_aesthetic.jsonl"))
    p4.add_argument("--clip-path", type=str, default="ViT-L/14")
    p4.add_argument("--mlp-path", type=Path, default=None)
    p4.add_argument("--threshold", type=float, default=5.0)
    p4.add_argument("--frames", type=int, default=4)
    p4.add_argument("--num-shards", type=int, default=1)
    p4.add_argument("--shard-id", type=int, default=0)

    p5 = subparsers.add_parser("caption", help="Stage 5: multi-frame VLM captioning (requires gpu extra)")
    p5.add_argument("scenes", type=Path)
    p5.add_argument("--out", type=Path, default=Path("stage5_captions.jsonl"))
    p5.add_argument("--model", type=str, default="Qwen/Qwen2.5-VL-7B-Instruct")
    p5.add_argument("--frames-dir", type=Path, default=None, help="saved frames; default: extract from segment")
    p5.add_argument("--frames", type=int, default=8)
    p5.add_argument("--min-words", type=int, default=50)
    p5.add_argument("--max-samples", type=int, default=None)

    p6 = subparsers.add_parser("tag-shot-language", help="Stage 6: controlled-vocab shot tags + camera motion")
    p6.add_argument("scenes", type=Path)
    p6.add_argument("--out", type=Path, default=Path("stage6_shot_language.jsonl"))
    p6.add_argument("--vlm", type=str, default=None, help="VLM model name (optional)")
    p6.add_argument("--max-samples", type=int, default=None)

    p7 = subparsers.add_parser("build-manifest", help="Merge all stages into the final T2V manifest")
    p7.add_argument("--sources", type=Path, default=Path("source_videos.jsonl"))
    p7.add_argument("--scenes", type=Path, default=Path("stage2_scenes.jsonl"))
    p7.add_argument("--motion", type=Path, default=Path("stage3_motion.jsonl"))
    p7.add_argument("--aesthetic", type=Path, default=Path("stage4_aesthetic.jsonl"))
    p7.add_argument("--captions", type=Path, default=Path("stage5_captions.jsonl"))
    p7.add_argument("--shot-language", type=Path, default=Path("stage6_shot_language.jsonl"))
    p7.add_argument("--out", type=Path, default=Path("final_manifest.jsonl"))
    return parser


def cmd_load_sources(args):
    records = load_mod.load_source_videos(args.src, args.out, max_samples=args.max_samples)
    print(f"source videos: {len(records)} -> {args.out}")
    return 0


def cmd_scene_detect(args):
    records = scene_mod.run_scene_detect(args.sources, args.out_root, args.out,
                                         threshold=args.threshold, min_shot_len=args.min_shot_len,
                                         max_samples=args.max_samples)
    print(f"shots: {len(records)} -> {args.out}")
    return 0


def cmd_motion_filter(args):
    shots = io.read_jsonl(args.scenes)
    with io.SafeJsonlWriter(args.out) as writer:
        for i, shot in enumerate(shots):
            if args.workers == 1 or i % args.workers == 0:
                writer.append(motion_mod.motion_filter_one(shot, threshold=args.threshold))
    print(f"motion records: {len(shots)}")
    return 0


def cmd_aesthetic_filter(args):
    from . import aesthetic as aesthetic_mod

    shots = io.read_jsonl(args.scenes)
    model, preprocess = aesthetic_mod._load_clip_model(args.clip_path)
    mlp = aesthetic_mod.build_aesthetic_mlp()
    if args.mlp_path:
        import torch

        state = torch.load(args.mlp_path, map_location="cpu")
        mlp.load_state_dict(state if isinstance(state, dict) else state.state_dict())
    with io.SafeJsonlWriter(args.out) as writer:
        for i, shot in enumerate(shots):
            if io.shard_for(i, args.num_shards) != args.shard_id:
                continue
            record = {"shot_id": shot["shot_id"], "segment_path": shot["segment_path"]}
            record.update(aesthetic_mod.score_shot_aesthetic(
                shot["segment_path"], model, preprocess, mlp,
                frames=args.frames, threshold=args.threshold,
            ))
            writer.append(record)
    print(f"aesthetic records written to shard {args.shard_id}")
    return 0


def _shot_frames(shot: dict, frames_dir: Path | None) -> list[str]:
    if frames_dir is not None:
        return [str(p) for p in sorted(frames_dir.glob("*.jpg")) + sorted(frames_dir.glob("*.png"))]
    return [shot["segment_path"]]


def cmd_caption(args):
    from . import caption as caption_mod

    shots = io.read_jsonl(args.scenes)
    model, processor = caption_mod.load_vlm(args.model)
    with io.SafeJsonlWriter(args.out) as writer:
        for i, shot in enumerate(shots):
            if args.max_samples is not None and i >= args.max_samples:
                break
            result = caption_mod.generate_video_caption(
                _shot_frames(shot, args.frames_dir), model, processor,
                frames_n=args.frames, min_words=args.min_words,
            )
            writer.append({"shot_id": shot["shot_id"], **result})
    print("captions written")
    return 0


def cmd_tag_shot_language(args):
    shots = io.read_jsonl(args.scenes)
    vlm_fn = None
    if args.vlm:
        def vlm_fn(frame_paths, allowed_vocab):
            from . import caption as caption_mod

            model, processor = caption_mod.load_vlm(args.vlm)
            caption_mod.generate_video_caption(frame_paths, model, processor)
            return {k: "unknown" for k in allowed_vocab}

    with io.SafeJsonlWriter(args.out) as writer:
        for i, shot in enumerate(shots):
            if args.max_samples is not None and i >= args.max_samples:
                break
            record = tag_mod.tag_shot_language(shot["shot_id"], shot["segment_path"],
                                               _shot_frames(shot, None), vlm_fn=vlm_fn)
            writer.append(record)
    print(f"shot-language records: {len(shots)}")
    return 0


def cmd_build_manifest(args):
    samples = manifest_mod.build_manifest(
        {
            "source": args.sources,
            "scenes": args.scenes,
            "motion": args.motion,
            "aesthetic": args.aesthetic,
            "captions": args.captions,
            "shot_language": args.shot_language,
        },
        args.out,
    )
    errors = manifest_mod.validate_manifest(samples)
    print(f"final manifest: {len(samples)} samples, {len(errors)} validation errors")
    return 0 if not errors else 1


def main(argv=None):
    args = get_parser().parse_args(argv)
    handlers = {
        "load-sources": cmd_load_sources,
        "scene-detect": cmd_scene_detect,
        "motion-filter": cmd_motion_filter,
        "aesthetic-filter": cmd_aesthetic_filter,
        "caption": cmd_caption,
        "tag-shot-language": cmd_tag_shot_language,
        "build-manifest": cmd_build_manifest,
    }
    return handlers[args.command](args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
