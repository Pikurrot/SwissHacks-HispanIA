from __future__ import annotations

import importlib
import json
import os
import re
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping, Protocol

from .errors import IntegrationError


class CRMProvider(Protocol):
    def extract(self, excel_path: str, client_id: str, client_name: str) -> dict[str, Any]: ...


class NewsProvider(Protocol):
    def fetch(self, client_id: str, dna: Mapping[str, Any]) -> dict[str, Any]: ...


class PortfolioProvider(Protocol):
    def snapshot(
        self,
        excel_path: str,
        client_id: str,
        dna: Mapping[str, Any],
        portfolio_sheet: str | None = None,
    ) -> dict[str, Any]: ...

    def propose_replacement(
        self,
        excel_path: str,
        portfolio_sheet: str,
        holding: Mapping[str, Any],
        dna: Mapping[str, Any],
        collision: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        dna_threshold_pct: float = 50.0,
    ) -> dict[str, Any]: ...


_CWD_LOCK = threading.Lock()
_AGENTS_DIR = Path(__file__).resolve().parents[1] / "agents"


def _load_agent_module(name: str):
    # portfolioAgent uses `from six_api_client import ...`; adding only its own
    # directory keeps that unchanged module importable from the package pipeline.
    agent_path = str(_AGENTS_DIR)
    if agent_path not in sys.path:
        sys.path.insert(0, agent_path)
    return importlib.import_module(f"demo.backend.agents.{name}")


@contextmanager
def _isolated_agent_workdir():
    # CRM and News currently communicate by files in cwd. Serialize only this
    # legacy boundary so concurrent web requests cannot read one another's files.
    with _CWD_LOCK:
        old_cwd = Path.cwd()
        with tempfile.TemporaryDirectory(prefix="swisshacks-agent-") as temp_dir:
            os.chdir(temp_dir)
            try:
                yield Path(temp_dir)
            finally:
                os.chdir(old_cwd)


