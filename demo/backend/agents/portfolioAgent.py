import os
import json
import re
import pandas as pd
import requests
from dotenv import load_dotenv

# Importamos nuestro cliente de la API de SIX
from .six_api_client import get_asset_price_info

# Cargar variables de entorno
load_dotenv()

API_KEY = os.environ.get("PHOENIQS_API_KEY")
API_URL = os.environ.get("PHOENIQS_API_URL")
MODEL = os.environ.get("PHOENIQS_MODEL", "inference-gpt-oss-120b")

def get_llm_dna_scores(candidates: list, client_dna: dict) -> dict:
    """
    Usa el LLM para evaluar qué tan bien encaja cada candidato con el DNA del cliente.
    Devuelve un diccionario con el puntaje y la justificación para cada empresa.
    """
    if not API_KEY or not API_URL:
        print("⚠️ Advertencia: API keys de Phoeniqs no configuradas. Se usarán puntajes base.")
        return {c['Issuer']: {"score": 5, "reason": "API keys no configuradas. Puntaje base."} for c in candidates}

    candidates_info = ""
    for c in candidates:
        candidates_info += f"- Empresa: {c['Issuer']} | Sector: {c['Industry Group']} | Razón del CIO: {c['CIO_View']}\n"

    prompt = f"""You are a Private Banking Expert. Evaluate the following investment candidates against the Client's DNA.
Assign a score from 1 to 10 for each candidate (1 = severe conflict/red line, 10 = perfect match with priorities).
You MUST also provide a brief, 1-2 sentence explanation of WHY you assigned that score based on specific parts of the DNA.

CLIENT DNA:
{json.dumps(client_dna, indent=2)}

CANDIDATES TO EVALUATE:
{candidates_info}

INSTRUCTIONS:
1. Penalize strictly if the candidate violates 'redLines' or 'avoidedSectors'.
2. Reward heavily if the candidate matches 'priorities' or 'preferredSectors'.
3. Return ONLY a valid JSON object mapping the exact Issuer name to an object containing 'score' and 'reason'. No markdown fences, no text outside JSON.

EXPECTED FORMAT:
{{
  "Issuer A": {{
    "score": 8,
    "reason": "Matches the client's preference for healthcare and tangible assets."
  }},
  "Issuer B": {{
    "score": 2,
    "reason": "Violates the red line against speculative software."
  }}
}}
"""
    print(f"🤖 Evaluando {len(candidates)} candidatos con el LLM ({MODEL})...")
    
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 5000
    }

    try:
        response = requests.post(f"{API_URL}/chat/completions", headers=headers, json=payload, timeout=45)
        response.raise_for_status()
        response_data = response.json()
        
        choices = response_data.get("choices", [])
        if not choices:
            raise ValueError(f"Estructura inesperada en la respuesta: {response_data}")
            
        content = choices[0].get("message", {}).get("content")
        
        if content is None:
            raise ValueError("La API devolvió 'content': null (Probable filtro de contenido o error del modelo).")
        
        clean_text = content.strip()
        
        if clean_text.startswith("```json"):
            clean_text = clean_text[7:]
        elif clean_text.startswith("```"):
            clean_text = clean_text[3:]
            
        if clean_text.endswith("```"):
            clean_text = clean_text[:-3]
            
        clean_text = clean_text.strip()
        
        scores = json.loads(clean_text)
        return scores
        
    except Exception as e:
        print(f"❌ Error al consultar el LLM para los puntajes: {e}")
        return {c['Issuer']: {"score": 5, "reason": f"Error del LLM: {e}"} for c in candidates}

