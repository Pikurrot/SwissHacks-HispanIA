import sys
import os
import json

# --- INYECCIÓN AGRESIVA DE RUTA ---
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

from fastapi import FastAPI
from fastapi.responses import FileResponse 
import uvicorn
from fastapi.middleware.cors import CORSMiddleware
from backend.agents.crmAgent import extract_and_save_dna
from backend.agents.newsAgent import compile_news_feed
from backend.agents.portfolioAgent import get_swap_candidates
from backend.agents.six_api_client import get_asset_price_info

CLIENT_NAMES = {
    "schneider": "Schneider",
    "raeber": "Raeber",
    "huber": "Huber",
    "ammann": "Ammann"
}

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def read_root():
    # Asegúrate de que esta ruta sea correcta relativa a donde ejecutas el script
    return FileResponse("static/dashboard.html")

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
