from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from app.errors import ApiError

_WORD = re.compile(r"\b[\w]+(?:['’][\w]+)?\b", re.UNICODE)


def apply_safe_word_removals(
    text: str, count: int, proposals: Sequence[dict[str, Any]]
) -> tuple[str, list[str]]:
    """Apply only exact provider-proposed source-word spans and verify the delta."""
    if not isinstance(proposals, Sequence) or isinstance(proposals, (str, bytes)):
        raise ApiError(
            "safe_edit_unavailable",
            "The provider returned no valid safety proposals. No words were changed.",
            409,
        )
    if len(proposals) != count:
        raise ApiError(
            "safe_edit_unavailable",
            "I could not confirm exactly the requested number of safe word removals. No words were changed.",
            409,
            {"requested": count, "confirmed": len(proposals)},
        )

    spans: list[tuple[int, int, str]] = []
    for proposal in proposals:
        if not isinstance(proposal, dict):
            raise ApiError(
                "safe_edit_unavailable",
                "A proposed removal had no verifiable safety verdict. No words were changed.",
                409,
            )
        start = proposal.get("start")
        end = proposal.get("end")
        word = proposal.get("word")
        safety_reason = proposal.get("justification")
        if (
            proposal.get("safe") is not True
            or proposal.get("confidence") != "high"
            or not isinstance(safety_reason, str)
            or not safety_reason.strip()
        ):
            raise ApiError(
                "safe_edit_unavailable",
                "A proposed word was not explicitly approved as safe with high confidence. No words were changed.",
                409,
            )
        if (
            isinstance(start, bool)
            or not isinstance(start, int)
            or isinstance(end, bool)
            or not isinstance(end, int)
            or not isinstance(word, str)
            or start < 0
            or end <= start
            or end > len(text)
            or text[start:end] != word
            or _WORD.fullmatch(word) is None
        ):
            raise ApiError(
                "safe_edit_unavailable",
                "The proposed removals did not match exact words in the source. No words were changed.",
                409,
            )
        spans.append((start, end, word))

    spans.sort(key=lambda span: span[0])
    if any(left[1] > right[0] for left, right in zip(spans, spans[1:])):
        raise ApiError(
            "safe_edit_unavailable",
            "The proposed removals overlapped. No words were changed.",
            409,
        )

    edited = text
    for start, end, _ in reversed(spans):
        left = start
        right = end
        if right < len(edited) and edited[right].isspace():
            right += 1
        elif left > 0 and edited[left - 1].isspace():
            left -= 1
        edited = edited[:left] + edited[right:]

    removed = [word for _, _, word in spans]
    if len(_WORD.findall(text)) - len(_WORD.findall(edited)) != count:
        raise ApiError(
            "edit_validation_failed",
            "The edit could not be validated safely. No edited result was saved.",
            500,
        )
    return edited, removed


def require_approved_edit_review(review: Any) -> None:
    """Reject edits without an explicit, high-confidence, issue-free review."""
    if not isinstance(review, dict):
        raise ApiError(
            "safe_edit_unavailable",
            "The final comparison returned no valid safety verdict. No edited result was saved.",
            409,
        )
    approved = review.get("approved") is True
    high_confidence = review.get("confidence") == "high"
    issues = review.get("issues")
    justification = review.get("justification")
    if (
        not approved
        or not high_confidence
        or not isinstance(issues, list)
        or issues
        or not isinstance(justification, str)
        or not justification.strip()
    ):
        raise ApiError(
            "safe_edit_unavailable",
            "The final comparison could not confidently approve the edit. No edited result was saved.",
            409,
        )
