import json
import unittest
from pathlib import Path

from demo.backend.orchestrator import (
    MessageOrchestrator,
    OrchestrationRequest,
    PipelineStatus,
    StaticLLMClient,
)
from demo.backend.orchestrator.normalizers import normalize_news
from demo.backend.orchestrator.normalizers import normalize_portfolio


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "demo" / "backend" / "orchestrator" / "examples" / "full_request.json"


def load_request():
    return OrchestrationRequest.from_dict(json.loads(EXAMPLE.read_text(encoding="utf-8")))


def valid_response(message="I recommend reviewing the CHF 112,461.84 Roche position. The decision remains yours."):
    return json.dumps({
        "internal_summary": "Verified portfolio event requires RM review.",
        "drafts": [{
            "label": "Recommended",
            "subject": "Portfolio review",
            "message": message,
            "style": "data-driven",
        }],
        "tone_notes": "Formal and concise.",
        "suggested_edits": [],
        "used_facts": ["Roche position", "CHF exposure"],
        "omitted_sensitive_information": ["private medical details"],
        "compliance_flags": [],
        "confidence": 0.91,
    })


def spanish_portfolio_candidate():
    return {
        "Issuer": "Industria de Diseño Textil (Inditex)",
        "Rating": "BUY",
        "Ya_En_Portfolio": False,
        "Posicion_Actual_CHF": 0.0,
        "Cuanto_Compramos_CHF": 131245.81,
        "ISIN": "ES0148396007",
        "Valor": "24956043",
        "MIC": "XMAD",
        "Explicacion_DNA": "Consumer discretionary exposure conflicts with the capital-preservation mandate.",
        "Asignacion_Recomendada_CHF": 131245.81,
        "Nueva_Posicion_Simulada_CHF": 131245.81,
        "Precio_Actual_SIX": 55.74,
        "Moneda_SIX": "EUR",
        "Confianza_Alineacion_DNA_Porcentaje": 13.3,
        "Cantidad_Acciones": 2354,
    }


