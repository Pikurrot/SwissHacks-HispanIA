"""End-to-end adapters for the unchanged CRM, News, and Portfolio agents."""

from .adapters import JsonNewsAdapter, LegacyCRMAgentAdapter, LegacyNewsAgentAdapter, LegacyPortfolioAgentAdapter
from .collision_detector import CollisionDetector
from .contracts import AgentPipelineRequest, AgentPipelineResult, AgentPipelineStatus
from .pipeline import AgentIntegrationPipeline

__all__ = [
    "AgentIntegrationPipeline",
    "AgentPipelineRequest",
    "AgentPipelineResult",
    "AgentPipelineStatus",
    "CollisionDetector",
    "JsonNewsAdapter",
    "LegacyCRMAgentAdapter",
    "LegacyNewsAgentAdapter",
    "LegacyPortfolioAgentAdapter",
]
