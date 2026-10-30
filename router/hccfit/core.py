"""Core: rule-based PubMed query plan + hybrid retrieval + readability gate."""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from typing import Any

from .scope import ScopeError, normalize_disease

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "toolkit"))
from run import search_hybrid  # noqa: E402

HCC = '("hepatocellular carcinoma"[tiab] OR "HCC"[tiab] OR hepatoma[tiab])'
MODEL = ('(nomogram[tiab] OR "prediction model"[tiab] OR "prognostic model"[tiab] '
         'OR "risk score"[tiab] OR "machine learning"[tiab] OR "deep learning"[tiab])')
MODEL2 = '("prediction model"[tiab] OR "prognostic model"[tiab] OR nomogram[tiab] OR "risk model"[tiab])'


def _terms(text: str, scenario: dict | None = None) -> dict:
    """文本 + 结构化字段；结构化字段优先，text 只做补充。"""
    scenario = scenario or {}
    t = (text or "").lower()

    treatment, tz = "", "any"
    ts = str(scenario.get("treatment_state") or "").lower()
    tz_in = str(scenario.get("time_zero") or "").lower()
    if ts in ("curative resection", "resection", "surgery", "hepatectomy"):
        treatment = ('("hepatectomy"[tiab] OR "liver resection"[tiab] '
                     'OR "curative resection"[tiab])')
        tz = "surgery"
    elif ts in ("tace", "haic", "interventional"):
        treatment = ('(TACE[tiab] OR "transarterial chemoembolization"[tiab] '
                     'OR "transcatheter arterial chemoembolization"[tiab] OR HAIC[tiab])')
        tz = "tace"
    elif ts in ("unresectable systemic", "palliative systemic", "systemic"):
        treatment = ('("systemic therapy"[tiab] OR "targeted therapy"[tiab] '
                     'OR "immunotherapy"[tiab] OR lenvatinib[tiab] OR atezolizumab[tiab])')
        tz = "systemic"
    elif ts in ("transplant", "liver transplantation"):
        treatment = '("liver transplantation"[tiab] OR "transplant"[tiab])'
        tz = "transplant"

    if tz_in in ("surgery", "tace", "systemic", "transplant"):
        tz = tz_in
        if not treatment:
            treatment = {
                "surgery": '("hepatectomy"[tiab] OR "liver resection"[tiab] OR "curative resection"[tiab])',
                "tace": '(TACE[tiab] OR "transarterial chemoembolization"[tiab] OR HAIC[tiab])',
                "systemic": '("systemic therapy"[tiab] OR "targeted therapy"[tiab] OR "immunotherapy"[tiab] OR lenvatinib[tiab] OR atezolizumab[tiab])',
                "transplant": '("liver transplantation"[tiab] OR "transplant"[tiab])',
            }[tz_in]

    if not treatment:
        if any(x in t for x in ["tace", "肝动脉", "介入"]):
            treatment = ('(TACE[tiab] OR "transarterial chemoembolization"[tiab] '
                         'OR "transcatheter arterial chemoembolization"[tiab])')
            tz = "TACE"
        elif any(x in t for x in ["移植", "transplant"]):
            treatment = '("liver transplantation"[tiab] OR "transplant"[tiab])'
            tz = "transplant"
        elif any(x in t for x in ["系统", "靶向", "免疫", "atezo", "lenva", "仑伐", "阿替利珠"]):
            treatment = ('("systemic therapy"[tiab] OR "targeted therapy"[tiab] '
                         'OR "immunotherapy"[tiab] OR lenvatinib[tiab] OR atezolizumab[tiab])')
            tz = "systemic"
        elif any(x in t for x in ["切除", "手术", "resection", "hepatectomy", "surgery", "术后"]):
            treatment = ('("hepatectomy"[tiab] OR "liver resection"[tiab] '
                         'OR "curative resection"[tiab])')
            tz = "surgery"

    out_in = str(scenario.get("outcome") or "").upper()
    if out_in in ("OS", "RFS", "PFS", "PHLF", "RESPONSE", "LOS"):
        outcome, ep = {
            "OS": ('("overall survival"[tiab] OR survival[tiab] OR prognosis[tiab])', "OS"),
            "RFS": ('("recurrence"[tiab] OR "recurrence-free survival"[tiab] OR "disease-free survival"[tiab])', "RFS"),
            "PFS": ('("progression-free survival"[tiab] OR PFS[tiab] OR progression[tiab])', "PFS"),
            "PHLF": ('("post-hepatectomy liver failure"[tiab] OR PHLF[tiab] OR "liver failure"[tiab])', "PHLF"),
            "RESPONSE": ('("treatment response"[tiab] OR "objective response"[tiab] OR RECIST[tiab] OR response[tiab])', "response"),
            "LOS": ('("length of stay"[tiab] OR "hospital stay"[tiab])', "LOS"),
        }[out_in]
    else:
        if any(x in t for x in ["复发", "recurrence", "rfs", "dfs", "无复发生存"]):
            outcome = '("recurrence"[tiab] OR "recurrence-free survival"[tiab] OR "disease-free survival"[tiab])'
            ep = "RFS"
        elif any(x in t for x in ["肝衰", "phlf", "liver failure"]):
            outcome = '("post-hepatectomy liver failure"[tiab] OR PHLF[tiab] OR "liver failure"[tiab])'
            ep = "PHLF"
        elif any(x in t for x in ["反应", "response", "pfs", "无进展", "疗效"]):
            outcome = '("treatment response"[tiab] OR "progression-free survival"[tiab] OR PFS[tiab] OR response[tiab])'
            ep = "response"
        else:
            outcome = '("overall survival"[tiab] OR survival[tiab] OR prognosis[tiab])'
            ep = "OS"

    horizon = 0
    try:
        horizon = max(0, min(120, int(scenario.get("horizon_months") or 0)))
    except (TypeError, ValueError):
        horizon = 0
    if not horizon:
        m = re.search(r"(\d+)\s*(年|year|个月|month)", t)
        if m:
            n = int(m.group(1))
            horizon = n * 12 if ("年" in m.group(2) or "year" in m.group(2)) else n
    return {"treatment": treatment, "time_zero": tz, "outcome": outcome,
            "endpoint": ep, "horizon": horizon}


