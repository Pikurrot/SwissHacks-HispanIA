"""
OSINT Agent — Client Lifestyle Enrichment
==========================================
Reads public social media profiles for each client and extracts structured
lifestyle signals using the Phoeniqs LLM. Output is used by the message agent
to personalise RM communications.

Flow per client:
  1. Load Twitter content  → from osint_cache/<client_id>/twitter.txt
  2. Load LinkedIn content → try Proxycurl API first, fall back to osint_cache/<client_id>/linkedin.txt
  3. Send combined text to Phoeniqs for extraction
  4. Save result to <client_id>_osint.json

Why cached files instead of live scraping?
  - Twitter: X's unofficial scraping APIs break regularly after anti-bot updates.
             The official API costs $100/month. Real accounts exist and are shown during demo.
  - LinkedIn: Firecrawl explicitly blocks LinkedIn. Proxycurl works but requires credits.
             Cache files are the guaranteed fallback for demo reliability.
"""

import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

PHOENIQS_API_KEY = os.environ.get("PHOENIQS_API_KEY")
PHOENIQS_API_URL = os.environ.get("PHOENIQS_API_URL")
PHOENIQS_MODEL   = os.environ.get("PHOENIQS_MODEL", "inference-gpt-oss-120b")
PROXYCURL_KEY    = os.environ.get("PROXYCURL_API_KEY")

# Cache dir is relative to this file — works regardless of where you run the script
CACHE_DIR = os.path.join(os.path.dirname(__file__), "osint_cache")

# Add a new client here to make it available to enrich_client_osint()
CLIENT_CONFIG = {
    "huber": {
        "full_name":    "Marius Huber",
        "linkedin_url": os.environ.get("HUBER_LINKEDIN_URL", ""),
    },
    "schneider": {
        "full_name":    "Hubertus Schneider",
        "linkedin_url": os.environ.get("SCHNEIDER_LINKEDIN_URL", ""),
    },
    "raeber": {
        "full_name":    "Eugen Räber",
        "linkedin_url": os.environ.get("RAEBER_LINKEDIN_URL", ""),
    },
    "ammann": {
        "full_name":    "Julian Ammann",
        "linkedin_url": os.environ.get("AMMANN_LINKEDIN_URL", ""),
    },
}


# ── Source loaders ────────────────────────────────────────────────────────────

def load_twitter_cache(client_id: str) -> str:
    """Loads pre-cached Twitter content for a client."""
    path = os.path.join(CACHE_DIR, client_id, "twitter.txt")
    if not os.path.exists(path):
        print(f"⚠️  No Twitter cache for '{client_id}' — skipping")
        return ""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    print(f"✅ Twitter cache loaded ({len(content)} chars)")
    return f"=== TWITTER ===\n{content}"


def fetch_linkedin_proxycurl(url: str) -> str:
    """
    Scrapes a LinkedIn profile via Proxycurl ($0.01 per call).
    Returns empty string on any error so the cache fallback kicks in.
    """
    if not url or not PROXYCURL_KEY:
        return ""
    try:
        res = requests.get(
            "https://nubela.co/proxycurl/api/v2/linkedin",
            params={"url": url},
            headers={"Authorization": f"Bearer {PROXYCURL_KEY}"},
            timeout=30,
        )
        res.raise_for_status()
        data = res.json()

        lines = [
            "=== LINKEDIN (live) ===",
            f"Name: {data.get('full_name', '')}",
            f"Headline: {data.get('headline', '')}",
            f"About: {data.get('summary', '')}",
            "",
            "Experience:",
        ]
        for exp in data.get("experiences", []):
            start = (exp.get("starts_at") or {}).get("year", "")
            end   = (exp.get("ends_at") or {}).get("year", "present")
            lines.append(f"  - {exp.get('title', '')} at {exp.get('company', '')} ({start}–{end})")

        for pub in data.get("accomplishment_publications", []):
            lines.append(f"Publication: {pub.get('name', '')} ({pub.get('publisher', '')})")

        for v in data.get("volunteer_work", []):
            lines.append(f"Volunteer/Board: {v.get('role', '')} at {v.get('company', '')}")

        content = "\n".join(lines)
        print(f"✅ LinkedIn scraped via Proxycurl ({len(content)} chars)")
        return content

    except Exception as e:
        print(f"⚠️  Proxycurl failed: {e} — falling back to cache")
        return ""


def load_linkedin_cache(client_id: str) -> str:
    """Fallback: loads pre-written LinkedIn content from the cache folder."""
    path = os.path.join(CACHE_DIR, client_id, "linkedin.txt")
    if not os.path.exists(path):
        print(f"⚠️  No LinkedIn cache for '{client_id}' — skipping")
        return ""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    print(f"✅ LinkedIn cache loaded ({len(content)} chars)")
    return f"=== LINKEDIN (cached) ===\n{content}"


