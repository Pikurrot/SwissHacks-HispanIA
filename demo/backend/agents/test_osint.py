#!/usr/bin/env python3
"""
Smoke-test for the OSINT agent — no LLM required for the default path.

Usage (from repo root):
  python demo/backend/agents/test_osint.py              # validate all cached outputs
  python demo/backend/agents/test_osint.py huber        # one client, merge preview
  python demo/backend/agents/test_osint.py --enrich huber   # live Phoeniqs run (costs credits)

Loads Phoeniqs keys from demo/.env regardless of cwd.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

AGENTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(AGENTS_DIR, "..", "..", ".."))
DEMO_ENV = os.path.join(REPO_ROOT, "demo", ".env")

CLIENT_IDS = ("huber", "schneider", "raeber", "ammann")

# Minimal keyword map for the demo "lifestyle opportunity" slice (idea doc).
LIFESTYLE_SECTOR_HINTS = {
    "arc'teryx": ("Amer Sports", "Outdoor — client wears the brand on expeditions"),
    "patagonia": ("Patagonia", "Outdoor — aligns with nature travel posts"),
    "natural wine": ("Pernod Ricard", "Conversation opener — sustainable viticulture angle"),
    "palm oil": ("Unilever", "Positive trigger — historic deforestation cut-off news"),
    "reforestation": ("EcoTree (alt)", "Direct fit with rewilding posts — outside standard mandate"),
}


def load_dotenv() -> None:
    try:
        from dotenv import load_dotenv as _load

        if os.path.isfile(DEMO_ENV):
            _load(DEMO_ENV)
    except ImportError:
        pass


def osint_path(client_id: str) -> str:
    return os.path.join(AGENTS_DIR, f"{client_id}_osint.json")


def load_osint(client_id: str) -> dict:
    path = osint_path(client_id)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Missing {path} — run: python osintAgent.py {client_id}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def validate_record(client_id: str, data: dict) -> list[str]:
    errors: list[str] = []
    if data.get("client_id") != client_id:
        errors.append("client_id mismatch")
    profile = data.get("lifestyle_profile")
    if not isinstance(profile, dict):
        errors.append("lifestyle_profile must be an object")
        return errors

    for key in ("rapport_triggers", "redline_signals"):
        val = profile.get(key)
        if val is not None and not isinstance(val, list):
            errors.append(f"lifestyle_profile.{key} must be a list")

    conf = data.get("osint_confidence")
    if conf is not None and not (0 <= float(conf) <= 1):
        errors.append("osint_confidence out of range [0, 1]")

    return errors


def find_dna_file(client_id: str) -> str | None:
    """Best-effort locate a CRM DNA JSON for merge preview."""
    candidates = [
        os.path.join(REPO_ROOT, f"{client_id}_dna.json"),
        os.path.join(REPO_ROOT, "hubertus_schneider_dna.json") if client_id == "schneider" else "",
        os.path.join(REPO_ROOT, "raeber_dna.json") if client_id == "raeber" else "",
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def merge_preview(client_id: str, osint: dict) -> None:
    profile = osint.get("lifestyle_profile") or {}
    osint_triggers = profile.get("rapport_triggers") or []
    redlines = profile.get("redline_signals") or []

    print(f"\n{'─' * 60}")
    print(f"  {osint.get('full_name', client_id)}  ·  confidence {osint.get('osint_confidence', '?')}")
    print(f"{'─' * 60}")

    if redlines:
        print("\n⚠️  Redline signals (show before any swap proposal):")
        for line in redlines[:4]:
            print(f"   • {line}")

    print("\n📋 Rapport triggers (OSINT):")
    for t in osint_triggers[:4]:
        print(f"   → {t}")

    dna_path = find_dna_file(client_id)
    if dna_path:
        with open(dna_path, encoding="utf-8") as f:
            dna = json.load(f)
        crm_triggers = dna.get("personalProfile", {}).get("rapportTriggers", "")
        if crm_triggers:
            print("\n🔗 CRM rapportTriggers (existing):")
            if isinstance(crm_triggers, list):
                for t in crm_triggers:
                    print(f"   · {t}")
            else:
                print(f"   · {crm_triggers}")
        print("\n💡 Merged pool (dedupe in orchestrator later):")
        pool = list(osint_triggers[:3])
        if isinstance(crm_triggers, str) and crm_triggers:
            pool.append(crm_triggers)
        elif isinstance(crm_triggers, list):
            pool.extend(crm_triggers[:2])
        for item in pool[:5]:
            print(f"   + {item}")
    else:
        print(f"\n(no CRM DNA JSON found for '{client_id}' — only OSINT shown)")

    if client_id == "huber":
        print("\n🎯 Lifestyle opportunities (keyword demo — not swap resolver):")
        blob = json.dumps(profile, ensure_ascii=False).lower()
        for keyword, (instrument, note) in LIFESTYLE_SECTOR_HINTS.items():
            if keyword in blob:
                print(f"   • {instrument}: {note}")


def cmd_validate(client_ids: list[str]) -> int:
    failed = 0
    for cid in client_ids:
        try:
            data = load_osint(cid)
        except FileNotFoundError as e:
            print(f"❌ {cid}: {e}")
            failed += 1
            continue

        errors = validate_record(cid, data)
        triggers = len((data.get("lifestyle_profile") or {}).get("rapport_triggers") or [])
        redlines = len((data.get("lifestyle_profile") or {}).get("redline_signals") or [])
        if errors:
            print(f"❌ {cid}: {', '.join(errors)}")
            failed += 1
        else:
            print(
                f"✅ {cid}: confidence={data.get('osint_confidence')} "
                f"triggers={triggers} redlines={redlines}"
            )
    return failed


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Test OSINT agent outputs")
    parser.add_argument("client_id", nargs="?", choices=CLIENT_IDS, help="Single client preview")
    parser.add_argument(
        "--enrich",
        action="store_true",
        help="Re-run osintAgent.enrich_client_osint (calls Phoeniqs)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Validate all four clients (default when no client_id)",
    )
    args = parser.parse_args()

    targets = [args.client_id] if args.client_id else list(CLIENT_IDS)

    if args.enrich:
        if not args.client_id:
            print("--enrich requires a single client_id", file=sys.stderr)
            return 2
        sys.path.insert(0, AGENTS_DIR)
        from osintAgent import enrich_client_osint

        enrich_client_osint(args.client_id)

    failed = cmd_validate(targets if args.client_id or args.all else list(CLIENT_IDS))

    preview_ids = [args.client_id] if args.client_id else (["huber"] if not args.all else [])
    for cid in preview_ids:
        try:
            merge_preview(cid, load_osint(cid))
        except FileNotFoundError:
            pass

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
