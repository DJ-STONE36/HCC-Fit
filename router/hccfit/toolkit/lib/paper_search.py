"""HCC 论文搜索 Agent —— 最小基线（Europe PMC + 三目标帕累托）

设计原则
--------
1. 只用一个检索 API：Europe PMC（覆盖 PubMed + PMC 全文 + 预印本，无需 key）。
2. 只在 metadata + abstract 上打分，不下载 PDF、不建向量库。
3. 硬约束先过滤（终点/时间零点/年份/文献类型），再三目标帕累托排序：
       M 匹配度 ↑   E 证据强度 ↑   R 可精读性 ↑
4. Top-K 附带"精读交接单"，让下游 reading agent 直接知道读哪、验什么。

用法
----
    python tools/paper_search.py --demo
    python tools/paper_search.py --case case.json --k 8 --json out.json

依赖：仅标准库（urllib / json / re / math）。
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
UA = "HCC-Fit-PaperSearch/0.1 (research prototype)"
_EPMC_DOWN_UNTIL = 0.0

# ---------------------------------------------------------------- 词典

OUTCOME_KW = {
    "OS": ["overall survival", "overall surv", " os ", "survival time", "long-term survival"],
    "RFS": ["recurrence-free survival", "rfs", "disease-free survival", "dfs", "recurrence free"],
    "PFS": ["progression-free survival", "pfs", "progression free"],
    "recurrence": ["recurrence", "early recurrence", "relapse"],
    "PHLF": ["post-hepatectomy liver failure", "phlf", "liver failure", "postoperative liver"],
    "response": ["objective response", "orr", "recist", "treatment response", "radiological response"],
    "LOS": ["length of stay", "hospital stay", "los "],
}

T0_KW = {
    "surgery": ["hepatectomy", "resection", "postoperative", "after surgery", "surgical"],
    "tace": ["tace", "transarterial", "chemoembolization", "chemoembolisation", "haic"],
    "systemic": ["immunotherapy", "immune checkpoint", "atezolizumab", "sorafenib",
                 "lenvatinib", "systemic therapy", "ici"],
    "transplant": ["transplant", "graft"],
}

VAR_ALIASES = {
    "AFP": ["afp", "alpha-fetoprotein", "α-fetoprotein"],
    "MVI": ["microvascular invasion", "mvi"],
    "tumor size": ["tumor size", "tumour size", "diameter", "largest tumor"],
    "tumor number": ["tumor number", "tumour number", "multinodular", "number of tumors"],
    "PT": ["prothrombin", "pt ", "inr"],
    "HBV-DNA": ["hbv dna", "hbv-dna", "viral load"],
    "GGT": ["gamma-glutamyl", "ggt", "γ-gt"],
    "satellite": ["satellite nodule", "satellite"],
    "ALBI": ["albi", "albumin-bilirubin"],
    "Child-Pugh": ["child-pugh", "child pugh"],
    "PLR": ["platelet-to-lymphocyte", "platelet lymphocyte"],
    "NLR": ["neutrophil-to-lymphocyte", "neutrophil lymphocyte"],
    "TBIL": ["total bilirubin", "tbil", "bilirubin"],
    "ALB": ["albumin", "alb "],
    "PLT": ["platelet", "plt"],
    "ECOG": ["ecog", "performance status"],
    "PIVKA-II": ["pivka", "des-gamma-carboxy"],
    "CRP": ["c-reactive protein", "crp"],
}

TOP_JOURNALS = {
    "hepatology", "journal of hepatology", "gut", "lancet oncology", "jama oncology",
    "annals of surgery", "radiology", "nature communications", "clinical cancer research",
    "journal of clinical oncology", "liver cancer", "clinical and molecular hepatology",
    "european radiology", "international journal of surgery", "jama surgery",
    "annals of surgical oncology", "british journal of surgery", "hpb",
}

CLUSTERS = [
    ("imaging", ["radiomic", "deep learning", "convolutional", "ct ", "mri", "imaging"]),
    ("pathology", ["patholog", "histolog", "whole-slide", "wsi"]),
    ("biomarker", ["biomarker", "gene", "signature", "serum", "cytokine", "mrna",
                   "inflammat", "monocyte", "lymphocyte", "platelet", "albumin",
                   "nutrition", "fib-4", "nlr", "plr"]),
    ("tace", ["tace", "transarterial", "chemoembolization", "chemoembolisation", "haic"]),
    ("systemic", ["immunotherapy", "immune checkpoint", "atezolizumab", "sorafenib",
                  "lenvatinib", "systemic"]),
    ("transplant", ["transplant", "graft"]),
    ("clinical", ["nomogram", "risk score", "scoring", "cox", "clinical model"]),
]

BAD_PUBTYPE = ("review", "guideline", "editorial", "comment", "case reports",
               "practice guideline", "letter", "news", "retraction")


# ---------------------------------------------------------------- 数据

@dataclass
class Paper:
    pmid: str
    doi: str
    title: str
    abstract: str
    journal: str
    year: int
    cited: int
    oa: bool
    url: str
    pubtypes: str = ""
    scores: dict = field(default_factory=dict)
    layer: int = 0
    cluster: str = "other"
    matched: list = field(default_factory=list)
    risks: list = field(default_factory=list)
    var_overlap: float = 0.0

    @property
    def text(self) -> str:
        return f"{self.title} {self.abstract}".lower()

    @property
    def key(self) -> str:
        return self.doi or self.pmid or self.title.lower()[:80]


@dataclass
class Case:
    disease: str = "HCC"
    treatment_state: str = "unspecified"
    time_zero: str = "any"
    outcome: str = "any"
    horizon_months: int = 0
    etiology: str = "unknown"
    variables: list = field(default_factory=list)
    year_min: int = 0
    k: int = 8
    include_preprints: bool = False


# ---------------------------------------------------------------- 检索

def build_queries(case: Case) -> list[str]:
    """两条查询模板：一条终点导向，一条模型+验证导向。"""
    pop = '("hepatocellular carcinoma" OR HCC)'
    broad_t0 = ('(hepatectomy OR resection OR TACE OR chemoembolization OR '
                'immunotherapy OR transplant)')
    broad_outcome = ('(survival OR prognosis OR recurrence OR response OR '
                     '"liver failure" OR "length of stay")')
    t0 = {
        "surgery": '(hepatectomy OR resection OR postoperative)',
        "tace": '(TACE OR transarterial OR chemoembolization OR HAIC)',
        "systemic": '(immunotherapy OR "immune checkpoint" OR atezolizumab OR sorafenib OR lenvatinib)',
        "transplant": '(transplant OR "liver transplantation")',
        "any": broad_t0,
    }.get(case.time_zero, broad_t0)

    outcome = {
        "OS": '("overall survival" OR prognostic OR survival)',
        "RFS": '("recurrence-free survival" OR "disease-free survival" OR recurrence)',
        "PFS": '("progression-free survival" OR PFS)',
        "recurrence": '(recurrence OR "early recurrence")',
        "PHLF": '("liver failure" OR "post-hepatectomy liver failure" OR morbidity)',
        "response": '(response OR "objective response" OR RECIST)',
        "LOS": '("length of stay" OR "hospital stay")',
        "any": broad_outcome,
    }.get(case.outcome, broad_outcome)

    model = ('(nomogram OR "prediction model" OR "prognostic model" OR "risk score" '
             'OR "machine learning" OR "deep learning" OR "artificial intelligence")')

    if case.year_min and case.year_min > 0:
        years = f" AND PUB_YEAR:[{case.year_min} TO {date.today().year}]"
        old_start = case.year_min
    else:
        years = ""
        old_start = 1900
    src = "" if case.include_preprints else " AND SRC:MED"

    q1 = f"{pop} AND {t0} AND {outcome} AND {model}{years}{src}"
    q2 = (f"{pop} AND {t0} AND ({model}) AND "
          f'("external validation" OR "validation cohort" OR "C-index" OR calibration)'
          f"{years}{src}")
    q3 = f"{pop} AND {t0} AND (nomogram OR \"risk score\" OR scoring) AND {outcome}{years}{src}"
    # 时代分层：EPMC 的相关度排序强烈偏向新论文，必须单独开旧窗口，否则经典模型永远进不来
    q4 = (f"{pop} AND {t0} AND {outcome} AND {model} AND "
          f"PUB_YEAR:[{old_start} TO 2022]{src}")
    q5 = (f"{pop} AND {t0} AND {outcome} AND {model} AND "
          f"PUB_YEAR:[2023 TO {date.today().year - 1}]{src}")
    return [(q1, ""), (q1, "CITED desc"), (q2, ""), (q3, ""), (q4, ""), (q5, "")]


def fetch(query: str, sort: str, page_size: int = 50) -> dict:
    params = {
        "query": query,
        "format": "json",
        "resultType": "core",
        "pageSize": str(page_size),
        "sort": sort,
    }
    url = EPMC + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    global _EPMC_DOWN_UNTIL
    if time.time() < _EPMC_DOWN_UNTIL:
        raise RuntimeError("epmc_circuit_open")
    last = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            last = exc
            if attempt == 0:
                time.sleep(1.0)
    msg = str(last)
    if any(x in msg for x in ("503", "429", "timed out", "Timeout")):
        _EPMC_DOWN_UNTIL = time.time() + 30
    raise last


def clean(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "").replace("&lt;", "<").replace("&gt;", ">")


def to_paper(item: dict) -> Paper | None:
    pmid = str(item.get("pmid") or item.get("id") or "")
    title = clean(item.get("title") or "").strip()
    abstract = clean(item.get("abstractText") or "").strip()
    if not title or len(abstract) < 250:
        return None
    journal = ""
    ji = item.get("journalInfo") or {}
    if isinstance(ji, dict):
        journal = ((ji.get("journal") or {}).get("title") or "").strip()
    journal = journal or (item.get("journalTitle") or "").strip()
    try:
        year = int(str(item.get("pubYear") or "0")[:4])
    except ValueError:
        year = 0
    pts = item.get("pubTypeList") or {}
    pubtypes = " ".join(pts.get("pubType") or []).lower() if isinstance(pts, dict) else ""
    url = ""
    for u in ((item.get("fullTextUrlList") or {}).get("fullTextUrl") or []):
        if u.get("availabilityCode") in ("OA", "F"):
            url = u.get("url", "")
            break
    url = url or (f"https://doi.org/{item['doi']}" if item.get("doi") else
                  f"https://europepmc.org/article/{item.get('source','MED')}/{item.get('id','')}")
    return Paper(
        pmid=pmid,
        doi=(item.get("doi") or "").lower(),
        title=title,
        abstract=abstract,
        journal=journal,
        year=year,
        cited=int(item.get("citedByCount") or 0),
        oa=(str(item.get("isOpenAccess")) == "Y" or str(item.get("hasPDF")) == "Y"),
        url=url,
        pubtypes=pubtypes,
    )


# ---------------------------------------------------------------- 打分

def endpoint_score(title: str, abstract: str, outcome: str) -> float:
    """终点匹配分两层：标题命中=主终点；摘要多次命中=可能的次要终点。"""
    if outcome in ("", "any"):
        return 0.5
    kws = OUTCOME_KW.get(outcome, [])
    if not kws:
        return 0.5  # agent 给了自定义终点：不惩罚，交给查询/人工核对
    t = title.lower()
    a = abstract.lower()
    if any(kw in t for kw in kws):
        return 1.0
    hits = sum(1 for kw in kws if kw in a)
    if hits >= 3 and any(k in t for k in ["survival", "prognos", "mortal", "outcome"]):
        return 0.7
    if hits >= 1:
        return 0.4
    return 0.0


def population_score(title: str, abstract: str, case: Case) -> float:
    t = f"{title} {abstract}".lower()
    score = 0.6
    if case.etiology != "unknown" and ("hbv" in t or "hepatitis b" in t):
        score = 1.0
    elif any(k in t for k in ["hcv", "hepatitis c", "nafld", "alcoholic", "alcohol-related"]):
        score = 0.35
    if "curative" in case.treatment_state and any(
            k in t for k in ["unresectable", "advanced", "palliative"]):
        score = min(score, 0.25)
    if case.time_zero != "transplant" and "transplant" in t:
        score = min(score, 0.3)
    return score


def timezero_score(text: str, t0: str) -> float:
    if t0 in ("", "any"):
        return 0.5
    if t0 not in T0_KW:
        return 0.5  # agent 自定义时点：不惩罚
    return 1.0 if any(kw in text for kw in T0_KW.get(t0, [])) else 0.0


def horizon_score(text: str, need_months: int) -> float:
    if not need_months or need_months <= 0:
        return 0.5
    years = [int(x) for x in re.findall(r"(\d+)[- ]year", text) if 1 <= int(x) <= 10]
    months = [int(x) for x in re.findall(r"(\d+)[- ]month", text) if 1 <= int(x) <= 120]
    reach = max(years) * 12 if years else 0
    if months:
        reach = max(reach, max(months))
    if reach == 0:
        return 0.5
    return max(0.2, min(1.0, reach / max(need_months, 1)))


def var_overlap(text: str, variables: list) -> float:
    if not variables:
        return 0.5
    hit = 0
    for v in variables:
        if any(a in text for a in VAR_ALIASES.get(v, [v.lower()])):
            hit += 1
    return hit / len(variables)


def design_score(text: str) -> float:
    if any(k in text for k in ["randomized", "randomised", "phase 3", "phase iii", "meta-analysis"]):
        return 1.0
    if "prospective" in text:
        return 0.8
    if "multicent" in text or "multi-cent" in text:
        return 0.7
    if "retrospective" in text:
        return 0.5
    return 0.4


def validation_score(text: str) -> float:
    if "external validation" in text or "externally validated" in text:
        return 0.9
    if "validation cohort" in text or "internal validation" in text:
        return 0.5
    return 0.3


def n_score(text: str) -> float:
    nums = [int(x) for x in re.findall(r"(?:n\s*=\s*|total of\s+|included\s+)(\d{2,6})", text)]
    n = max(nums) if nums else 0
    if n == 0:
        return 0.3
    if n < 100:
        return 0.3
    if n < 300:
        return 0.55
    if n < 1000:
        return 0.8
    return 1.0


def journal_score(journal: str) -> float:
    j = journal.lower().strip()
    if not j:
        return 0.4
    if j in TOP_JOURNALS:
        return 1.0
    return 0.6


def citation_score(cited: int, year: int) -> float:
    age = max(1, date.today().year - year + 1)
    return min(1.0, (cited / age) / 30.0)


def score_paper(p: Paper, case: Case) -> None:
    t = p.text
    ep = endpoint_score(p.title, p.abstract, case.outcome)
    vo = var_overlap(t, case.variables)
    p.var_overlap = round(vo, 3)
    m = (0.35 * ep
         + 0.20 * timezero_score(t, case.time_zero)
         + 0.15 * horizon_score(t, case.horizon_months)
         + 0.15 * vo
         + 0.15 * population_score(p.title, p.abstract, case))
    e = (0.35 * design_score(t)
         + 0.30 * validation_score(t)
         + 0.15 * n_score(t)
         + 0.15 * journal_score(p.journal)
         + 0.05 * citation_score(p.cited, p.year))
    has_number = bool(re.search(r"\b(0\.\d{2,3}|95%\s*ci)\b", t))
    r = ((0.50 if p.oa else 0.0)
         + (0.30 if has_number and ("c-index" in t or "auc" in t) else 0.10)
         + (0.15 if any(k in t for k in ["validation", "calibration", "multivariable"]) else 0.05)
         + (0.05 if any(k in t for k in ["github", "code is available", "calculator"]) else 0.0))
    p.scores = {"M": round(m, 3), "E": round(e, 3), "R": round(min(r, 1.0), 3)}

    if ep >= 0.7:
        p.matched.append(f"终点含 {case.outcome}")
    if timezero_score(t, case.time_zero) >= 0.5:
        p.matched.append(f"场景含 {case.time_zero}")
    if validation_score(t) >= 0.9:
        p.matched.append("外部验证")
    if design_score(t) >= 0.8:
        p.matched.append("前瞻/RCT/meta")

    if design_score(t) <= 0.5 and validation_score(t) < 0.9:
        p.risks.append("回顾性/单中心，缺外部验证")
    if not p.oa:
        p.risks.append("EPMC 标记为非 OA（可尝试出版社/机构获取）")
    if horizon_score(t, case.horizon_months) < 0.6:
        p.risks.append("预测时程可能不足")
    if vo < 0.3:
        p.risks.append("未覆盖场景关键变量")


def cluster_of(p: Paper) -> str:
    """先看标题，再看摘要；避免摘要里一句 CT 就把血液指标论文标成 imaging。"""
    for source in (p.title.lower(), p.text):
        for name, kws in CLUSTERS:
            if any(k in source for k in kws):
                return name
    return "other"


# ---------------------------------------------------------------- 帕累托

def dominates(a: Paper, b: Paper) -> bool:
    ka, kb = a.scores, b.scores
    return (all(ka[k] >= kb[k] for k in ("M", "E", "R"))
            and any(ka[k] > kb[k] for k in ("M", "E", "R")))


def pareto_layers(papers: list[Paper]) -> list[list[Paper]]:
    remaining = list(papers)
    layers = []
    while remaining:
        front = [p for p in remaining if not any(dominates(q, p) for q in remaining if q is not p)]
        if not front:  # 浮点相等时兜底
            front = remaining[:]
        layers.append(front)
        ids = {id(p) for p in front}
        remaining = [p for p in remaining if id(p) not in ids]
    return layers


def knee_order(papers: list[Paper]) -> list[Paper]:
    if not papers:
        return []
    keys = ("M", "E", "R")
    lo = {k: min(p.scores[k] for p in papers) for k in keys}
    hi = {k: max(p.scores[k] for p in papers) for k in keys}
    span = {k: (hi[k] - lo[k]) or 1.0 for k in keys}

    def dist(p: Paper) -> float:
        return math.sqrt(sum(((p.scores[k] - lo[k]) / span[k] - 1.0) ** 2 for k in keys))

    return sorted(papers, key=dist)


def title_sim(a: str, b: str) -> float:
    ta = set(re.findall(r"[a-z]{4,}", a.lower()))
    tb = set(re.findall(r"[a-z]{4,}", b.lower()))
    return len(ta & tb) / max(1, len(ta | tb))


def select_top(papers: list[Paper], k: int) -> list[Paper]:
    layers = pareto_layers(papers)
    pool: list[Paper] = []
    for layer in layers:
        if len(pool) >= k:
            break
        pool.extend(knee_order(layer))
    pool = knee_order(pool)

    picked: list[Paper] = []
    per_cluster: dict[str, int] = {}
    for p in pool:
        if len(picked) >= k:
            break
        if per_cluster.get(p.cluster, 0) >= 2:
            continue
        if any(title_sim(p.title, q.title) > 0.9 for q in picked):
            continue
        picked.append(p)
        per_cluster[p.cluster] = per_cluster.get(p.cluster, 0) + 1

    for p in pool:  # 名额没满则放宽多样性
        if len(picked) >= k:
            break
        if p not in picked:
            picked.append(p)
    for p in papers:
        p.layer = next((i + 1 for i, layer in enumerate(layers) if p in layer), 99)
    return picked[:k]


# ---------------------------------------------------------------- 主流程

def search(case: Case) -> tuple[list[Paper], dict]:
    tasks = build_queries(case)

    raw: list[dict] = []
    hits: list[dict] = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = [ex.submit(fetch, q, s, 100 if s else 50) for q, s in tasks]
        for (q, s), fut in zip(tasks, futures):
            try:
                data = fut.result()
            except Exception as exc:  # 单源失败不影响整体
                hits.append({"query": q, "sort": s, "error": str(exc)})
                continue
            hits.append({"query": q, "sort": s, "hits": data.get("hitCount", 0)})
            raw.extend((data.get("resultList") or {}).get("result") or [])

    papers: dict[str, Paper] = {}
    for item in raw:
        p = to_paper(item)
        if p and p.key not in papers:
            papers[p.key] = p
    candidates = list(papers.values())

    kept: list[Paper] = []
    for p in candidates:
        if p.year < case.year_min:
            continue
        if any(b in p.pubtypes for b in BAD_PUBTYPE):
            continue
        t = p.text
        if case.etiology.upper() == "HBV" and any(
                k in t for k in ["nafld", "nash", "non-alcoholic", "metabolic dysfunction",
                                 "mafld", "masld"]):
            continue
        if any(k in p.title.lower() for k in ["cholangiocarcinoma", "chcc-cca", "combined hepatocellular"]):
            continue
        if "curative" in case.treatment_state and "unresectable" in t:
            continue
        if case.time_zero == "surgery" and "transplant" in p.title.lower():
            continue
        kept.append(p)

    def hard_ok(p: Paper) -> bool:
        return (endpoint_score(p.title, p.abstract, case.outcome) >= 0.7
                and timezero_score(p.text, case.time_zero) >= 0.5)

    survivors = [p for p in kept if hard_ok(p)]
    relaxed = []
    if len(survivors) < 3:
        survivors = [p for p in kept
                     if endpoint_score(p.title, p.abstract, case.outcome) >= 0.4]
        relaxed.append("放宽时间零点")
    if len(survivors) < 3:
        survivors = kept
        relaxed.append("放宽终点")

    for p in survivors:
        score_paper(p, case)
        p.cluster = cluster_of(p)

    top = select_top(survivors, case.k)
    meta = {
        "queries": hits,
        "raw": len(raw),
        "unique": len(candidates),
        "after_type_year": len(kept),
        "after_hard": len(survivors),
        "relaxed": relaxed,
        "layers": len(pareto_layers(survivors)) if survivors else 0,
    }
    return top, meta


def render(top: list[Paper], meta: dict, case: Case) -> str:
    lines = [
        f"场景：{case.treatment_state} / t0={case.time_zero} / 终点={case.outcome} "
        f"{case.horizon_months}m / K={case.k}",
        f"召回 {meta['raw']} → 去重 {meta['unique']} → 类型年份 {meta['after_type_year']} "
        f"→ 硬约束 {meta['after_hard']}；帕累托层数 {meta['layers']}"
        + (f"；松弛：{meta['relaxed']}" if meta["relaxed"] else ""),
        "",
        "| # | PMID | 年 | 期刊 | 标题 | M | E | R | 层 | 簇 | OA |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, p in enumerate(top, 1):
        title = p.title if len(p.title) <= 62 else p.title[:59] + "..."
        lines.append(
            f"| {i} | {p.pmid} | {p.year} | {p.journal[:22]} | {title} | "
            f"{p.scores['M']:.2f} | {p.scores['E']:.2f} | {p.scores['R']:.2f} | "
            f"{p.layer} | {p.cluster} | {'Y' if p.oa else 'N'} |"
        )
    lines.append("")
    for i, p in enumerate(top, 1):
        lines.append(f"[{i}] {p.title}")
        lines.append(f"    {p.url}")
        lines.append(f"    匹配：{'、'.join(p.matched) or '—'}｜风险：{'、'.join(p.risks) or '—'}")
    return "\n".join(lines)


def to_json(top: list[Paper], meta: dict, case: Case) -> dict:
    return {
        "case": case.__dict__,
        "meta": meta,
        "top_k": [
            {
                "rank": i,
                "pmid": p.pmid,
                "doi": p.doi,
                "title": p.title,
                "journal": p.journal,
                "year": p.year,
                "oa_url": p.url,
                "scores": p.scores,
                "pareto_layer": p.layer,
                "cluster": p.cluster,
                "variable_overlap": getattr(p, "var_overlap", 0.0),
                "why": p.matched,
                "risk": p.risks,
                "abstract": (p.abstract or "")[:1600],
                "verify": ["终点定义", "时间零点", "样本量与验证方式"],
                "read": ["Methods: cohort", "Table 2", "Discussion: limitations"],
                "matched_need": {
                    "outcome": case.outcome,
                    "horizon_months": case.horizon_months,
                    "time_zero": case.time_zero,
                },
            }
            for i, p in enumerate(top, 1)
        ],
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="HCC 论文搜索最小基线")
    ap.add_argument("--case", help="case JSON 路径")
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--json", help="把结果写成 JSON")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--debug", action="store_true", help="打印每条查询的命中数")
    args = ap.parse_args()

    if args.case:
        with open(args.case, encoding="utf-8") as fh:
            case = Case(**json.load(fh))
    else:
        case = Case()
    case.k = args.k

    top, meta = search(case)
    print(render(top, meta, case))
    if args.debug:
        print("\n查询命中：")
        for q in meta["queries"]:
            print(f"  sort={q.get('sort') or 'relevance':10s} hits={q.get('hits', q.get('error'))}  "
                  f"{q['query'][:110]}...")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(to_json(top, meta, case), fh, ensure_ascii=False, indent=2)
        print(f"\nsaved: {args.json}")


if __name__ == "__main__":
    main()
