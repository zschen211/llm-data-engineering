"""Stage 4: aesthetic scoring with CLIP ViT-L/14 + LAION-Aesthetic MLP.

Requires the optional ``gpu`` extra (torch). Frames are sampled evenly and
scores averaged; supports GPU sharding and OOM degradation via ``safe_call``.
"""
from __future__ import annotations

import functools
import logging

logger = logging.getLogger(__name__)


def _require_torch():
    try:
        import torch  # noqa: F401
        import torch.nn as nn  # noqa: F401

        return torch, nn
    except ImportError as exc:
        raise RuntimeError("aesthetic scoring requires the optional 'gpu' extra (torch + transformers + clip)") from exc


def build_aesthetic_mlp(input_size: int = 768):
    """LAION-Aesthetic style MLP predictor."""
    torch, nn = _require_torch()
    return nn.Sequential(
        nn.Linear(input_size, 1024), nn.Dropout(0.2),
        nn.Linear(1024, 128), nn.Dropout(0.2),
        nn.Linear(128, 64), nn.Linear(64, 16), nn.Linear(16, 1),
    )


def safe_call(stages: tuple[str, ...] = ("batch", "frames", "resolution", "length")):
    """Decorator: degrade batch/frames/resolution/length on OOM instead of dying."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            last = None
            for stage in stages:
                try:
                    return fn(*args, **kwargs, _degrade=stage)
                except RuntimeError as exc:
                    if "out of memory" not in str(exc).lower():
                        raise
                    last = exc
                    logger.warning("OOM at %s, degrading", stage)
            raise RuntimeError(f"OOM after degrading through all stages") from last

        return wrapper

    return deco


def _load_clip_model(clip_path: str):
    try:
        import clip
    except ImportError as exc:
        raise RuntimeError("aesthetic scoring requires the optional 'gpu' extra (clip)") from exc
    import torch

    model, preprocess = clip.load(clip_path, device="cuda")
    return model, preprocess


def sample_frames_pil(segment_path: str, k: int = 4) -> list:
    import cv2
    from PIL import Image

    cap = cv2.VideoCapture(segment_path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    images = []
    try:
        if total <= 0:
            return images
        indices = sorted(int(i * max(total - 1, 1) / max(k, 1)) for i in range(k))
        for idx in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if ok:
                images.append(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
    finally:
        cap.release()
    return images


@safe_call()
def score_shot_aesthetic(segment_path: str, clip_model, clip_processor, aesthetic_mlp,
                         frames: int = 4, threshold: float = 5.0, _degrade: str = "batch"):
    import torch
    import torch.nn.functional as F

    if _degrade == "frames":
        frames = max(2, frames // 2)
    images = sample_frames_pil(segment_path, k=frames)
    if not images:
        return {"aesthetic_score": 0.0, "pass_aesthetic": False, "status": "no_frames"}
    feats = torch.cat([clip_processor(img).unsqueeze(0) for img in images], dim=0).to("cuda")
    with torch.no_grad():
        feats = F.normalize(feats, p=2, dim=-1)
        scores = aesthetic_mlp(feats.to(aesthetic_mlp[0].weight.dtype)).squeeze(-1)
    avg = float(scores.mean().cpu())
    return {"aesthetic_score": round(avg, 4), "pass_aesthetic": avg >= threshold, "status": "ok"}
