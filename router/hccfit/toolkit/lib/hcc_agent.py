"""HCC 论文搜索 Agent —— 工具层 CLI（供 agent / 子 agent 调用）

设计给 agent 用：
  - 所有子命令输出 JSON 到 stdout（或 --out 文件），无交互、无进度条。
  - 退出码：0 成功；2 无结果；3 上游失败。
  - 可拆步：compile → search → rank，也可一步 run。
  - 支持二次精排：用户补充条件后，用 rank --candidates 复用已抓取的候选，不再重复请求。

子命令
------
    python tools/hcc_agent.py sources
    python tools/hcc_agent.py compile --case case.json
    python tools/hcc_agent.py run     --case case.json --strategy S2 --k 5 --out result.json
    python tools/hcc_agent.py search  --case case.json --strategy S2 --out cand.json
    python tools/hcc_agent.py rank    --candidates cand.json --case case.json --k 5 --out result.json
    python tools/hcc_agent.py selftest

策略（用于消融与帕累托估计）
----
    S1  单查询 + 原始顺序            最便宜，质量最低
    S2  多查询 + 硬过滤 + 加权总分    中等
    S2  多查询 + 硬过滤 + 加权总分    默认（12 场景盲评膝点）
    S3  多查询 + 时代分层 + 帕累托    质量模式（与 S2 打平，成本翻倍）
    S4  S3 + 引文滚雪球              最贵，召回最好
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import paper_search as ps  # noqa: E402

STRATEGIES = {
    "S1": {"queries": 1, "hard": False, "pareto": False, "snowball": False},
    "S2": {"queries": 3, "hard": True, "pareto": False, "snowball": False},
    "S3": {"queries": 6, "hard": True, "pareto": True, "snowball": False},
    "S4": {"queries": 6, "hard": True, "pareto": True, "snowball": True},
}

PAPER_FIELDS = (
    "pmid", "doi", "title", "abstract", "journal", "year", "cited",
    "oa", "url", "pubtypes", "scores", "layer", "cluster", "matched", "risks",
)


@dataclass
class AgentCase(ps.Case):
    must_have: list = field(default_factory=list)
    exclude_terms: list = field(default_factory=list)


class Counter:
    def __init__(self) -> None:
        self.calls = 0
        self.errors = 0

    def tick(self) -> None:
        self.calls += 1


def paper_to_dict(p: ps.Paper) -> dict:
    return {k: getattr(p, k) for k in PAPER_FIELDS}


def paper_from_dict(d: dict) -> ps.Paper:
    p = ps.Paper(
        pmid=d.get("pmid", ""), doi=d.get("doi", ""), title=d.get("title", ""),
        abstract=d.get("abstract", ""), journal=d.get("journal", ""),
        year=int(d.get("year") or 0), cited=int(d.get("cited") or 0),
        oa=bool(d.get("oa")), url=d.get("url", ""), pubtypes=d.get("pubtypes", ""),
    )
    p.scores = d.get("scores") or {}
    p.layer = int(d.get("layer") or 0)
    p.cluster = d.get("cluster") or "other"
    p.matched = d.get("matched") or []
    p.risks = d.get("risks") or []
    return p


def queries_for(case: AgentCase, strategy: str) -> list[tuple[str, str]]:
    all_q = ps.build_queries(case)
    cfg = STRATEGIES[strategy]
    return all_q[: cfg["queries"]]


def retrieve(case: AgentCase, strategy: str, counter: Counter) -> tuple[list[ps.Paper], list[dict], int]:
    tasks = queries_for(case, strategy)
    raw: list[dict] = []
    hits: list[dict] = []

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = []
        for q, s in tasks:
            counter.tick()
            futures.append((q, s, ex.submit(ps.fetch, q, s, 100 if s else 50)))
        for q, s, fut in futures:
            try:
                data = fut.result()
            except Exception as exc:
                counter.errors += 1
                hits.append({"query": q, "sort": s, "error": str(exc)[:200]})
                continue
            hits.append({"query": q, "sort": s, "hits": data.get("hitCount", 0)})
            raw.extend((data.get("resultList") or {}).get("result") or [])

    papers: dict[str, ps.Paper] = {}
    for item in raw:
        p = ps.to_paper(item)
        if p and p.key not in papers:
            papers[p.key] = p
    return list(papers.values()), hits, len(raw)


def filter_papers(papers: list[ps.Paper], case: AgentCase, hard: bool) -> tuple[list[ps.Paper], list[str], int]:
    kept = []
    for p in papers:
        if p.year < case.year_min:
            continue
        if any(b in p.pubtypes for b in ps.BAD_PUBTYPE):
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
        if any(term.lower() not in t for term in case.must_have):
            continue
        if any(term.lower() in t for term in case.exclude_terms):
            continue
        kept.append(p)

    if not hard:
        return kept, [], len(kept)

    def hard_ok(p: ps.Paper) -> bool:
        if case.outcome not in ("", "any") and case.outcome in ps.OUTCOME_KW:
            if ps.endpoint_score(p.title, p.abstract, case.outcome) < 0.7:
                return False
        if case.time_zero not in ("", "any") and case.time_zero in ps.T0_KW:
            if ps.timezero_score(p.text, case.time_zero) < 0.5:
                return False
        return True

    survivors = [p for p in kept if hard_ok(p)]
    relaxed = []
    if len(survivors) < 3:
        survivors = [p for p in kept
                     if ps.endpoint_score(p.title, p.abstract, case.outcome) >= 0.4]
        relaxed.append("放宽时间零点")
    if len(survivors) < 3:
        survivors = kept
        relaxed.append("放宽终点")
    return survivors, relaxed, len(kept)


def snowball(case: AgentCase, papers: list[ps.Paper], counter: Counter, top_n: int = 3) -> list[ps.Paper]:
    """对当前 Top-N 做 1 跳前向引文，再一次性取回这些引文的完整记录。"""
    seeds = [p.pmid for p in papers[:top_n] if p.pmid]
    ids: list[str] = []
    for pmid in seeds:
        counter.tick()
        url = (f"https://www.ebi.ac.uk/europepmc/webservices/rest/MED/{pmid}/citations"
               f"?format=json&pageSize=25")
        try:
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": ps.UA})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for c in (data.get("citationList") or {}).get("citation") or []:
                if c.get("source") == "MED" and c.get("id"):
                    ids.append(str(c["id"]))
        except Exception:
            counter.errors += 1
    ids = list(dict.fromkeys(ids))[:60]
    if not ids:
        return []

    query = " OR ".join(f"EXT_ID:{i}" for i in ids)
    counter.tick()
    try:
        data = ps.fetch(query, "", 60)
    except Exception:
        counter.errors += 1
        return []
    out = []
    for item in (data.get("resultList") or {}).get("result") or []:
        p = ps.to_paper(item)
        if p:
            out.append(p)
    return out


def weighted_total(p: ps.Paper, w: tuple[float, float, float] = (0.5, 0.3, 0.2)) -> float:
    return w[0] * p.scores.get("M", 0) + w[1] * p.scores.get("E", 0) + w[2] * p.scores.get("R", 0)


def score_all(papers: list[ps.Paper], case: AgentCase) -> None:
    for p in papers:
        ps.score_paper(p, case)
        p.cluster = ps.cluster_of(p)


def rank(papers: list[ps.Paper], case: AgentCase, strategy: str, k: int) -> list[ps.Paper]:
    mode = "pareto" if STRATEGIES[strategy]["pareto"] else (
        "weighted" if strategy == "S2" else "naive")
    if mode == "naive":
        chosen = papers[:k]
        for p in papers:
            p.layer = 1
    elif mode == "weighted":
        ordered = sorted(papers, key=weighted_total, reverse=True)
        chosen = ordered[:k]
        for p in ordered:
            p.layer = 1
    else:
        chosen = ps.select_top(papers, k)
    return chosen


def build_result(case: AgentCase, strategy: str, top: list[ps.Paper], papers: list[ps.Paper],
                 meta: dict) -> dict:
    payload = ps.to_json(top, meta, case)
    payload["strategy"] = strategy
    payload["config"] = STRATEGIES[strategy]
    payload["pool_stats"] = {
        "scored": len(papers),
        "pareto_layers": len(ps.pareto_layers(papers)) if papers else 0,
        "front_size": len(ps.pareto_layers(papers)[0]) if papers else 0,
    }
    return payload


def run_pipeline(case: AgentCase, strategy: str, k: int) -> tuple[dict, dict]:
    counter = Counter()
    t0 = time.time()

    papers, hits, raw_n = retrieve(case, strategy, counter)
    kept, relaxed, kept_n = filter_papers(papers, case, STRATEGIES[strategy]["hard"])

    if STRATEGIES[strategy]["snowball"] and kept:
        score_all(kept, case)
        extra = snowball(case, sorted(kept, key=weighted_total, reverse=True), counter)
        if extra:
            merged = {p.key: p for p in kept}
            for p in extra:
                merged.setdefault(p.key, p)
            kept, relaxed2, kept_n = filter_papers(list(merged.values()), case, hard=True)
            relaxed.extend(relaxed2)

    score_all(kept, case)
    top = rank(kept, case, strategy, k)

    meta = {
        "queries": hits,
        "raw": raw_n,
        "unique": len(papers),
        "after_type_year": kept_n,
        "after_hard": len(kept),
        "relaxed": relaxed,
        "layers": len(ps.pareto_layers(kept)) if kept else 0,
        "calls": counter.calls,
        "errors": counter.errors,
        "latency_ms": int((time.time() - t0) * 1000),
    }
    return build_result(case, strategy, top, kept, meta), meta


def load_case(path: str) -> AgentCase:
    with open(path, encoding="utf-8") as fh:
        return AgentCase(**json.load(fh))


def write_json(obj: dict, path: str | None) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text, encoding="utf-8")
    else:
        print(text)


def cmd_sources(args: argparse.Namespace) -> int:
    tiers = {
        "Tier 1（默认，agent 必查）": [
            "Europe PMC — PubMed 摘要 + PMC 全文 + 预印本 + 指南；无 key；全文检索；citations 端点",
            "PubMed E-utilities — MeSH 精确检索；备用/交叉验证",
        ],
        "Tier 2（按需，质量补强）": [
            "OpenAlex — 引文图（需 key，$1/天免费额度）",
            "Semantic Scholar — TLDR、引用上下文（限流）",
            "ClinicalTrials.gov / ChiCTR — 终点定义、时间零点",
        ],
        "Tier 3（人工/受限）": [
            "CNKI / 万方 / 维普 — 中文队列（无开放 API，人工补卡）",
            "NCCN / CSCO / ESMO / AASLD — 指南（PDF，人工抽取）",
            "arXiv / medRxiv / bioRxiv — 预印本（不进高证据档）",
        ],
        "不用": ["Google Scholar 抓取 — 无 API、反爬、ToS；LitSearch 实测弱于稠密检索"],
    }
    write_json({"tiers": tiers}, args.out)
    return 0


def cmd_compile(args: argparse.Namespace) -> int:
    case = load_case(args.case)
    queries = [{"q": q, "sort": s} for q, s in queries_for(case, args.strategy)]
    write_json({"case": case.__dict__, "strategy": args.strategy,
                "queries": queries, "k": case.k}, args.out)
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    case = load_case(args.case)
    counter = Counter()
    papers, hits, raw_n = retrieve(case, args.strategy, counter)
    write_json({"case": case.__dict__, "strategy": args.strategy, "hits": hits,
                "calls": counter.calls, "raw": raw_n,
                "papers": [paper_to_dict(p) for p in papers]},
               args.out)
    return 0 if papers else 2


def cmd_rank(args: argparse.Namespace) -> int:
    case = load_case(args.case)
    with open(args.candidates, encoding="utf-8") as fh:
        payload = json.load(fh)
    papers = [paper_from_dict(d) for d in payload.get("papers") or []]
    kept, relaxed, kept_n = filter_papers(papers, case, STRATEGIES[args.strategy]["hard"])
    score_all(kept, case)
    top = rank(kept, case, args.strategy, args.k)
    meta = {"calls": 0, "latency_ms": 0, "raw": len(papers), "unique": len(papers),
            "after_type_year": kept_n, "after_hard": len(kept), "relaxed": relaxed,
            "layers": len(ps.pareto_layers(kept)) if kept else 0, "queries": []}
    write_json(build_result(case, args.strategy, top, kept, meta), args.out)
    return 0 if top else 2


def cmd_run(args: argparse.Namespace) -> int:
    case = load_case(args.case)
    case.k = args.k
    result, meta = run_pipeline(case, args.strategy, args.k)
    write_json(result, args.out)
    return 0 if result.get("top_k") else 2


def cmd_selftest(args: argparse.Namespace) -> int:
    """离线自检：不联网，验证打分/帕累托/K=5。"""
    papers = []
    for i, (m, e, r) in enumerate([(0.9, 0.5, 0.5), (0.7, 0.8, 0.7), (0.6, 0.6, 0.9),
                                   (0.5, 0.9, 0.4), (0.8, 0.4, 0.8), (0.4, 0.7, 0.6)]):
        p = ps.Paper(pmid=str(i), doi="", title=f"paper {i}", abstract="x" * 300,
                     journal="test", year=2024, cited=0, oa=True, url="")
        p.scores = {"M": m, "E": e, "R": r}
        p.cluster = ["clinical", "imaging", "biomarker"][i % 3]
        papers.append(p)
    top = ps.select_top(papers, 5)
    assert len(top) == 5, f"expected 5, got {len(top)}"
    assert all(p.scores for p in top)
    assert len(ps.pareto_layers(papers)) >= 1
    write_json({"ok": True, "k": len(top), "ids": [p.pmid for p in top],
                "layers": len(ps.pareto_layers(papers))}, args.out)
    return 0


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="HCC 论文搜索 Agent 工具层")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("sources"); p.add_argument("--out"); p.set_defaults(func=cmd_sources)
    p = sub.add_parser("compile"); p.add_argument("--case", required=True)
    p.add_argument("--strategy", default="S2", choices=list(STRATEGIES))
    p.add_argument("--out"); p.set_defaults(func=cmd_compile)
    p = sub.add_parser("search"); p.add_argument("--case", required=True)
    p.add_argument("--strategy", default="S2", choices=list(STRATEGIES))
    p.add_argument("--out"); p.set_defaults(func=cmd_search)
    p = sub.add_parser("rank"); p.add_argument("--candidates", required=True)
    p.add_argument("--case", required=True)
    p.add_argument("--strategy", default="S2", choices=list(STRATEGIES))
    p.add_argument("--k", type=int, default=5); p.add_argument("--out")
    p.set_defaults(func=cmd_rank)
    p = sub.add_parser("run"); p.add_argument("--case", required=True)
    p.add_argument("--strategy", default="S2", choices=list(STRATEGIES))
    p.add_argument("--k", type=int, default=5); p.add_argument("--out")
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("selftest"); p.add_argument("--out")
    p.set_defaults(func=cmd_selftest)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
