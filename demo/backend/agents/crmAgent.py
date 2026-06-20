import os
import json
import re
import pandas as pd
import requests
from dotenv import load_dotenv

# Load environment variables from .env file automatically
load_dotenv()

# Ensure these environment variables are set before running the script
API_KEY = os.environ.get("PHOENIQS_API_KEY")
API_URL = os.environ.get("PHOENIQS_API_URL")
MODEL = os.environ.get("PHOENIQS_MODEL", "inference-gpt-oss-120b")

OSINT_DIR = os.path.join(os.path.dirname(__file__), 'osint_cache')


def build_osint_messages(client_id: str) -> list:
    """
    Parse osint_cache/{client_id}/twitter.txt and linkedin.txt into CRM-compatible
    message strings. Each Twitter event and LinkedIn post becomes its own row;
    the LinkedIn profile header becomes a single summary row.
    Returns a list of formatted strings ready to merge with CRM messages.
    """
    messages = []
    base = os.path.join(OSINT_DIR, client_id)

    # --- Twitter: each [DATE] block → one row ---
    tw_path = os.path.join(base, 'twitter.txt')
    if os.path.exists(tw_path):
        with open(tw_path, encoding='utf-8') as f:
            tw_text = f.read()
        for m in re.finditer(r'\[(\d{4}-\d{2}-\d{2})\]\s*(.*?)(?=\n\[|\Z)', tw_text, re.DOTALL):
            date, note = m.group(1), m.group(2).strip()
            if note:
                messages.append(f"Date: {date}\nSource: Twitter\nNote: {note}")

    # --- LinkedIn posts: each [DATE] block in "Recent Posts" → one row ---
    li_path = os.path.join(base, 'linkedin.txt')
    if os.path.exists(li_path):
        with open(li_path, encoding='utf-8') as f:
            li_text = f.read()

        posts_match = re.search(r'Recent Posts:(.*?)(?:Skills:|$)', li_text, re.DOTALL)
        if posts_match:
            for m in re.finditer(r'\[(\d{4}-\d{2}-\d{2})\]\s*(.*?)(?=\n\[|\Z)', posts_match.group(1), re.DOTALL):
                date, note = m.group(1), m.group(2).strip()
                if note:
                    messages.append(f"Date: {date}\nSource: LinkedIn Post\nNote: {note}")

        # LinkedIn profile summary (everything except the Recent Posts section)
        profile_text = re.sub(r'Recent Posts:.*', '', li_text, flags=re.DOTALL).strip()
        if profile_text:
            messages.append(f"Date: Unknown\nSource: LinkedIn Profile\nNote: {profile_text}")

    if messages:
        print(f"  📡 OSINT: loaded {len(messages)} rows for '{client_id}' "
              f"({tw_path and os.path.exists(tw_path) and 'Twitter ✓' or 'Twitter ✗'}, "
              f"{li_path and os.path.exists(li_path) and 'LinkedIn ✓' or 'LinkedIn ✗'})")
    return messages


