import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from demo.backend.integration import (
    AgentIntegrationPipeline,
    AgentPipelineRequest,
    AgentPipelineStatus,
    CollisionDetector,
    JsonNewsAdapter,
    LegacyPortfolioAgentAdapter,
)
from demo.backend.integration.__main__ import _important_warnings
from demo.backend.orchestrator import StaticLLMClient


DNA = {
    "values": {
        "priorities": ["Healthcare research"],
        "redLines": ["Avoid companies abandoning neurological research"],
        "preferredSectors": ["Healthcare"],
        "avoidedSectors": [],
        "esgFocus": ["Medical research"],
    },
    "investmentBehavior": {
        "riskTolerance": "moderate",
        "mandate": "Global Balanced Growth",
    },
    "communicationStyle": {"language": "en", "tone": "formal"},
    "confidence": 0.9,
}


class FakeCRM:
    def extract(self, excel_path, client_id, client_name):
        return DNA


class GermanCRM:
    def extract(self, excel_path, client_id, client_name):
        value = json.loads(json.dumps(DNA))
        value["communicationStyle"]["language"] = "de"
        return value


class FakeNews:
    def __init__(self, conflict=True):
        self.conflict = conflict

    def fetch(self, client_id, dna):
        return {
            "client_id": client_id,
            "analysis": [{
                "id": "news-1",
                "headline": "Roche changes neurological research programme",
                "summary": "The company announced a change to its research programme.",
                "source": "Reuters",
                "url": "https://www.reuters.com/example",
                "company": "Roche",
                "alertType": "conflict" if self.conflict else "market",
                "belief_alignment": "negative" if self.conflict else "neutral",
                "portfolio_impact": "The event may conflict with the client's stated research priorities.",
                "affectedISINs": ["CH0012032048"],
                "isMock": False,
            }],
        }


class FakePortfolio:
    def __init__(self):
        self.proposal_calls = 0

    def snapshot(self, excel_path, client_id, dna, portfolio_sheet=None):
        return {
            "client_id": client_id,
            "portfolio_sheet": portfolio_sheet or "Sample Portfolio Balanced",
            "strategy": "Balanced",
            "holdings": [{
                "name": "Roche Holding AG",
                "isin": "CH0012032048",
                "current_chf": 112461.84,
                "portfolio_weight_pct": 1.1,
                "sub_asset_class": "Domestic (CHF)",
                "cio_rating": "HOLD",
            }],
            "allocation": [{
                "sub_asset_class": "Domestic (CHF)",
                "target_pct": 10.0,
                "current_pct": 10.2,
                "drift_pp": 0.2,
                "within_tolerance": True,
            }],
        }

    def propose_replacement(
        self, excel_path, portfolio_sheet, holding, dna, collision, snapshot,
        dna_threshold_pct=50.0,
    ):
        self.proposal_calls += 1
        return {
            "client_id": snapshot["client_id"],
            "suggested_swaps": [{
                "event_id": collision["event_id"],
                "mandate": "Balanced",
                "holding": holding,
                "recommended_action": "replace",
                "rationale": collision["news"]["portfolio_impact"],
                "urgency": "high",
                "trade_chf": holding["current_chf"],
                "current_cio_rating": "HOLD",
                "alternatives": [{
                    "name": "Novartis AG",
                    "isin": "CH0012005267",
                    "cio_rating": "BUY",
                    "match_score": 90,
                }],
                "dna_alignment_confidence_pct": 90,
                "mandate_check": {
                    "before_valid": True,
                    "after_valid": True,
                    "drift_after_pp": 0.2,
                },
            }],
        }


class BreachedPortfolio(FakePortfolio):
    def propose_replacement(self, *args, **kwargs):
        proposal = super().propose_replacement(*args, **kwargs)
        proposal["suggested_swaps"][0]["mandate_check"]["after_valid"] = False
        return proposal


