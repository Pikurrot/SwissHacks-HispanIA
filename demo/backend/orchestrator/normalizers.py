"""Adapters from branch-specific agent dictionaries to one stable context."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def first(data: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    return default


def unwrap(raw: Any) -> Any:
    if not isinstance(raw, Mapping):
        return raw
    for key in ("payload", "data", "result"):
        value = raw.get(key)
        if isinstance(value, (Mapping, list)):
            return value
    return raw


def identity_from(raw: Any) -> str | None:
    if not isinstance(raw, Mapping):
        return None
    value = first(raw, "client_id", "clientId")
    if value:
        return str(value)
    nested = unwrap(raw)
    if nested is not raw and isinstance(nested, Mapping):
        value = first(nested, "client_id", "clientId")
        return str(value) if value else None
    return None


def normalize_crm(raw: Mapping[str, Any], client_id: str, client_name: str) -> dict[str, Any]:
    data = unwrap(raw)
    if not isinstance(data, Mapping):
        data = {}
    values = first(data, "values", default={}) or {}
    behavior = first(data, "investmentBehavior", "investment_behavior", default={}) or {}
    communication = first(data, "communicationStyle", "communication_style", default={}) or {}

    priorities = list(first(values, "priorities", default=[]) or [])
    red_lines = list(first(values, "redLines", "red_lines", default=[]) or [])
    esg_focus = list(first(values, "esgFocus", "esg_focus", default=[]) or [])

    sensitive_text = " ".join(
        [str(item) for item in first(data, "keyQuotes", "key_quotes", default=[]) or []]
        + [str(item) for item in first(data, "lifeEvents", "life_events", default=[]) or []]
        + red_lines
        + priorities
    ).lower()
    sensitive_terms = [
        term
        for term in (
            "daughter", "son", "chloe", "diagnosed", "diagnosis", "parkinson",
            "alzheimer", "cancer", "illness", "disease", "medical condition",
        )
        if term in sensitive_text
    ]

    return {
        "client_id": client_id,
        "client_name": client_name,
        "mandate": first(behavior, "mandate", default=""),
        "risk_tolerance": first(behavior, "riskTolerance", "risk_tolerance", default=""),
        "communication": {
            "language": first(communication, "language", default="en"),
            "tone": first(communication, "tone", default="formal"),
            "preferred_style": first(communication, "preferred", default="data-driven"),
            "format_preference": first(
                communication, "formatPreference", "format_preference", default="concise paragraphs"
            ),
        },
        "priorities": priorities,
        "red_lines": red_lines,
        "preferred_sectors": list(
            first(values, "preferredSectors", "preferred_sectors", default=[]) or []
        ),
        "avoided_sectors": list(
            first(values, "avoidedSectors", "avoided_sectors", default=[]) or []
        ),
        "esg_focus": esg_focus,
        "confidence": float(first(data, "confidence", default=0.0) or 0.0),
        "source_entries": list(first(data, "sourcedFrom", "sourced_from", default=[]) or []),
        "sensitive_terms": sensitive_terms,
    }


def _news_candidates(raw: Any) -> list[Mapping[str, Any]]:
    data = unwrap(raw)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, Mapping)]
    if not isinstance(data, Mapping):
        return []
    for key in ("alerts", "articles", "events", "news"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, Mapping)]
    alert = data.get("alert")
    if isinstance(alert, Mapping):
        return [alert]
    return [data]


def _news_rank(item: Mapping[str, Any]) -> tuple[float, int, int]:
    relevance = float(first(item, "relevance_score", "relevanceScore", default=0.0) or 0.0)
    severity_name = str(first(item, "severity", default="medium")).lower()
    severity = {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(severity_name, 2)
    affected = first(item, "affected_isins", "affectedISINs", default=[]) or []
    return relevance, severity, 1 if affected else 0


def normalize_news(raw: Any) -> dict[str, Any] | None:
    candidates = _news_candidates(raw)
    if not candidates:
        return None
    selected = max(candidates, key=_news_rank)
    source_value = first(selected, "source", default="")
    if isinstance(source_value, Mapping):
        source_name = first(source_value, "title", "name", default="")
        source_url = first(source_value, "url", default="")
    else:
        source_name = str(source_value or "")
        source_url = ""
    sources = first(selected, "sources", default=[]) or []
    if not sources and source_name:
        sources = [{"publisher": source_name, "url": first(selected, "url", default=source_url)}]

    return {
        "event_id": str(first(selected, "id", "event_id", "eventId", default="")),
        "headline": str(first(selected, "headline", "title", default="")),
        "summary": str(first(selected, "summary", "description", default="")),
        "company": str(first(selected, "company", "issuer", default="")),
        "event_type": str(first(selected, "event_type", "eventType", "alert_type", "alertType", default="market")),
        "severity": str(first(selected, "severity", default="medium")),
        "sentiment": str(first(selected, "sentiment", default="neutral")),
        "published_at": str(first(selected, "published_at", "publishedAt", "dateTime", default="")),
        "relevance_score": float(first(selected, "relevance_score", "relevanceScore", default=0.0) or 0.0),
        "confidence": float(first(selected, "confidence", default=0.0) or 0.0),
        "affected_isins": list(first(selected, "affected_isins", "affectedISINs", default=[]) or []),
        "affected_sectors": list(first(selected, "affected_sectors", "affectedSectors", default=[]) or []),
        "sources": sources,
        "is_mock": bool(first(selected, "is_mock", "isMock", default=False)),
    }


def _portfolio_candidates(raw: Any) -> list[Mapping[str, Any]]:
    data = unwrap(raw)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, Mapping)]
    if not isinstance(data, Mapping):
        return []
    for key in ("proposals", "suggested_swaps", "suggestedSwaps", "swaps", "recommendations"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, Mapping)]
    return [data]


def _matches_news(item: Mapping[str, Any], affected_isins: set[str]) -> bool:
    from_isin = str(first(item, "from_isin", "fromISIN", "sell_isin", "sellISIN", default=""))
    holding = first(item, "holding", "affected_holding", "affectedHolding", default={}) or {}
    holding_isin = str(first(holding, "isin", "ISIN", default="")) if isinstance(holding, Mapping) else ""
    return bool(affected_isins.intersection({from_isin, holding_isin}))


def normalize_portfolio(raw: Any, news: dict[str, Any] | None) -> dict[str, Any] | None:
    candidates = _portfolio_candidates(raw)
    if not candidates:
        return None
    affected = set(news.get("affected_isins", [])) if news else set()
    selected = next((item for item in candidates if _matches_news(item, affected)), candidates[0])

    holding = first(selected, "holding", "affected_holding", "affectedHolding", default={}) or {}
    if not isinstance(holding, Mapping):
        holding = {}
    replacement = first(selected, "replacement", default={}) or {}
    if not isinstance(replacement, Mapping):
        replacement = {}
    mandate_check = first(selected, "mandate_check", "mandateCheck", default={}) or {}
    if not isinstance(mandate_check, Mapping):
        mandate_check = {}

    alternatives_raw = first(selected, "alternatives", "candidates", default=[]) or []
    alternatives = []
    if isinstance(alternatives_raw, list):
        for item in alternatives_raw[:3]:
            if isinstance(item, Mapping):
                alternatives.append({
                    "name": first(item, "name", "Issuer", "issuer", "to_name", "toName", default=""),
                    "isin": first(item, "isin", "ISIN", "to_isin", "toISIN", default=""),
                    "cio_rating": first(item, "cio_rating", "cioRating", "Rating", "rating", default=""),
                    "cio_view": first(item, "cio_view", "cioView", default=""),
                    "allocation_pct": first(item, "allocation_pct", "allocationPct", default=None),
                    "match_score": first(item, "match_score", "matchScore", default=None),
                })

    direct_to_name = first(selected, "to_name", "toName", default="")
    if direct_to_name and not alternatives:
        alternatives.append({
            "name": direct_to_name,
            "isin": first(selected, "to_isin", "toISIN", default=""),
            "cio_rating": first(selected, "cio_rating", "cioRating", default=""),
            "cio_view": first(selected, "cio_view", "cioView", default=""),
            "allocation_pct": 100,
            "match_score": None,
        })
    elif replacement and not alternatives:
        alternatives.append({
            "name": first(replacement, "name", "issuer", default=""),
            "isin": first(replacement, "isin", "ISIN", default=""),
            "cio_rating": first(replacement, "cio_rating", "cioRating", "rating", default=""),
            "cio_view": first(replacement, "cio_view", "cioView", default=""),
            "allocation_pct": 100,
            "match_score": first(replacement, "match_score", "matchScore", default=None),
        })

    return {
        "mandate": first(selected, "mandate", default=""),
        "holding": {
            "name": first(holding, "name", "issuer", default=first(selected, "from_name", "fromName", default="")),
            "isin": first(holding, "isin", "ISIN", default=first(selected, "from_isin", "fromISIN", default="")),
            "current_chf": first(
                holding, "current_chf", "currentCHF",
                default=first(selected, "from_current_chf", "fromCurrentCHF", "trade_chf", "tradeCHF", default=None),
            ),
            "portfolio_weight_pct": first(holding, "portfolio_weight_pct", "portfolioWeightPct", "portfolioWeight", default=None),
        },
        "recommended_action": str(first(selected, "recommended_action", "recommendedAction", "action", default="review")),
        "rationale": str(first(selected, "rationale", "explanation", "mandate_note", "mandateNote", default="")),
        "urgency": str(first(selected, "urgency", "priority", default="medium")),
        "trade_chf": first(selected, "trade_chf", "tradeCHF", "from_current_chf", "fromCurrentCHF", default=None),
        "cio_rating": str(first(selected, "cio_rating", "cioRating", default="")),
        "alternatives": alternatives,
        "mandate_check": {
            "before_valid": first(mandate_check, "before_valid", "beforeValid", default=None),
            "after_valid": first(
                mandate_check, "after_valid", "afterValid",
                default=first(selected, "mandate_compliant", "mandateCompliant", default=None),
            ),
            "drift_after_pp": first(mandate_check, "drift_after_pp", "driftAfterPp", "driftAfter", default=None),
            "note": first(mandate_check, "note", default=first(selected, "mandate_note", "mandateNote", default="")),
        },
    }


def safe_relevance_reason(crm: dict[str, Any], news: dict[str, Any]) -> str:
    combined = " ".join(
        crm.get("priorities", []) + crm.get("red_lines", []) + crm.get("esg_focus", [])
        + [news.get("headline", ""), news.get("summary", "")]
    ).lower()
    medical = ("parkinson", "neurolog", "health", "medical", "disease", "pharma", "research")
    environmental = ("climate", "forest", "palm oil", "environment", "sustainab", "esg")
    governance = ("governance", "labour", "labor", "reputation", "ethical", "wage", "fraud")
    if any(term in combined for term in medical):
        return "The event may conflict with the client's stated healthcare and research priorities."
    if any(term in combined for term in environmental):
        return "The event is relevant to the client's stated sustainability priorities."
    if any(term in combined for term in governance):
        return "The event may conflict with the client's stated governance and reputation standards."
    return "The event is relevant to the client's documented investment preferences."
