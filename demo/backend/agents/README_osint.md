# OSINT Agent

Extracts lifestyle signals from public social media profiles and converts them into structured data the RM (relationship manager) can use to personalise client communications.

---

## What it does

For each client, it:
1. Loads their Twitter content from a local cache file
2. Scrapes their LinkedIn via Proxycurl (falls back to local cache if it fails)
3. Sends the combined text to Phoeniqs (LLM) for extraction
4. Saves the result to `<client_id>_osint.json`

The output includes `rapport_triggers` — concrete, date-stamped details the RM can drop naturally into a call or message.

Example output:
```
→ Visited Borneo for five weeks in mid-2024, posted about orangutans 200m from a palm oil plantation
→ Natural wine weekend in Valais, February 2024 — biodynamic Petite Arvine, no sulphites
→ Fly-fishing on the Soča river, Slovenia, December 2023
```

---

## Setup

Add to `.env`:
```
PHOENIQS_API_KEY=...
PHOENIQS_API_URL=https://maas.phoeniqs.com/v1
PHOENIQS_MODEL=inference-gpt-oss-120b
PROXYCURL_API_KEY=...          # proxycurl.com — $0.01/profile, has free trial
HUBER_LINKEDIN_URL=https://www.linkedin.com/in/marius-huber-404397418/
```

Install dependencies:
```bash
pip install requests python-dotenv
```

---

## Run

```bash
# All clients
python osintAgent.py

# Single client
python osintAgent.py huber
python osintAgent.py ammann
```

---

## File structure

```
agents/
  osintAgent.py
  osint_cache/
    huber/
      twitter.txt       ← real @MHuber_Rewild account content, manually cached
      linkedin.txt      ← fallback if Proxycurl fails or profile not indexed yet
    schneider/
      twitter.txt
      linkedin.txt
    raeber/
      linkedin.txt      ← no Twitter (minimal online presence by choice)
    ammann/
      twitter.txt
      linkedin.txt
```

---

## Why cached files?

**Twitter:** X's unofficial scraping libraries (twikit, twscrape) break regularly after anti-bot updates. The official API costs $100/month. The real accounts exist and are shown to judges — the pipeline just uses pre-fetched content from those same accounts.

**LinkedIn:** Firecrawl explicitly does not support LinkedIn. Proxycurl is purpose-built for it and works well, but requires API credits. The cache is the guaranteed demo fallback.

---

## Dashboard integration

The agent outputs one JSON file per client: `<client_id>_osint.json`.

### JSON structure

```
{
  "client_id":        string,
  "full_name":        string,
  "osint_confidence": float (0.0–1.0),
  "lifestyle_profile": {
    "gastronomy": {
      "wine":   string | null,
      "dining": string | null,
      "habits": string | null
    },
    "sports": {
      "participates": string[],
      "destinations": string[],
      "level":        "casual" | "serious amateur" | "competitive" | null
    },
    "lifestyle": {
      "brands":  string[],
      "travel":  string | null,
      "culture": string[],
      "causes":  string[]
    },
    "professional": {
      "conferences":  string[],
      "publications": string[],
      "boards":       string[]
    },
    "rapport_triggers": string[],
    "redline_signals":  string[],
    "confidence":       float
  }
}
```

### What to show and how

| Field | Component | Notes |
|---|---|---|
| `osint_confidence` | Colour badge | ≥0.85 green, ≥0.65 yellow, <0.65 grey |
| `rapport_triggers` | Highlighted list in client profile | Show max 3–4, most recent first |
| `redline_signals` | Alert panel with warning icon | Only render if array is non-empty |
| `sports.participates` + `lifestyle.causes` | Chips / tags | Good for visual client DNA |
| `gastronomy.wine` | Text line in lifestyle section | Hide if null |
| `lifestyle.travel` | Text line in lifestyle section | Hide if null |
| `professional.boards` + `conferences` | Collapsible section | Less urgent, useful for RM onboarding |

### What the frontend must handle

- **Nulls and empty arrays** — many fields are `null` or `[]` depending on the client. Render conditionally, never show an empty section.
- **Variable confidence** — Räber has 0.71 (LinkedIn only). Show the badge so the RM knows how much to rely on the profile.
- **`rapport_triggers` is free text** — not sub-structured. Display as-is, ready to read.
- **`redline_signals` is critical** — when non-empty, the RM must see it before any swap proposal or outbound message. Use distinct visual treatment (red/orange).

### Suggested API endpoint

```
GET /api/clients/{client_id}/osint
→ returns the full JSON above
```

The backend can serve the file directly from disk or load it into the client record. The frontend decides what to render and when.

### Current data snapshot

| Client | Confidence | Rapport triggers (examples) | Redlines |
|---|---|---|---|
| huber | 0.93 | Borneo trip Jun 2024; biodynamic wine Valais; fly-fishing Soča | Palm oil, Wilmar, greenwashing |
| schneider | 0.78 | Verbier ski trip; Basel–Zurich cycling; automotive forum Zug | — |
| raeber | 0.71 | Sold company 2016; holiday home Flims; Rotary Küsnacht | — |
| ammann | 0.78 | Art purchase Kunsthaus; governance keynote; 38th store Lausanne | Supply chain opacity, labor rights |

---

## Adding a new client

1. Add an entry to `CLIENT_CONFIG` in `osintAgent.py`
2. Create `osint_cache/<client_id>/twitter.txt` and/or `linkedin.txt`
3. Optionally add `<CLIENT_ID>_LINKEDIN_URL` to `.env` for live Proxycurl scraping
4. Run `python osintAgent.py <client_id>`
