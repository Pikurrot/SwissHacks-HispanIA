# Agent Integration Pipeline

This folder connects the existing agents without modifying `crmAgent.py`,
`newsAgent.py`, or `portfolioAgent.py`.

```text
CRM Agent
    ├── News Agent ───────────────┐
    └── Portfolio snapshot ───────┤
                                  v
                         Collision detector
                         (conflict + held asset)
                                  |
                       no collision: stop safely
                                  |
                         Portfolio Agent swap
                                  |
                         Message Orchestrator
                                  |
                            RM review drafts
```

The adapters recover the current agents' file outputs in isolated temporary
directories. Collision matching is deterministic: a News item must first be
classified as a CRM conflict, then match a current holding by ISIN or company
name. The LLM is not trusted to perform portfolio arithmetic.

If the Portfolio agent's best replacement scores below 50% against the client
DNA, the pipeline rejects that trade but still generates an alert-only review
message. It never forces a poorly aligned replacement merely to produce a draft.

The default is an integration policy, not a value from the workbook or SIX. For
demo experiments, change it with `--dna-threshold 30`. A lower value accepts
more weakly aligned candidates and should not be treated as production policy.

## Run

From the repository root:

```powershell
python -m demo.backend.integration `
  --client-id schneider `
  --client-name "Hubertus Schneider" `
  --crm-excel "data/SwissHacks CRM.xlsx" `
  --portfolio-excel "data/SwissHacks Portfolio Construction.xlsx" `
  --portfolio-sheet "Sample Portfolio Balanced" `
  --run-id "demo-001"
```

Required environment variables are `NEWSAPI_KEY`, `PHOENIQS_API_KEY`,
`PHOENIQS_API_URL`, and `PHOENIQS_MODEL`.

Integrated runs default to English even when CRM detects another preferred
language. Pass `--language en` explicitly if desired.

Important statuses:

- `no_collision`: news did not conflict with CRM values and a held security.
- `ready_for_rm_review`: at least one validated draft is available.
- `needs_rm_attention`: a recommendation exists but needs manual attention.
- `partial_failure`: some collisions worked and others failed.
- `failed`: the pipeline could not produce any usable result.

The command is quiet and compact by default. It prints only collisions,
recommendations, message drafts, and errors. Use `--verbose` to show logs from
the legacy agents, or `--full-output` to include complete CRM, News, and
portfolio payloads for debugging.

## Clean Python environment

If Anaconda prints `_ARRAY_API not found`, it is an environment mismatch between
NumPy 2 and old optional `pyarrow`, `numexpr`, or `bottleneck` binaries. It is not
a News or Phoeniqs API error. Run this repository in an isolated environment:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Demo with a guaranteed collision

The included fixture is synthetic and is always marked demo-only. It matches
Roche (`CH0012032048`), which is held in the Balanced sample portfolio:

```powershell
python -m demo.backend.integration `
  --client-id schneider `
  --client-name "Hubertus Schneider" `
  --crm-excel "data/SwissHacks CRM.xlsx" `
  --portfolio-excel "data/SwissHacks Portfolio Construction.xlsx" `
  --portfolio-sheet "Sample Portfolio Balanced" `
  --news-json "demo/backend/integration/examples/roche_collision_news.json" `
  --run-id "demo-collision-001"
```

For a collision whose same-sector replacement candidates are Nike and Inditex,
use the Amazon fixture instead:

```powershell
python -m demo.backend.integration `
  --client-id schneider `
  --client-name "Hubertus Schneider" `
  --crm-excel "data/SwissHacks CRM.xlsx" `
  --portfolio-excel "data/SwissHacks Portfolio Construction.xlsx" `
  --portfolio-sheet "Sample Portfolio Balanced" `
  --news-json "demo/backend/integration/examples/amazon_collision_news.json" `
  --run-id "demo-amazon-collision-001"
```
