from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class AgentPipelineStatus(str, Enum):
    NO_COLLISION = "no_collision"
    READY_FOR_RM_REVIEW = "ready_for_rm_review"
    NEEDS_RM_ATTENTION = "needs_rm_attention"
    PARTIAL_FAILURE = "partial_failure"
    FAILED = "failed"


@dataclass(slots=True)
class AgentPipelineRequest:
    client_id: str
    client_name: str
    crm_excel_path: str
    portfolio_excel_path: str
    portfolio_sheet: str | None = None
    relationship_manager_name: str = "Sarah Meier"
    run_id: str | None = None
    draft_count: int = 2
    language: str = "en"
    dna_threshold_pct: float = 50.0


@dataclass(slots=True)
class AgentPipelineResult:
    status: AgentPipelineStatus
    client_id: str
    run_id: str | None
    portfolio_sheet: str | None = None
    dna_threshold_pct: float = 50.0
    crm_output: dict[str, Any] = field(default_factory=dict)
    news_output: dict[str, Any] = field(default_factory=dict)
    portfolio_snapshot: dict[str, Any] = field(default_factory=dict)
    collisions: list[dict[str, Any]] = field(default_factory=list)
    replacement_proposals: list[dict[str, Any]] = field(default_factory=list)
    message_results: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value

    def compact_dict(self) -> dict[str, Any]:
        """Frontend-friendly result without raw CRM, News, or portfolio dumps."""
        collisions = []
        for item in self.collisions:
            news = item.get("news", {})
            holding = item.get("holding", {})
            collisions.append({
                "event_id": item.get("event_id"),
                "headline": news.get("headline"),
                "company": news.get("company"),
                "holding": {
                    "name": holding.get("name"),
                    "isin": holding.get("isin"),
                    "current_chf": holding.get("current_chf"),
                },
                "match_method": item.get("match_method"),
            })

        recommendations = []
        for proposal in self.replacement_proposals:
            swaps = proposal.get("suggested_swaps", []) if isinstance(proposal, dict) else []
            for swap in swaps:
                alternatives = swap.get("alternatives", [])
                recommendations.append({
                    "event_id": swap.get("event_id"),
                    "action": swap.get("recommended_action"),
                    "holding": swap.get("holding"),
                    "trade_chf": swap.get("trade_chf"),
                    "alternative": alternatives[0] if alternatives else None,
                    "selection_note": swap.get("selection_note"),
                    "rejected_alternatives": swap.get("rejected_alternatives", []),
                    "mandate_check": swap.get("mandate_check"),
                })

        messages = []
        for result in self.message_results:
            messages.append({
                "status": result.get("status"),
                "internal_summary": result.get("internal_summary"),
                "drafts": result.get("drafts", []),
                "compliance_flags": result.get("compliance_flags", []),
                "error": result.get("error"),
            })

        output: dict[str, Any] = {
            "status": self.status.value,
            "client_id": self.client_id,
            "run_id": self.run_id,
        }
        if self.status is AgentPipelineStatus.NO_COLLISION:
            output["message"] = "No relevant news conflict matched a current holding; no action was taken."
            return output
        if self.portfolio_sheet:
            output["portfolio_sheet"] = self.portfolio_sheet
        if collisions:
            output["dna_threshold_pct"] = self.dna_threshold_pct
            output["collision_count"] = len(collisions)
            output["collisions"] = collisions
        if recommendations:
            output["recommendations"] = recommendations
        if messages:
            output["messages"] = messages
        if self.errors:
            output["errors"] = self.errors
        return output
