import pandas as pd

def get_swap_candidates(excel_path: str, portfolio_sheet: str, company_to_sell: str):
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
        
        print(f"💰 Vamos a vender {target_asset['Issuer / Asset']} liberando: {dinero_liberado_chf:,.2f} CHF")
        
        # 3. Buscar el Límite en la estrategia Macro (Portfolio Strategies)
        # Asumimos que la columna se llama 'Balanced (CHF)' porque usamos el portfolio Balanced
        strategy_row = strategies_df[strategies_df['Sub-Asset Class'] == sub_asset]
        limite_macro_chf = strategy_row['Balanced (CHF)'].values[0] if not strategy_row.empty else "Desconocido"
        
        print(f"📊 Límite Macro para '{sub_asset}': {limite_macro_chf:,.2f} CHF\n")
        
        # 4. Filtrar candidatos en el CIO (Permitiendo empresas que YA tenemos)
        mask = (
            (cio_list_df['Rating'] == 'BUY') &
            (cio_list_df['Sub-Asset Class'] == sub_asset) &
            (cio_list_df['Industry Group'] == target_asset['Industry Group']) &
            (cio_list_df['Issuer / Asset'] != target_asset['Issuer / Asset']) # No podemos comprarnos a nosotros mismos
        )
        candidates = cio_list_df[mask]
        
        # 5. Calcular la nueva realidad financiera para cada candidato
        results = []
        for _, row in candidates.iterrows():
            candidato_nombre = row['Issuer / Asset']
            
            # Ver si ya tenemos esta empresa en el portfolio
            posicion_actual = portfolio_df[portfolio_df['Issuer / Asset'] == candidato_nombre]
            dinero_actual_chf = posicion_actual['Current (CHF)'].values[0] if not posicion_actual.empty else 0.0
            
            # Simular el Swap: Dinero que ya teníamos + Dinero de la venta
            dinero_simulado_chf = dinero_actual_chf + dinero_liberado_chf
            
            results.append({
                "Issuer": candidato_nombre,
                "Rating": row['Rating'],
                "Ya_En_Portfolio": not posicion_actual.empty,
                "Posicion_Actual_CHF": float(round(dinero_actual_chf, 2)),
                "Cuanto_Compramos_CHF": float(round(dinero_liberado_chf, 2)),
                "Nueva_Posicion_Simulada_CHF": float(round(dinero_simulado_chf, 2)),
                "ISIN": row['ISIN'],
                "Valor": str(row['Valor']).split('.')[0],
                "MIC": str(row['MIC']) if pd.notna(row['MIC']) else "",
            })
            
        return results

    except Exception as e:
        return {"error": str(e)}

# ==========================================
# 🧪 PRUEBA DEL NUEVO ENFOQUE
# ==========================================
if __name__ == "__main__":
    EXCEL_FILE = "data/SwissHacks Portfolio Construction.xlsx"
    PORTFOLIO_NAME = "Sample Portfolio Balanced"
    TO_SELL = "Tesla"
    
    candidatos = get_swap_candidates(EXCEL_FILE, PORTFOLIO_NAME, TO_SELL)
    print(candidatos)
    
    if isinstance(candidatos, dict) and "error" in candidatos:
        print("❌ Error:", candidatos["error"])
    elif not candidatos:
        print("⚠️ No se encontraron candidatos. (Revisa si hay espacios extra en los nombres de Industria en el Excel)")
    else:
        print(f"✅ Se encontraron {len(candidatos)} candidato(s) viable(s):")
        for i, cand in enumerate(candidatos, 1):
            print(f"\n[{i}] Empresa: {cand['Issuer']} (¿Ya la tenemos?: {'SÍ' if cand['Ya_En_Portfolio'] else 'NO'})")
            print(f"    - Teníamos: {cand['Posicion_Actual_CHF']:,.2f} CHF")
            print(f"    - Tras el swap pasaremos a tener: {cand['Nueva_Posicion_Simulada_CHF']:,.2f} CHF")