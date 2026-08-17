"""Stage 3: motion filtering via Farneback optical flow.

Distinguishes "dynamic, trainable" shots from near-static image-like clips.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2


@dataclass
class MotionStats:
    motion_strength: float
    n_pairs: int


def compute_motion_magnitude(
    path: str,
    proxy_wh: tuple[int, int] = (480, 270),
    stride: int = 2,
    max_pairs: int = 60,
) -> MotionStats:
    """Mean Farneback flow magnitude over sampled consecutive frame pairs."""
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise ValueError(f"cannot open video: {path}")
    prev = None
    pairs = 0
    total = 0.0
    frame_index = -1
    try:
        while pairs < max_pairs:
            ok, frame = cap.read()
            if not ok:
                break
            frame_index += 1
            if prev is not None and frame_index % stride == 0:
                prev_small = cv2.resize(prev, proxy_wh)
                cur_small = cv2.resize(frame, proxy_wh)
                prev_gray = cv2.cvtColor(prev_small, cv2.COLOR_BGR2GRAY)
                cur_gray = cv2.cvtColor(cur_small, cv2.COLOR_BGR2GRAY)
                flow = cv2.calcOpticalFlowFarneback(
                    prev_gray, cur_gray, None, 0.5, 3, 15, 3, 5, 1.2, 0
                )
                mag, _ = cv2.cartToPolar(flow[..., 0], flow[..., 1])
                total += float(mag.mean())
                pairs += 1
            prev = frame
    finally:
        cap.release()
    return MotionStats(motion_strength=round(total / max(pairs, 1), 4), n_pairs=pairs)


def motion_filter_one(shot: dict, threshold: float = 0.5) -> dict:
    """Score one shot; keep failure records with status='error'."""
    try:
        motion = compute_motion_magnitude(shot["segment_path"])
        passed = motion.motion_strength >= threshold and motion.n_pairs > 0
        return {
            "shot_id": shot["shot_id"],
            "motion_strength": motion.motion_strength,
            "n_pairs": motion.n_pairs,
            "pass_motion": bool(passed),
            "status": "ok",
        }
    except Exception as exc:
        return failed_motion_record(shot["shot_id"], str(exc))


def failed_motion_record(shot_id: str, error: str) -> dict:
    return {
        "shot_id": shot_id,
        "motion_strength": 0.0,
        "n_pairs": 0,
        # B105: "pass_motion" key name matches the password pattern
        "pass_motion": False,  # nosec B105
        "status": "error",
        "error": error,
    }
