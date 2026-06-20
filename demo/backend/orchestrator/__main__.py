from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contracts import OrchestrationRequest
from .llm_client import PhoeniqsLLMClient, StaticLLMClient
from .pipeline import MessageOrchestrator


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare or run the RM message orchestration pipeline")
    parser.add_argument("--input", required=True, help="Path to an OrchestrationRequest JSON file")
    parser.add_argument("--dry-run", action="store_true", help="Print the selected context and prompts without calling an LLM")
    args = parser.parse_args()

    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    request = OrchestrationRequest.from_dict(data)
    if args.dry_run:
        placeholder = StaticLLMClient("{}")
        prepared = MessageOrchestrator(placeholder).prepare(request)
        print(json.dumps({
            "missing_inputs": prepared.missing_inputs,
            "context": prepared.context,
            "system_prompt": prepared.system_prompt,
            "user_prompt": prepared.user_prompt,
        }, indent=2, ensure_ascii=False))
        return

    result = MessageOrchestrator(PhoeniqsLLMClient()).run(request)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

