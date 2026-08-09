"""Stage 6: shot-language tagging — VLM controlled-vocab labels + camera motion.

The VLM part requires the optional ``gpu`` extra; camera motion
classification is a pure function over optical-flow statistics and always runs.
"""
from __future__ import annotations

import cv2
import numpy as np

VOCAB = {
    "shot_size": ["extreme_wide", "wide", "medium", "close_up"],
    "camera_angle": ["eye_level", "high_angle", "low_angle", "overhead"],
    "composition": ["rule_of_thirds", "centered", "symmetrical", "framing"],
    "lighting": ["high_key", "low_key", "natural", "backlit"],
    "style": ["cinematic", "documentary", "vlog", "commercial"],
}


def sanitize_and_coerce_to_vocab(raw: dict, vocab: dict[str, list[str]] | None = None) -> dict:
    """Coerce free-form VLM output into the controlled vocabulary."""
    vocab = vocab or VOCAB
    cleaned: dict[str, str] = {}
    for key, allowed in vocab.items():
        value = str(raw.get(key, "")).strip().lower()
        value = value.replace(" ", "_").replace("-", "_")
        if value in allowed:
            cleaned[key] = value
        else:
            cleaned[key] = "unknown"
    return cleaned


def flow_statistics(segment_path: str, proxy_wh: tuple[int, int] = (480, 270),
                    max_pairs: int = 30) -> dict:
    """Aggregate flow statistics (mean/var magnitude and angle) for a shot."""
    cap = cv2.VideoCapture(segment_path)
    if not cap.isOpened():
        raise ValueError(f"cannot open video: {segment_path}")
    mags: list[float] = []
    angles: list[float] = []
    prev = None
    try:
        while len(mags) < max_pairs:
            ok, frame = cap.read()
            if not ok:
                break
            if prev is not None:
                small = lambda img: cv2.resize(img, proxy_wh)
                g_prev = cv2.cvtColor(small(prev), cv2.COLOR_BGR2GRAY)
                g_cur = cv2.cvtColor(small(frame), cv2.COLOR_BGR2GRAY)
                flow = cv2.calcOpticalFlowFarneback(g_prev, g_cur, None,
                                                    0.5, 3, 15, 3, 5, 1.2, 0)
                mag, ang = cv2.cartToPolar(flow[..., 0], flow[..., 1])
                mags.append(float(np.mean(mag)))
                angles.extend(float(a) for a in ang.ravel()[::64])
            prev = frame
    finally:
        cap.release()
    if not mags:
        return {"mean_magnitude": 0.0, "std_magnitude": 0.0, "mean_angle": 0.0}
    return {
        "mean_magnitude": round(float(np.mean(mags)), 4),
        "std_magnitude": round(float(np.std(mags)), 4),
        "mean_angle": round(float(np.mean(angles)), 4) if angles else 0.0,
    }


def summarize_camera_motion(stats: dict) -> str:
    """Classify camera motion from flow statistics.

    static: no motion; zoom: low variance of magnitude with positive mean;
    pan/tilt: consistent angle; jitter: high variance; complex: otherwise.
    """
    mean = stats.get("mean_magnitude", 0.0)
    std = stats.get("std_magnitude", 0.0)
    if mean < 0.05:
        return "static"
    if std < 0.3 * max(mean, 1e-6):
        return "pan" if 0 <= stats.get("mean_angle", 0.0) < 3.0 else "tilt"
    if std > mean:
        return "jitter"
    return "complex"


def tag_shot_language(shot_id: str, segment_path: str, frame_paths: list[str],
                      vlm_fn=None) -> dict:
    """Merge VLM controlled-vocab tags with flow-based camera motion."""
    motion_stats = flow_statistics(segment_path)
    if vlm_fn is None:
        tags = {key: "unknown" for key in VOCAB}
    else:
        raw = vlm_fn(frame_paths=frame_paths, allowed_vocab=VOCAB)
        tags = sanitize_and_coerce_to_vocab(raw)
    return {
        "shot_id": shot_id,
        "vlm_tags": tags,
        "camera_motion": summarize_camera_motion(motion_stats),
        "status": "ok",
    }
