"""多源多策略检索：Europe PMC + PubMed + OpenAlex。

    python tools/multi_search.py run --scenarios experiments/scenarios.json \
        --id resection_os_hbv --strategy M3 --k 5 --out /tmp/r.json

策略：
    S1  EPMC 单查询
    S2  EPMC 3 查询 + 硬过滤 + 加权
    S3  EPMC 6 查询（时代分层）+ 帕累托
    M1  EPMC 3 + PubMed 2 + OpenAlex 1 + 帕累托
    M2  M1 + 预测研究过滤器（Ingui/Haynes 思路）
    M3  EPMC 6 + PubMed 2 + OpenAlex 1 + 帕累托
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import paper_search as ps  # noqa: E402
import hcc_agent as agent  # noqa: E402
import sources  # noqa: E402

for _s in ("M1", "M2", "M3"):
    agent.STRATEGIES[_s] = {"queries": 0, "hard": True, "pareto": True, "snowball": False}

POP = '("hepatocellular carcinoma" OR HCC)'
T0 = {
    "surgery": '(hepatectomy OR resection OR postoperative)',
    "tace": '(TACE OR transarterial OR chemoembolization OR HAIC)',
    "systemic": '(immunotherapy OR "immune checkpoint" OR atezolizumab OR sorafenib OR lenvatinib OR "systemic therapy")',
    "transplant": '(transplant OR "liver transplantation")',
    "any": '(hepatectomy OR resection OR TACE OR chemoembolization OR immunotherapy OR transplant)',
}
OUT = {
    "OS": '("overall survival" OR prognostic OR survival)',
    "RFS": '("recurrence-free survival" OR "disease-free survival" OR recurrence)',
    "PFS": '("progression-free survival" OR PFS)',
    "PHLF": '("liver failure" OR "post-hepatectomy liver failure" OR morbidity)',
    "response": '(response OR "objective response" OR RECIST)',
    "LOS": '("length of stay" OR "hospital stay")',
    "any": '(survival OR prognosis OR recurrence OR response OR "liver failure" OR "length of stay")',
}
MODEL = ('(nomogram OR "prediction model" OR "prognostic model" OR "risk score" '
         'OR "machine learning" OR "deep learning" OR "artificial intelligence")')
PRED_FILTER = ('(predict* OR prognostic OR "risk score" OR nomogram OR "prediction model" '
               'OR "clinical prediction rule" OR "risk prediction" OR "machine learning") '
               'AND (valid* OR calibration OR discrimination OR "C-index" OR concordance)')

TEXT_T0 = {"surgery": "hepatectomy resection", "tace": "TACE chemoembolization HAIC",
           "systemic": "immunotherapy immune checkpoint", "transplant": "liver transplantation",
           "any": "hepatectomy TACE immunotherapy transplant"}
TEXT_OUT = {"OS": "overall survival", "RFS": "recurrence-free survival",
            "PFS": "progression-free survival", "PHLF": "post-hepatectomy liver failure",
            "response": "objective response", "LOS": "length of stay",
            "any": "survival recurrence response liver failure length of stay"}


def epmc_query(case: dict, variant: str, pred: bool = False) -> str:
    body = (f"{POP} AND {T0.get(case['time_zero'], T0['any'])} "
            f"AND {OUT.get(case['outcome'], OUT['any'])}")
    body += f" AND {MODEL if variant != 'nomogram' else '(nomogram OR \"risk score\" OR scoring)'}"
    if variant == "validation":
        body += (' AND ("external validation" OR "validation cohort" OR "C-index" OR calibration)')
    if pred:
        body += f" AND {PRED_FILTER}"
    if not case.get("year_min"):
        return f"{body} AND SRC:MED"
    if variant == "old":
        years = f"PUB_YEAR:[{case['year_min']} TO 2022]"
    elif variant == "recent":
        years = f"PUB_YEAR:[2023 TO {time.localtime().tm_year - 1}]"
    else:
        years = f"PUB_YEAR:[{case['year_min']} TO {time.localtime().tm_year}]"
    return f"{body} AND {years} AND SRC:MED"


def pubmed_query(case: dict, variant: str, pred: bool = False) -> str:
    body = (f"{POP} AND {T0.get(case['time_zero'], T0['any'])} "
            f"AND {OUT.get(case['outcome'], OUT['any'])} AND {MODEL}")
    if variant == "validation":
        body += (' AND ("external validation" OR "validation cohort" OR "C-index" OR calibration)')
    if pred:
        body += f" AND {PRED_FILTER}"
    if not case.get("year_min"):
        return body
    return f'{body} AND ("{case["year_min"]}"[dp] : "{time.localtime().tm_year}"[dp])'


def openalex_query(case: dict, pred: bool = False) -> str:
    text = (f"hepatocellular carcinoma {TEXT_T0.get(case['time_zero'], TEXT_T0['any'])} "
            f"{TEXT_OUT.get(case['outcome'], TEXT_OUT['any'])} "
            f"prediction model nomogram external validation")
    if pred:
        text += " clinical prediction rule calibration discrimination"
    return text


def plans_for(case: dict, strategy: str) -> list[dict]:
    base = {"source": "epmc", "query": epmc_query(case, "base"), "sort": "", "tag": "base", "limit": 50}
    val = {"source": "epmc", "query": epmc_query(case, "validation"), "sort": "", "tag": "validation", "limit": 50}
    nom = {"source": "epmc", "query": epmc_query(case, "nomogram"), "sort": "", "tag": "nomogram", "limit": 50}
    cited = {"source": "epmc", "query": epmc_query(case, "base"), "sort": "CITED desc", "tag": "cited", "limit": 100}
    old = {"source": "epmc", "query": epmc_query(case, "old"), "sort": "", "tag": "old", "limit": 50}
    recent = {"source": "epmc", "query": epmc_query(case, "recent"), "sort": "", "tag": "recent", "limit": 50}
    pm1 = {"source": "pubmed", "query": pubmed_query(case, "base"), "sort": "", "tag": "pubmed_base", "limit": 25}
    pm2 = {"source": "pubmed", "query": pubmed_query(case, "validation"), "sort": "", "tag": "pubmed_validation", "limit": 25}
    oa = {"source": "openalex", "query": openalex_query(case), "sort": "", "tag": "openalex_base", "limit": 25}

    if strategy == "S1":
        return [base]
    if strategy == "S2":
        return [base, val, nom]
    if strategy == "S3":
        return [base, cited, val, nom, old, recent]
    if strategy == "M1":
        return [base, val, nom, pm1, pm2, oa]
    if strategy == "M2":
        return [
            {"source": "epmc", "query": epmc_query(case, "base", True), "sort": "", "tag": "base_pred", "limit": 50},
            {"source": "epmc", "query": epmc_query(case, "validation", True), "sort": "", "tag": "val_pred", "limit": 50},
            {"source": "epmc", "query": epmc_query(case, "nomogram", True), "sort": "", "tag": "nom_pred", "limit": 50},
            {"source": "pubmed", "query": pubmed_query(case, "base", True), "sort": "", "tag": "pubmed_pred", "limit": 25},
            {"source": "pubmed", "query": pubmed_query(case, "validation", True), "sort": "", "tag": "pubmed_val_pred", "limit": 25},
            {"source": "openalex", "query": openalex_query(case, True), "sort": "", "tag": "openalex_pred", "limit": 25},
        ]
    if strategy == "M3":
        return [base, cited, val, nom, old, recent, pm1, pm2, oa]
    raise ValueError(strategy)


def execute(case: dict, strategy: str) -> tuple[list[ps.Paper], dict, dict]:
    plans = plans_for(case, strategy)

    def one(it: dict):
        papers, meta = sources.search(it["source"], it["query"], it["limit"],
                                      case["year_min"], it.get("sort", ""))
        return it["source"], papers, meta

    t0 = time.time()
    papers: list[ps.Paper] = []
    metas: list[dict] = []
    papers_by_source: dict[str, list[ps.Paper]] = defaultdict(list)
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = [ex.submit(one, p) for p in plans]
        for fut in futures:
            src, got, m = fut.result()
            papers.extend(got)
            metas.append(m)
            papers_by_source[src].extend(got)

    keys_by_source = {src: {p.key for p in ps_} for src, ps_ in papers_by_source.items()}
    unique_to = {
        src: len(keys - set().union(*(v for k, v in keys_by_source.items() if k != src)))
        for src, keys in keys_by_source.items()
    }
    found_by: dict[str, set] = defaultdict(set)
    for src, ps_ in papers_by_source.items():
        for p in ps_:
            found_by[p.key].add(src)

    merged = sources.merge(papers)
    meta = {
        "calls": sum(m.get("calls", 1) for m in metas),
        "errors": sum(1 for m in metas if m.get("error")),
        "latency_ms": int((time.time() - t0) * 1000),
        "raw": len(papers),
        "unique": len(merged),
        "queries": metas,
        "relaxed": [],
        "layers": 0,
        "after_type_year": 0,
        "after_hard": 0,
        "unique_to_source": unique_to,
        "papers_by_source": {k: len(v) for k, v in papers_by_source.items()},
    }
    return merged, meta, {"found_by": found_by, "keys_by_source": keys_by_source}


def run_case(case: dict, strategy: str, k: int = 5) -> dict:
    merged, meta, info = execute(case, strategy)
    case_obj = agent.AgentCase(**case)
    kept, relaxed, kept_n = agent.filter_papers(merged, case_obj, True)
    agent.score_all(kept, case_obj)
    top = agent.rank(kept, case_obj, strategy, k)

    meta.update({"relaxed": relaxed, "after_type_year": kept_n, "after_hard": len(kept),
                 "layers": len(ps.pareto_layers(kept)) if kept else 0})
    payload = agent.build_result(case_obj, strategy, top, kept, meta)
    for item, p in zip(payload.get("top_k") or [], top):
        item["abstract"] = p.abstract[:1600]
        item["source"] = p.url
        item["found_by"] = sorted(info["found_by"].get(p.key, []))
    return payload


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("--scenarios", required=True)
    p.add_argument("--id", required=True)
    p.add_argument("--strategy", required=True, choices=["S1", "S2", "S3", "M1", "M2", "M3"])
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--out")
    args = ap.parse_args()

    data = json.loads(Path(args.scenarios).read_text(encoding="utf-8"))
    entry = next(s for s in data["scenarios"] if s["id"] == args.id)
    payload = run_case(entry["case"], args.strategy, args.k)
    payload["scenario_id"] = args.id
    payload["description"] = entry["description"]
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"saved {args.out} top={len(payload.get('top_k') or [])} calls={payload['meta']['calls']}")
    else:
        print(text)


if __name__ == "__main__":
    main()
