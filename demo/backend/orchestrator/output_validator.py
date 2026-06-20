from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from .contracts import MessageDraft, SuggestedEdit
from .errors import LLMOutputError


def parse_json_response(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LLMOutputError(f"LLM returned invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise LLMOutputError("LLM output must be a JSON object")
    return value


def _all_numbers(value: Any) -> list[float]:
    found: list[float] = []
    if isinstance(value, bool):
        return found
    if isinstance(value, (int, float)):
        found.append(float(value))
    elif isinstance(value, dict):
        for item in value.values():
            found.extend(_all_numbers(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_all_numbers(item))
    return found


def _currency_claims(message: str) -> list[float]:
    claims = []
    pattern = re.compile(r"\b(?:CHF|USD|EUR|GBP)\s*([0-9][0-9\s,.'’]*)", re.IGNORECASE)
    for match in pattern.finditer(message):
        raw = re.sub(r"[\s'’]", "", match.group(1)).rstrip(".,")
        if not raw:
            continue
        if "," in raw and "." in raw:
            if raw.rfind(".") > raw.rfind(","):
                normalized = raw.replace(",", "")
            else:
                normalized = raw.replace(".", "").replace(",", ".")
        elif "," in raw:
            tail = raw.rsplit(",", 1)[1]
            normalized = raw.replace(",", ".") if len(tail) == 2 else raw.replace(",", "")
        elif "." in raw:
            tail = raw.rsplit(".", 1)[1]
            normalized = raw if len(tail) == 2 else raw.replace(".", "")
        else:
            normalized = raw
        try:
            claims.append(float(normalized))
        except ValueError:
            continue
    return claims


def validate_output(
    value: dict[str, Any],
    context: dict[str, Any],
    sensitive_terms: Iterable[str],
) -> tuple[
    str,
    list[MessageDraft],
    str,
    list[SuggestedEdit],
    list[str],
    list[str],
    list[str],
    float,
]:
    raw_drafts = value.get("drafts")
    if not isinstance(raw_drafts, list) or not raw_drafts:
        raise LLMOutputError("LLM output must contain at least one draft")
    drafts = []
    for item in raw_drafts[:3]:
        if not isinstance(item, dict):
            raise LLMOutputError("Every draft must be an object")
        subject = str(item.get("subject") or "").strip()
        message = str(item.get("message") or "").strip()
        if not subject or not message:
            raise LLMOutputError("Every draft requires subject and message")
        drafts.append(MessageDraft(
            label=str(item.get("label") or "Draft"),
            subject=subject,
            message=message,
            style=str(item.get("style") or ""),
        ))

    suggested_edits = []
    for item in value.get("suggested_edits") or []:
        if isinstance(item, dict):
            suggested_edits.append(SuggestedEdit(
                original=str(item.get("original") or ""),
                suggestion=str(item.get("suggestion") or ""),
                reason=str(item.get("reason") or ""),
            ))

    flags = [str(flag) for flag in value.get("compliance_flags") or []]
    if context.get("news_event", {}).get("is_mock"):
        flags.append("DEMO ALERT — draft must not be sent to a client")
    allowed_numbers = _all_numbers(context)
    forbidden_phrases = (
        "guaranteed return", "will definitely", "trade has been placed",
        "we have executed", "order has been executed",
    )
    internal_labels = ("safe_reason", "client_id", "event_id", "mandate_check")
    sensitive_lower = [str(term).lower() for term in sensitive_terms]
    for draft in drafts:
        lower = draft.message.lower()
        for phrase in forbidden_phrases:
            if phrase in lower:
                flags.append(f"Prohibited certainty or execution phrase: '{phrase}'")
        for label in internal_labels:
            if label in lower:
                flags.append(f"Internal schema label exposed in draft: '{label}'")
        for term in sensitive_lower:
            if term and re.search(rf"\b{re.escape(term)}\b", lower):
                flags.append(f"Sensitive CRM term exposed in draft: '{term}'")
        for claim in _currency_claims(draft.message):
            if not any(abs(claim - allowed) <= max(1.0, abs(allowed) * 0.001) for allowed in allowed_numbers):
                flags.append(f"Unverified currency amount in draft: {claim:g}")

    confidence = float(value.get("confidence") or 0.0)
    confidence = max(0.0, min(confidence, 1.0))
    return (
        str(value.get("internal_summary") or ""),
        drafts,
        str(value.get("tone_notes") or ""),
        suggested_edits,
        [str(item) for item in value.get("used_facts") or []],
        [str(item) for item in value.get("omitted_sensitive_information") or []],
        list(dict.fromkeys(flags)),
        confidence,
    )