class MessageOrchestratorTests(unittest.TestCase):
    def test_full_pipeline_returns_reviewable_draft(self):
        client = StaticLLMClient(valid_response())
        result = MessageOrchestrator(client).run(load_request())
        self.assertEqual(PipelineStatus.READY_FOR_RM_REVIEW, result.status)
        self.assertEqual(1, len(result.drafts))
        self.assertEqual([], result.compliance_flags)
        self.assertEqual(1, len(client.calls))

    def test_replacement_rating_is_not_mislabeled_as_current_rating(self):
        prepared = MessageOrchestrator(StaticLLMClient(valid_response())).prepare(load_request())
        recommendation = prepared.context["recommendation"]
        self.assertEqual("", recommendation["current_cio_rating"])
        self.assertEqual("BUY", recommendation["alternatives"][0]["cio_rating"])

    def test_partial_input_waits_instead_of_calling_llm(self):
        request = load_request()
        request.news_output = None
        client = StaticLLMClient(valid_response())
        result = MessageOrchestrator(client).run(request)
        self.assertEqual(PipelineStatus.WAITING_FOR_INPUTS, result.status)
        self.assertIn("news_output", result.missing_inputs)
        self.assertEqual([], client.calls)

    def test_missing_crm_waits_instead_of_generating(self):
        request = load_request()
        request.crm_output = {}
        client = StaticLLMClient(valid_response())
        result = MessageOrchestrator(client).run(request)
        self.assertEqual(PipelineStatus.WAITING_FOR_INPUTS, result.status)
        self.assertIn("crm_output", result.missing_inputs)
        self.assertEqual([], client.calls)

    def test_cross_client_input_is_rejected(self):
        request = load_request()
        request.news_output["clientId"] = "ammann"
        result = MessageOrchestrator(StaticLLMClient(valid_response())).run(request)
        self.assertEqual(PipelineStatus.INVALID_INPUT, result.status)
        self.assertIn("Cross-client", result.error)

    def test_private_crm_details_are_not_in_prompt(self):
        prepared = MessageOrchestrator(StaticLLMClient(valid_response())).prepare(load_request())
        self.assertNotIn("family member was diagnosed", prepared.user_prompt.lower())
        self.assertNotIn("parkinson", prepared.user_prompt.lower())
        self.assertIn("healthcare and research priorities", prepared.user_prompt.lower())

    def test_unverified_amount_is_flagged(self):
        client = StaticLLMClient(valid_response("I recommend selling CHF 999,999 of Roche."))
        result = MessageOrchestrator(client).run(load_request())
        self.assertEqual(PipelineStatus.NEEDS_RM_ATTENTION, result.status)
        self.assertTrue(any("Unverified currency amount" in flag for flag in result.compliance_flags))

    def test_internal_schema_label_is_flagged(self):
        client = StaticLLMClient(valid_response("This is relevant because of safe_reason."))
        result = MessageOrchestrator(client).run(load_request())
        self.assertEqual(PipelineStatus.NEEDS_RM_ATTENTION, result.status)
        self.assertTrue(any("Internal schema label" in flag for flag in result.compliance_flags))

    def test_sensitive_term_in_draft_is_flagged(self):
        client = StaticLLMClient(valid_response("Because of Parkinson's disease, I recommend a review."))
        result = MessageOrchestrator(client).run(load_request())
        self.assertEqual(PipelineStatus.NEEDS_RM_ATTENTION, result.status)
        self.assertTrue(any("Sensitive CRM term" in flag for flag in result.compliance_flags))

    def test_mock_news_is_never_marked_ready_to_send(self):
        request = load_request()
        request.news_output["alerts"][0]["isMock"] = True
        result = MessageOrchestrator(StaticLLMClient(valid_response())).run(request)
        self.assertEqual(PipelineStatus.NEEDS_RM_ATTENTION, result.status)
        self.assertTrue(any("DEMO ALERT" in flag for flag in result.compliance_flags))

    def test_current_news_agent_analysis_shape_is_supported(self):
        news = {
            "analysis": [
                {
                    "id": "neutral-1",
                    "headline": "Tencent valuation article",
                    "url": "https://seekingalpha.com/example",
                    "alertType": "market",
                    "belief_alignment": "neutral",
                    "portfolio_impact": "No relevance to the client's priorities.",
                },
                {
                    "id": "opportunity-1",
                    "headline": "Organs-on-a-Chip advance drug design",
                    "url": "https://www.insideprecisionmedicine.com/example",
                    "alertType": "opportunity",
                    "belief_alignment": "positive",
                    "portfolio_impact": "A direct match with Parkinson's research priorities.",
                },
            ]
        }
        normalized = normalize_news(news)
        self.assertEqual("opportunity-1", normalized["event_id"])
        self.assertEqual("insideprecisionmedicine.com", normalized["sources"][0]["publisher"])

    def test_crm_and_current_news_shape_wait_only_for_portfolio(self):
        request = load_request()
        request.news_output = {
            "analysis": [{
                "id": "opportunity-1",
                "headline": "Organs-on-a-Chip advance drug design",
                "url": "https://www.insideprecisionmedicine.com/example",
                "alertType": "opportunity",
                "belief_alignment": "positive",
                "portfolio_impact": "A direct match with Parkinson's research priorities.",
            }]
        }
        request.portfolio_output = None
        client = StaticLLMClient(valid_response())
        result = MessageOrchestrator(client).run(request)
        self.assertEqual(PipelineStatus.WAITING_FOR_INPUTS, result.status)
        self.assertEqual(["portfolio_output"], result.missing_inputs)
        self.assertEqual([], client.calls)

    def test_spanish_portfolio_candidate_shape_is_supported(self):
        news = {"affected_isins": ["ES0148396007"]}
        normalized = normalize_portfolio(spanish_portfolio_candidate(), news)
        self.assertEqual("Industria de Diseño Textil (Inditex)", normalized["holding"]["name"])
        self.assertEqual("do_not_recommend", normalized["recommended_action"])
        self.assertEqual(13.3, normalized["dna_alignment_confidence_pct"])
        self.assertFalse(normalized["mandate_check"]["after_valid"])

    def test_unrelated_news_and_portfolio_are_rejected(self):
        request = load_request()
        request.news_output = {
            "analysis": [{
                "id": "opportunity-1",
                "headline": "Organs-on-a-Chip advance drug design",
                "url": "https://www.insideprecisionmedicine.com/example",
                "alertType": "opportunity",
                "company": "Organs-on-a-Chip biotech",
            }]
        }
        request.portfolio_output = spanish_portfolio_candidate()
        client = StaticLLMClient(valid_response())
        result = MessageOrchestrator(client).run(request)
        self.assertEqual(PipelineStatus.INVALID_INPUT, result.status)
        self.assertIn("not linked", result.error)
        self.assertEqual([], client.calls)

    def test_linked_low_alignment_candidate_skips_llm(self):
        request = load_request()
        request.news_output = {
            "alerts": [{
                "id": "inditex-1",
                "headline": "Inditex strategy update",
                "url": "https://example.test/inditex",
                "alertType": "market",
                "company": "Inditex",
                "affectedISINs": ["ES0148396007"],
                "source": "Reuters",
            }]
        }
        request.portfolio_output = spanish_portfolio_candidate()
        client = StaticLLMClient(valid_response())
        result = MessageOrchestrator(client).run(request)
        self.assertEqual(PipelineStatus.NO_MESSAGE_RECOMMENDED, result.status)
        self.assertEqual([], result.drafts)
        self.assertEqual([], client.calls)


if __name__ == "__main__":
    unittest.main()