def extract_and_save_dna(excel_path: str, client_name: str):
    """
    Reads an Excel file, finds the sheet for the specific client, extracts the notes,
    generates a Client DNA profile using the Phoeniqs API, and saves the output to a JSON file.
    """
    if not API_KEY or not API_URL:
        print("Error: PHOENIQS_API_KEY and PHOENIQS_API_URL must be set.")
        return

    print(f"Loading Excel file from: {excel_path}...")
    try:
        # Load the Excel file object to inspect sheet names
        xls = pd.ExcelFile(excel_path)
        sheet_names = xls.sheet_names
        
        # Extract surname from the client name (e.g., "Hubertus Schneider" -> "Schneider")
        surname = client_name.split()[-1]
        
        # Find the matching sheet name (case-insensitive)
        target_sheet = None
        for sheet in sheet_names:
            if surname.lower() in sheet.lower():
                target_sheet = sheet
                break
                
        if not target_sheet:
            raise ValueError(f"Could not find a sheet for client '{client_name}' (looked for '{surname}'). Available sheets: {sheet_names}")
            
        print(f"Found matching sheet: '{target_sheet}' for client '{client_name}'")
        
        # Read the specific sheet
        df = pd.read_excel(xls, sheet_name=target_sheet)
        
        # --- NEW: Extract Date and Note together ---
        # Find the date column
        date_col = None
        for possible_col in ['Date', 'date', 'Excel Date']:
            if possible_col in df.columns:
                date_col = possible_col
                break
                
        # Find the note column
        note_col = None
        for possible_col in ['Note', 'message', 'Notes', 'Message']:
            if possible_col in df.columns:
                note_col = possible_col
                break
                
        if not note_col:
            raise ValueError(f"The sheet '{target_sheet}' does not contain a recognizable notes/message column. Columns found: {df.columns.tolist()}")

        # Ensure we have data to process
        df = df.dropna(subset=[note_col])
        
        messages = []
        for index, row in df.iterrows():
            note_text = str(row[note_col])
            
            # Format the date if we found a date column
            date_str = "Unknown Date"
            if date_col and pd.notna(row[date_col]):
                try:
                    # Convert to datetime, handling potential Excel serial numbers or standard strings
                    if isinstance(row[date_col], (int, float)):
                        # Handle Excel serial dates
                        dt = pd.to_datetime('1899-12-30') + pd.to_timedelta(row[date_col], 'D')
                    else:
                        dt = pd.to_datetime(row[date_col])
                        
                    date_str = dt.strftime('%Y-%m-%d')
                except Exception:
                    date_str = str(row[date_col]) # Fallback to raw string if parsing fails
            
            # Combine Date, Source and Note
            messages.append(f"Date: {date_str}\nSource: CRM\nNote: {note_text}")

        print(f"Successfully extracted {len(messages)} CRM messages from '{target_sheet}'.")

        # Merge OSINT rows (prepend so CRM entries remain authoritative)
        client_id = client_name.split()[-1].lower()
        osint_messages = build_osint_messages(client_id)
        if osint_messages:
            messages = osint_messages + messages

        stacked_logs = "\n\n---\n\n".join(messages)
        print(f"Total input rows for DNA extraction: {len(messages)} "
              f"({len(osint_messages)} OSINT + {len(messages) - len(osint_messages)} CRM)")
        
    except Exception as e:
        print(f"Failed to read Excel file: {e}")
        return

    prompt = f"""You are an expert private banking analyst. Analyse the following relationship intelligence logs for client "{client_name}" and extract their investment DNA.

Each log entry includes a Date, a Source (CRM, Twitter, LinkedIn Post, or LinkedIn Profile), and a Note.
- CRM entries are formal advisor notes — highest weight for investment preferences.
- Twitter / LinkedIn entries reveal personal values, lifestyle, and publicly stated red lines — use them to enrich the DNA but give CRM notes priority when there is a conflict.
- Count how many entries came from each source for the sourceSummary field.

LOGS:
{stacked_logs}

Return a JSON object with EXACTLY this structure (no markdown, pure JSON):
{{
  "values": {{
    "priorities": ["array of 3-5 key personal/investment priorities"],
    "redLines": ["array of explicit deal-breakers or non-negotiables stated by client"],
    "preferredSectors": ["sectors or themes the client favours"],
    "avoidedSectors": ["sectors the client explicitly avoids"],
    "esgFocus": ["specific ESG themes mentioned"]
  }},
  "investmentBehavior": {{
    "riskTolerance": "conservative|moderate|aggressive",
    "timeHorizon": "short description of time horizon",
    "liquidity": "short description of liquidity needs",
    "mandate": "name of their investment mandate"
  }},
  "lifeEvents": [
    {{
      "date": "YYYY-MM-DD",
      "type": "category (e.g. health_crisis, family, business, philanthropy)",
      "description": "1-2 sentence description",
      "portfolioImpact": "how this affects their portfolio priorities"
    }}
  ],
  "personalProfile": {{
    "interests": ["list of personal interests"],
    "quirks": ["notable habits or personal quirks"],
    "rapportTriggers": ["topics or references that build rapport"],
    "avoidTopics": ["topics to avoid in conversation"]
  }},
  "communicationStyle": {{
    "language": "de|en|fr",
    "tone": "formal|informal",
    "preferred": "values-led|data-driven|executive|collaborative",
    "formatPreference": "optional: e.g. tables and numbers, bullet points",
    "directness": "direct|diplomatic",
    "detailLevel": "summary|detailed",
    "openingStyle": "personal|professional"
  }},
  "keyQuotes": [
    {{
      "quote": "exact or near-exact quote from the log",
      "source": "CRM|Twitter|LinkedIn Post|LinkedIn Profile",
      "date": "YYYY-MM-DD or Unknown"
    }}
  ],
  "sourceSummary": {{
    "crm": 0,
    "twitter": 0,
    "linkedin_post": 0,
    "linkedin_profile": 0
  }},
  "confidence": 0.0,
  "sourcedFrom": [1, 2, 3]
}}

Rules:
- confidence: 0.0-1.0 based on how much evidence exists across all sources
- sourcedFrom: array of log entry numbers [1-indexed] that most informed the DNA
- keyQuotes: 3-5 most revealing quotes; include source and date for each
- sourceSummary: count of input entries per source type (not zero — fill from the actual logs)
- Be specific about redLines — these are critical for conflict detection
- Life events should be ordered chronologically; date should match exactly what appears in the text
"""

    print(f"🤖 Sending data to API ({MODEL})...")
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
        response = requests.post(f"{API_URL}/chat/completions", headers=headers, json=payload, timeout=45)
        response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)
        response_data = response.json()
        
        # Safely extract content using dictionary `.get()`
        choices = response_data.get("choices", [])
        if not choices:
            raise ValueError(f"Unexpected API response structure: {response_data}")
            
        content = choices[0].get("message", {}).get("content")
        
        if not content:
            # Print the raw response to see exactly why the API refused to answer
            print(f"⚠️ Raw API Response:\n{json.dumps(response_data, indent=2)}")
            raise ValueError("API returned empty content.")

        # Strip markdown fences if the model wraps the output in ```json
        clean_text = content.strip()

        # Strip markdown fences if the model wraps the output in ```json
        clean_text = content.strip()
        clean_text = re.sub(r"^```(?:json)?\n?", "", clean_text)
        clean_text = re.sub(r"\n?```$", "", clean_text)

        # Parse to ensure it's valid JSON before saving
        dna_data = json.loads(clean_text)
        
        output_filename = f"{client_name.replace(' ', '_').lower()}_dna.json"
        
        with open(output_filename, 'w', encoding='utf-8') as f:
            json.dump(dna_data, f, indent=2, ensure_ascii=False)
            
        print(f"🎉 Success! Client DNA saved to {output_filename}")

    except requests.exceptions.RequestException as e:
        print(f"❌ API Request Failed: {e}")
    except json.JSONDecodeError as e:
        print(f"❌ Failed to parse AI response as JSON: {e}")
    except Exception as e:
        print(f"❌ An unexpected error occurred: {e}")

