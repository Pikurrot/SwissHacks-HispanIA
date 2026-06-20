from __future__ import annotations

from typing import Any
import re

from .contracts import OrchestrationRequest, PreparedPrompt
from .errors import InputValidationError
from .normalizers import (
    identity_from,
    normalize_crm,
    normalize_news,
    normalize_portfolio,
    safe_relevance_reason,
)
from .prompt_builder import SYSTEM_PROMPT, build_user_prompt


def _validate_identity(request: OrchestrationRequest) -> None:
    if not request.client_id:
        raise InputValidationError("client_id is required")
    if not request.client_name:
        raise InputValidationError("client_name is required")
    identities = {
        name: identity
        for name, identity in (
            ("CRM Agent", identity_from(request.crm_output)),
            ("News Agent", identity_from(request.news_output)),
            ("Portfolio Agent", identity_from(request.portfolio_output)),
        )
        if identity
    }
    mismatched = {name: value for name, value in identities.items() if value != request.client_id}
    if mismatched:
        details = ", ".join(f"{name}={value}" for name, value in mismatched.items())
        raise InputValidationError(
            f"Cross-client input mismatch: request={request.client_id}; {details}"
        )


def _missing_context(news: dict[str, Any] | None, portfolio: dict[str, Any] | None) -> list[str]:
    missing = []
    if not news:
        missing.append("news_output")
    else:
        if not news.get("headline"):
            missing.append("news_output.headline")
        if not news.get("sources"):
            missing.append("news_output.sources")
    if not portfolio:
        missing.append("portfolio_output")
    else:
        holding = portfolio.get("holding", {})
        if not holding.get("name") and not holding.get("isin"):
            missing.append("portfolio_output.holding")
        if not portfolio.get("recommended_action"):
            missing.append("portfolio_output.recommended_action")
    return missing


def _name_tokens(value: str) -> set[str]:
    stop = {"the", "and", "plc", "inc", "ag", "sa", "ltd", "holding", "company", "sector"}
    return {
        token for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) >= 4 and token not in stop
    }


def _portfolio_is_linked_to_news(news: dict[str, Any], portfolio: dict[str, Any]) -> bool:
    if portfolio.get("event_id") and portfolio["event_id"] == news.get("event_id"):
        return True
    portfolio_isins = {
        str(portfolio.get("holding", {}).get("isin") or ""),
        *{str(item.get("isin") or "") for item in portfolio.get("alternatives", [])},
    } - {""}
    affected_isins = {str(value) for value in news.get("affected_isins", []) if value}
    if affected_isins:
        return bool(portfolio_isins.intersection(affected_isins))

    news_tokens = _name_tokens(f"{news.get('company', '')} {news.get('headline', '')}")
    portfolio_tokens = _name_tokens(
        " ".join(
            [str(portfolio.get("holding", {}).get("name") or "")]
            + [str(item.get("name") or "") for item in portfolio.get("alternatives", [])]
        )
    )
    return bool(news_tokens.intersection(portfolio_tokens))


def prepare_prompt(request: OrchestrationRequest) -> PreparedPrompt:
    _validate_identity(request)
    crm = normalize_crm(request.crm_output, request.client_id, request.client_name)
    news = normalize_news(request.news_output)
    portfolio = normalize_portfolio(request.portfolio_output, news)
    if news and portfolio and not _portfolio_is_linked_to_news(news, portfolio):
        raise InputValidationError(
            "News and Portfolio outputs are not linked: provide a matching affected ISIN, "
            "company name, or the same event_id in both outputs"
        )
    missing = _missing_context(news, portfolio)
    if not request.crm_output:
        missing.insert(0, "crm_output")
    if missing:
        return PreparedPrompt("", "", {}, crm["sensitive_terms"], missing)

    assert news is not None and portfolio is not None
    safe_context = {
        "client": {
            "client_id": request.client_id,
            "client_name": request.client_name,
            "mandate": portfolio.get("mandate") or crm.get("mandate"),
            "risk_tolerance": crm.get("risk_tolerance"),
            "communication": crm.get("communication"),
        },
        "personal_relevance": {
            "safe_reason": safe_relevance_reason(crm, news),
            "crm_confidence": crm.get("confidence"),
            "crm_source_entries": crm.get("source_entries"),
        },
        "news_event": news,
        "portfolio_impact": portfolio.get("holding"),
        "recommendation": {
            "action": portfolio.get("recommended_action"),
            "rationale": portfolio.get("rationale"),
            "urgency": portfolio.get("urgency"),
            "trade_chf": portfolio.get("trade_chf"),
            "current_cio_rating": portfolio.get("cio_rating"),
            "dna_alignment_confidence_pct": portfolio.get("dna_alignment_confidence_pct"),
            "alternatives": portfolio.get("alternatives"),
            "mandate_check": portfolio.get("mandate_check"),
        },
        "guardrails": {
            "draft_only": True,
            "human_approval_required": True,
            "client_decides": True,
            "do_not_expose_private_crm_details": True,
        },
        "trace": {
            "run_id": request.run_id,
            "event_id": news.get("event_id"),
        },
    }
    return PreparedPrompt(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=build_user_prompt(
            safe_context,
            request.relationship_manager_name,
            request.draft_count,
            request.rm_draft,
        ),
        context=safe_context,
        sensitive_terms=crm["sensitive_terms"],
    )
