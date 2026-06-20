import os
import json
import time
import requests
import re
from dotenv import load_dotenv

load_dotenv()

NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "")
NEWSAI_URL = os.environ.get("NEWSAI_API_URL", "https://eventregistry.org/api/v1")

news_cache = {}
CACHE_TTL = 15 * 60 

def fetch_real_news(query: str, limit: int = 5) -> list:
    if not NEWSAPI_KEY:
        print("⚠️ No NEWSAPI_KEY found, skipping fetch.")
        return []

    cache_key = re.sub(r'\s+', '_', query.lower())[:60]
    cached = news_cache.get(cache_key)
    
    if cached and (time.time() - cached['ts'] < CACHE_TTL):
        return cached['alerts']

    try:
        payload = {
            "apiKey": NEWSAPI_KEY,
            "keyword": query,
            "articlesCount": limit,
            "articlesSortBy": "date",
            "articlesSortByAsc": False,
            "dataType": ["news"],
            "lang": "eng",
            "resultType": "articles",
            "includeArticleTitle": True,
            "includeArticleSummary": True,
            "includeArticleEventUri": True,
            "includeArticleSource": True,
            "includeArticleBasicInfo": True,
            "includeArticleSentiment": True,
        }
        
        res = requests.post(f"{NEWSAI_URL}/article/getArticles", json=payload, timeout=10)
        res.raise_for_status()
        articles = res.json().get("articles", {}).get("results", [])
        alerts = []
        
        for i, a in enumerate(articles):
            sentiment_val = a.get("sentiment", 0)
            sentiment_label = 'positive' if sentiment_val > 0.15 else 'negative' if sentiment_val < -0.15 else 'neutral'
            
            alerts.append({
                "id": f"real-{cache_key}-{i}",
                "headline": a.get("title", ""),
                "summary": str(a.get("summary") or a.get("body") or "")[:350],
                "source": a.get("source", {}).get("title", "News"),
                "publishedAt": a.get("dateTime", time.strftime("%Y-%m-%dT%H:%M:%SZ")),
                "url": a.get("url", ""),
                "sentiment": sentiment_label,
                "sentimentScore": sentiment_val,
                "relevanceScore": 0.6,
                "affectedISINs": [],
                "affectedSectors": [],
                "alertType": "market",
                "isMock": False,
            })

        news_cache[cache_key] = {"alerts": alerts, "ts": time.time()}
        return alerts
    except Exception as e:
        print(f"Error fetching news for '{query}': {e}")
        return []

def find_relevant_news(client_id: str, real_news: list, dna: dict) -> list:
    values = dna.get("values", {})
    priorities = values.get("priorities", [])
    red_lines = values.get("redLines", [])
    esg_focus = values.get("esgFocus", [])

    def get_keywords(phrase):
        return [word.lower() for word in phrase.replace('-', ' ').split() if len(word) > 4]

    for alert in real_news:
        text = f"{alert['headline']} {alert['summary']}".lower()
        score = alert['relevanceScore']
        
        for priority in priorities:
            if any(kw in text for kw in get_keywords(priority)):
                score = min(1.0, score + 0.05)
        for red_line in red_lines:
            if any(kw in text for kw in get_keywords(red_line)):
                score = min(1.0, score + 0.1)
        for esg in esg_focus:
            if any(kw in text for kw in get_keywords(esg)):
                score = min(1.0, score + 0.05)
                
        alert['relevanceScore'] = round(score, 2)

    relevant_news = [a for a in real_news if a['relevanceScore'] >= 0.65]
    
    if not relevant_news:
        relevant_news = sorted(real_news, key=lambda x: x['relevanceScore'], reverse=True)[:3]
    else:
        relevant_news.sort(key=lambda x: x['relevanceScore'], reverse=True)
        relevant_news = relevant_news[:15] # Select num of relevant news

    output_filename = f"{client_id.lower()}_news.json"
    with open(output_filename, 'w', encoding='utf-8') as f:
        json.dump(relevant_news, f, indent=2, ensure_ascii=False)
        
    print(f"{len(relevant_news)} relevant news alerts scored and saved to {output_filename}")
    return relevant_news


