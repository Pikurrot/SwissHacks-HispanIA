from __future__ import annotations

from .context_builder import prepare_prompt
from .contracts import OrchestrationRequest, OrchestrationResult, PipelineStatus, PreparedPrompt
from .errors import InputValidationError, LLMClientError, LLMOutputError
from .llm_client import LLMClient
from .output_validator import parse_json_response, validate_output


class MessageOrchestrator:
    def __init__(self, llm_client: LLMClient) -> None:
        self.llm_client = llm_client

    def prepare(self, request: OrchestrationRequest) -> PreparedPrompt:
        return prepare_prompt(request)

    def run(self, request: OrchestrationRequest) -> OrchestrationResult:
        try:
            prepared = self.prepare(request)
        except InputValidationError as exc:
            return OrchestrationResult(
                status=PipelineStatus.INVALID_INPUT,
                client_id=request.client_id,
                run_id=request.run_id,
                error=str(exc),
            )

        if prepared.missing_inputs:
            return OrchestrationResult(
                status=PipelineStatus.WAITING_FOR_INPUTS,
                client_id=request.client_id,
                run_id=request.run_id,
                missing_inputs=prepared.missing_inputs,
            )

        action = str(prepared.context.get("recommendation", {}).get("action") or "").lower()
        if action in {"do_not_recommend", "no_action", "ignore"}:
            return OrchestrationResult(
                status=PipelineStatus.NO_MESSAGE_RECOMMENDED,
                client_id=request.client_id,
                run_id=request.run_id,
                internal_summary=(
                    "Portfolio Agent did not recommend a client action; no client-facing draft was generated."
                ),
                used_facts=[
                    str(prepared.context.get("recommendation", {}).get("rationale") or "")
                ],
                confidence=float(
                    prepared.context.get("recommendation", {}).get("dna_alignment_confidence_pct") or 0.0
                ) / 100.0,
                prepared_context=prepared.context,
            )

        try:
            raw = self.llm_client.complete(prepared.system_prompt, prepared.user_prompt)
            parsed = parse_json_response(raw)
            (
                internal_summary,
                drafts,
                tone_notes,
                suggested_edits,
                used_facts,
                omitted_sensitive_information,
                compliance_flags,
                confidence,
            ) = validate_output(parsed, prepared.context, prepared.sensitive_terms)
        except (LLMClientError, LLMOutputError) as exc:
            return OrchestrationResult(
                status=PipelineStatus.GENERATION_FAILED,
                client_id=request.client_id,
                run_id=request.run_id,
                error=str(exc),
                prepared_context=prepared.context,
            )

        status = (
            PipelineStatus.NEEDS_RM_ATTENTION
            if compliance_flags
            else PipelineStatus.READY_FOR_RM_REVIEW
        )
        return OrchestrationResult(
            status=status,
            client_id=request.client_id,
            run_id=request.run_id,
            internal_summary=internal_summary,
            drafts=drafts,
            tone_notes=tone_notes,
            suggested_edits=suggested_edits,
            used_facts=used_facts,
            omitted_sensitive_information=omitted_sensitive_information,
            compliance_flags=compliance_flags,
            confidence=confidence,
            prepared_context=prepared.context,
        )
