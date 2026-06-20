import sys
import os
import json
import re

# --- INYECCIÓN AGRESIVA DE RUTA ---
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)
repo_root = os.path.dirname(current_dir)
sys.path.insert(0, repo_root)

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse 
import uvicorn
import pandas as pd
from fastapi.middleware.cors import CORSMiddleware
from backend.agents.crmAgent import extract_and_save_dna
from backend.agents.newsAgent import compile_news_feed
from backend.agents.portfolioAgent import get_swap_candidates
from backend.agents.six_api_client import get_asset_price_info
from demo.backend.integration import (
    AgentIntegrationPipeline,
    AgentPipelineRequest,
    LegacyPortfolioAgentAdapter,
)
from demo.backend.orchestrator import PhoeniqsLLMClient

CLIENT_NAMES = {
    "schneider": "Schneider",
    "raeber": "Raeber",
    "huber": "Huber",
    "ammann": "Ammann"
}

CLIENT_FULL_NAMES = {
    "schneider": "Hubertus Schneider",
    "raeber": "Eugen Räber",
    "huber": "Marius Huber",
    "ammann": "Julian Ammann",
}

PORTFOLIO_SHEETS = {
    "schneider": "Sample Portfolio Balanced",
    "raeber":    "Sample Portfolio Defensive",
    "huber":     "Sample Portfolio Defensive",
    "ammann":    "Sample Portfolio Growth",
}

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class _CachedCRMProvider:
    def __init__(self, dna: dict):
        self.dna = dna

    def extract(self, excel_path: str, client_id: str, client_name: str) -> dict:
        return self.dna


class _CachedNewsProvider:
    def __init__(self, news: dict):
        self.news = news

    def fetch(self, client_id: str, dna: dict) -> dict:
        return {"client_id": client_id, "analysis": self.news.get("analysis", [])}

@app.get("/")
async def read_root():
    # Asegúrate de que esta ruta sea correcta relativa a donde ejecutas el script
    return FileResponse("static/dashboard.html")

def _portfolio_issuers(client_id: str) -> list[str]:
    """Return lowercase issuer names from the client's portfolio sheet."""
    try:
        sheet = PORTFOLIO_SHEETS.get(client_id, "Sample Portfolio Balanced")
        df = pd.read_excel("../data/SwissHacks Portfolio Construction.xlsx", sheet_name=sheet)
        return df['Issuer / Asset'].dropna().str.lower().tolist()
    except Exception as e:
        print(f"⚠️ Could not load portfolio issuers for {client_id}: {e}")
        return []


def _company_in_portfolio(company, issuers: list[str]) -> bool:
    """True if company name overlaps with any portfolio issuer via substring match."""
    name = (company or "").lower().strip()
    if not name or name == "unknown":
        return False
    return any(name in issuer or issuer in name for issuer in issuers)


