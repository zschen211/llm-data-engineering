"""LLaVA conversation templates and task prompt builders.

Templates are controlled so generated samples stay uniform and checkable.
"""
from __future__ import annotations

IMAGE_TOKEN = "<image>"
MULTI_IMAGE_TOKEN = "<image>"

TEMPLATE_NAMES = (
    "description",
    "counting",
    "recognition",
    "inference",
    "ocr_summary",
    "document_qa",
    "chart_reading",
    "chart_comparison",
    "region_grounding",
    "multi_image_comparison",
)


def build_conversations(question: str, answer: str, n_images: int = 1) -> list[dict]:
    """Wrap a question/answer pair into LLaVA conversation format.

    For n_images > 1 the human turn interleaves image tokens (one per image).
    """
    image_part = f"{MULTI_IMAGE_TOKEN}\n" * n_images
    human_value = f"{image_part}{question}"
    return [
        {"from": "human", "value": human_value.strip()},
        {"from": "gpt", "value": answer},
    ]


def _human(question: str, n_images: int = 1) -> str:
    image_part = f"{MULTI_IMAGE_TOKEN}\n" * n_images
    return f"{image_part}{question}".strip()


def describe_scene(caption: str) -> dict:
    """Template: image description from a (re-)caption."""
    return {
        "template": "description",
        "question": "Describe this image in detail.",
        "answer": caption,
        "n_images": 1,
    }


def count_objects(subject: str, count: int) -> dict:
    return {
        "template": "counting",
        "question": f"How many {subject} are there in the image?",
        "answer": f"There are {count} {subject} in the image.",
        "n_images": 1,
    }


def recognize_region(region: str, label: str) -> dict:
    return {
        "template": "recognition",
        "question": f"What is the most prominent object on the {region}?",
        "answer": f"The most prominent object on the {region} is {label}.",
        "n_images": 1,
    }


def infer_scene(place: str, reason: str) -> dict:
    return {
        "template": "inference",
        "question": "Is this more likely an indoor or outdoor scene, and why?",
        "answer": f"This is more likely an {place} scene because {reason}.",
        "n_images": 1,
    }


def ocr_summary(ocr_text: str) -> dict:
    return {
        "template": "ocr_summary",
        "question": "Read and summarize the text in this document image.",
        "answer": f"The document contains the following text: {ocr_text}",
        "n_images": 1,
    }


def document_qa(question: str, evidence: str) -> dict:
    return {
        "template": "document_qa",
        "question": question,
        "answer": evidence,
        "n_images": 1,
    }


def chart_reading(chart_kind: str, trend: str) -> dict:
    return {
        "template": "chart_reading",
        "question": f"Describe the structure and main trend of this {chart_kind}.",
        "answer": trend,
        "n_images": 1,
    }


def chart_comparison(compared: str, conclusion: str) -> dict:
    return {
        "template": "chart_comparison",
        "question": f"Compare the {compared} shown in this chart.",
        "answer": conclusion,
        "n_images": 1,
    }


def region_grounding(question: str, answer: str) -> dict:
    return {
        "template": "region_grounding",
        "question": question,
        "answer": answer,
        "n_images": 1,
    }


def multi_image_comparison(question: str, answer: str) -> dict:
    return {
        "template": "multi_image_comparison",
        "question": question,
        "answer": answer,
        "n_images": 2,
    }
