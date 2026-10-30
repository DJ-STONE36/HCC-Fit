"""HCC 论文搜索 Agent —— 单入口工具包（可被真实 agent 调用）

    python agent_pack/run.py run --case examples/detailed_case.json --mode auto --k 5
    python agent_pack/run.py run --text examples/vague_case.txt --mode auto --k 5
    python agent_pack/run.py extract --text examples/vague_case.txt
    python agent_pack/run.py selftest

设计：
  - 一个工具 search_hcc_papers(scenario, mode, k) 完成：规范化 → 路由 → 检索 → 过滤 → 排序 → 精读交接单。
  - mode: auto / fast / balanced / quality / broad。
  - 输出严格 JSON，字段见 schema/result.schema.json。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PKG = Path(__file__).resolve().parent
sys.path.insert(0, str(PKG / "lib"))
sys.path.insert(0, str(PKG))
import hcc_agent as agent  # noqa: E402
import multi_search  # noqa: E402
import paper_search as ps  # noqa: E402
import sources  # noqa: E402
from validate import validate  # noqa: E402

ALLOWED = {"disease", "treatment_state", "time_zero", "outcome", "horizon_months",
           "etiology", "variables", "year_min", "k", "include_preprints",
           "must_have", "exclude_terms"}

OUTCOMES = {"OS", "RFS", "PFS", "PHLF", "response", "LOS", "any"}
TIMEZEROS = {"surgery", "tace", "systemic", "transplant", "any"}
TREATMENTS = {"curative resection", "TACE", "HAIC", "unresectable systemic",
              "palliative systemic", "transplant", "unspecified"}
OUT_ALIASES = {"overall survival": "OS", "os": "OS", "survival": "OS",
               "recurrence": "RFS", "rfs": "RFS", "dfs": "RFS",
               "pfs": "PFS", "progression": "PFS", "phlf": "PHLF",
               "liver failure": "PHLF", "response": "response", "orr": "response",
               "los": "LOS", "length of stay": "LOS"}
TZ_ALIASES = {"resection": "surgery", "postop": "surgery", "postoperative": "surgery",
              "surgery": "surgery", "tace": "tace", "haic": "tace",
              "systemic": "systemic", "transplant": "transplant", "any": "any"}
TREAT_ALIASES = {"resection": "curative resection", "curative resection": "curative resection",
                 "tace": "TACE", "haic": "HAIC", "systemic": "unresectable systemic",
                 "unresectable": "unresectable systemic", "palliative": "palliative systemic",
                 "transplant": "transplant"}

HORIZON = {"OS": 36, "RFS": 24, "PFS": 12, "PHLF": 3, "response": 6, "LOS": 1}
TZ_LABEL = {"surgery": "手术日", "tace": "TACE/HAIC 开始", "systemic": "系统治疗开始",
            "transplant": "移植日", "any": "未指定治疗起点"}

DEFAULT_VARS = ["AFP"]

CHAIN = {"S1": ["S2", "M1"], "S2": ["M1", "S3"], "M1": ["S3"], "S3": []}

VAR_KW = {
    "AFP": ["afp", "甲胎蛋白"], "PIVKA-II": ["pivka", "异常凝血酶原"],
    "MVI": ["mvi", "微血管侵犯", "微血管癌栓"], "tumor size": ["肿瘤大小", "最大径", "直径", "cm"],
    "tumor number": ["肿瘤数目", "多发", "结节数", "枚"], "satellite": ["卫星灶", "子灶"],
    "PT": ["pt", "凝血酶原", "inr"], "HBV-DNA": ["hbv-dna", "hbv dna", "病毒载量"],
    "GGT": ["ggt", "γ-gt", "谷氨酰"], "ALBI": ["albi"], "Child-Pugh": ["child-pugh", "child pugh", "child"],
    "PLT": ["plt", "血小板"], "TBIL": ["tbil", "胆红素"], "ALB": ["alb", "白蛋白"],
    "NLR": ["nlr", "中性粒"], "PLR": ["plr"], "ECOG": ["ecog", "体能状态"], "CRP": ["crp", "c反应蛋白"],
}


def _hash(obj) -> str:
    return hashlib.sha1(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]


def infer_from_text(text: str) -> tuple[dict, list[str], str]:
    """规则版抽取，作为 LLM 失败时的确定性回退；真实 agent 应优先用 LLM 抽取。"""
    t = text.lower()
    assumptions: list[str] = []

    if any(k in t for k in ["肝衰", "肝衰竭", "phlf"]):
        outcome = "PHLF"
    elif any(k in t for k in ["复发", "rfs", "无复发生存"]):
        outcome = "RFS"
    elif any(k in t for k in ["进展", "pfs", "无进展"]):
        outcome = "PFS"
    elif any(k in t for k in ["缓解", "有效", "反应", "orr", "recist"]):
        outcome = "response"
    elif any(k in t for k in ["生存", "活", "几年", "寿命"]):
        outcome = "OS"
    elif any(k in t for k in ["住院", "恢复", "住院时间", "los"]):
        outcome = "LOS"
    else:
        outcome = "any"
        assumptions.append("未识别明确终点，按 any 检索")

    m = re.search(r"([一二两三四五六七八九十\d]+)\s*年", text)
    cn = {"一": 12, "二": 24, "两": 24, "三": 36, "四": 48, "五": 60,
          "六": 72, "七": 84, "八": 96, "九": 108, "十": 120,
          "十五": 180, "二十": 240, "二十五": 300, "三十": 360}
    if m:
        raw = m.group(1)
        months = int(raw) * 12 if raw.isdigit() else cn.get(raw, 0)
    else:
        months = 0
        assumptions.append("未识别时程，按 any 检索")
    months = max(0, min(120, months))

    if any(k in t for k in ["移植", "transplant"]):
        treatment_state, time_zero = "transplant", "transplant"
    elif any(k in t for k in ["haic", "灌注"]):
        treatment_state, time_zero = "HAIC", "tace"
    elif any(k in t for k in ["tace", "介入", "栓塞"]):
        treatment_state, time_zero = "TACE", "tace"
    elif any(k in t for k in ["免疫", "pd-1", "pd1", "阿替利珠", "仑伐", "靶向", "系统治疗"]):
        treatment_state, time_zero = "unresectable systemic", "systemic"
    elif any(k in t for k in ["切除", "手术", "术后", "肝切"]):
        treatment_state, time_zero = "curative resection", "surgery"
    else:
        treatment_state, time_zero = "unspecified", "any"
        assumptions.append("未识别治疗场景，按任意治疗起点检索")

    if any(k in t for k in ["乙肝", "hbv", "乙型肝炎"]):
        etiology = "HBV"
    elif any(k in t for k in ["丙肝", "hcv", "丙型肝炎"]):
        etiology = "HCV"
    elif any(k in t for k in ["脂肪肝", "nafld", "masld", "代谢"]):
        etiology = "NAFLD"
    else:
        etiology = "unknown"

    variables = [name for name, kws in VAR_KW.items() if any(k in t for k in kws)]
    if not variables:
        variables = []
        assumptions.append("未识别具体变量，变量按 any 处理")

    explicit = sum(1 for k in ["生存", "活", "复发", "进展", "缓解", "肝衰", "住院"] if k in t)
    confidence = "high" if treatment_state != "unspecified" and explicit >= 1 and m else \
                 ("medium" if treatment_state != "unspecified" or explicit >= 1 else "low")

    case = {"disease": "HCC", "treatment_state": treatment_state, "time_zero": time_zero,
            "outcome": outcome, "horizon_months": months, "etiology": etiology,
            "variables": variables, "year_min": 0, "k": 5}
    return case, assumptions, confidence


def clean_input(raw) -> dict:
    """只保留前端真正传了的字段；不填默认值、不补空字段。"""
    if isinstance(raw, str):
        return {"text": raw.strip()} if raw.strip() else {}
    out = {}
    for k, v in (raw or {}).items():
        if k not in ALLOWED and k != "text":
            continue
        if v is None or v == "" or v == []:
            continue
        out[k] = v
    for field, aliases, allowed in (
        ("outcome", OUT_ALIASES, OUTCOMES),
        ("time_zero", TZ_ALIASES, TIMEZEROS),
        ("treatment_state", TREAT_ALIASES, TREATMENTS),
    ):
        if field in out:
            key = str(out[field]).lower().strip()
            out[field] = aliases.get(key, key if key in allowed else out[field])
    return out


def build_search_case(inp: dict) -> tuple[dict, list[str], str]:
    """把用户输入转成“检索用的解释”；所有补全都只写进 assumptions，不写回 input。"""
    if "text" in inp:
        case, assumptions, confidence = infer_from_text(str(inp["text"]))
        overlay = {k: v for k, v in inp.items() if k != "text"}
        if overlay:
            case.update(overlay)
            assumptions.append("结构化字段优先于文本抽取")
    else:
        case, assumptions, confidence = normalize_case(inp)
    case, extra, conf2 = normalize_case(case)
    assumptions.extend(a for a in extra if a not in assumptions)
    order = {"low": 0, "medium": 1, "high": 2}
    confidence = min((confidence, conf2), key=lambda x: order[x])
    return case, assumptions, confidence


def normalize_case(raw: dict) -> tuple[dict, list[str], str]:
    c = {k: v for k, v in raw.items() if k in ALLOWED}
    assumptions: list[str] = []
    invalid = False
    c.setdefault("disease", "HCC")

    if "outcome" in c:
        key = str(c["outcome"]).lower().strip()
        canon = OUT_ALIASES.get(key, key if key in OUTCOMES else None)
        if canon:
            c["outcome"] = canon
        else:
            assumptions.append(f"未识别终点 {c['outcome']}，保留原文，按宽检索")
            c["outcome"] = str(c["outcome"])
    else:
        c["outcome"] = "any"
        assumptions.append("未给终点，按 any 检索")

    if "treatment_state" in c:
        key = str(c["treatment_state"]).lower().strip()
        canon = TREAT_ALIASES.get(key, key if key in TREATMENTS else None)
        if canon:
            c["treatment_state"] = canon
        else:
            assumptions.append(f"未识别治疗场景 {c['treatment_state']}，保留原文")
            c["treatment_state"] = str(c["treatment_state"])
    else:
        c["treatment_state"] = "unspecified"
        assumptions.append("未给治疗场景，按 any 检索")

    if "time_zero" in c:
        key = str(c["time_zero"]).lower().strip()
        canon = TZ_ALIASES.get(key, key if key in TIMEZEROS else None)
        if canon:
            c["time_zero"] = canon
        else:
            assumptions.append(f"未识别时间零点 {c['time_zero']}，按 any 检索")
            c["time_zero"] = "any"
    else:
        c["time_zero"] = "any"
        assumptions.append("未给时间零点，按 any 检索")

    if "horizon_months" not in c:
        c["horizon_months"] = 0
        assumptions.append("未给时程，按 any 检索")
    try:
        c["horizon_months"] = max(0, min(120, int(c["horizon_months"])))
    except (TypeError, ValueError):
        c["horizon_months"] = 0
        assumptions.append("时程非法，按 any 检索")

    if "etiology" not in c:
        c["etiology"] = "unknown"
    if "variables" not in c:
        c["variables"] = []
        assumptions.append("未给变量，按 any 处理")
    c.setdefault("year_min", 0)
    try:
        c["k"] = max(3, min(8, int(c.get("k", 5))))
    except (TypeError, ValueError):
        c["k"] = 5

    confidence = "high"
    if assumptions or invalid:
        confidence = "medium"
    if invalid or c["time_zero"] == "any" or any("终点" in a for a in assumptions):
        confidence = "low"
    return c, assumptions, confidence


def choose_strategy(case: dict, mode: str) -> tuple[str, str]:
    if mode == "fast":
        return "S1", "fast 模式：单查询"
    if mode == "balanced":
        return "S2", "balanced 模式：三查询 + 硬过滤 + 加权"
    if mode == "quality":
        return "S3", "quality 模式：时代窗 + 帕累托"
    if mode == "broad":
        return "M1", "broad 模式：三源补漏"
    # auto
    if case.get("treatment_state") == "HAIC" or case.get("outcome") in ("PHLF", "LOS"):
        return "M1", "auto：HAIC/PHLF/LOS 用多源补漏"
    if case.get("treatment_state") == "transplant" or case.get("outcome") == "PFS":
        return "S1", "auto：移植/PFS 用单查询，避免时代窗漂移"
    return "S2", "auto：默认三查询"


def execute(strategy: str, case: dict, k: int) -> dict:
    case = dict(case)
    case["k"] = k
    if strategy == "M1":
        return multi_search.run_case(case, "M1", k)
    return agent.run_pipeline(agent.AgentCase(**case), strategy, k)[0]


def mean_m(payload: dict) -> float:
    scores = [(p.get("scores") or {}).get("M", 0) for p in payload.get("top_k") or []]
    return sum(scores) / len(scores) if scores else 0.0


def run_search(case: dict, mode: str = "auto", k: int = 5, base_confidence: str = "high") -> dict:
    t0 = time.time()
    strategy, reason = choose_strategy(case, mode)
    history = [strategy]
    payload = execute(strategy, case, k)

    for nxt in CHAIN.get(strategy, [])[:1]:
        if len(payload.get("top_k") or []) >= 3 and mean_m(payload) >= 0.55:
            break
        strategy = nxt
        history.append(strategy)
        payload = execute(strategy, case, k)

    errors = int((payload.get("meta") or {}).get("errors") or 0)

    papers = []
    for p in payload.get("top_k") or []:
        scores = p.get("scores") or {}
        papers.append({
            "pmid": p.get("pmid", ""),
            "doi": p.get("doi", ""),
            "title": p.get("title", ""),
            "journal": p.get("journal", ""),
            "year": p.get("year"),
            "oa_url": p.get("oa_url") or p.get("url") or "",
            "scores": scores,
            "variable_overlap": p.get("variable_overlap", 0.0),
            "pareto_layer": p.get("pareto_layer", 1),
            "cluster": p.get("cluster", ""),
            "why": p.get("why") or [],
            "risk": p.get("risk") or [],
            "verify": [
                "主要终点是否为该场景终点（不是摘要里提一句）",
                f"时间零点是否为{TZ_LABEL.get(case.get('time_zero'), '目标治疗')}",
                "样本量、事件数与验证方式（外部/内部）",
                "关键变量口径与单位（AFP/HBV-DNA/PT 等）",
            ],
            "read": ["Methods: 队列与纳入排除", "结果：主表/性能表（C-index/AUC/校准）",
                     "Discussion: 局限与外推"],
            "abstract_snippet": (p.get("abstract") or "")[:1200],
        })

    unknown = any(x in (case.get("time_zero") or "") for x in ("any",))
    empty_reason = None
    if not papers:
        empty_reason = "upstream_unavailable" if errors > 0 else "hard_constraints_no_match"

    coverage = sum(p["variable_overlap"] for p in papers) / len(papers) if papers else 0.0
    coverage_gap = bool(papers) and coverage < 0.3

    confidence = base_confidence
    if len(papers) < 3 and confidence == "high":
        confidence = "medium"
    if unknown:
        confidence = "low"
    if errors > 0 and len(papers) < 3:
        confidence = "low"
    if coverage_gap and confidence == "high":
        confidence = "medium"

    return {
        "query_id": _hash({"case": case, "mode": mode, "k": k}),
        "scenario": case,
        "route": {"mode": mode, "strategy": strategy, "history": history, "reason": reason},
        "confidence": confidence,
        "assumptions": [],
        "coverage_gap": coverage_gap,
        "shortfall_reason": (f"only_{len(papers)}_passed_hard_constraints"
                             if 0 < len(papers) < k else None),
        "papers": papers,
        "meta": {
            "calls": (payload.get("meta") or {}).get("calls", 0),
            "latency_ms": int((time.time() - t0) * 1000),
            "relaxed": (payload.get("meta") or {}).get("relaxed", []),
            "candidates": (payload.get("meta") or {}).get("after_hard", 0),
            "k_requested": k,
            "returned": len(papers),
            "coverage": round(coverage, 3),
            "errors": errors,
            "partial": bool(errors > 0 or len(papers) < k),
        },
        "empty_reason": empty_reason,
    }


OUT_LABEL = {"OS": "总生存", "RFS": "复发/无复发生存", "PFS": "无进展生存",
             "PHLF": "术后肝衰竭", "response": "治疗反应", "LOS": "住院/康复时间"}
TREAT_LABEL = {"curative resection": "切除后", "TACE": "TACE", "HAIC": "HAIC",
               "unresectable systemic": "系统治疗", "palliative systemic": "姑息系统治疗",
               "transplant": "移植", "unspecified": "治疗阶段未定"}


def _fmt_h(months: int) -> str:
    if months % 12 == 0:
        return f"{months // 12}年"
    return f"{months}个月"


def build_interpretations(case: dict, primary_conf: str, missing: list[str]) -> list[dict]:
    """返回“可能想问的几种解释”。只有第一项是实际检索过的；其余是前端可点的选项，不是数据。"""
    items: list[dict] = []
    base = {k: case.get(k) for k in ("disease", "treatment_state", "time_zero", "outcome",
                                     "horizon_months", "etiology", "variables", "year_min", "k")}
    base = {k: v for k, v in base.items() if v is not None}

    def add(label: str, rationale: str, **over):
        sc = dict(base)
        sc.update(over)
        items.append({
            "id": f"i{len(items) + 1}",
            "label": label,
            "rationale": rationale,
            "scenario": sc,
            "is_primary": len(items) == 0,
            "searched": len(items) == 0,
            "confidence": primary_conf if len(items) == 0 else "low",
        })

    add(f"{OUT_LABEL.get(case['outcome'], case['outcome'])} {_fmt_h(case['horizon_months'])}",
        "当前检索使用的解释")

    if "outcome" in missing:
        for outcome, months, tz, treat in [
            ("RFS", 24, case["time_zero"], case["treatment_state"]),
            ("response", 6, case["time_zero"], case["treatment_state"]),
            ("PHLF", 3, "surgery", "curative resection"),
            ("LOS", 1, "surgery", "curative resection"),
        ]:
            add(f"{OUT_LABEL[outcome]} {_fmt_h(months)}", f"如果更关心{OUT_LABEL[outcome]}",
                outcome=outcome, horizon_months=months, time_zero=tz, treatment_state=treat)
    elif "treatment" in missing:
        for treat, tz in [("curative resection", "surgery"), ("TACE", "tace"),
                          ("unresectable systemic", "systemic"), ("transplant", "transplant")]:
            add(f"{TREAT_LABEL[treat]}的{OUT_LABEL.get(case['outcome'], case['outcome'])}",
                f"如果治疗阶段是{TREAT_LABEL[treat]}", treatment_state=treat, time_zero=tz)
    return items[:5]


def _serialize(p, case: dict) -> dict:
    scores = p.scores or {}
    return {
        "pmid": p.pmid, "doi": p.doi, "title": p.title, "journal": p.journal,
        "year": p.year, "oa_url": p.url, "scores": scores,
        "variable_overlap": getattr(p, "var_overlap", 0.0),
        "pareto_layer": p.layer, "cluster": p.cluster,
        "why": p.matched, "risk": p.risks,
        "verify": ["主要终点是否为该场景终点（不是摘要里提一句）",
                   f"时间零点是否为{TZ_LABEL.get(case.get('time_zero'), '目标治疗')}",
                   "样本量、事件数与验证方式（外部/内部）",
                   "关键变量口径与单位（AFP/HBV-DNA/PT 等）"],
        "read": ["Methods: 队列与纳入排除", "结果：主表/性能表（C-index/AUC/校准）",
                 "Discussion: 局限与外推"],
        "abstract_snippet": (p.abstract or "")[:1200],
    }


def _resolve_pmid(identifier: str) -> str:
    ident = str(identifier or "").strip()
    if ident.isdigit():
        return ident
    if not ident:
        return ""
    data = ps.fetch(f'DOI:"{ident}"', "", 5)
    for item in (data.get("resultList") or {}).get("result") or []:
        if item.get("pmid"):
            return str(item["pmid"])
    return ""


def expand_hcc_papers(identifier: str, scenario, k: int = 10) -> dict:
    """对一篇种子论文做 1 跳前向引文，再过滤/打分/排序。用于 agent 的补漏动作。"""
    if isinstance(scenario, dict):
        case, _, _ = normalize_case(scenario)
    else:
        case, _, _ = infer_from_text(str(scenario))
    pmid = _resolve_pmid(identifier)
    if not pmid:
        return {"seed": None, "papers": [], "meta": {"calls": 1, "error": "seed_not_found"}}

    calls = 1
    url = (f"https://www.ebi.ac.uk/europepmc/webservices/rest/MED/{pmid}/citations"
           f"?format=json&pageSize=50")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": ps.UA})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"seed": pmid, "papers": [], "meta": {"calls": calls, "error": str(exc)[:160]}}
    ids = [str(c["id"]) for c in (data.get("citationList") or {}).get("citation") or []
           if c.get("source") == "MED" and c.get("id")][:60]
    if not ids:
        return {"seed": pmid, "papers": [], "meta": {"calls": calls, "error": "no_citations"}}

    calls += 1
    try:
        detail = ps.fetch(" OR ".join(f"EXT_ID:{i}" for i in ids), "", len(ids))
    except Exception as exc:
        return {"seed": pmid, "papers": [], "meta": {"calls": calls, "error": str(exc)[:160]}}

    papers = []
    for item in (detail.get("resultList") or {}).get("result") or []:
        p = ps.to_paper(item)
        if p:
            papers.append(p)
    kept, _, _ = agent.filter_papers(papers, agent.AgentCase(**case), True)
    agent.score_all(kept, agent.AgentCase(**case))
    top = agent.rank(kept, agent.AgentCase(**case), "S2", k)
    return {"seed": pmid, "papers": [_serialize(p, case) for p in top],
            "meta": {"calls": calls, "candidates": len(papers), "kept": len(kept)}}


def rank_hcc_papers(candidates: list[dict], scenario, k: int = 5) -> dict:
    """对前端/上游给的候选（含 abstract_snippet）重新过滤、打分、排序；0 次网络调用。"""
    k = max(3, min(8, int(k)))
    if isinstance(scenario, dict):
        case, _, _ = normalize_case(scenario)
    else:
        case, _, _ = infer_from_text(str(scenario))
    papers = []
    for c in candidates or []:
        abstract = (c.get("abstract_snippet") or c.get("abstract") or "")
        if len(abstract) < 250:
            continue
        papers.append(ps.Paper(
            pmid=str(c.get("pmid") or ""), doi=str(c.get("doi") or "").lower(),
            title=c.get("title") or "", abstract=abstract, journal=c.get("journal") or "",
            year=int(c.get("year") or 0), cited=0, oa=bool(c.get("oa_url")),
            url=c.get("oa_url") or "",
        ))
    kept, relaxed, _ = agent.filter_papers(papers, agent.AgentCase(**case), True)
    agent.score_all(kept, agent.AgentCase(**case))
    top = agent.rank(kept, agent.AgentCase(**case), "S2", k)
    return {"papers": [_serialize(p, case) for p in top],
            "meta": {"calls": 0, "candidates": len(papers), "kept": len(kept), "relaxed": relaxed}}


def search_hybrid(plan: dict, k: int = 5) -> dict:
    """LLM 规划的查询 + 算法检索/RRF/过滤/多样性。plan:
    {"scenario": {...} 或 {"text": "..."}, "queries": ["PubMed Boolean", ...],
     "must_have": [], "exclude_terms": [], "year_min": 0}
    """
    try:
        k = max(3, min(8, int(k)))
    except (TypeError, ValueError):
        k = 5
    t0 = time.time()
    scenario = plan.get("scenario") if isinstance(plan, dict) else None
    if scenario is None:
        scenario = {"text": str(plan.get("text", ""))} if isinstance(plan, dict) else {"text": str(plan)}
    queries = [str(q).strip() for q in (plan.get("queries") or []) if str(q).strip()][:8]
    if not queries:
        raise ValueError("plan.queries is required")

    inp = clean_input(scenario)
    case, assumptions, confidence = build_search_case(inp)
    for key in ("must_have", "exclude_terms"):
        vals = plan.get(key) or []
        if key == "must_have" and len(vals) > 2:
            assumptions.append("must_have 过多，已忽略；用查询词表达约束")
            continue
        if key == "exclude_terms" and len(vals) > 5:
            vals = vals[:5]
        if vals:
            case[key] = vals
    if plan.get("year_min") is not None:
        case["year_min"] = int(plan["year_min"])
    case["k"] = k

    def fetch(q: str):
        return sources.search("pubmed", q, 50, case.get("year_min", 0), "")

    results: list[list[ps.Paper]] = []
    errors = 0
    with ThreadPoolExecutor(max_workers=min(6, len(queries))) as ex:
        futures = [ex.submit(fetch, q) for q in queries]
        for i, fut in enumerate(futures):
            try:
                papers, _meta = fut.result()
                results.append(papers)
            except Exception:
                errors += 1
                results.append([])

    rrf: dict[str, float] = {}
    by_key: dict[str, ps.Paper] = {}
    for plist in results:
        for rank, p in enumerate(plist):
            rrf[p.key] = rrf.get(p.key, 0.0) + 1.0 / (60 + rank + 1)
            by_key.setdefault(p.key, p)
    if not by_key:
        return {
            "query_id": _hash({"plan": plan, "k": k}),
            "input_scenario": inp,
            "search_scenario": case,
            "route": {"mode": "hybrid", "strategy": "H1", "history": ["H1"], "reason": "LLM plan + RRF"},
            "confidence": "low",
            "assumptions": assumptions,
            "coverage_gap": True,
            "shortfall_reason": None,
            "context_missing": [],
            "papers": [],
            "meta": {"calls": len(queries), "latency_ms": int((time.time() - t0) * 1000),
                     "errors": errors, "partial": True, "returned": 0, "k_requested": k,
                     "candidates": 0, "coverage": 0.0},
            "empty_reason": "upstream_unavailable" if errors else "hard_constraints_no_match",
        }

    max_rrf = max(rrf.values()) or 1.0
    case_obj = agent.AgentCase(**case)
    kept, relaxed, _ = agent.filter_papers(list(by_key.values()), case_obj, True)
    if not kept:
        kept = list(by_key.values())
    model_kw = ("nomogram", "prediction model", "predictive model", "prognostic model",
                "risk score", "scoring system", "machine learning", "deep learning",
                "artificial intelligence", "random forest", "neural network", "calculator")
    model_kept = [p for p in kept if any(k in p.text for k in model_kw)]
    if len(model_kept) >= 3:
        kept = model_kept
        relaxed.append("模型词过滤")
    agent.score_all(kept, case_obj)

    scored = []
    for p in kept:
        r = rrf.get(p.key, 0.0) / max_rrf
        scored.append((r, r, p))
    scored.sort(key=lambda x: x[0], reverse=True)

    picked = []
    per_cluster: dict[str, int] = {}
    for h, r, p in scored:
        if len(picked) >= k:
            break
        if per_cluster.get(p.cluster, 0) >= 2:
            continue
        if any(ps.title_sim(p.title, q.title) > 0.9 for _, _, q in picked):
            continue
        picked.append((h, r, p))
        per_cluster[p.cluster] = per_cluster.get(p.cluster, 0) + 1
    for h, r, p in scored:
        if len(picked) >= k:
            break
        if all(p is not q for _, _, q in picked):
            picked.append((h, r, p))

    papers = []
    for h, r, p in picked[:k]:
        p.layer = 1
        item = _serialize(p, case)
        item["hybrid_score"] = round(h, 3)
        item["rrf"] = round(r, 3)
        papers.append(item)

    pool = []
    for h, r, p in scored[:20]:
        p.layer = 1
        item = _serialize(p, case)
        item["hybrid_score"] = round(h, 3)
        item["rrf"] = round(r, 3)
        pool.append(item)

    coverage = sum(x.get("variable_overlap", 0) for x in papers) / len(papers) if papers else 0.0
    empty_reason = None
    if not papers:
        empty_reason = "upstream_unavailable" if errors else "hard_constraints_no_match"
    return {
        "query_id": _hash({"plan": plan, "k": k}),
        "input_scenario": inp,
        "search_scenario": case,
        "route": {"mode": "hybrid", "strategy": "H1", "history": ["H1"], "reason": "LLM plan + PubMed + RRF"},
        "confidence": "low" if (assumptions or errors or case["time_zero"] == "any") else "medium",
        "assumptions": assumptions,
        "coverage_gap": coverage < 0.3,
        "shortfall_reason": f"only_{len(papers)}_passed_hard_constraints" if len(papers) < k else None,
        "context_missing": [],
        "papers": papers,
        "pool": pool,
        "meta": {"calls": len(queries) * 2, "latency_ms": int((time.time() - t0) * 1000),
                 "errors": errors, "partial": bool(errors or len(papers) < k),
                 "returned": len(papers), "k_requested": k,
                 "candidates": len(by_key), "kept": len(kept), "coverage": round(coverage, 3),
                 "queries": queries, "relaxed": relaxed},
        "empty_reason": empty_reason,
    }


def rerank_hybrid(candidates: list[dict], scenario, llm_scores: dict | None = None, k: int = 5) -> dict:
    """LLM 给 0–1 相关性分；最终用 RRF 融合 LLM 排名与算法排名，无手工权重。"""
    try:
        k = max(3, min(8, int(k)))
    except (TypeError, ValueError):
        k = 5
    base = []
    for c in candidates or []:
        s = c.get("hybrid_score")
        if s is None:
            s = c.get("rrf")
        if s is None:
            s = (c.get("scores") or {}).get("M", 0.5)
        base.append((float(s), c))
    if not base:
        return {"papers": [], "meta": {"calls": 0, "returned": 0}}

    def llm_of(c: dict) -> float:
        if not llm_scores:
            return 0.0
        for key in (c.get("pmid"), c.get("doi"), (c.get("title") or "").lower()):
            if key and key in llm_scores:
                return max(0.0, min(1.0, float(llm_scores[key])))
        return 0.0

    base_sorted = sorted(base, key=lambda x: x[0], reverse=True)
    base_rank = {id(c): i for i, (_, c) in enumerate(base_sorted)}
    if llm_scores:
        # LLM 是相关性主排序；算法只做 tie-break，不再用 0.7/0.3 权重。
        scored = [(llm_of(c), -base_rank[id(c)], c) for _, c in base]
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        scored = [(s, c) for s, _, c in scored]
    else:
        scored = [(1.0 / (60 + base_rank[id(c)] + 1), c) for _, c in base]
        scored.sort(key=lambda x: x[0], reverse=True)

    picked = []
    per_cluster: dict[str, int] = {}
    for score, c in scored:
        if len(picked) >= k:
            break
        cluster = c.get("cluster") or "other"
        if per_cluster.get(cluster, 0) >= 2:
            continue
        if any(ps.title_sim(c.get("title", ""), q.get("title", "")) > 0.9 for _, q in picked):
            continue
        picked.append((score, c))
        per_cluster[cluster] = per_cluster.get(cluster, 0) + 1
    for score, c in scored:
        if len(picked) >= k:
            break
        if all(c is not q for _, q in picked):
            picked.append((score, c))

    papers = []
    for score, c in picked[:k]:
        d = dict(c)
        d["hybrid_score_final"] = round(score, 3)
        papers.append(d)
    return {"papers": papers, "meta": {"calls": 0, "returned": len(papers),
                                       "llm_scored": bool(llm_scores)}}


def search_hcc_papers(scenario, mode: str = "auto", k: int = 5) -> dict:
    """真实 agent 唯一需要调用的函数。scenario 可以是 dict 或自由文本；k 默认 5，范围 3–8。"""
    try:
        k = max(3, min(8, int(k)))
    except (TypeError, ValueError):
        k = 5

    inp = clean_input(scenario)
    case, assumptions, confidence = build_search_case(inp)
    case["k"] = k

    result = run_search(case, mode, k, confidence)
    result["query_id"] = _hash({"raw": scenario, "case": case, "mode": mode, "k": k})
    result["assumptions"] = assumptions
    result["input_scenario"] = inp
    result["search_scenario"] = case
    result["scenario"] = inp  # 兼容：scenario 始终是用户输入，不是系统补全

    missing = []
    if any(("未识别明确终点" in a) or ("未给终点" in a) or ("未知终点" in a) for a in assumptions):
        missing.append("outcome")
    if any(("未识别治疗场景" in a) or ("未给治疗场景" in a) or ("未知治疗" in a) for a in assumptions):
        missing.append("treatment")
    if any(("未给时间零点" in a) or ("未识别时间零点" in a) for a in assumptions):
        missing.append("time_zero")
    if any(("未给时程" in a) or ("未识别时程" in a) for a in assumptions):
        missing.append("horizon_months")
    if any(("未给变量" in a) or ("未识别具体变量" in a) for a in assumptions):
        missing.append("variables")
    result["context_missing"] = missing

    # 置信度：任何推断项最高 medium；终点默认或时点 any 为 low
    if any(("终点" in a) or ("未知治疗" in a) for a in assumptions) or case["time_zero"] == "any":
        result["confidence"] = "low"
    elif assumptions and result["confidence"] == "high":
        result["confidence"] = "medium"
    if result["confidence"] == "low":
        inferred = bool(assumptions) or case["time_zero"] == "any"
        for p in result["papers"]:
            if inferred and "场景为推断，需人工确认" not in p["risk"]:
                p["risk"].append("场景为推断，需人工确认")
    if result["meta"].get("errors", 0) > 0:
        for p in result["papers"]:
            if "部分数据源失败，结果可能不全" not in p["risk"]:
                p["risk"].append("部分数据源失败，结果可能不全")

    errs = validate(result)
    if errs:
        result["validation_errors"] = errs
        result["confidence"] = "low"
    return result


def _load_scenario(args):
    if getattr(args, "case", None):
        return json.loads(Path(args.case).read_text(encoding="utf-8"))
    raw = str(args.text)
    p = Path(raw)
    if p.exists() and p.is_file():
        return p.read_text(encoding="utf-8")
    return raw  # 内联文本（建议设置 PYTHONUTF8=1）


def cmd_expand(args) -> int:
    res = expand_hcc_papers(args.identifier, _load_scenario(args), args.k)
    _write(res, args.out)
    return 0 if res.get("papers") else 2


def cmd_rank(args) -> int:
    payload = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    candidates = payload.get("papers") if isinstance(payload, dict) else payload
    res = rank_hcc_papers(candidates or [], _load_scenario(args), args.k)
    _write(res, args.out)
    return 0 if res.get("papers") else 2


def cmd_hybrid(args) -> int:
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    res = search_hybrid(plan, args.k)
    _write(res, args.out)
    return 0 if res.get("papers") else 2


def cmd_extract(args) -> int:
    raw = str(args.text)
    p = Path(raw)
    text = p.read_text(encoding="utf-8") if p.exists() and p.is_file() else raw
    case, assumptions, confidence = infer_from_text(text)
    out = {"scenario": case, "assumptions": assumptions, "confidence": confidence}
    _write(out, args.out)
    return 0


def cmd_run(args) -> int:
    scenario = _load_scenario(args)
    result = search_hcc_papers(scenario, args.mode, args.k)
    _write(result, args.out)
    if result["papers"]:
        return 0
    return 3 if result.get("empty_reason") == "upstream_unavailable" else 2


def cmd_selftest(args) -> int:
    # 路由
    assert choose_strategy({"treatment_state": "HAIC", "outcome": "OS"}, "auto")[0] == "M1"
    assert choose_strategy({"treatment_state": "transplant", "outcome": "RFS"}, "auto")[0] == "S1"
    assert choose_strategy({"treatment_state": "TACE", "outcome": "OS"}, "auto")[0] == "S2"
    # 模糊抽取
    c, a, conf = infer_from_text("患者肝癌术后，乙肝，想知道还能活多久")
    assert c["outcome"] == "OS" and c["time_zero"] == "surgery" and c["etiology"] == "HBV"
    c2, _, _ = infer_from_text("肝癌，做TACE，会不会复发")
    assert c2["outcome"] == "RFS" and c2["time_zero"] == "tace"
    c3, _, _ = infer_from_text("术后恢复，还能活多久")
    assert c3["outcome"] == "OS", c3
    c4, _, _ = infer_from_text("二十年生存")
    assert c4["horizon_months"] == 120
    # 规范化/枚举/clamp
    c5, _, _ = normalize_case({"outcome": "overall survival", "time_zero": "resection", "k": 10})
    assert c5["outcome"] == "OS" and c5["time_zero"] == "surgery" and c5["k"] == 8
    c6, _, _ = normalize_case({"outcome": "PHLF"})
    assert c6["horizon_months"] == 0 and c6["time_zero"] == "any"
    # 上游故障 ≠ 无文献
    orig = globals()["execute"]

    def boom(strategy, case, k):
        return {"top_k": [], "meta": {"errors": 2}}

    globals()["execute"] = boom
    try:
        r = run_search({"outcome": "OS", "time_zero": "any", "horizon_months": 36,
                        "treatment_state": "unspecified", "etiology": "unknown",
                        "variables": ["AFP"], "year_min": 2018, "k": 5}, "fast", 5)
        assert r["empty_reason"] == "upstream_unavailable", r["empty_reason"]
        assert r["meta"]["partial"] is True
    finally:
        globals()["execute"] = orig
    # 输入不被默认值污染
    assert clean_input({"text": "肝癌", "year_min": None}) == {"text": "肝癌"}
    assert "outcome" not in clean_input({"text": "肝癌"})
    # 分数
    assert mean_m({"top_k": [], "meta": {}}) == 0.0
    _write({"ok": True, "tests": 15}, args.out)
    return 0


def _write(obj: dict, path: str | None) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text, encoding="utf-8")
        print(f"saved {path}", file=sys.stderr)
    else:
        print(text)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="HCC 论文搜索 Agent 工具包")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("extract"); p.add_argument("--text", required=True)
    p.add_argument("--out"); p.set_defaults(func=cmd_extract)

    p = sub.add_parser("run")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--case"); g.add_argument("--text")
    p.add_argument("--mode", default="auto", choices=["auto", "fast", "balanced", "quality", "broad"])
    p.add_argument("--k", type=int, default=5); p.add_argument("--out")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("expand")
    p.add_argument("--identifier", required=True, help="PMID 或 DOI")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--case"); g.add_argument("--text")
    p.add_argument("--k", type=int, default=10); p.add_argument("--out")
    p.set_defaults(func=cmd_expand)

    p = sub.add_parser("hybrid")
    p.add_argument("--plan", required=True, help="LLM 查询计划 JSON")
    p.add_argument("--k", type=int, default=5); p.add_argument("--out")
    p.set_defaults(func=cmd_hybrid)

    p = sub.add_parser("rank")
    p.add_argument("--candidates", required=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--case"); g.add_argument("--text")
    p.add_argument("--k", type=int, default=5); p.add_argument("--out")
    p.set_defaults(func=cmd_rank)

    p = sub.add_parser("selftest"); p.add_argument("--out"); p.set_defaults(func=cmd_selftest)
    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