def fetch_linkedin(client_id: str, linkedin_url: str) -> str:
    """Tries Proxycurl first, then falls back to the local cache file."""
    live = fetch_linkedin_proxycurl(linkedin_url)
    return live if live else load_linkedin_cache(client_id)


# ── Phoeniqs extraction ───────────────────────────────────────────────────────

EXTRACTION_PROMPT = """You are analysing public social media profiles for a private banking client.
Extract lifestyle and interest signals ONLY from what is explicitly stated in the content.

CLIENT: {client_name}

PUBLIC PROFILE CONTENT:
{raw_content}

Return ONLY a valid JSON object (no markdown fences):
{{
  "gastronomy": {{
    "wine": "specific wine preferences if mentioned, else null",
    "dining": "dining habits or preferences if mentioned, else null",
    "habits": "recurring food or drink habits if mentioned, else null"
  }},
  "sports": {{
    "participates": ["list of sports or outdoor activities mentioned"],
    "destinations": ["locations mentioned in context of activities"],
    "level": "casual | serious amateur | competitive — based on evidence"
  }},
  "lifestyle": {{
    "brands": ["brands explicitly mentioned or worn"],
    "travel": "travel style and typical destinations if mentioned",
    "culture": ["cultural interests: art, music, media if mentioned"],
    "causes": ["public causes or organisations they support"]
  }},
  "professional": {{
    "conferences": ["events or summits attended"],
    "publications": ["articles, interviews or papers mentioned"],
    "boards": ["board roles or advisory positions mentioned"]
  }},
  "rapport_triggers": [
    "specific, actionable detail the RM can naturally reference — include dates, places, quotes, names"
  ],
  "redline_signals": [
    "any strong public stance that connects directly to investment values or red lines"
  ],
  "confidence": 0.0
}}

Rules:
- Extract ONLY what is explicitly in the content — never infer or speculate
- rapport_triggers must be concrete: 'visited Borneo May 2024, posted about orangutans near palm oil plantations'
- confidence: 0.0–1.0 based on how much signal the content contains
- If a section has no evidence, use null or empty list
"""


def extract_lifestyle_signals(client_name: str, raw_content: str) -> dict:
    """Sends raw profile text to Phoeniqs and returns a structured lifestyle dict."""
    if not raw_content.strip():
        print("⚠️  No content to analyse — returning empty profile")
        return {}

    payload = {
        "model": PHOENIQS_MODEL,
        "messages": [{"role": "user", "content": EXTRACTION_PROMPT.format(
            client_name=client_name,
            raw_content=raw_content,
        )}],
        "temperature": 0.1,
        "max_tokens": 2000,
    }

    try:
        res = requests.post(
            f"{PHOENIQS_API_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {PHOENIQS_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=45,
        )
        res.raise_for_status()
        text = res.json()["choices"][0]["message"]["content"].strip()

        # Strip markdown fences if the model wrapped the JSON
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]

        signals = json.loads(text.strip())
        print(f"✅ Signals extracted (confidence: {signals.get('confidence', '?')})")
        return signals

    except Exception as e:
        print(f"❌ Phoeniqs extraction failed: {e}")
        return {}


# ── Main entry point ──────────────────────────────────────────────────────────

def enrich_client_osint(client_id: str) -> dict:
    """
    Full OSINT pipeline for one client.
    Returns the enriched profile dict and saves it to <client_id>_osint.json.
    """
    config = CLIENT_CONFIG.get(client_id)
    if not config:
        print(f"❌ No config for client '{client_id}'. Available: {list(CLIENT_CONFIG)}")
        return {}

    print(f"\n🔍 OSINT enrichment — {config['full_name']}\n{'─'*45}")

    twitter_content  = load_twitter_cache(client_id)
    linkedin_content = fetch_linkedin(client_id, config.get("linkedin_url", ""))

    raw = "\n\n".join(filter(None, [twitter_content, linkedin_content]))
    if not raw.strip():
        print("⚠️  No content from any source — aborting")
        return {}

    signals = extract_lifestyle_signals(config["full_name"], raw)

    result = {
        "client_id":         client_id,
        "full_name":         config["full_name"],
        "lifestyle_profile": signals,
        "osint_confidence":  signals.get("confidence", 0),
    }

    output_file = os.path.join(os.path.dirname(__file__), f"{client_id}_osint.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\n✅ Saved → {output_file}")

    return result


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    # Usage: python osintAgent.py [client_id]
    # If no argument is given, runs all clients
    targets = [sys.argv[1]] if len(sys.argv) > 1 else list(CLIENT_CONFIG)

    for cid in targets:
        result = enrich_client_osint(cid)
        if result:
            triggers = result.get("lifestyle_profile", {}).get("rapport_triggers", [])
            print(f"\n📋 Rapport triggers for {result['full_name']}:")
            for t in triggers:
                print(f"   → {t}")
        print()
