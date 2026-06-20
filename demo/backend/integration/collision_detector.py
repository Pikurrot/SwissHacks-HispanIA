from __future__ import annotations

import re
import unicodedata
from typing import Any, Mapping


_CORPORATE_STOP_WORDS = {
    "ag", "company", "corp", "corporation", "group", "holding", "holdings",
    "inc", "international", "limited", "ltd", "nv", "plc", "sa", "se", "the",
}


def _tokens(value: str) -> set[str]:
    plain = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower()
    return {
        token for token in re.findall(r"[a-z0-9]+", plain)
        if len(token) >= 3 and token not in _CORPORATE_STOP_WORDS
    }


class CollisionDetector:
    """Matches CRM-classified conflict news to securities the client actually holds."""

    def detect(
        self,
        news_output: Mapping[str, Any],
        portfolio_snapshot: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        news_items = news_output.get("analysis", [])
        holdings = portfolio_snapshot.get("holdings", [])
        collisions: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        for news in news_items:
            if not isinstance(news, Mapping) or not self._is_conflict(news):
                continue
            affected = {
                str(value).upper() for value in (
                    news.get("affectedISINs") or news.get("affected_isins") or []
                ) if value
            }
            company_text = f"{news.get('company', '')} {news.get('headline', '')}"
            news_tokens = _tokens(company_text)

            for holding in holdings:
                if not isinstance(holding, Mapping):
                    continue
                isin = str(holding.get("isin") or "").upper()
                holding_tokens = _tokens(str(holding.get("name") or ""))
                isin_match = bool(isin and isin in affected)
                shared = news_tokens.intersection(holding_tokens)
                name_match = bool(shared) and len(shared) / max(1, len(holding_tokens)) >= 0.5
                if not isin_match and not name_match:
                    continue
                event_id = str(news.get("id") or news.get("event_id") or news.get("headline") or "")
                key = (event_id, isin or str(holding.get("name")))
                if key in seen:
                    continue
                seen.add(key)
                collisions.append({
                    "event_id": event_id,
                    "news": dict(news),
                    "holding": dict(holding),
                    "match_method": "isin" if isin_match else "company_name",
                    "matched_terms": sorted(shared),
                })
        return collisions

    @staticmethod
    def _is_conflict(news: Mapping[str, Any]) -> bool:
        alert_type = str(news.get("alertType") or news.get("alert_type") or "").lower()
        alignment = str(news.get("belief_alignment") or "").lower()
        return alert_type in {"conflict", "mandate_conflict", "risk"} or alignment == "negative"
