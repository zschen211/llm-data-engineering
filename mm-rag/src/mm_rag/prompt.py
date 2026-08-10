"""Prompt construction: anti-noise constraints for the generation stage.

The system prompt explicitly tells the model that some images may be
table-of-contents pages and must be ignored, and that answers must cite
evidence pages (P05 section 10).
"""

from __future__ import annotations

# VLM template token, not a credential (B105 name-pattern false positive)
IMAGE_TOKEN = "<image>"  # nosec B105

SYSTEM_PROMPT = (
    "You are a financial analyst assistant. The user provides a question and "
    "several page images from a company annual report. Some images may be "
    "table-of-contents pages or cover pages: ignore them and answer strictly "
    "from pages that contain actual data. Base every numeric, trend or "
    "conclusion on the provided evidence and list the page numbers you used."
)

ANSWER_TEMPLATE = (
    "结论：{conclusion}\n\n证据页：{page_numbers}\n\n限制：以上结论基于检索到的页面，"
    "如需精确数值请以页面截图为准。"
)


def build_messages(query: str, evidence: list[dict]) -> list[dict]:
    """Build chat messages with one image token per evidence page."""
    images = "".join(f"{IMAGE_TOKEN}\n" for _ in evidence)
    user_content = f"{images}{query}\n请基于提供的页面证据回答，并给出使用的页码。"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def format_fallback_answer(query: str, evidence: list[dict]) -> dict:
    """Deterministic evidence-organizing answer used when no VLM is available."""
    page_numbers = ", ".join(f"p{int(e['page_no'])}" for e in evidence) or "无"
    conclusion = f"基于 {len(evidence)} 页证据组织的候选回答（未调用视觉模型，请接入 VLM 后生成最终结论）"
    return {
        "answer": ANSWER_TEMPLATE.format(
            conclusion=conclusion, page_numbers=page_numbers
        ),
        "evidence_pages": [e["page_no"] for e in evidence],
    }
