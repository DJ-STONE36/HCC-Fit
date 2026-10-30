"""最小结果校验器（不依赖 jsonschema）。"""

from __future__ import annotations

import json
import sys


def validate(r: dict) -> list[str]:
    errs = []
    for k in ("query_id", "input_scenario", "search_scenario", "route", "confidence",
              "assumptions", "papers", "meta", "empty_reason"):
        if k not in r:
            errs.append(f"missing top-level: {k}")
    sc = r.get("search_scenario") or {}
    for k in ("outcome", "time_zero", "horizon_months"):
        if k not in sc:
            errs.append(f"search_scenario missing: {k}")
    if not isinstance(r.get("input_scenario"), dict):
        errs.append("input_scenario must be object")
    if r.get("confidence") not in ("high", "medium", "low"):
        errs.append("confidence must be high/medium/low")
    if r.get("empty_reason") not in (None, "hard_constraints_no_match",
                                     "insufficient_matches", "upstream_unavailable"):
        errs.append("bad empty_reason")
    for i, p in enumerate(r.get("papers") or []):
        for k in ("title", "scores", "why", "risk", "verify", "read"):
            if k not in p:
                errs.append(f"paper[{i}] missing {k}")
        for k in ("M", "E", "R"):
            v = (p.get("scores") or {}).get(k)
            if not isinstance(v, (int, float)) or not (0 <= v <= 1):
                errs.append(f"paper[{i}] bad score {k}={v}")
        if not (p.get("pmid") or p.get("doi")):
            errs.append(f"paper[{i}] no pmid/doi")
    if len(r.get("papers") or []) > 8:
        errs.append("too many papers")
    interp = r.get("interpretations")
    if interp is not None:
        if not interp:
            errs.append("empty interpretations")
        if sum(1 for i in interp if i.get("searched")) > 1:
            errs.append("more than one searched interpretation")
    return errs


if __name__ == "__main__":
    path = sys.argv[1]
    errs = validate(json.loads(open(path, encoding="utf-8").read()))
    print("OK" if not errs else "\n".join(errs))
    sys.exit(0 if not errs else 1)