def get_swap_candidates(excel_path: str, portfolio_sheet: str, company_to_sell: str, client_dna: dict):
    try:
        portfolio_df = pd.read_excel(excel_path, sheet_name=portfolio_sheet)
        cio_list_df = pd.read_excel(excel_path, sheet_name='CIO Recommendation List')
        strategies_df = pd.read_excel(excel_path, sheet_name='Portfolio Strategies')
        
        asset_to_sell = portfolio_df[portfolio_df['Issuer / Asset'].str.contains(company_to_sell, case=False, na=False)]
        if asset_to_sell.empty:
            return {"error": f"La empresa '{company_to_sell}' no se encontró."}
        
        target_asset = asset_to_sell.iloc[0]
        dinero_liberado_chf = target_asset['Current (CHF)']
        sub_asset = target_asset['Sub-Asset Class']
        industry_group = target_asset['Industry Group']
        
        print(f"💰 Vamos a vender {target_asset['Issuer / Asset']} liberando: {dinero_liberado_chf:,.2f} CHF")
        
        mask = (
            (cio_list_df['Rating'] == 'BUY') &
            (cio_list_df['Sub-Asset Class'] == sub_asset) &
            (cio_list_df['Industry Group'] == industry_group) &
            (cio_list_df['Issuer / Asset'] != target_asset['Issuer / Asset'])
        )
        candidates_df = cio_list_df[mask]
        
        if candidates_df.empty:
            return {"error": "No hay candidatos de reemplazo disponibles en el mismo sector con rating BUY."}

        results = []
        for _, row in candidates_df.iterrows():
            candidato_nombre = row['Issuer / Asset']
            posicion_actual = portfolio_df[portfolio_df['Issuer / Asset'] == candidato_nombre]
            dinero_actual_chf = posicion_actual['Current (CHF)'].values[0] if not posicion_actual.empty else 0.0
            
            results.append({
                "Issuer": candidato_nombre,
                "Industry Group": row['Industry Group'], 
                "CIO_View": row['CIO View'],             
                "Rating": row['Rating'],
                "Ya_En_Portfolio": not posicion_actual.empty,
                "Posicion_Actual_CHF": float(round(dinero_actual_chf, 2)),
                "Cuanto_Compramos_CHF": float(round(dinero_liberado_chf, 2)),
                "ISIN": row['ISIN'],
                "Valor": str(row['Valor']).split('.')[0],
                "MIC": str(row['MIC']) if pd.notna(row['MIC']) else "",
            })
            
        llm_scores = get_llm_dna_scores(results, client_dna)
        
        # 6. PROCESAMIENTO DE SCORING (Afinidad Individual 0-100%)
        for cand in results:
            llm_eval = llm_scores.get(cand['Issuer'], {"score": 5, "reason": "Sin evaluación"})
            
            if isinstance(llm_eval, dict):
                score = max(1, llm_eval.get("score", 5))
                reason = llm_eval.get("reason", "No se proporcionó explicación.")
            else:
                score = max(1, int(llm_eval) if str(llm_eval).isdigit() else 5)
                reason = "Explicación omitida por error de formato del LLM."
                
            cand['Explicacion_DNA'] = reason
            cand['Afinidad_DNA_Porcentaje'] = float(score * 10) # 1-10 -> 10-100%
            
            # Limpieza de campos intermedios
            if 'Industry Group' in cand: del cand['Industry Group']
            if 'CIO_View' in cand: del cand['CIO_View']

        # 7. INTEGRACIÓN CON SIX API
        print(f"\n🔗 Conectando con SIX API para obtener precios...")
        for cand in results:
            market_data = get_asset_price_info(
                valor=cand['Valor'], 
                mic=cand['MIC'], 
                isin=cand['ISIN'], 
                issuer_name=cand['Issuer']
            )
            
            if "error" not in market_data and market_data.get("price", 0.0) > 0:
                cand['Precio_Actual_SIX'] = market_data['price']
                cand['Moneda_SIX'] = market_data['currency']
            else:
                cand['Precio_Actual_SIX'] = None
                cand['Error_SIX'] = market_data.get("error", "Precio no disponible")

        # 8. SELECCIONAR EL MEJOR CANDIDATO (Winner takes all)
        valid_candidates = [c for c in results if c.get('Precio_Actual_SIX') is not None]

        if not valid_candidates:
            return {"error": "Ningún candidato superó las validaciones de mercado."}

        # Ordenar por Afinidad_DNA_Porcentaje de forma descendente
        valid_candidates.sort(key=lambda x: x['Afinidad_DNA_Porcentaje'], reverse=True)
        
        best_candidate = valid_candidates[0]
        
        # Asignación del 100% del capital liberado
        best_candidate['Asignacion_Recomendada_CHF'] = float(round(dinero_liberado_chf, 2))
        best_candidate['Nueva_Posicion_Simulada_CHF'] = float(round(best_candidate['Posicion_Actual_CHF'] + dinero_liberado_chf, 2))
        best_candidate['Cantidad_Acciones'] = int(best_candidate['Asignacion_Recomendada_CHF'] / best_candidate['Precio_Actual_SIX'])

        print(f"\n🏆 Ganador: {best_candidate['Issuer']} ({best_candidate['Afinidad_DNA_Porcentaje']}% afinidad)")
        return best_candidate

    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    EXCEL_FILE = "data/SwissHacks Portfolio Construction.xlsx"
    PORTFOLIO_NAME = "Sample Portfolio Balanced"
    TO_SELL = "Apple"
    
    SAMPLE_DNA = json.load(open("raeber_dna.json"))
    
    candidato_final = get_swap_candidates(EXCEL_FILE, PORTFOLIO_NAME, TO_SELL, SAMPLE_DNA)
    print(json.dumps(candidato_final, indent=2, ensure_ascii=False))