if __name__ == "__main__":
    # --- Instructions to run ---
    # 1. pip install pandas openpyxl requests python-dotenv
    # 2. Ensure your .env file is in the same directory and contains your keys
    
    # Replace these with your actual file path and client name
    TARGET_EXCEL_FILE = "data/SwissHacks CRM.xlsx" 
    TARGET_CLIENT_NAME = "Hubertus Schneider"
    
    # # Create a dummy excel file for immediate testing if it doesn't exist
    # if not os.path.exists(TARGET_EXCEL_FILE):
    #     print(f"⚠️ {TARGET_EXCEL_FILE} not found. Creating a multi-sheet sample one for testing...")
        
    #     # Ensure the directory exists
    #     os.makedirs(os.path.dirname(TARGET_EXCEL_FILE), exist_ok=True)
        
    #     schneider_df = pd.DataFrame({
    #         "Note": [
    #             "Met with client today. He is deeply concerned about neurodegenerative research funding.",
    #             "Client explicitly stated he will divest from any pharma company abandoning Parkinson's research.",
    #             "Mandate remains Global Balanced Growth. Very data-driven."
    #         ]
    #     })
        
    #     raeber_df = pd.DataFrame({
    #         "Note": [
    #             "Client wants purely defensive value stocks.",
    #             "Will avoid US tech."
    #         ]
    #     })
        
    #     # Write multiple sheets to simulate the real file
    #     with pd.ExcelWriter(TARGET_EXCEL_FILE) as writer:
    #         schneider_df.to_excel(writer, sheet_name="CRM Schneider", index=False)
    #         raeber_df.to_excel(writer, sheet_name="CRM Raeber", index=False)
            
    extract_and_save_dna(TARGET_EXCEL_FILE, TARGET_CLIENT_NAME)