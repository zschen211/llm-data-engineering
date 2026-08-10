"""Generation: multi-image reasoning over retrieved evidence pages.

``backend="fallback"`` produces a deterministic evidence-organizing response.
``backend="vlm"`` requires the optional ``gpu`` extra (transformers + a VLM
such as Qwen2.5-VL) and feeds the retrieved page images back into the model.
"""

from __future__ import annotations

from .prompt import build_messages, format_fallback_answer


def _sanitize_caption(text: str) -> str:
    return text.strip()


def answer(
    query: str,
    evidence: list[dict],
    backend: str = "fallback",
    model_name: str = "Qwen/Qwen2.5-VL-7B-Instruct",
) -> dict:
    if backend == "fallback":
        return format_fallback_answer(query, evidence)
    if backend != "vlm":
        raise ValueError(f"unknown backend: {backend}")
    try:
        from transformers import AutoModelForCausalLM, AutoProcessor
    except ImportError as exc:
        raise RuntimeError(
            "vlm generation requires the optional 'gpu' extra (torch + transformers)"
        ) from exc

    # B615: model_name is operator-chosen at runtime; a commit-hash pin is
    # not possible for arbitrary public checkpoints.
    processor = AutoProcessor.from_pretrained(model_name, revision="main")  # nosec B615
    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        device_map="auto",
        trust_remote_code=True,
        revision="main",  # nosec B615
    )
    messages = build_messages(query, evidence)
    prompt = processor.apply_chat_template(messages, add_generation_prompt=True)
    images = [_load_image(e["image_path"]) for e in evidence if e.get("image_path")]
    inputs = processor(text=prompt, images=images or None, return_tensors="pt").to(
        model.device
    )
    output = model.generate(**inputs, max_new_tokens=512, do_sample=False)
    generated = processor.decode(
        output[0][inputs.input_ids.shape[1] :], skip_special_tokens=True
    )
    return {
        "answer": _sanitize_caption(generated),
        "evidence_pages": [e["page_no"] for e in evidence],
    }


def _load_image(path: str):
    from PIL import Image

    return Image.open(path)