@app.get("/api/news/check/{client_id}")
async def check_news(client_id: str):
    dna_path = f"{client_id.lower()}_dna.json"
    if not os.path.exists(dna_path):
        return {"analysis": []}
    compile_news_feed(client_id, dna_path)
    analyzed_path = f"{client_id.lower()}_analyzed_news.json"
    try:
        with open(analyzed_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        return {"analysis": []}

    # Filter: keep positives always; keep negatives/neutrals only if the
    # flagged company is actually present in the client's portfolio.
    issuers = _portfolio_issuers(client_id)
    filtered = []
    for item in data.get("analysis", []):
        alignment = item.get("belief_alignment", "neutral")
        if alignment == "positive":
            filtered.append(item)
        elif _company_in_portfolio(item.get("company", ""), issuers):
            filtered.append(item)
        else:
            print(f"🗑 Dropping '{item.get('company')}' ({alignment}) — not in portfolio.")

    return {"analysis": filtered}


@app.get("/api/portfolio/conflicts/{client_id}")
async def analyze_conflicts(client_id: str, sell_assets: str = "Apple"):
    dna_path = f"{client_id.lower()}_dna.json"
    if not os.path.exists(dna_path):
        return {"error": "DNA not generated."}

    with open(dna_path, 'r') as f:
        dna = json.load(f)

    excel_path = "../data/SwissHacks Portfolio Construction.xlsx"
    sheet = PORTFOLIO_SHEETS.get(client_id, "Sample Portfolio Balanced")
    companies = [s.strip() for s in sell_assets.split(',') if s.strip()]

    results = []
    for company in companies:
        r = get_swap_candidates(excel_path, sheet, company, dna)
        r["conflict_company"] = company
        results.append(r)
    return results


_company_info_cache: dict = {}


def _fetch_company_info(name: str) -> dict:
    """LLM call to generate a company brief. Checks cache first."""
    cache_key = name.lower().strip()
    if cache_key in _company_info_cache:
        print(f"  📦 Company info cache hit: {name}")
        return _company_info_cache[cache_key]

    api_key = os.environ.get("PHOENIQS_API_KEY")
    api_url = os.environ.get("PHOENIQS_API_URL")
    model   = os.environ.get("PHOENIQS_MODEL", "inference-gpt-oss-120b")

    if not api_key or not api_url:
        return {"error": "LLM API not configured.", "name": name}

    import requests as _req
    prompt = f"""You are a senior equity analyst briefing a private banking relationship manager.
Write a concise, factual company brief for "{name}" that a wealth manager can read in 30 seconds before a client call.

Return a pure JSON object (no markdown) with exactly this structure:
{{
  "name": "{name}",
  "oneLiner": "One sentence: what the company does and where it operates.",
  "sector": "Industry sector and sub-sector.",
  "businessModel": "2-3 sentences on how the company makes money.",
  "keyStrengths": ["3-4 bullet points of competitive advantages or notable facts"],
  "keyRisks": ["2-3 bullet points of main investment risks"],
  "relevanceForWealthClients": "1-2 sentences on why this asset may be relevant for private banking portfolios."
}}"""

    try:
        resp = _req.post(
            f"{api_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.2, "max_tokens": 800},
            timeout=30,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        content = re.sub(r"^```(?:json)?\n?", "", content)
        content = re.sub(r"\n?```$", "", content)
        result = json.loads(content)
        _company_info_cache[cache_key] = result
        print(f"  🧠 Company info cached: {name}")
        return result
    except Exception as e:
        return {"error": str(e), "name": name}


@app.get("/api/company/info")
async def company_info(name: str):
    """Return a cached or freshly generated company brief."""
    return _fetch_company_info(name)


@app.post("/api/company/prefetch")
async def prefetch_company_info(payload: dict):
    """
    Fire-and-forget background pre-warming of the company info cache.
    Accepts {"names": ["Company A", "Company B", ...]}
    Runs sequentially and stores results; returns immediately with the list processed.
    """
    import asyncio, concurrent.futures
    names = payload.get("names", [])
    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor() as pool:
        await asyncio.gather(*[
            loop.run_in_executor(pool, _fetch_company_info, n)
            for n in names if n
        ])
    return {"prefetched": len(names)}


@app.get("/api/portfolio/analyze/{client_id}")
async def analyze_portfolio(client_id: str, sell_asset: str = "Apple"):
    dna_path = f"{client_id.lower()}_dna.json"
    if not os.path.exists(dna_path):
        return {"error": "DNA no generado."}
    
    with open(dna_path, 'r') as f:
        dna = json.load(f)
        
    excel_path = "../data/SwissHacks Portfolio Construction.xlsx"
    # Usamos el parámetro sell_asset que viene del frontend
    result = get_swap_candidates(excel_path, "Sample Portfolio Balanced", sell_asset, dna)
    return result


@app.post("/api/message/generate/{client_id}")
async def generate_client_message(
    client_id: str,
    dna_threshold: float = 50.0,
    language: str = "en",
):
    """Generate the real messageAgent draft from cached CRM and News outputs."""
    if client_id not in CLIENT_NAMES:
        raise HTTPException(status_code=404, detail="Client ID not recognized")

    dna_path = os.path.join(current_dir, f"{client_id.lower()}_dna.json")
    news_path = os.path.join(current_dir, f"{client_id.lower()}_analyzed_news.json")
    try:
        with open(dna_path, "r", encoding="utf-8") as handle:
            dna = json.load(handle)
        with open(news_path, "r", encoding="utf-8") as handle:
            news = json.load(handle)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=409,
            detail="Run CRM and News analysis before generating the message",
        ) from exc

    try:
        pipeline = AgentIntegrationPipeline(
            _CachedCRMProvider(dna),
            _CachedNewsProvider(news),
            LegacyPortfolioAgentAdapter(),
            PhoeniqsLLMClient(),
        )
        request = AgentPipelineRequest(
            client_id=client_id,
            client_name=CLIENT_FULL_NAMES[client_id],
            crm_excel_path=os.path.join(repo_root, "data", "SwissHacks CRM.xlsx"),
            portfolio_excel_path=os.path.join(repo_root, "data", "SwissHacks Portfolio Construction.xlsx"),
            portfolio_sheet=PORTFOLIO_SHEETS[client_id],
            relationship_manager_name="Sarah Meier",
            run_id=f"dashboard-{client_id}",
            language=language,
            dna_threshold_pct=dna_threshold,
        )
        result = await run_in_threadpool(pipeline.run, request)
        return result.compact_dict()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

@app.post("/api/analyze/{client_id}")
async def analyze_client(client_id: str):
    if client_id not in CLIENT_NAMES:
        return {"error": "Client ID not recognized"}
        
    full_name = CLIENT_NAMES[client_id]
    
    # Asegúrate de que esta ruta sea relativa a demo/backend
    excel_path = "../data/SwissHacks CRM.xlsx"
    
    expected_dna_filename = f"{full_name.replace(' ', '_').lower()}_dna.json"
    
    print(f"🧠 [crmAgent] Ejecutando extracción para: {full_name}")
    
    extract_and_save_dna(excel_path, full_name)
    
    try:
        with open(expected_dna_filename, 'r', encoding='utf-8') as f:
            dna_data = json.load(f)
        return dna_data
    except FileNotFoundError:
        return {"error": f"No se pudo generar el archivo {expected_dna_filename}"}

    
# --- EJECUCIÓN DIRECTA ---
if __name__ == "__main__":
    print("🚀 Arrancando servidor directamente...")
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
