import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

SIX_MCP_URL = os.environ.get("SIX_MCP_URL", "https://ca-mcpwebapi-tools.nicepebble-599ed11f.westeurope.azurecontainerapps.io/mcp")
SIX_TOKEN = os.environ.get("SIX_MCP_TOKEN")

def call_six_tool(tool_name: str, arguments: dict) -> str:
    """Función base para llamar a SIX MCP JSON-RPC."""
    if not SIX_TOKEN:
        raise ValueError("Falta el SIX_MCP_TOKEN en el archivo .env")

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": SIX_TOKEN if SIX_TOKEN.startswith("Bearer ") else f"Bearer {SIX_TOKEN}"
    }

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments}
    }

    response = requests.post(SIX_MCP_URL, headers=headers, json=payload, timeout=30)
    
    try:
        data = response.json()
    except json.JSONDecodeError:
        raw_text = response.text.strip()
        json_str = [line.replace("data:", "").strip() for line in raw_text.split('\n') if line.startswith("data:")][0]
        data = json.loads(json_str)

    if "error" in data:
        raise Exception(f"SIX API Error: {data['error']}")

    content_array = data.get("result", {}).get("content", [])
    text_content = next((c.get("text", "") for c in content_array if c.get("type") == "text"), "")
    
    if data.get("result", {}).get("isError"):
        raise Exception(f"SIX Tool Error: {text_content}")
        
    return text_content

def parse_six_table(text: str) -> list:
    """Convierte TSV a Lista de Diccionarios."""
    text = text.replace('\r', '') # Limpieza crucial
    lines = [line for line in text.strip().split('\n') if line]
    if len(lines) < 2:
        return []
    
    headers = lines[0].split('\t')
    results = []
    
    for line in lines[1:]:
        cells = line.split('\t')
        row = {headers[i]: (cells[i] if i < len(cells) else "") for i in range(len(headers))}
        results.append(row)
        
    return results

def _fetch_price_for_listing(listing_id: str) -> dict:
    """
    Función interna: Hace la llamada HTTP para UN solo listing_id.
    Tolerante a cambios de nombre de columnas (close.value vs value).
    """
    precio_final = 0.0
    timestamp_final = ""
    moneda_final = "Unknown"
    
    try:
        # Intento 1: Precio de cierre (End of Day)
        snap_raw = call_six_tool("end_of_day_snapshot", {
            "mode": "execute",
            "listing_ids": [listing_id],
            "fields": ["close.value", "close.timestamp"]
        })
        snap_data = parse_six_table(snap_raw)
        
        if snap_data:
            row = snap_data[0]
            val_key = "close.value" if "close.value" in row else "value" if "value" in row else None
            time_key = "close.timestamp" if "close.timestamp" in row else "timestamp" if "timestamp" in row else None
            
            if val_key and str(row.get(val_key, "")).strip():
                precio_final = float(str(row[val_key]).strip())
                timestamp_final = str(row.get(time_key, ""))
        
        # Intento 2: Precio en vivo (Intraday) si el de cierre falló
        if precio_final == 0.0:
            intra_raw = call_six_tool("intraday_snapshot", {
                "mode": "execute",
                "listing_ids": [listing_id],
                "fields": ["last.value", "last.timestamp"]
            })
            intra_data = parse_six_table(intra_raw)
            if intra_data:
                row = intra_data[0]
                val_key = "last.value" if "last.value" in row else "value" if "value" in row else None
                time_key = "last.timestamp" if "last.timestamp" in row else "timestamp" if "timestamp" in row else None
                
                if val_key and str(row.get(val_key, "")).strip():
                    precio_final = float(str(row[val_key]).strip())
                    timestamp_final = str(row.get(time_key, ""))
                    
        # Si conseguimos un precio válido, buscamos la moneda
        if precio_final > 0.0:
            listing_raw = call_six_tool("listing_base", {
                "mode": "execute",
                "listing_ids": [listing_id],
                "fields": ["listingCurrency"]
            })
            listing_data = parse_six_table(listing_raw)
            if listing_data and "listingCurrency" in listing_data[0]:
                moneda_final = listing_data[0]["listingCurrency"]
                
            return {
                "price": precio_final,
                "currency": moneda_final,
                "timestamp": timestamp_final
            }
            
    except Exception as e:
        print(f"   [DEBUG] Error interno consultando {listing_id}: {e}")
        
    return {} # Diccionario vacío si falla

