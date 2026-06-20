# RM Message Orchestrator

Isolated Python pipeline that combines outputs from CRM, News, and Portfolio agents and generates message drafts for Relationship Manager review.

## Why this folder is merge-friendly

- It does not import any agent implementation.
- Agent branches pass plain dictionaries.
- Both `camelCase` and `snake_case` fields are accepted for common fields.
- Missing News or Portfolio output returns `waiting_for_inputs`; nothing is invented.
- Every output may include `client_id`. If IDs disagree, the pipeline rejects the request.

## Flow

```text
CRM output ───────┐
News output ──────┼─> normalize + validate ─> safe context ─> prompt ─> LLM
Portfolio output ┘                                           │
                                                             └─> validated RM drafts
```

The orchestrator selects the highest-relevance news event, matches a portfolio proposal by affected ISIN, removes private CRM details from the LLM context, and validates the generated JSON.

## Python integration

```python
from demo.backend.orchestrator import (
    MessageOrchestrator,
    OrchestrationRequest,
    PhoeniqsLLMClient,
)

request = OrchestrationRequest(
    client_id="schneider",
    client_name="Hubertus Schneider",
    crm_output=crm_agent_result,
    news_output=news_agent_result,
    portfolio_output=portfolio_agent_result,
    run_id="analysis-123",
)

result = MessageOrchestrator(PhoeniqsLLMClient()).run(request)
return result.to_dict()
```

The caller/orchestrator endpoint is responsible for collecting agent outputs. The Message Orchestrator never sends messages or executes trades.

## Required environment

```env
PHOENIQS_API_KEY=...
PHOENIQS_API_URL=https://maas.phoeniqs.com/v1
PHOENIQS_MODEL=inference-gpt-oss-120b
```

## Local dry run

Dry run validates and prints the selected safe context without calling the LLM:

```bash
python -m demo.backend.orchestrator \
  --input demo/backend/orchestrator/examples/full_request.json \
  --dry-run
```

Remove `--dry-run` to call Phoeniqs.

## Agent contracts

Each agent may return its payload directly or wrap it in `data`, `payload`, or `result`.

### CRM Agent

The existing DNA JSON is accepted. The caller supplies `client_id` and `client_name`. Recommended future addition:

```json
{"client_id": "schneider", "data": {"values": {}, "communicationStyle": {}}}
```

### News Agent

```json
{
  "client_id": "schneider",
  "alerts": [{
    "id": "event-1",
    "headline": "...",
    "summary": "...",
    "affected_isins": ["CH0012032048"],
    "sources": [{"publisher": "Reuters", "url": "..."}],
    "severity": "high",
    "relevance_score": 0.9,
    "confidence": 0.9
  }]
}
```

### Portfolio Agent

```json
{
  "client_id": "schneider",
  "suggested_swaps": [{
    "holding": {"name": "Roche", "isin": "CH0012032048", "current_chf": 112461.84},
    "recommended_action": "replace",
    "trade_chf": 112461.84,
    "alternatives": [{"name": "Novartis", "isin": "CH0012005267", "cio_rating": "BUY"}],
    "mandate_check": {"after_valid": true, "drift_after_pp": 0.02}
  }]
}
```

## Result statuses

- `waiting_for_inputs`: News or Portfolio facts are missing.
- `ready_for_rm_review`: drafts passed deterministic checks.
- `needs_rm_attention`: drafts exist but compliance checks found warnings.
- `invalid_input`: cross-client mismatch or missing identity.
- `generation_failed`: LLM request or output-format failure.

