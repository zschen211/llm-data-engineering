"""Visual verification: reverse-render bboxes onto the original image (P03 section 15)."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


def render_bboxes(image_path: Path, boxes: list[dict], out_path: Path) -> Path:
    """Draw labelled rectangles on a copy of the image and save it."""
    with Image.open(image_path) as img:
        draw = ImageDraw.Draw(img)
        for box in boxes:
            bbox = box.get("bbox")
            label = box.get("label", "")
            if not bbox:
                continue
            x, y, w, h = (float(v) for v in bbox)
            draw.rectangle([x, y, x + w, y + h], outline="red", width=3)
            if label:
                draw.text((x, max(0, y - 12)), label, fill="red")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path)
    return out_path


def render_sample_boxes(sample: dict, image_root: Path, out_dir: Path) -> Path:
    """Render the bbox recorded in a sample's meta onto its first image."""
    bbox = sample.get("meta", {}).get("bbox")
    if not bbox:
        raise ValueError(f"sample {sample['id']} has no bbox in meta")
    image = image_root / sample["image"][0]
    out = out_dir / f"{sample['id']}_bbox.png"
    return render_bboxes(image, [{"bbox": bbox, "label": str(sample.get("task_type", ""))}], out)
