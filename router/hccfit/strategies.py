"""Strategy router: fast / balanced / auto (deep disabled in this package)."""
from __future__ import annotations

import time
import uuid

from . import config
from .core import annotate_readability, fast_search
from .sciencepal import SciencePalError, filter_papers


def _pick(papers, keep, k):
    by = {str(x.get("pmid")): x for x in papers if x.get("pmid")}
    out = []
    for x in keep:
        pmid = str(x.get("pmid"))
        if pmid in by and float(x.get("fit", 0)) >= 1:
            p = dict(by[pmid])
            p["sp_fit"] = x.get("fit")
            p["sp_reason"] = x.get("reason")
            out.append(p)
    return out[:k]


def route(scenario: dict, requested: str, deadline_ms: int, thinking: bool,
          enabled: list[str] | None = None) -> str:
    enabled = enabled or list(config.ENABLED_STRATEGIES)
    if requested in ("fast", "balanced") and requested in enabled:
        return requested
    if deadline_ms <= 15000 and "fast" in enabled:
        return "fast"
    if "balanced" in enabled:
        return "balanced"
    return "fast"


def run(scenario: dict, strategy: str = "auto", k: int = 5,
        deadline_ms: int = 120000, thinking: bool = False,
        require_fulltext: bool = False, request_id: str | None = None,
        api_key: str | None = None) -> dict:
    t0 = time.perf_counter()
    rid = request_id or str(uuid.uuid4())
    used = route(scenario, strategy, deadline_ms, thinking, config.ENABLED_STRATEGIES)
    warnings = []
    base = fast_search(scenario, k=k, require_fulltext=False)
    papers = list(base["papers"])
    pool = base.get("pool") or []
    meta = {
        "retrieval_ms": base["meta"]["retrieval_ms"],
        "strategy_used": used,
        "base_papers": len(papers),
        "filter_in": 0,
        "filter_out": 0,
        "llm_ms": 0,
        "readability": base["meta"]["readability"],
        "degraded": False,
        "requested_strategy": strategy,
    }

    if used == "balanced":
        candidates = pool[:10] if pool else papers
        meta["filter_in"] = len(candidates)
        try:
            remaining = max(10, int(deadline_ms / 1000)
                            - int(time.perf_counter() - t0))
            filtered = filter_papers(scenario, candidates,
                                     timeout_s=min(90, remaining), api_key=api_key)
            parsed = filtered.get("parsed") or {}
            kept = _pick(candidates, parsed.get("keep") or [], k)
            meta["llm_ms"] = filtered.get("latency_ms")
            if filtered.get("status") != "completed" or not parsed:
                warnings.append(
                    "SciencePal filter returned no structured result; "
                    "fallback to algorithm top-k")
                meta["degraded"] = True
            else:
                papers = kept
                meta["filter_out"] = len(kept)
                if len(papers) < k:
                    warnings.append(
                        f"filter kept {len(papers)}/{k}; no unverified padding")
        except SciencePalError as exc:
            warnings.append(
                f"SciencePal unavailable: {exc}; fallback to algorithm top-k")
            meta["degraded"] = True
        except Exception as exc:  # noqa: BLE001
            warnings.append(
                f"SciencePal error: {type(exc).__name__}; fallback")
            meta["degraded"] = True

    # readability gate (annotate once, on final papers)
    papers = annotate_readability(papers)
    if require_fulltext:
        before = len(papers)
        papers = [p for p in papers
                  if p.get("readability", {}).get("level") == "fulltext"]
        if len(papers) < before:
            warnings.append(
                f"require_fulltext: dropped {before - len(papers)} "
                "abstract-only candidates")

    meta["latency_ms"] = round((time.perf_counter() - t0) * 1000)
    meta["papers_returned"] = len(papers)
    meta["readability_counts"] = {
        lvl: sum(1 for p in papers if p.get("readability", {}).get("level") == lvl)
        for lvl in ("fulltext", "abstract_only", "metadata_only")
    }
    meta["readability"] = dict(meta["readability_counts"])
    return {
        "request_id": rid,
        "strategy_requested": strategy,
        "strategy_used": used,
        "scenario": scenario,
        "papers": papers,
        "meta": meta,
        "warnings": warnings,
        "next_actions": ["open_full_text", "verify_model_variables",
                         "ask_missing_parameters"],
    }