class LegacyCRMAgentAdapter:
    """Turns crmAgent's write-to-file function into a dictionary-returning call."""

    def extract(self, excel_path: str, client_id: str, client_name: str) -> dict[str, Any]:
        del client_id  # The unchanged agent identifies the CRM sheet by client name.
        module = _load_agent_module("crmAgent")
        source = str(Path(excel_path).resolve())
        filename = f"{client_name.replace(' ', '_').lower()}_dna.json"
        with _isolated_agent_workdir() as workdir:
            module.extract_and_save_dna(source, client_name)
            output = workdir / filename
            if not output.exists():
                raise IntegrationError("CRM Agent did not produce a DNA JSON file")
            try:
                value = json.loads(output.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise IntegrationError(f"CRM Agent output is not usable JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise IntegrationError("CRM Agent output must be a JSON object")
        return value


class LegacyNewsAgentAdapter:
    """Runs the unchanged file-based News workflow and restores full article facts."""

    def fetch(self, client_id: str, dna: Mapping[str, Any]) -> dict[str, Any]:
        module = _load_agent_module("newsAgent")
        safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", client_id).lower()
        with _isolated_agent_workdir() as workdir:
            dna_path = workdir / "client_dna.json"
            dna_path.write_text(json.dumps(dict(dna), ensure_ascii=False), encoding="utf-8")
            module.compile_news_feed(safe_id, str(dna_path))

            analyzed_path = workdir / f"{safe_id}_analyzed_news.json"
            raw_path = workdir / f"{safe_id}_news.json"
            if not analyzed_path.exists():
                raise IntegrationError("News Agent did not produce analyzed news")
            analyzed = json.loads(analyzed_path.read_text(encoding="utf-8"))
            raw_items = json.loads(raw_path.read_text(encoding="utf-8")) if raw_path.exists() else []

        analysis_items = analyzed.get("analysis", []) if isinstance(analyzed, dict) else []
        raw_by_id = {str(item.get("id")): item for item in raw_items if isinstance(item, dict)}
        merged = []
        for item in analysis_items:
            if not isinstance(item, dict):
                continue
            merged.append({**raw_by_id.get(str(item.get("id")), {}), **item})
        return {"client_id": client_id, "analysis": merged}


class JsonNewsAdapter:
    """Loads deterministic News output for demos and integration testing."""

    def __init__(self, json_path: str) -> None:
        self.json_path = str(Path(json_path).resolve())

    def fetch(self, client_id: str, dna: Mapping[str, Any]) -> dict[str, Any]:
        del dna
        try:
            value = json.loads(Path(self.json_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise IntegrationError(f"Cannot load News JSON fixture: {exc}") from exc
        if isinstance(value, list):
            items = value
        elif isinstance(value, dict):
            items = value.get("analysis") or value.get("alerts") or []
        else:
            items = []
        if not isinstance(items, list):
            raise IntegrationError("News JSON must contain an analysis or alerts list")
        return {"client_id": client_id, "analysis": items}


class LegacyPortfolioAgentAdapter:
    """Reads a snapshot and invokes portfolioAgent only after a collision exists."""

    tolerance_pp = 2.0

    def _pandas(self):
        return importlib.import_module("pandas")

    @staticmethod
    def _portfolio_kind(sheet: str) -> str:
        for kind in ("Defensive", "Balanced", "Growth"):
            if kind.lower() in sheet.lower():
                return kind
        raise IntegrationError(f"Cannot determine portfolio strategy from sheet '{sheet}'")

    @staticmethod
    def _select_sheet(dna: Mapping[str, Any], override: str | None) -> str:
        if override:
            return override
        behavior = dna.get("investmentBehavior", {}) if isinstance(dna, Mapping) else {}
        mandate = str(behavior.get("mandate", "")).lower()
        risk = str(behavior.get("riskTolerance", "")).lower()
        if "defensive" in mandate or risk in {"conservative", "low"}:
            return "Sample Portfolio Defensive"
        if "balanced" in mandate or risk in {"moderate", "medium"}:
            return "Sample Portfolio Balanced"
        if "growth" in mandate or risk in {"aggressive", "high"}:
            return "Sample Portfolio Growth"
        raise IntegrationError("Cannot infer portfolio sheet; provide portfolio_sheet explicitly")

    def snapshot(
        self,
        excel_path: str,
        client_id: str,
        dna: Mapping[str, Any],
        portfolio_sheet: str | None = None,
    ) -> dict[str, Any]:
        pd = self._pandas()
        source = str(Path(excel_path).resolve())
        sheet = self._select_sheet(dna, portfolio_sheet)
        try:
            portfolio = pd.read_excel(source, sheet_name=sheet)
            strategies = pd.read_excel(source, sheet_name="Portfolio Strategies")
            cio = pd.read_excel(source, sheet_name="CIO Recommendation List")
        except Exception as exc:
            raise IntegrationError(f"Cannot load portfolio snapshot: {exc}") from exc

        required = {"Issuer / Asset", "ISIN", "Current (CHF)", "Sub-Asset Class"}
        missing = required.difference(portfolio.columns)
        if missing:
            raise IntegrationError(f"Portfolio sheet is missing columns: {sorted(missing)}")

        current = pd.to_numeric(portfolio["Current (CHF)"], errors="coerce").fillna(0.0)
        # The workbook ends each portfolio with a Total row whose Current value
        # repeats the sum of all securities. Only rows with a security identifier
        # and issuer are actual holdings.
        security_rows = portfolio["ISIN"].notna() & portfolio["Issuer / Asset"].notna()
        active = portfolio.loc[(current > 0) & security_rows].copy()
        active["Current (CHF)"] = current.loc[active.index]
        active["Sub-Asset Class"] = active["Sub-Asset Class"].astype(str).str.strip()
        total = float(active["Current (CHF)"].sum())
        if total <= 0:
            raise IntegrationError(f"Portfolio sheet '{sheet}' has no positive holdings")

        cio_by_isin: dict[str, dict[str, str]] = {}
        if "ISIN" in cio.columns:
            for _, row in cio.iterrows():
                isin = str(row.get("ISIN") or "").strip()
                if isin and isin.lower() != "nan":
                    cio_by_isin[isin] = {
                        "rating": str(row.get("Rating") or ""),
                        "view": str(row.get("CIO View") or ""),
                    }

        holdings = []
        for _, row in active.iterrows():
            isin = str(row.get("ISIN") or "").strip()
            cio_info = cio_by_isin.get(isin, {})
            amount = float(row["Current (CHF)"])
            holdings.append({
                "name": str(row.get("Issuer / Asset") or ""),
                "isin": isin,
                "current_chf": round(amount, 2),
                "portfolio_weight_pct": round(amount / total * 100.0, 4),
                "target_chf": round(float(row.get("Target (CHF)") or 0.0), 2),
                "asset_class": str(row.get("Asset Class") or ""),
                "sub_asset_class": str(row.get("Sub-Asset Class") or "").strip(),
                "industry_group": str(row.get("Industry Group") or ""),
                "region": str(row.get("Region") or ""),
                "cio_rating": cio_info.get("rating", ""),
                "cio_view": cio_info.get("view", ""),
            })

        kind = self._portfolio_kind(sheet)
        target_col = {"Defensive": "Def %", "Balanced": "Balanced %", "Growth": "Growth %"}[kind]
        target_by_subasset = {
            str(row["Sub-Asset Class"]).strip(): float(row[target_col])
            for _, row in strategies.iterrows()
            if str(row.get("Asset Class", "")) != "TOTAL"
            and pd.notna(row.get("Sub-Asset Class"))
            and pd.notna(row.get(target_col))
        }
        current_by_subasset = active.groupby("Sub-Asset Class")["Current (CHF)"].sum().to_dict()
        allocation = []
        all_subassets = sorted(set(target_by_subasset).union(str(x).strip() for x in current_by_subasset))
        for subasset in all_subassets:
            amount = float(current_by_subasset.get(subasset, 0.0))
            current_pct = amount / total * 100.0
            target_pct = float(target_by_subasset.get(subasset, 0.0))
            drift = current_pct - target_pct
            allocation.append({
                "sub_asset_class": subasset,
                "target_pct": round(target_pct, 4),
                "current_pct": round(current_pct, 4),
                "drift_pp": round(drift, 4),
                "within_tolerance": abs(drift) <= self.tolerance_pp,
            })

        cash = sum(h["current_chf"] for h in holdings if h["asset_class"].lower() == "liquidity")
        return {
            "client_id": client_id,
            "portfolio_sheet": sheet,
            "strategy": kind,
            "total_current_chf": round(total, 2),
            "cash_chf": round(cash, 2),
            "tolerance_pp": self.tolerance_pp,
            "holdings": holdings,
            "allocation": allocation,
        }

    def propose_replacement(
        self,
        excel_path: str,
        portfolio_sheet: str,
        holding: Mapping[str, Any],
        dna: Mapping[str, Any],
        collision: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        dna_threshold_pct: float = 50.0,
    ) -> dict[str, Any]:
        module = _load_agent_module("portfolioAgent")
        portfolio_result = module.get_swap_candidates(
            str(Path(excel_path).resolve()),
            portfolio_sheet,
            str(holding.get("name") or holding.get("isin") or ""),
            dict(dna),
        )
        if not isinstance(portfolio_result, dict) or portfolio_result.get("error"):
            detail = portfolio_result.get("error") if isinstance(portfolio_result, dict) else "invalid output"
            raise IntegrationError(f"Portfolio Agent could not find an alternative: {detail}")
        # Support both teammate versions: an older direct candidate object and
        # the newer {sell_asset, top_candidate, alternatives} response.
        candidate = portfolio_result.get("top_candidate", portfolio_result)
        if not isinstance(candidate, dict) or not candidate.get("Issuer"):
            raise IntegrationError("Portfolio Agent returned no usable top candidate")

        alignment = float(
            candidate.get("Afinidad_DNA_Porcentaje")
            or candidate.get("Confianza_Alineacion_DNA_Porcentaje")
            or 0.0
        )
        threshold = max(0.0, min(float(dna_threshold_pct), 100.0))
        suitable_alternative = alignment >= threshold
        action = "replace" if suitable_alternative else "review"
        allocation_row = next(
            (
                row for row in snapshot.get("allocation", [])
                if row.get("sub_asset_class") == holding.get("sub_asset_class")
            ),
            {},
        )
        portfolio_valid = all(row.get("within_tolerance", False) for row in snapshot.get("allocation", []))
        currency = str(candidate.get("Moneda_SIX", ""))
        alternative = {
            "name": candidate.get("Issuer", ""),
            "isin": candidate.get("ISIN", ""),
            "cio_rating": candidate.get("Rating", ""),
            "cio_view": candidate.get("Explicacion_DNA", ""),
            "match_score": alignment,
            "recommended_chf": candidate.get("Asignacion_Recomendada_CHF"),
            "current_price": candidate.get("Precio_Actual_SIX"),
            "currency": currency,
            # portfolioAgent divides CHF proceeds by a local-currency price. That
            # quantity is valid only when no FX conversion is needed.
            "quantity": candidate.get("Cantidad_Acciones") if currency.upper() == "CHF" else None,
        }
        event_id = str(collision.get("news", {}).get("id") or collision.get("news", {}).get("event_id") or "")
        collision_rationale = str(collision.get("news", {}).get("portfolio_impact") or "")
        if suitable_alternative:
            rationale = collision_rationale
            alternatives = [alternative]
            rejected_alternatives = []
            selection_note = "Portfolio Agent found a replacement meeting the DNA threshold."
        else:
            rationale = (
                f"{collision_rationale} The available replacement scored only {alignment:.1f}% "
                f"against the client's DNA, below the {threshold:.1f}% threshold, "
                "so no replacement trade is recommended."
            ).strip()
            alternatives = []
            rejected_alternatives = [alternative]
            selection_note = (
                f"No candidate met the {threshold:.1f}% client-DNA threshold; "
                "alert and RM review only."
            )

        return {
            "client_id": snapshot.get("client_id", ""),
            "suggested_swaps": [{
                "event_id": event_id,
                "mandate": snapshot.get("strategy", ""),
                "holding": dict(holding),
                "recommended_action": action,
                "rationale": rationale,
                "urgency": "high" if str(collision.get("news", {}).get("alertType", "")).lower() == "conflict" else "medium",
                "trade_chf": holding.get("current_chf") if suitable_alternative else None,
                "current_cio_rating": holding.get("cio_rating", ""),
                "alternatives": alternatives,
                "rejected_alternatives": rejected_alternatives,
                "selection_note": selection_note,
                "dna_alignment_confidence_pct": alignment,
                "mandate_check": {
                    "before_valid": portfolio_valid,
                    # The unchanged agent replaces inside the same sub-asset class,
                    # so this trade leaves that allocation drift unchanged.
                    "after_valid": portfolio_valid,
                    "drift_after_pp": allocation_row.get("drift_pp"),
                    "note": "Same-sub-asset replacement; allocation drift is unchanged.",
                },
            }],
        }