def build_plan(scenario: dict) -> dict:
    normalize_disease(scenario)
    text = scenario.get("text") or ""
    t = _terms(text, scenario)
    queries = []
    if t["treatment"]:
        queries.append(f"{HCC} AND {t['treatment']} AND {t['outcome']} AND {MODEL}")
    else:
        queries.append(f"{HCC} AND {t['outcome']} AND {MODEL}")
    queries.append(f"{HCC} AND {t['outcome']} AND {MODEL2} AND (\"external validation\"[tiab] OR \"C-index\"[tiab] OR calibration[tiab])")
    if t["treatment"]:
        queries.append(f"{HCC} AND {t['treatment']} AND {MODEL}")
    queries.append(f"{HCC} AND {t['outcome']} AND ({MODEL} OR \"risk stratification\"[tiab])")
    return {"disease": "HCC", "scenario": scenario, "queries": queries[:4],
            "endpoint": t["endpoint"], "horizon_months": t["horizon"]}

# ---------- readability ----------
IDCONV = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"


def _pmc_map(pmids: list[str]) -> dict:
    out = {}
    for i in range(0, len(pmids), 100):
        ids = [x for x in pmids[i:i + 100] if x]
        if not ids:
            continue
        try:
            r = requests.get(IDCONV, params={"ids": ",".join(ids), "format": "json",
                                             "tool": "hccfit", "email": "hccfit@example.org"},
                             timeout=8)
            for rec in (r.json().get("records") or []):
                if rec.get("pmid"):
                    out[str(rec["pmid"])] = rec.get("pmcid") or ""
        except Exception:
            pass
    return out


def annotate_readability(papers: list[dict]) -> list[dict]:
    pmids = [str(p.get("pmid")) for p in papers if p.get("pmid")]
    pmc = _pmc_map(pmids) if pmids else {}
    out = []
    for p in papers:
        pmid = str(p.get("pmid") or "")
        pmcid = pmc.get(pmid, "")
        if pmcid:
            level, url = "fulltext", f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/"
        elif p.get("abstract_snippet"):
            level, url = "abstract_only", (f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "")
        else:
            level, url = "metadata_only", (f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "")
        p = dict(p)
        p["readability"] = {"level": level, "pmcid": pmcid, "url": url,
                            "full_text": level == "fulltext"}
        out.append(p)
    return out


def apply_readability(papers: list[dict], require_fulltext: bool) -> tuple[list[dict], dict]:
    papers = annotate_readability(papers)
    counts = {"fulltext": 0, "abstract_only": 0, "metadata_only": 0}
    for p in papers:
        counts[p["readability"]["level"]] = counts.get(p["readability"]["level"], 0) + 1
    if require_fulltext:
        papers = [p for p in papers if p["readability"]["level"] == "fulltext"]
    return papers, counts


def fast_search(scenario: dict, k: int = 5, require_fulltext: bool = False) -> dict:
    """只做检索；可读性注释/过滤统一在 strategies.run() 最终结果上做一次。"""
    t0 = time.perf_counter()
    plan = build_plan(scenario)
    res = search_hybrid(plan, k=k)
    papers = res.get("papers") or []
    return {"papers": papers[:k], "pool": res.get("pool") or [], "plan": plan,
            "meta": {"retrieval_ms": round((time.perf_counter() - t0) * 1000),
                     "readability": {}, "base_papers": len(papers)}}
