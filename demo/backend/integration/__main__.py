from __future__ import annotations

import argparse
import contextlib
import io
import json
import re

from demo.backend.orchestrator import PhoeniqsLLMClient

from .adapters import JsonNewsAdapter, LegacyCRMAgentAdapter, LegacyNewsAgentAdapter, LegacyPortfolioAgentAdapter
from .contracts import AgentPipelineRequest
from .pipeline import AgentIntegrationPipeline


def _important_warnings(log_text: str) -> list[str]:
    patterns = (
        "api request failed",
        "credentials missing",
        "no newsapi_key",
        "error fetching news",
        "did not produce",
        "request failed",
    )
    warnings = []
    for raw_line in log_text.splitlines():
        line = re.sub(r"^[^A-Za-z0-9]+", "", raw_line).strip()
        if line and any(pattern in line.lower() for pattern in patterns):
            warnings.append(line[:300])
    return list(dict.fromkeys(warnings))[-3:]


def _run_pipeline(args):
    news_provider = JsonNewsAdapter(args.news_json) if args.news_json else LegacyNewsAgentAdapter()
    pipeline = AgentIntegrationPipeline(
        LegacyCRMAgentAdapter(),
        news_provider,
        LegacyPortfolioAgentAdapter(),
        PhoeniqsLLMClient(),
    )
    return pipeline.run(AgentPipelineRequest(
        client_id=args.client_id,
        client_name=args.client_name,
        crm_excel_path=args.crm_excel,
        portfolio_excel_path=args.portfolio_excel,
        portfolio_sheet=args.portfolio_sheet,
        relationship_manager_name=args.rm_name,
        run_id=args.run_id,
        language=args.language,
        dna_threshold_pct=args.dna_threshold,
    ))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run CRM → News/Portfolio → collision → RM message")
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--client-name", required=True)
    parser.add_argument("--crm-excel", required=True)
    parser.add_argument("--portfolio-excel", required=True)
    parser.add_argument("--portfolio-sheet")
    parser.add_argument("--news-json", help="Use a News Agent JSON fixture instead of calling the News API")
    parser.add_argument("--rm-name", default="Sarah Meier")
    parser.add_argument("--run-id")
    parser.add_argument("--language", default="en", help="Output language code; defaults to English")
    parser.add_argument(
        "--dna-threshold",
        type=float,
        default=50.0,
        help="Minimum client-DNA compatibility percentage required for a trade; default: 50",
    )
    parser.add_argument("--full-output", action="store_true", help="Include raw agent payloads")
    parser.add_argument("--verbose", action="store_true", help="Show logs printed by legacy agents")
    args = parser.parse_args()

    startup_error = None
    if args.verbose:
        try:
            result = _run_pipeline(args)
        except Exception as exc:
            result = None
            startup_error = str(exc)
        captured_logs = ""
    else:
        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
                result = _run_pipeline(args)
        except Exception as exc:
            result = None
            startup_error = str(exc)
        captured_logs = f"{stdout_buffer.getvalue()}\n{stderr_buffer.getvalue()}"

    if result is None:
        output = {
            "status": "failed",
            "client_id": args.client_id,
            "run_id": args.run_id,
            "errors": [startup_error or "Pipeline initialization failed"],
        }
    else:
        output = result.to_dict() if args.full_output else result.compact_dict()
    warnings = _important_warnings(captured_logs)
    if warnings:
        output["warnings"] = warnings
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
