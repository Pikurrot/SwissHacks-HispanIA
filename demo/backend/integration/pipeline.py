from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from typing import Any

from demo.backend.orchestrator import MessageOrchestrator, OrchestrationRequest
from demo.backend.orchestrator.llm_client import LLMClient

from .adapters import CRMProvider, NewsProvider, PortfolioProvider
from .collision_detector import CollisionDetector
from .contracts import AgentPipelineRequest, AgentPipelineResult, AgentPipelineStatus


class AgentIntegrationPipeline:
    def __init__(
        self,
        crm: CRMProvider,
        news: NewsProvider,
        portfolio: PortfolioProvider,
        message_llm: LLMClient,
        collision_detector: CollisionDetector | None = None,
    ) -> None:
        self.crm = crm
        self.news = news
        self.portfolio = portfolio
        self.messages = MessageOrchestrator(message_llm)
        self.collision_detector = collision_detector or CollisionDetector()

    def run(self, request: AgentPipelineRequest) -> AgentPipelineResult:
        result = AgentPipelineResult(
            status=AgentPipelineStatus.FAILED,
            client_id=request.client_id,
            run_id=request.run_id,
            dna_threshold_pct=max(0.0, min(float(request.dna_threshold_pct), 100.0)),
        )
        try:
            dna = self.crm.extract(
                request.crm_excel_path,
                request.client_id,
                request.client_name,
            )
            dna = self._force_language(dna, request.language)
            result.crm_output = dna

            # Once CRM DNA exists, News discovery and current portfolio loading are independent.
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="agent-pipeline") as pool:
                news_future = pool.submit(self.news.fetch, request.client_id, dna)
                portfolio_future = pool.submit(
                    self.portfolio.snapshot,
                    request.portfolio_excel_path,
                    request.client_id,
                    dna,
                    request.portfolio_sheet,
                )
                result.news_output = news_future.result()
                result.portfolio_snapshot = portfolio_future.result()

            result.portfolio_sheet = str(result.portfolio_snapshot.get("portfolio_sheet") or "")
            result.collisions = self.collision_detector.detect(
                result.news_output,
                result.portfolio_snapshot,
            )
            if not result.collisions:
                result.status = AgentPipelineStatus.NO_COLLISION
                return result

            for collision in result.collisions:
                try:
                    proposal = self.portfolio.propose_replacement(
                        request.portfolio_excel_path,
                        result.portfolio_sheet,
                        collision["holding"],
                        dna,
                        collision,
                        result.portfolio_snapshot,
                        result.dna_threshold_pct,
                    )
                    result.replacement_proposals.append(proposal)
                    linked_news = self._linked_news(request.client_id, collision)
                    message_result = self.messages.run(OrchestrationRequest(
                        client_id=request.client_id,
                        client_name=request.client_name,
                        crm_output=dna,
                        news_output=linked_news,
                        portfolio_output=proposal,
                        relationship_manager_name=request.relationship_manager_name,
                        run_id=request.run_id,
                        draft_count=request.draft_count,
                    ))
                    result.message_results.append(message_result.to_dict())
                except Exception as exc:
                    result.errors.append(
                        f"Collision {collision.get('event_id', 'unknown')}: {exc}"
                    )

            result.status = self._final_status(result.message_results, result.errors)
            return result
        except Exception as exc:
            result.errors.append(str(exc))
            result.status = AgentPipelineStatus.FAILED
            return result

    @staticmethod
    def _force_language(dna: dict[str, Any], language: str) -> dict[str, Any]:
        value = deepcopy(dna)
        style = value.get("communicationStyle")
        if not isinstance(style, dict):
            style = {}
            value["communicationStyle"] = style
        style["language"] = language or "en"
        return value

    @staticmethod
    def _linked_news(client_id: str, collision: dict[str, Any]) -> dict[str, Any]:
        news = dict(collision["news"])
        holding_isin = collision["holding"].get("isin")
        affected = list(news.get("affectedISINs") or news.get("affected_isins") or [])
        if holding_isin and holding_isin not in affected:
            affected.append(holding_isin)
        news["affectedISINs"] = affected
        return {"client_id": client_id, "analysis": [news]}

    @staticmethod
    def _final_status(message_results: list[dict[str, Any]], errors: list[str]) -> AgentPipelineStatus:
        if errors and not message_results:
            return AgentPipelineStatus.FAILED
        if errors:
            return AgentPipelineStatus.PARTIAL_FAILURE
        statuses = {str(item.get("status")) for item in message_results}
        if "needs_rm_attention" in statuses or "generation_failed" in statuses:
            return AgentPipelineStatus.NEEDS_RM_ATTENTION
        if "ready_for_rm_review" in statuses:
            return AgentPipelineStatus.READY_FOR_RM_REVIEW
        return AgentPipelineStatus.NEEDS_RM_ATTENTION