# ------ LLM Call for alerts -------
def analyze_news_with_llm(news_alerts: list, dna: dict) -> list:
    if not news_alerts:
        return []
        
    print("🤖 Analyzing news context with LLM for belief and value alignment...")
    
    API_KEY = os.environ.get("PHOENIQS_API_KEY")
    API_URL = os.environ.get("PHOENIQS_API_URL")
    MODEL = os.environ.get("PHOENIQS_MODEL", "inference-gpt-oss-120b")
    
    if not API_KEY or not API_URL:
        print("Phoeniqs API credentials missing in .env. Skipping LLM analysis.")
        return news_alerts

    # 1. Extraemos solo las partes del ADN que importan para no saturar los tokens
    dna_context = {
        "priorities": dna.get("values", {}).get("priorities", []),
        "redLines": dna.get("values", {}).get("redLines", []),
        "preferredSectors": dna.get("values", {}).get("preferredSectors", []),
        "esgFocus": dna.get("values", {}).get("esgFocus", []) # Añadimos ESG porque suele tener gran carga de valores
    }
    
    # 2. Preparamos las noticias resumiéndolas para el prompt
    news_context = [{"id": a["id"], "headline": a["headline"], "summary": a["summary"]} for a in news_alerts]
    
    prompt = f"""You are an expert wealth management AI specialized in behavioral finance. Your task is to analyze news articles to determine if they positively or negatively trigger a client's deep personal beliefs, values, and investment red lines.

CLIENT DNA (Beliefs & Values):
{json.dumps(dna_context, indent=2)}

NEWS ARTICLES:
{json.dumps(news_context, indent=2)}

For each article, deeply analyze its emotional and strategic alignment with the client's values. Does this company's action violate a red line? Does it champion a cause they care about?

Return a pure JSON object (no markdown, no markdown fences) with the following structure:
{{
  "analysis": [
    {{
      "id": "the exact id of the news article",
      "alertType": "opportunity" | "conflict" | "market",
      "company": "Nestle", "Tencent", "Miscrosoft",
      "belief_alignment": "positive" | "negative" | "neutral",
      "affectedSectors": ["Sector 1", "Sector 2"],
      "portfolio_impact": "2-3 short sentences explicitly stating how this news aligns or clashes with the client's personal beliefs, priorities, or red lines."
    }}
  ]
}}

Rules for alertType and belief_alignment:
- "conflict" & "negative": The news violates a red line or goes against their core beliefs (e.g., funding cuts to a disease they care about, bad labor practices if they care about ESG).
- "opportunity" & "positive": The news strongly supports a priority or ESG focus (e.g., a breakthrough in a research area they fund).
- "market" & "neutral": General news with no strong emotional or belief-based trigger.
- "company" should be the company the article talks about
"""

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 5000
    }
    
    try:
        response = requests.post(f"{API_URL}/chat/completions", headers=headers, json=payload, timeout=60)
        response.raise_for_status() 
        response_data = response.json()
        
        choices = response_data.get("choices", [])
        if not choices:
            raise ValueError(f"Unexpected API response structure: {response_data}")
            
        content = choices[0].get("message", {}).get("content")
        
        if not content:
            print(f"Raw API Response:\n{json.dumps(response_data, indent=2)}")
            raise ValueError("API returned empty content.")
            
        clean_text = content.strip()
        clean_text = re.sub(r"^```(?:json)?\n?", "", clean_text)
        clean_text = re.sub(r"\n?```$", "", clean_text)
        
        analysis_data = json.loads(clean_text).get("analysis", [])
        
        analysis_map = {item["id"]: item for item in analysis_data}
        
        # --- NUEVO: Creamos una estructura limpia exactamente como la pediste ---
        final_clean_analysis = []
        
        for alert in news_alerts:
            insight = analysis_map.get(alert["id"], {})
            
            final_clean_analysis.append({
                "id": alert["id"],
                "headline": alert["headline"], # Lo mantenemos para que sepas qué noticia es
                "url": alert["url"],           # Lo mantenemos por si necesitas el link
                "alertType": insight.get("alertType", alert.get("alertType", "market")),
                "company": insight.get("company", "Unknown"),
                "belief_alignment": insight.get("belief_alignment", "neutral"),
                "affectedSectors": insight.get("affectedSectors", alert.get("affectedSectors", [])),
                "portfolio_impact": insight.get("portfolio_impact", "")
            })
                
        print("Value-alignment LLM analysis complete and formatted into strict JSON!")
        
        # Devolvemos exactamente la estructura solicitada: {"analysis": [...]}
        return {"analysis": final_clean_analysis}
        
    except requests.exceptions.RequestException as e:
        print(f"API Request Failed during analysis: {e}")
    except json.JSONDecodeError as e:
        print(f"Failed to parse AI response as JSON: {e}")
        if 'clean_text' in locals():
             print(f"Raw Output:\n{clean_text}")
    except Exception as e:
        print(f"Error during LLM analysis: {e}")
        
    # Fallback in case of error
    return {"analysis": []}



def compile_news_feed(client_id: str, dna_filepath: str):
    try:
        with open(dna_filepath, 'r', encoding='utf-8') as f:
            dna = json.load(f)
    except FileNotFoundError:
        print(f"Error: DNA file '{dna_filepath}' not found.")
        return

    preferred_sectors = dna.get("values", {}).get("preferredSectors", [])
    raw_queries = preferred_sectors[:3] if preferred_sectors else ["Global Markets"]
    
    def simplify_query(text):
        base_concept = re.split(r'\(|\/| with | for | dedicated | to | and | in ', text, flags=re.IGNORECASE)[0]
        clean_text = re.sub(r'[^a-zA-Z0-9\s]', ' ', base_concept)
        keywords = [w for w in clean_text.split() if len(w) > 3]
        return " ".join(keywords[:2]) if keywords else "Markets"

    real_news = []
    for raw_query in raw_queries:
        query = simplify_query(raw_query)
        articles = fetch_real_news(query, limit=5)
        real_news.extend(articles)

    if not real_news:
        print("No real news articles found.")
        return

    # 1. Score, filter, and save initial results
    relevant_news = find_relevant_news(client_id, real_news, dna)
    
    # 2. Pass to the LLM (Ready for your implementation!)
    analyzed_news = analyze_news_with_llm(relevant_news, dna)
    
    final_output_filename = f"{client_id.lower()}_analyzed_news.json"
    with open(final_output_filename, 'w', encoding='utf-8') as f:
        json.dump(analyzed_news, f, indent=2, ensure_ascii=False)
    print(f"Final LLM-enriched news feed saved to {final_output_filename}")

if __name__ == "__main__":
    TARGET_CLIENT_ID = "schneider" 
    DNA_FILE_PATH = "hubertus_schneider_dna.json" 
    
    compile_news_feed(TARGET_CLIENT_ID, DNA_FILE_PATH)