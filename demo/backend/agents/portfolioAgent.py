import os
import json
import re
import pandas as pd
import requests
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

API_KEY = os.environ.get("PHOENIQS_API_KEY")
API_URL = os.environ.get("PHOENIQS_API_URL")
MODEL = os.environ.get("PHOENIQS_MODEL", "inference-gpt-oss-120b")

def get_llm_dna_scores(candidates: list, client_dna: dict) -> dict:
    """
    Usa el LLM para evaluar qué tan bien encaja cada candidato con el DNA del cliente.
    Devuelve un diccionario con puntajes del 1 al 10. Ejemplo: {"Novartis AG": 9, "Alcon": 4}
    """
    if not API_KEY or not API_URL:
        print("⚠️ Advertencia: API keys de Phoeniqs no configuradas. Se usarán puntajes base.")
        return {c['Issuer']: 5 for c in candidates}

    # Preparamos la información de los candidatos para el prompt
    candidates_info = ""
    for c in candidates:
        candidates_info += f"- Empresa: {c['Issuer']} | Sector: {c['Industry Group']} | Razón del CIO: {c['CIO_View']}\n"

    prompt = f"""You are a Private Banking Expert. Evaluate the following investment candidates against the Client's DNA.
Assign a score from 1 to 10 for each candidate (1 = severe conflict/red line, 10 = perfect match with priorities).

CLIENT DNA:
{json.dumps(client_dna, indent=2)}

CANDIDATES TO EVALUATE:
{candidates_info}

INSTRUCTIONS:
1. Penalize strictly if the candidate violates 'redLines' or 'avoidedSectors'.
2. Reward heavily if the candidate matches 'priorities' or 'preferredSectors'.
3. Return ONLY a valid JSON object mapping the exact Issuer name to its integer score. No markdown fences, no text outside JSON.

EXPECTED FORMAT:
{{
  "Issuer A": 8,
  "Issuer B": 2
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
        "temperature": 0.1, # Baja temperatura para que sea más determinista y estricto
        "max_tokens": 5000
    }

    try:
        response = requests.post(f"{API_URL}/chat/completions", headers=headers, json=payload, timeout=45)
        response.raise_for_status()
        response_data = response.json()
        
        # Extraer de forma segura y garantizar que sea un string
        choices = response_data.get("choices", [])
        if not choices:
            raise ValueError(f"Estructura inesperada en la respuesta: {response_data}")
            
        content = choices[0].get("message", {}).get("content")
        
        # Si content es None (null en JSON), disparamos una excepción controlada
        if content is None:
            raise ValueError("La API devolvió 'content': null (Probable filtro de contenido o error del modelo).")
        
        # Limpieza segura de markdown SIN regex problemáticas
        clean_text = content.strip()
        
        # Si empieza con ```json o ``` lo quitamos manualmente
        if clean_text.startswith("```json"):
            clean_text = clean_text[7:]
        elif clean_text.startswith("```"):
            clean_text = clean_text[3:]
            
        # Si termina con ``` lo quitamos manualmente
        if clean_text.endswith("```"):
            clean_text = clean_text[:-3]
            
        clean_text = clean_text.strip()
        
        scores = json.loads(clean_text)
        return scores
        
    except Exception as e:
        print(f"❌ Error al consultar el LLM para los puntajes: {e}")
        # En caso de fallo de red o parseo, devolvemos un puntaje neutro (5) para no romper el pipeline
        return {c['Issuer']: 5 for c in candidates}

def get_swap_candidates(excel_path: str, portfolio_sheet: str, company_to_sell: str, client_dna: dict):
    try:
        # 1. Cargar las TRES pestañas necesarias
        portfolio_df = pd.read_excel(excel_path, sheet_name=portfolio_sheet)
        cio_list_df = pd.read_excel(excel_path, sheet_name='CIO Recommendation List')
        strategies_df = pd.read_excel(excel_path, sheet_name='Portfolio Strategies')
        
        # 2. Identificar qué vamos a vender y cuánto dinero libera
        asset_to_sell = portfolio_df[portfolio_df['Issuer / Asset'].str.contains(company_to_sell, case=False, na=False)]
        if asset_to_sell.empty:
            return {"error": f"La empresa '{company_to_sell}' no se encontró."}
        
        target_asset = asset_to_sell.iloc[0]
        dinero_liberado_chf = target_asset['Current (CHF)']
        sub_asset = target_asset['Sub-Asset Class']
        industry_group = target_asset['Industry Group']
        
        print(f"💰 Vamos a vender {target_asset['Issuer / Asset']} liberando: {dinero_liberado_chf:,.2f} CHF")
        
        # 3. Buscar el Límite Macro
        strategy_row = strategies_df[strategies_df['Sub-Asset Class'] == sub_asset]
        limite_macro_chf = strategy_row['Balanced (CHF)'].values[0] if not strategy_row.empty else "Desconocido"
        
        # 4. Filtrar candidatos en el CIO
        mask = (
            (cio_list_df['Rating'] == 'BUY') &
            (cio_list_df['Sub-Asset Class'] == sub_asset) &
            (cio_list_df['Industry Group'] == industry_group) &
            (cio_list_df['Issuer / Asset'] != target_asset['Issuer / Asset'])
        )
        candidates_df = cio_list_df[mask]
        
        if candidates_df.empty:
            return []

        # 5. Pre-armar la lista de resultados
        results = []
        for _, row in candidates_df.iterrows():
            candidato_nombre = row['Issuer / Asset']
            
            # Ver si ya tenemos esta empresa en el portfolio
            posicion_actual = portfolio_df[portfolio_df['Issuer / Asset'] == candidato_nombre]
            dinero_actual_chf = posicion_actual['Current (CHF)'].values[0] if not posicion_actual.empty else 0.0
            
            results.append({
                "Issuer": candidato_nombre,
                "Industry Group": row['Industry Group'], # Para el LLM
                "CIO_View": row['CIO View'],             # Para el LLM
                "Rating": row['Rating'],
                "Ya_En_Portfolio": not posicion_actual.empty,
                "Posicion_Actual_CHF": float(round(dinero_actual_chf, 2)),
                "Cuanto_Compramos_CHF": float(round(dinero_liberado_chf, 2)),
                "ISIN": row['ISIN'],
                "Valor": str(row['Valor']).split('.')[0],
                "MIC": str(row['MIC']) if pd.notna(row['MIC']) else "",
            })
            
        # 6. LLM SCORING (Llamada a la API)
        llm_scores = get_llm_dna_scores(results, client_dna)
        
        # 7. MATEMÁTICA DETERMINÍSTICA (Asegurar que sume 100%)
        total_score = 0
        for cand in results:
            # Obtener el puntaje del LLM (fallback a 1 si lo omitió o dio 0)
            score = llm_scores.get(cand['Issuer'], 5)
            cand['Raw_LLM_Score'] = max(1, score) 
            total_score += cand['Raw_LLM_Score']
            
        for cand in results:
            # Cálculo del porcentaje exacto
            porcentaje = (cand['Raw_LLM_Score'] / total_score) * 100
            cand['Recomendacion_Porcentaje'] = float(round(porcentaje, 1))
            
            # Dinero exacto a comprar según el porcentaje
            dinero_asignado = (porcentaje / 100) * cand['Cuanto_Compramos_CHF']
            cand['Asignacion_Recomendada_CHF'] = float(round(dinero_asignado, 2))
            
            # Recalcular la nueva posición con el capital asignado real
            cand['Nueva_Posicion_Simulada_CHF'] = float(round(cand['Posicion_Actual_CHF'] + dinero_asignado, 2))
            
            # Limpiar llaves temporales que ya no necesitamos en el output JSON
            del cand['Industry Group']
            del cand['CIO_View']
            del cand['Raw_LLM_Score']
            
        return results

    except Exception as e:
        return {"error": str(e)}

# ==========================================
# 🧪 PRUEBA (Ejecución standalone)
# ==========================================
if __name__ == "__main__":
    EXCEL_FILE = "data/SwissHacks Portfolio Construction.xlsx"
    PORTFOLIO_NAME = "Sample Portfolio Balanced"
    TO_SELL = "Tesla"
    
    # El DNA JSON que proporcionaste (simplificado para el script de prueba)
    SAMPLE_DNA = {
      "values": {
        "priorities": ["capital preservation", "tangible, mature businesses"],
        "redLines": ["no exposure to high-beta speculative asset classes", "avoid speculative US software"],
        "preferredSectors": ["healthcare and pharma (e.g., Johnson & Johnson, Novartis)", "Swiss value equities"]
      }
    }
    
    print("Iniciando Portfolio Agent (Hybrid Mode)...\n")
    candidatos = get_swap_candidates(EXCEL_FILE, PORTFOLIO_NAME, TO_SELL, SAMPLE_DNA)
    
    if isinstance(candidatos, dict) and "error" in candidatos:
        print("❌ Error:", candidatos["error"])
    else:
        print("\n✅ Resultados Finales con Asignación de Capital:")
        print(json.dumps(candidatos, indent=2, ensure_ascii=False))