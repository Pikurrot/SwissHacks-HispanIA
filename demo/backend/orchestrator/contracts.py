"""Public contracts for the message orchestration pipeline.

The orchestrator deliberately accepts plain dictionaries from agent branches.
Only this module is imported by callers, keeping agent implementations decoupled.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping


class PipelineStatus(str, Enum):
    WAITING_FOR_INPUTS = "waiting_for_inputs"
    NO_MESSAGE_RECOMMENDED = "no_message_recommended"
    READY_FOR_RM_REVIEW = "ready_for_rm_review"
    NEEDS_RM_ATTENTION = "needs_rm_attention"
    INVALID_INPUT = "invalid_input"
    GENERATION_FAILED = "generation_failed"


@dataclass(slots=True)
class OrchestrationRequest:
    client_id: str
    client_name: str
    crm_output: Mapping[str, Any]
    news_output: Mapping[str, Any] | list[Mapping[str, Any]] | None = None
    portfolio_output: Mapping[str, Any] | list[Mapping[str, Any]] | None = None
    relationship_manager_name: str = "Sarah Meier"
    rm_draft: str | None = None
    run_id: str | None = None
    draft_count: int = 2

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OrchestrationRequest":
        return cls(
            client_id=str(data.get("client_id") or data.get("clientId") or "").strip(),
            client_name=str(data.get("client_name") or data.get("clientName") or "").strip(),
            crm_output=data.get("crm_output") or data.get("crmOutput") or {},
            news_output=data.get("news_output") or data.get("newsOutput"),
            portfolio_output=data.get("portfolio_output") or data.get("portfolioOutput"),
            relationship_manager_name=str(
                data.get("relationship_manager_name")
                or data.get("relationshipManagerName")
                or "Sarah Meier"
            ),
            rm_draft=data.get("rm_draft") or data.get("rmDraft"),
            run_id=data.get("run_id") or data.get("runId"),
            draft_count=int(data.get("draft_count") or data.get("draftCount") or 2),
        )


@dataclass(slots=True)
class MessageDraft:
    label: str
    subject: str
    message: str
    style: str


@dataclass(slots=True)
class SuggestedEdit:
    original: str
    suggestion: str
    reason: str


@dataclass(slots=True)
class OrchestrationResult:
    status: PipelineStatus
    client_id: str
    run_id: str | None
    missing_inputs: list[str] = field(default_factory=list)
    internal_summary: str = ""
    drafts: list[MessageDraft] = field(default_factory=list)
    tone_notes: str = ""
    suggested_edits: list[SuggestedEdit] = field(default_factory=list)
    used_facts: list[str] = field(default_factory=list)
    omitted_sensitive_information: list[str] = field(default_factory=list)
    compliance_flags: list[str] = field(default_factory=list)
    confidence: float = 0.0
    error: str | None = None
    prepared_context: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value


@dataclass(slots=True)
class PreparedPrompt:
    system_prompt: str
    user_prompt: str
    context: dict[str, Any]
    sensitive_terms: list[str]
    missing_inputs: list[str] = field(default_factory=list)
