from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import json
import os

# Import your agents here!
from agents.crmAgent import extract_and_save_dna
from agents.newsAgent import compile_news_feed
# from agents.portfolioAgent import run_portfolio_analysis # (When you have it)

app = FastAPI()

# Allow React (which runs on a different port) to talk to this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, restrict this to your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/api/analyze/{client_id}")
async def analyze_client(client_id: str):
    print(f"🚀 Starting full AI analysis pipeline for {client_id}...")
    
    excel_path = "../../data/SwissHacks CRM.xlsx"
    dna_file = f"{client_id.lower()}_dna.json"
    news_file = f"{client_id.lower()}_analyzed_news.json"
    
    try:
        # 1. Run CRM Agent (Extract DNA)
        # Note: You might need to adjust your python scripts to return the JSON directly 
        # or just let them save to disk and read them back here.
        extract_and_save_dna(excel_path, client_id)
        
        # 2. Run News Agent
        compile_news_feed(client_id, dna_file)
        
        # 3. Read the final results from the files to send back to React
        with open(dna_file, 'r', encoding='utf-8') as f:
            dna_data = json.load(f)
            
        with open(news_file, 'r', encoding='utf-8') as f:
            news_data = json.load(f)
            
        # Combine everything into the payload React expects!
        return {
            "portfolioTotalCHF": 12500000, # You can pull this from your portfolio agent later
            "dna": dna_data,
            "alerts": news_data.get("analysis", []),
            "holdings": [] # Add portfolio agent results here later
        }

    except Exception as e:
        print(f"❌ Pipeline failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    # Runs the server on http://localhost:8000
    uvicorn.run(app, host="0.0.0.0", port=8000)