def llm_response():
    return json.dumps({
        "internal_summary": "Conflict matched to a current holding.",
        "drafts": [{
            "label": "Recommended",
            "subject": "Portfolio review",
            "message": "Dear Client, I recommend that we review the Roche position and consider Novartis as an alternative. Your decision remains required.\n\nSarah Meier\nRelationship Manager",
            "style": "formal",
        }],
        "tone_notes": "Formal and concise.",
        "suggested_edits": [],
        "used_facts": ["Roche is a current holding", "Novartis is the proposed alternative"],
        "omitted_sensitive_information": ["private CRM details"],
        "compliance_flags": [],
        "confidence": 0.9,
    })


def request():
    return AgentPipelineRequest(
        client_id="schneider",
        client_name="Hubertus Schneider",
        crm_excel_path="crm.xlsx",
        portfolio_excel_path="portfolio.xlsx",
        portfolio_sheet="Sample Portfolio Balanced",
        run_id="test-run",
    )


class CollisionDetectorTests(unittest.TestCase):
    def test_requires_both_conflict_and_current_holding_match(self):
        snapshot = FakePortfolio().snapshot("", "schneider", DNA)
        detector = CollisionDetector()
        self.assertEqual(1, len(detector.detect(FakeNews(True).fetch("schneider", DNA), snapshot)))
        self.assertEqual([], detector.detect(FakeNews(False).fetch("schneider", DNA), snapshot))

    def test_demo_fixture_guarantees_roche_collision(self):
        fixture = (
            Path(__file__).resolve().parents[1]
            / "demo/backend/integration/examples/roche_collision_news.json"
        )
        news = JsonNewsAdapter(str(fixture)).fetch("schneider", DNA)
        snapshot = FakePortfolio().snapshot("", "schneider", DNA)
        collisions = CollisionDetector().detect(news, snapshot)
        self.assertEqual(1, len(collisions))
        self.assertTrue(collisions[0]["news"]["isMock"])

    def test_amazon_fixture_matches_balanced_amazon_holding(self):
        fixture = (
            Path(__file__).resolve().parents[1]
            / "demo/backend/integration/examples/amazon_collision_news.json"
        )
        news = JsonNewsAdapter(str(fixture)).fetch("schneider", DNA)
        snapshot = {
            "holdings": [{
                "name": "Amazon.com Inc.",
                "isin": "US0231351067",
                "current_chf": 206405.94,
            }]
        }
        collisions = CollisionDetector().detect(news, snapshot)
        self.assertEqual("US0231351067", collisions[0]["holding"]["isin"])


