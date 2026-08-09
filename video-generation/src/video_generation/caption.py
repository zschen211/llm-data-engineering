"""Stage 5: multi-frame video captioning with a VLM (Qwen2.5-VL / InternVL3).

Frames are sampled in time order and described as one continuous English
paragraph (no per-frame enumeration).
"""
from __future__ import annotations

import os
import re

CAPTION_PROMPT = (
    "Write one English paragraph describing the whole shot: subjects, setting, "
    "actions, camera framing, lighting, color mood, and atmosphere. "
    "Do not enumerate frames."
)


def sample_frames_in_time_order(frame_paths: list[str], k: int = 8) -> list[str]:
    """Deterministically pick k frames spread across the shot, in time order."""
    paths = sorted(frame_paths)
    n = len(paths)
    if n <= k:
        return paths
    indices = sorted(int(i * (n - 1) / (k - 1)) for i in range(k))
    return [paths[i] for i in dict.fromkeys(indices)]


def _require_vlm():
    try:
        import torch  # noqa: F401
        from transformers import AutoModelForCausalLM, AutoProcessor  # noqa: F401

        return AutoModelForCausalLM, AutoProcessor
    except ImportError as exc:
        raise RuntimeError("captioning requires the optional 'gpu' extra (torch + transformers)") from exc


def load_vlm(model_name: str):
    AutoModelForCausalLM, AutoProcessor = _require_vlm()
    processor = AutoProcessor.from_pretrained(model_name, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(model_name, device_map="auto", trust_remote_code=True)
    return model, processor


def _decode_new_tokens(gen_ids, input_ids, processor) -> str:
    return processor.decode(gen_ids[0][input_ids.shape[1]:], skip_special_tokens=True).strip()


def torch_inference_guard():
    """Guard for functions requiring torch; raises ImportError when unavailable."""
    import functools

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            import torch  # noqa: F401

            with torch.inference_mode():
                return fn(*args, **kwargs)

        return wrapper

    return deco


@torch_inference_guard()
def generate_video_caption(frame_paths: list[str], model, processor,
                           frames_n: int = 8, max_new_tokens: int = 220,
                           min_words: int = 50, retries: int = 2) -> dict:
    """Generate a video-level caption; retry with higher temperature if too short."""
    selected = sample_frames_in_time_order(frame_paths, k=frames_n)
    last = None
    for attempt in range(retries + 1):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "video", "video": [f"file://{p}" for p in selected]},
                    {"type": "text", "text": CAPTION_PROMPT},
                ],
            }
        ]
        inputs = processor.apply_chat_template(messages, return_tensors="pt", add_generation_prompt=True)
        temperature = 0.0 if attempt == 0 else 0.7
        gen_ids = model.generate(
            inputs.to(model.device),
            max_new_tokens=max_new_tokens,
            do_sample=temperature > 0,
            temperature=temperature or 1.0,
        )
        caption = _decode_new_tokens(gen_ids, inputs.input_ids, processor)
        if len(caption.split()) >= min_words:
            return _caption_result(caption)
        last = caption
    return _caption_result(last or "")


def _caption_result(caption: str) -> dict:
    words = caption.split()
    return {
        "caption_en": caption,
        "n_words": len(words),
        "caption_short": len(words) < 50,
    }