def get_asset_price_info(valor: str, mic: str, isin: str, issuer_name: str) -> dict:
    """
    Obtiene el precio de manera secuencial y segura.
    1. Intenta el mercado original.
    2. Si falla, busca el mercado más líquido como fallback.
    """
    original_listing = f"{valor}_{mic}" if valor and mic else None
    
    # 1. Intentar con el mercado exacto que nos llegó del Excel
    if original_listing:
        print(f"📡 Intentando mercado original: {original_listing}...")
        result = _fetch_price_for_listing(original_listing)
        if result and result.get("price", 0.0) > 0.0:
            print(f"✅ ¡Éxito! Liquidez encontrada en el mercado original.")
            return result
            
    # 2. Plan B: Si el mercado original falló (ej. Novartis en XSWX no retorna datos)
    search_query = isin if isin else issuer_name
    print(f"⚠️ Sin liquidez en original. Buscando mercado alternativo para: {search_query}...")
    
    try:
        search_raw = call_six_tool("find_instrument", {"text": search_query, "size": 1})
        search_data = parse_six_table(search_raw)
        
        if search_data:
            mejor_valor = search_data[0].get("hit.valor")
            mejor_mic = search_data[0].get("hit.mostLiquidMarket.mic")
            
            if mejor_valor and mejor_mic:
                alt_listing = f"{mejor_valor}_{mejor_mic}"
                # Evitar volver a buscar si SIX nos sugirió el mismo mercado que ya falló
                if alt_listing != original_listing:
                    print(f"📡 Intentando mercado alternativo sugerido por SIX: {alt_listing}...")
                    result = _fetch_price_for_listing(alt_listing)
                    if result and result.get("price", 0.0) > 0.0:
                        print(f"✅ ¡Éxito! Liquidez encontrada en mercado alternativo.")
                        return result
                        
        return {"error": "No se encontraron datos de precio para este activo en ningún mercado."}
        
    except Exception as e:
        return {"error": str(e)}

# ==========================================
# 🧪 PRUEBA INTEGRADA: PORTFOLIO AGENT -> SIX API
# ==========================================
if __name__ == "__main__":
    candidato_ejemplo = {
        "Issuer": "Apple Inc.",
        "ISIN": "US0378331005", 
        "Asignacion_Recomendada_CHF": 112461.84,
        "Valor": "908440",
        "MIC": "XNAS"
    }
    
    print(f"🏦 Analizando propuesta para comprar {candidato_ejemplo['Issuer']} con {candidato_ejemplo['Asignacion_Recomendada_CHF']:,.2f} CHF")
    
    market_data = get_asset_price_info(
        valor=candidato_ejemplo["Valor"], 
        mic=candidato_ejemplo["MIC"],
        isin=candidato_ejemplo["ISIN"],
        issuer_name=candidato_ejemplo["Issuer"]
    )
    print(market_data)
    
    if "error" in market_data:
        print(f"❌ Error de Mercado: {market_data['error']}")
    else:
        precio_actual = market_data['price']
        moneda = market_data['currency']
        
        print("\n✅ Datos de Mercado (SIX API):")
        print(f"   - Precio de cierre: {precio_actual} {moneda}")
        print(f"   - Actualizado al: {market_data['timestamp']}")
        
        if precio_actual > 0:
            cantidad_acciones = int(candidato_ejemplo["Asignacion_Recomendada_CHF"] / precio_actual)
            print("\n📝 PROPUESTA LISTA PARA EL MESSAGE AGENT:")
            print(f"   'Estimado cliente, sugerimos invertir sus {candidato_ejemplo['Asignacion_Recomendada_CHF']:,.2f} CHF en {candidato_ejemplo['Issuer']}.")
            print(f"    Al precio actual de mercado de {precio_actual} {moneda}, esto equivale a comprar {cantidad_acciones} acciones.'")