class AgentIntegrationPipelineTests(unittest.TestCase):
    def test_no_collision_stops_before_portfolio_replacement_and_message_llm(self):
        portfolio = FakePortfolio()
        llm = StaticLLMClient(llm_response())
        result = AgentIntegrationPipeline(FakeCRM(), FakeNews(False), portfolio, llm).run(request())
        self.assertEqual(AgentPipelineStatus.NO_COLLISION, result.status)
        self.assertEqual(0, portfolio.proposal_calls)
        self.assertEqual(0, len(llm.calls))
        self.assertEqual(
            {"status", "client_id", "run_id", "message"},
            set(result.compact_dict()),
        )

    def test_collision_calls_portfolio_then_builds_rm_message(self):
        portfolio = FakePortfolio()
        llm = StaticLLMClient(llm_response())
        result = AgentIntegrationPipeline(FakeCRM(), FakeNews(True), portfolio, llm).run(request())
        self.assertEqual(AgentPipelineStatus.READY_FOR_RM_REVIEW, result.status)
        self.assertEqual(1, portfolio.proposal_calls)
        self.assertEqual(1, len(llm.calls))
        self.assertEqual("ready_for_rm_review", result.message_results[0]["status"])
        self.assertEqual("CH0012032048", result.collisions[0]["holding"]["isin"])
        compact = result.compact_dict()
        self.assertNotIn("crm_output", compact)
        self.assertNotIn("news_output", compact)
        self.assertNotIn("portfolio_snapshot", compact)
        self.assertEqual(1, compact["collision_count"])
        self.assertEqual("Novartis AG", compact["recommendations"][0]["alternative"]["name"])

    def test_post_trade_mandate_breach_forces_rm_attention(self):
        result = AgentIntegrationPipeline(
            FakeCRM(), FakeNews(True), BreachedPortfolio(), StaticLLMClient(llm_response())
        ).run(request())
        self.assertEqual(AgentPipelineStatus.NEEDS_RM_ATTENTION, result.status)
        self.assertTrue(result.message_results[0]["compliance_flags"])

    def test_integration_forces_english_even_when_crm_prefers_german(self):
        llm = StaticLLMClient(llm_response())
        result = AgentIntegrationPipeline(
            GermanCRM(), FakeNews(True), FakePortfolio(), llm
        ).run(request())
        self.assertEqual("en", result.crm_output["communicationStyle"]["language"])
        self.assertIn("OUTPUT_LANGUAGE: English (en)", llm.calls[0][1])

    def test_cli_warning_filter_keeps_api_error_and_drops_dependency_noise(self):
        log = "A module compiled with NumPy 1.x cannot run\n❌ API Request Failed: 401 Unauthorized"
        self.assertEqual(["API Request Failed: 401 Unauthorized"], _important_warnings(log))

    def test_low_dna_candidate_becomes_review_message_not_forced_trade(self):
        low_candidate = {
            "Issuer": "Industria de Diseño Textil (Inditex)",
            "ISIN": "ES0148396007",
            "Rating": "BUY",
            "Explicacion_DNA": "Conflicts with the client's preferences.",
            "Afinidad_DNA_Porcentaje": 10.0,
            "Asignacion_Recomendada_CHF": 206405.94,
            "Precio_Actual_SIX": 55.74,
            "Moneda_SIX": "EUR",
        }
        module = SimpleNamespace(get_swap_candidates=lambda *args, **kwargs: low_candidate)
        holding = {
            "name": "Amazon.com Inc.",
            "isin": "US0231351067",
            "current_chf": 206405.94,
            "sub_asset_class": "Foreign (Dev. Markets)",
        }
        snapshot = {
            "client_id": "schneider",
            "strategy": "Balanced",
            "allocation": [{
                "sub_asset_class": "Foreign (Dev. Markets)",
                "drift_pp": 2.2,
                "within_tolerance": False,
            }],
        }
        collision = {
            "news": {
                "id": "demo-amazon-conflict-001",
                "alertType": "conflict",
                "portfolio_impact": "A current holding has a governance conflict.",
            }
        }
        with patch("demo.backend.integration.adapters._load_agent_module", return_value=module):
            proposal = LegacyPortfolioAgentAdapter().propose_replacement(
                "portfolio.xlsx", "Sample Portfolio Balanced", holding, DNA, collision, snapshot
            )
        swap = proposal["suggested_swaps"][0]
        self.assertEqual("review", swap["recommended_action"])
        self.assertIsNone(swap["trade_chf"])
        self.assertEqual([], swap["alternatives"])
        self.assertEqual("ES0148396007", swap["rejected_alternatives"][0]["isin"])

        with patch("demo.backend.integration.adapters._load_agent_module", return_value=module):
            demo_proposal = LegacyPortfolioAgentAdapter().propose_replacement(
                "portfolio.xlsx", "Sample Portfolio Balanced", holding, DNA,
                collision, snapshot, dna_threshold_pct=10.0,
            )
        demo_swap = demo_proposal["suggested_swaps"][0]
        self.assertEqual("replace", demo_swap["recommended_action"])
        self.assertEqual("ES0148396007", demo_swap["alternatives"][0]["isin"])


if __name__ == "__main__":
    unittest.main()
